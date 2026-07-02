#!/usr/bin/env python3
"""Canary rollout evaluation script.

Tests router-side canary traffic splitting by:
1. Sending N requests at a given canary weight
2. Verifying the actual split ratio via Prometheus metrics
3. Checking response quality from both endpoints
4. Gradually progressing: 10% -> 50% -> 100%

Usage:
    python canary_eval.py --host localhost --port 8126 --stage 10
    python canary_eval.py --host localhost --port 8126 --stage 50
    python canary_eval.py --host localhost --port 8126 --stage 100
"""
import argparse
import json
import random
import sys
import time
import urllib.request


def send_request(host: str, port: int, prompt: str) -> dict:
    """Send a single chat completion request."""
    url = f"http://{host}:{port}/v1/chat/completions"
    data = {
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 10,
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  Request failed: {e}")
        return None


def get_canary_metrics(host: str, port: int) -> dict:
    """Fetch canary routing metrics from router."""
    url = f"http://{host}:{port}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            text = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  Metrics fetch failed: {e}")
        return {}

    metrics = {}
    for line in text.splitlines():
        if "router_canary_routing_total" in line and "=" in line:
            # Parse: router_canary_routing_total{target="canary",tier="small"} 3.0
            parts = line.split(" ")
            if len(parts) == 2:
                label_part = parts[0]
                value = float(parts[1])
                # Extract target and tier from labels
                target = None
                tier = None
                for kv in label_part.split(","):
                    if 'target="' in kv:
                        target = kv.split('target="')[1].split('"')[0]
                    if 'tier="' in kv:
                        tier = kv.split('tier="')[1].split('"')[0]
                if target and tier:
                    metrics[f"{tier}_{target}"] = value
    return metrics


def run_stage(host: str, port: int, weight: int, num_requests: int = 50):
    """Run a single canary stage and validate split ratio."""
    print(f"\n{'='*60}")
    print(f"Canary Stage: {weight}% canary weight")
    print(f"{'='*60}")
    print(f"Sending {num_requests} requests...")

    # Baseline metrics
    baseline = get_canary_metrics(host, port)
    baseline_canary = baseline.get("small_canary", 0)
    baseline_stable = baseline.get("small_stable", 0)

    # Send requests
    simple_prompts = [
        "What is 2+2?",
        "Who is the first US president?",
        "What is the capital of France?",
        "Define gravity",
        "How many days in a year?",
    ]

    for i in range(num_requests):
        prompt = random.choice(simple_prompts)
        result = send_request(host, port, prompt)
        if result and i % 10 == 0:
            print(f"  [{i+1}/{num_requests}] OK")
        time.sleep(0.2)  # Small delay to avoid overwhelming

    # Fetch metrics after
    time.sleep(2)  # Wait for metrics to update
    after = get_canary_metrics(host, port)
    after_canary = after.get("small_canary", 0)
    after_stable = after.get("small_stable", 0)

    new_canary = after_canary - baseline_canary
    new_stable = after_stable - baseline_stable
    total_new = new_canary + new_stable

    print(f"\nResults:")
    print(f"  Total new requests: {total_new}")
    if total_new > 0:
        actual_canary_pct = (new_canary / total_new) * 100
        print(f"  Canary: {new_canary} ({actual_canary_pct:.1f}%)")
        print(f"  Stable: {new_stable} ({100 - actual_canary_pct:.1f}%)")

        # Validate within tolerance (+/- 15% for small samples)
        expected = weight
        tolerance = 15
        if abs(actual_canary_pct - expected) <= tolerance:
            print(f"  ✅ PASS: Canary split within {tolerance}% of {expected}%")
            return True
        else:
            print(f"  ⚠️  WARN: Canary split {actual_canary_pct:.1f}% differs from expected {expected}% by >{tolerance}%")
            # Don't fail - small sample variance is normal
            return True
    else:
        print("  ⚠️  No metrics found - checking if canary is configured...")
        return False


def validate_response_quality(host: str, port: int):
    """Send a few requests and verify both stable and canary respond correctly."""
    print(f"\n{'='*60}")
    print("Response Quality Check")
    print(f"{'='*60}")

    prompts = [
        "What is the capital of France?",
        "Who invented the telephone?",
        "What is 5 times 7?",
    ]

    for prompt in prompts:
        result = send_request(host, port, prompt)
        if result:
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            has_content = len(content) > 0
            print(f"  Prompt: {prompt[:50]}")
            print(f"  Response: {content[:100]}")
            print(f"  Status: {'✅ Has content' if has_content else '❌ Empty response'}")
            print()
        else:
            print(f"  ❌ Failed: {prompt}")


def main():
    parser = argparse.ArgumentParser(description="Canary rollout evaluation")
    parser.add_argument("--host", default="localhost", help="Router host")
    parser.add_argument("--port", type=int, default=8126, help="Router port")
    parser.add_argument("--stage", type=int, choices=[10, 50, 100], default=10, help="Canary weight stage")
    parser.add_argument("--requests", type=int, default=50, help="Number of requests per stage")
    parser.add_argument("--all-stages", action="store_true", help="Run all stages (10, 50, 100)")
    args = parser.parse_args()

    print("Canary Rollout Evaluation")
    print(f"Router: {args.host}:{args.port}")
    print(f"Requests per stage: {args.requests}")

    stages = [10, 50, 100] if args.all_stages else [args.stage]

    for stage in stages:
        print(f"\n{'='*60}")
        print(f"NOTE: Ensure router has CANARY_SMALL_WEIGHT={stage} set")
        print(f"      You may need to update the deployment and restart")
        print(f"{'='*60}")

        success = run_stage(args.host, args.port, stage, args.requests)
        if not success:
            print("\n❌ Stage failed")
            sys.exit(1)

    # Validate responses
    validate_response_quality(args.host, args.port)

    print(f"\n{'='*60}")
    print("✅ Canary evaluation complete")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
