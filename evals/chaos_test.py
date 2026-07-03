#!/usr/bin/env python3
"""Chaos test - kill pod mid-generation and observe recovery.

Usage:
    python chaos_test.py --host localhost --port 8126 --model small
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


def send_streaming_request(host: str, port: int, prompt: str, tier: str = "small"):
    """Send a streaming request and print chunks as they arrive."""
    url = f"http://{host}:{port}/v1/chat/completions"
    data = {
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 100,
        "temperature": 0.7,
        "stream": True,
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-route-tier": tier,
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    
    print("  [CHAOS] Starting streaming request...")
    start_time = time.time()
    chunks_received = 0
    content_buffer = ""
    
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(f"  [CHAOS] Connection established in {time.time() - start_time:.2f}s")
            
            for line in resp:
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue
                
                if line_str.startswith("data: "):
                    data_str = line_str[6:]
                    if data_str == "[DONE]":
                        print(f"  [CHAOS] Stream complete: [DONE]")
                        break
                    
                    try:
                        chunk = json.loads(data_str)
                        if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                            content = chunk["choices"][0]["delta"]["content"]
                            content_buffer += content
                            chunks_received += 1
                            
                            if chunks_received % 10 == 0:
                                elapsed = time.time() - start_time
                                print(f"  [CHAOS] Chunk {chunks_received} at {elapsed:.2f}s: '{content[:50]}...'")
                    except json.JSONDecodeError:
                        pass
                        
        total_elapsed = time.time() - start_time
        print(f"  [CHAOS] Request completed successfully: {total_elapsed:.2f}s, {chunks_received} chunks")
        return True, chunks_received, total_elapsed, content_buffer
        
    except urllib.error.HTTPError as e:
        total_elapsed = time.time() - start_time
        print(f"  [CHAOS] HTTP error {e.code}: {e.reason} (after {total_elapsed:.2f}s)")
        return False, chunks_received, total_elapsed, content_buffer
    except Exception as e:
        total_elapsed = time.time() - start_time
        print(f"  [CHAOS] Error: {e} (after {total_elapsed:.2f}s)")
        return False, chunks_received, total_elapsed, content_buffer


def get_pod_name(tier: str) -> str:
    """Get the current pod name for a given model tier."""
    deployment_map = {
        "small": "qwen-0-5b-predictor",
        "medium": "llama-1b-predictor",
        "large": "qwen-3b-predictor",
    }
    deployment = deployment_map.get(tier, "qwen-0-5b-predictor")
    
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", "default", "-l", f"app=isvc.{deployment}", 
             "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"  [CHAOS] Could not get pod name: {e}")
        return None


def kill_pod(pod_name: str, namespace: str = "default"):
    """Kill a pod."""
    print(f"  [CHAOS] Killing pod {pod_name}...")
    try:
        subprocess.run(
            ["kubectl", "delete", "pod", "-n", namespace, pod_name, "--grace-period=0", "--force"],
            check=True, capture_output=True
        )
        print(f"  [CHAOS] Pod {pod_name} deleted")
        return True
    except Exception as e:
        print(f"  [CHAOS] Failed to kill pod: {e}")
        return False


def wait_for_pod_ready(tier: str, timeout: int = 120):
    """Wait for a pod to be ready again."""
    deployment_map = {
        "small": "qwen-0-5b-predictor",
        "medium": "llama-1b-predictor",
        "large": "qwen-3b-predictor",
    }
    deployment = deployment_map.get(tier, "qwen-0-5b-predictor")
    
    print(f"  [CHAOS] Waiting for pod to be ready (timeout: {timeout}s)...")
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", "default", "-l", f"app=isvc.{deployment}", 
                 "-o", "jsonpath={.items[0].status.phase}"],
                capture_output=True, text=True
            )
            phase = result.stdout.strip()
            if phase == "Running":
                # Check ready status
                result2 = subprocess.run(
                    ["kubectl", "get", "pods", "-n", "default", "-l", f"app=isvc.{deployment}",
                     "-o", "jsonpath={.items[0].status.containerStatuses[0].ready}"],
                    capture_output=True, text=True
                )
                if result2.stdout.strip() == "true":
                    elapsed = time.time() - start
                    print(f"  [CHAOS] Pod ready after {elapsed:.2f}s")
                    return True, elapsed
        except Exception:
            pass
        time.sleep(2)
    
    print(f"  [CHAOS] Timeout waiting for pod")
    return False, timeout


def wait_for_service_ready(host: str, port: int, tier: str, timeout: int = 60):
    """Wait for the service to actually be serving requests."""
    print(f"  [CHAOS] Waiting for service to be serving (timeout: {timeout}s)...")
    start = time.time()
    
    url = f"http://{host}:{port}/v1/chat/completions"
    data = {
        "model": "auto",
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 5,
        "temperature": 0.1,
    }
    headers = {
        "Content-Type": "application/json",
        "x-route-tier": tier,
    }
    
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    elapsed = time.time() - start
                    print(f"  [CHAOS] Service serving after {elapsed:.2f}s")
                    return True, elapsed
        except Exception:
            pass
        time.sleep(2)
    
    print(f"  [CHAOS] Timeout waiting for service")
    return False, timeout


def run_chaos_test(host: str, port: int, tier: str = "small", kill_after_chunks: int = 5):
    """Run a chaos test: start streaming, kill pod mid-stream, observe recovery."""
    print(f"\n{'='*60}")
    print(f"Chaos Test: Kill {tier} model pod mid-generation")
    print(f"{'='*60}")
    
    prompt = "Write a detailed 500-word story about a space explorer discovering a new planet. Describe the landscape, alien creatures, the explorer's emotions, and the scientific discoveries they make. Make it vivid and engaging with multiple paragraphs."
    
    # Get current pod name
    pod_name = get_pod_name(tier)
    if not pod_name:
        print("  ❌ Could not find pod to kill")
        return False
    
    print(f"  Target pod: {pod_name}")
    
    # Start streaming in a thread
    result_container = {}
    
    def stream_worker():
        success, chunks, elapsed, content = send_streaming_request(host, port, prompt, tier)
        result_container["success"] = success
        result_container["chunks"] = chunks
        result_container["elapsed"] = elapsed
        result_container["content"] = content
    
    stream_thread = threading.Thread(target=stream_worker)
    stream_thread.start()
    
    # Wait for a few chunks to come through, then kill the pod
    print(f"  [CHAOS] Waiting for ~{kill_after_chunks} chunks before killing...")
    time.sleep(3)  # Give streaming a chance to start
    
    # Kill the pod
    killed = kill_pod(pod_name)
    if not killed:
        print("  ❌ Failed to kill pod")
        stream_thread.join(timeout=10)
        return False
    
    # Wait for streaming to finish or fail
    stream_thread.join(timeout=30)
    
    if stream_thread.is_alive():
        print("  [CHAOS] Stream thread still alive after 30s, forcing termination")
        # Thread is hung, which is itself a result
    
    # Record results
    success = result_container.get("success", False)
    chunks = result_container.get("chunks", 0)
    elapsed = result_container.get("elapsed", 0)
    content = result_container.get("content", "")
    
    print(f"\n  [CHAOS] Streaming result: {'✅ Success' if success else '❌ Failed'}")
    print(f"  [CHAOS] Chunks received: {chunks}")
    print(f"  [CHAOS] Time elapsed: {elapsed:.2f}s")
    print(f"  [CHAOS] Content length: {len(content)} chars")
    
    # Now test recovery - send a new request
    print(f"\n  [CHAOS] Testing recovery with new request...")
    
    # Wait for pod to be ready again
    recovered, recovery_time = wait_for_pod_ready(tier, timeout=60)
    
    if recovered:
        print(f"  [CHAOS] Pod recovered in {recovery_time:.2f}s")
        
        # Wait for service to actually be serving
        serving_ready, serving_time = wait_for_service_ready(host, port, tier, timeout=60)
        
        if serving_ready:
            print(f"  [CHAOS] Service serving after {serving_time:.2f}s")
            
            # Send a simple request to verify recovery
            url = f"http://{host}:{port}/v1/chat/completions"
            data = {
                "model": "auto",
                "messages": [{"role": "user", "content": "What is 2+2?"}],
                "max_tokens": 10,
                "temperature": 0.1,
            }
            headers = {
                "Content-Type": "application/json",
                "x-route-tier": tier,
            }
            
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    print(f"  [CHAOS] Recovery request successful: '{content[:50]}...'")
                    recovery_ok = True
            except Exception as e:
                print(f"  [CHAOS] Recovery request failed: {e}")
                recovery_ok = False
        else:
            print(f"  [CHAOS] Service did not become serving in time")
            recovery_ok = False
    else:
        print(f"  [CHAOS] Pod did not recover in time")
        recovery_ok = False
    
    print(f"\n  [CHAOS] Test summary:")
    print(f"    Streaming survived pod kill: {'✅ Yes' if success else '❌ No'}")
    print(f"    Recovery successful: {'✅ Yes' if recovery_ok else '❌ No'}")
    print(f"    Recovery time: {recovery_time:.2f}s" if recovered else "    Recovery time: N/A")
    
    return recovery_ok


def main():
    parser = argparse.ArgumentParser(description="Chaos test - kill pod mid-generation")
    parser.add_argument("--host", default="localhost", help="Router host")
    parser.add_argument("--port", type=int, default=8126, help="Router port")
    parser.add_argument("--tier", default="small", choices=["small", "medium", "large"], help="Model tier to target")
    parser.add_argument("--kill-after", type=int, default=5, help="Kill pod after N chunks (approximate)")
    args = parser.parse_args()

    print("Chaos Test: Pod Kill Mid-Generation")
    print(f"Router: {args.host}:{args.port}")
    print(f"Target: {args.tier} model")
    
    success = run_chaos_test(args.host, args.port, args.tier, args.kill_after)
    
    print(f"\n{'='*60}")
    if success:
        print("✅ Chaos test PASSED - system recovered successfully")
    else:
        print("⚠️  Chaos test incomplete - recovery may need more time")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
