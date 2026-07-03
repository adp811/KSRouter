import asyncio
import logging
import os
import random
import re
from typing import Optional, Tuple

import httpx

from .metrics import route_decisions_total, fallback_count_total, router_overhead_seconds, canary_routing_total

logger = logging.getLogger("router")

# Model endpoint URLs (from inside cluster)
MODEL_ENDPOINTS = {
    "small": os.environ.get("SMALL_MODEL_URL", "http://qwen-0-5b-predictor.default.svc.cluster.local/v1/chat/completions"),
    "medium": os.environ.get("MEDIUM_MODEL_URL", "http://llama-1b-predictor.default.svc.cluster.local/v1/chat/completions"),
    "large": os.environ.get("LARGE_MODEL_URL", "http://qwen-3b-predictor.default.svc.cluster.local/v1/chat/completions"),
}

# Canary configuration per tier (weight 0-100)
# e.g., CANARY_SMALL_WEIGHT=10 means 10% to canary, 90% to stable
CANARY_CONFIG = {
    "small": {
        "weight": int(os.environ.get("CANARY_SMALL_WEIGHT", "0")),
        "stable_url": os.environ.get("SMALL_MODEL_URL", "http://qwen-0-5b-predictor.default.svc.cluster.local/v1/chat/completions"),
        "canary_url": os.environ.get("CANARY_SMALL_URL", ""),
    },
    "medium": {
        "weight": int(os.environ.get("CANARY_MEDIUM_WEIGHT", "0")),
        "stable_url": os.environ.get("MEDIUM_MODEL_URL", "http://llama-1b-predictor.default.svc.cluster.local/v1/chat/completions"),
        "canary_url": os.environ.get("CANARY_MEDIUM_URL", ""),
    },
    "large": {
        "weight": int(os.environ.get("CANARY_LARGE_WEIGHT", "0")),
        "stable_url": os.environ.get("LARGE_MODEL_URL", "http://qwen-3b-predictor.default.svc.cluster.local/v1/chat/completions"),
        "canary_url": os.environ.get("CANARY_LARGE_URL", ""),
    },
}


def apply_canary(tier: str, url: str) -> str:
    """Apply canary routing if configured for the tier."""
    config = CANARY_CONFIG.get(tier)
    if not config or not config["canary_url"] or config["weight"] <= 0:
        return url
    
    roll = random.randint(1, 100)
    if roll <= config["weight"]:
        canary_routing_total.labels(tier=tier, target="canary").inc()
        logger.info(f"Canary routing: {tier} -> canary (roll={roll}/{config['weight']})")
        return config["canary_url"]
    else:
        canary_routing_total.labels(tier=tier, target="stable").inc()
        return config["stable_url"]

CLASSIFIER_PROMPT = """You are a request classifier. Classify the user request complexity.

Respond with EXACTLY ONE word: simple|moderate|complex
No other text, no explanation, no punctuation.

- simple: factual questions ("What is X?"), simple math, definitions, short prompts under 50 words
- moderate: multi-step reasoning, code explanations, comparisons, 50-200 words
- complex: deep analysis, essays, creative writing, complex algorithms, proofs, over 200 words

Request: {prompt}

Answer:"""

# Keywords that indicate complexity
COMPLEX_KEYWORDS = [
    "explain in detail", "comprehensive", "analysis", "compare and contrast",
    "write a", "generate a", "implement", "debug", "optimize", "algorithm",
    "proof", "theorem", "essay", "report", "document", "review", "critique"
]

MEDIUM_KEYWORDS = [
    "explain", "how does", "difference between", "compare", "contrast",
    "works", "function", "example", "steps", "process", "overview"
]

CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```")
SIMPLE_PATTERNS = [
    r"^what is\b", r"^who is\b", r"^when is\b", r"^where is\b",
    r"^how (many|much|old|long)\b", r"^define\b", r"^tell me\b"
]


async def heuristic_route(prompt: str) -> str:
    """Fast heuristic-based routing."""
    prompt_lower = prompt.lower()
    word_count = len(prompt.split())
    
    # Check for code blocks
    has_code = bool(CODE_BLOCK_PATTERN.search(prompt))
    
    # Check for complex keywords
    has_complex_keywords = any(kw in prompt_lower for kw in COMPLEX_KEYWORDS)
    
    # Check for simple patterns
    is_simple_pattern = any(re.search(pat, prompt_lower) for pat in SIMPLE_PATTERNS)
    
    # Check for medium keywords
    has_medium_keywords = any(kw in prompt_lower for kw in MEDIUM_KEYWORDS)
    
    # Decision logic - map to tier names
    if word_count <= 30 and (is_simple_pattern or not has_complex_keywords) and not has_medium_keywords and not has_code:
        return "small"  # simple -> small tier
    elif word_count > 150 or has_complex_keywords or (has_code and word_count > 50):
        return "large"  # complex -> large tier
    else:
        return "medium"  # moderate -> medium tier


async def classifier_route(prompt: str, timeout: float = 2.0) -> Optional[str]:
    """Use the small model as a classifier with strict timeout."""
    try:
        classification_prompt = CLASSIFIER_PROMPT.format(prompt=prompt[:500])  # truncate long prompts
        
        payload = {
            "model": "classifier",
            "messages": [{"role": "user", "content": classification_prompt}],
            "max_tokens": 10,
            "temperature": 0.0,
        }
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                MODEL_ENDPOINTS["small"],
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            
            classification = data["choices"][0]["message"]["content"].strip().lower()
            
            # Map classifier output to tier names
            if "simple" in classification:
                return "small"  # simple -> small tier
            elif "moderate" in classification:
                return "medium"
            elif "complex" in classification:
                return "large"
            else:
                return None
                
    except httpx.TimeoutException:
        logger.warning("Classifier timeout, falling back to heuristic")
        fallback_count_total.labels(reason="timeout").inc()
        return None
    except Exception as e:
        logger.warning(f"Classifier error: {e}, falling back to heuristic")
        fallback_count_total.labels(reason="error").inc()
        return None


async def determine_tier(prompt: str, explicit_tier: Optional[str] = None) -> Tuple[str, str]:
    """
    Determine routing tier.
    
    Returns: (tier, method)
    tier: small|medium|large
    method: explicit|classifier|heuristic
    """
    start_time = asyncio.get_event_loop().time()
    
    # 1. Explicit override takes highest priority
    if explicit_tier:
        tier = explicit_tier
        method = "explicit"
        route_decisions_total.labels(tier=tier, method=method).inc()
        elapsed = asyncio.get_event_loop().time() - start_time
        router_overhead_seconds.labels(method=method).observe(elapsed)
        return tier, method
    
    # 2. Fast heuristic first
    heuristic_result = await heuristic_route(prompt)
    
    # 3. If heuristic says "medium" (ambiguous) AND prompt is long enough,
    # try classifier for refinement. Short prompts are handled purely by heuristic
    # for accuracy (the 0.5B classifier is unreliable for simple prompts).
    word_count = len(prompt.split())
    if heuristic_result == "medium" and word_count > 80:
        classifier_result = await classifier_route(prompt)
        if classifier_result:
            tier = classifier_result
            method = "classifier"
            route_decisions_total.labels(tier=tier, method=method).inc()
            elapsed = asyncio.get_event_loop().time() - start_time
            router_overhead_seconds.labels(method=method).observe(elapsed)
            return tier, method
    
    # 4. Use heuristic result (either small, medium, or large)
    tier = heuristic_result
    method = "heuristic"
    route_decisions_total.labels(tier=tier, method=method).inc()
    elapsed = asyncio.get_event_loop().time() - start_time
    router_overhead_seconds.labels(method=method).observe(elapsed)
    return tier, method
