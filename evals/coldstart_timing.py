#!/usr/bin/env python3
"""Large model cold-start timing script.

Measures time from first request to a scaled-to-zero large model
until the first successful response (including cold-start wait).

Usage:
    python coldstart_timing.py --host localhost --port 8126 --trials 3
"""
import argparse
import json
import time
import urllib.request
import urllib.error
import sys


def send_request(host: str, port: int, prompt: str, tier: str = "large", stream: bool = False) -> tuple:
    """Send a request and return (response_dict, elapsed_seconds, first_token_time).
    
    Handles 503 cold-start responses by retrying after the Retry-After header.
    """
    url = f"http://{host}:{port}/v1/chat/completions"
    data = {
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 20,
        "temperature": 0.1,
        "stream": stream,
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-route-tier": tier,
    }
    
    total_start = time.time()
    total_elapsed = 0.0
    first_token_time = None
    attempts = 0
    max_attempts = 5
    
    while attempts < max_attempts:
        attempts += 1
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        
        try:
            req_start = time.time()
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_body = resp.read().decode("utf-8")
                req_elapsed = time.time() - req_start
                
                if stream:
                    # For streaming, measure time to first chunk
                    first_token_time = req_elapsed
                    # We got a successful response - return summary
                    return {"status": "success", "streaming": True}, time.time() - total_start, first_token_time
                else:
                    result = json.loads(resp_body)
                    result["_req_elapsed"] = req_elapsed
                    return result, time.time() - total_start, req_elapsed
                    
        except urllib.error.HTTPError as e:
            if e.code == 503:
                # Cold start - read retry-after header
                retry_after = e.headers.get("Retry-After", "30")
                try:
                    wait_sec = int(retry_after)
                except ValueError:
                    wait_sec = 30
                
                print(f"  Attempt {attempts}: 503 - model warming up, retrying after {wait_sec}s...")
                time.sleep(wait_sec)
            else:
                print(f"  Attempt {attempts}: HTTP {e.code}")
                return None, time.time() - total_start, None
        except Exception as e:
            print(f"  Attempt {attempts}: Error - {e}")
            return None, time.time() - total_start, None
    
    print(f"  Max attempts ({max_attempts}) reached")
    return None, time.time() - total_start, None


def ensure_scaled_zero():
    """Ensure the large model is scaled to zero before starting."""
    import subprocess
    try:
        result = subprocess.run(
            ["kubectl", "get", "deployment", "-n", "default", "qwen-3b-predictor", "-o", "jsonpath={.spec.replicas}"],
            capture_output=True, text=True, check=True
        )
        replicas = int(result.stdout.strip())
        if replicas > 0:
            print(f"  Scaling down large model (current: {replicas} replicas)...")
            subprocess.run(
                ["kubectl", "scale", "deployment", "-n", "default", "qwen-3b-predictor", "--replicas=0"],
                check=True, capture_output=True
            )
            # Wait for pods to terminate
            for _ in range(30):
                result = subprocess.run(
                    ["kubectl", "get", "pods", "-n", "default", "-l", "app=isvc.qwen-3b-predictor", "--no-headers"],
                    capture_output=True, text=True
                )
                if not result.stdout.strip():
                    print("  Large model scaled to 0")
                    return True
                time.sleep(2)
            print("  Warning: pods still terminating, continuing anyway")
        else:
            print("  Large model is already at 0 replicas")
        return True
    except Exception as e:
        print(f"  Warning: could not check/scale model: {e}")
        return False


def run_trial(host: str, port: int, trial_num: int, prompt: str) -> dict:
    """Run a single cold-start trial."""
    print(f"\n{'='*60}")
    print(f"Trial {trial_num}: Large model cold-start")
    print(f"{'='*60}")
    print(f"Prompt: {prompt}")
    
    # Ensure model is at 0
    ensure_scaled_zero()
    
    # Wait a bit for KEDA to register scale-to-zero
    print("  Waiting 5s for KEDA to stabilize...")
    time.sleep(5)
    
    # Send request
    print("  Sending request to large model...")
    start_time = time.time()
    result, total_elapsed, req_elapsed = send_request(host, port, prompt, tier="large")
    
    if result is None:
        print(f"  ❌ Trial {trial_num}: FAILED")
        return {
            "trial": trial_num,
            "success": False,
            "total_elapsed": total_elapsed,
            "error": "Request failed",
        }
    
    # Extract response content
    content = ""
    if result.get("status") == "success" and result.get("streaming"):
        content = "[streaming response]"
    elif "choices" in result:
        content = result["choices"][0].get("message", {}).get("content", "")
    
    cold_start_time = total_elapsed
    
    print(f"  ✅ Trial {trial_num}: SUCCESS")
    print(f"  Total time (cold-start + generation): {cold_start_time:.2f}s")
    print(f"  Request processing time: {req_elapsed:.2f}s")
    print(f"  Response preview: {content[:100]}...")
    
    return {
        "trial": trial_num,
        "success": True,
        "total_elapsed": cold_start_time,
        "request_elapsed": req_elapsed,
        "response_preview": content[:100],
    }


def main():
    parser = argparse.ArgumentParser(description="Large model cold-start timing")
    parser.add_argument("--host", default="localhost", help="Router host")
    parser.add_argument("--port", type=int, default=8126, help="Router port")
    parser.add_argument("--trials", type=int, default=3, help="Number of trials")
    parser.add_argument("--prompt", default="Explain the theory of relativity in simple terms", help="Prompt to use")
    args = parser.parse_args()

    print("Large Model Cold-Start Timing Test")
    print(f"Router: {args.host}:{args.port}")
    print(f"Trials: {args.trials}")
    print(f"Prompt: {args.prompt}")

    results = []
    for i in range(1, args.trials + 1):
        result = run_trial(args.host, args.port, i, args.prompt)
        results.append(result)
        
        if i < args.trials:
            print(f"\n  Waiting 30s before next trial (to allow scale-to-zero)...")
            time.sleep(30)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    
    successes = [r for r in results if r["success"]]
    if successes:
        times = [r["total_elapsed"] for r in successes]
        avg = sum(times) / len(times)
        min_t = min(times)
        max_t = max(times)
        
        print(f"Successful trials: {len(successes)}/{args.trials}")
        print(f"Cold-start times:")
        for r in successes:
            print(f"  Trial {r['trial']}: {r['total_elapsed']:.2f}s")
        print(f"\nAverage: {avg:.2f}s")
        print(f"Min: {min_t:.2f}s")
        print(f"Max: {max_t:.2f}s")
    else:
        print("All trials failed!")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("✅ Cold-start timing complete")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
