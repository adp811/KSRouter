import asyncio
import json
import logging
import time
import uuid
from collections import deque
from typing import AsyncIterator, Optional

import httpx
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .metrics import (
    upstream_latency_seconds,
    upstream_errors_total,
    time_to_first_token_seconds,
    tokens_streamed_total,
    active_requests,
    recent_requests,
    recent_requests_by_tier,
    generate_latest,
    CONTENT_TYPE_LATEST
)
from .routing import determine_tier, MODEL_ENDPOINTS, apply_canary

RECENT_WINDOW = 60.0
recent_timestamps = deque()
recent_timestamps_by_tier = {"small": deque(), "medium": deque(), "large": deque()}
recent_lock = asyncio.Lock()

async def update_recent_requests(tier: Optional[str] = None):
    now = time.time()
    async with recent_lock:
        recent_timestamps.append(now)
        while recent_timestamps and (now - recent_timestamps[0]) > RECENT_WINDOW:
            recent_timestamps.popleft()
        recent_requests.set(len(recent_timestamps))

        if tier:
            recent_timestamps_by_tier[tier].append(now)
            while recent_timestamps_by_tier[tier] and (now - recent_timestamps_by_tier[tier][0]) > RECENT_WINDOW:
                recent_timestamps_by_tier[tier].popleft()
            recent_requests_by_tier.labels(tier=tier).set(len(recent_timestamps_by_tier[tier]))

logger = logging.getLogger("router")

app = FastAPI(title="KSRouter", version="1.0.0")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        
        # Set request_id on logger adapter
        old_request_id = getattr(logger, "request_id", None)
        logger.request_id = request_id
        
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        
        if old_request_id:
            logger.request_id = old_request_id
        
        return response


app.add_middleware(RequestIdMiddleware)


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/metrics")
async def metrics():
    await update_recent_requests()
    # Clean up per-tier deques as well
    now = time.time()
    async with recent_lock:
        for t, dq in recent_timestamps_by_tier.items():
            while dq and (now - dq[0]) > RECENT_WINDOW:
                dq.popleft()
            recent_requests_by_tier.labels(tier=t).set(len(dq))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_route_tier: Optional[str] = Header(None, alias="x-route-tier")
):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    
    body = await request.json()
    messages = body.get("messages", [])
    
    # Extract prompt text for routing
    prompt_text = ""
    if messages:
        last_message = messages[-1]
        if isinstance(last_message, dict):
            prompt_text = last_message.get("content", "")
    
    # Determine tier
    tier, method = await determine_tier(prompt_text, explicit_tier=x_route_tier)
    
    logger.info(
        "Routing decision",
        extra={
            "request_id": request_id,
            "tier": tier,
            "method": method,
            "prompt_length": len(prompt_text),
        }
    )
    
    # Route to appropriate model, with canary if configured
    upstream_url = apply_canary(tier, MODEL_ENDPOINTS[tier])
    
    # Update request body with routed model name
    body["model"] = tier
    
    is_streaming = body.get("stream", False)

    await update_recent_requests(tier)
    active_requests.inc()
    start_time = time.time()
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if is_streaming:
                return await stream_response(client, upstream_url, body, tier, request_id)
            else:
                return await non_stream_response(client, upstream_url, body, tier, request_id)
    except httpx.ConnectError as e:
        # Large model may be scaled to zero (cold start)
        if tier == "large":
            upstream_errors_total.labels(tier=tier, error_type="cold_start").inc()
            logger.warning(
                "Large model cold start - returning 503 with retry",
                extra={"request_id": request_id, "tier": tier}
            )
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "30"},
                content={
                    "error": "Large model is warming up! 🔥",
                    "message": "Our large brain is taking a power nap. Give it 30 seconds to wake up and grab some coffee! ☕",
                    "request_id": request_id,
                    "tier": tier,
                    "retry_after": 30
                }
            )
        else:
            upstream_errors_total.labels(tier=tier, error_type="connect").inc()
            logger.error(f"Upstream connect error: {e}", extra={"request_id": request_id, "tier": tier})
            return JSONResponse(
                status_code=502,
                content={"error": "Upstream connect error", "request_id": request_id}
            )
    except httpx.TimeoutError:
        upstream_errors_total.labels(tier=tier, error_type="timeout").inc()
        logger.error("Upstream timeout", extra={"request_id": request_id, "tier": tier})
        return JSONResponse(
            status_code=504,
            content={"error": "Upstream timeout", "request_id": request_id}
        )
    except Exception as e:
        upstream_errors_total.labels(tier=tier, error_type="other").inc()
        logger.error(f"Upstream error: {e}", extra={"request_id": request_id, "tier": tier})
        return JSONResponse(
            status_code=502,
            content={"error": "Upstream error", "request_id": request_id}
        )
    finally:
        active_requests.dec()
        elapsed = time.time() - start_time
        upstream_latency_seconds.labels(tier=tier).observe(elapsed)


async def non_stream_response(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    tier: str,
    request_id: str
) -> Response:
    response = await client.post(
        url,
        json=body,
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    
    data = response.json()
    
    # Add routing metadata
    data["router_metadata"] = {
        "tier": tier,
        "request_id": request_id,
    }
    
    return JSONResponse(content=data)


async def stream_response(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    tier: str,
    request_id: str
) -> StreamingResponse:
    
    async def event_generator() -> AsyncIterator[str]:
        first_token = True
        token_start = time.time()
        
        async with client.stream(
            "POST",
            url,
            json=body,
            headers={"Content-Type": "application/json"}
        ) as response:
            response.raise_for_status()
            
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    
                    if data_str == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break
                    
                    try:
                        data = json.loads(data_str)
                        
                        # Record TTFT
                        if first_token and data.get("choices"):
                            first_token = False
                            ttft = time.time() - token_start
                            time_to_first_token_seconds.labels(tier=tier).observe(ttft)
                        
                        # Count tokens
                        if data.get("choices") and data["choices"][0].get("delta", {}).get("content"):
                            tokens_streamed_total.labels(tier=tier).inc()
                        
                        # Add router metadata to first chunk
                        if data.get("choices") and first_token is False:
                            if "router_metadata" not in data:
                                data["router_metadata"] = {
                                    "tier": tier,
                                    "request_id": request_id,
                                }
                        
                        yield f"data: {json.dumps(data)}\n\n"
                        
                    except json.JSONDecodeError:
                        yield f"data: {data_str}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "x-request-id": request_id,
            "x-route-tier": tier,
        }
    )
