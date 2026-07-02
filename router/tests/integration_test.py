#!/usr/bin/env python3
"""Integration test for LLM semantic router.

Tests a labeled corpus of prompts and verifies routing accuracy.
"""

import asyncio
import json
import sys
import time

import httpx

ROUTER_URL = "http://localhost:8096"

# Labeled corpus: prompt -> expected tier
CORPUS = [
    # Small tier (factual, simple, short)
    ("What is the capital of France?", "small"),
    ("What is 2+2?", "small"),
    ("Who is the president of the US?", "small"),
    ("When was the moon landing?", "small"),
    ("Where is the Eiffel Tower?", "small"),
    ("How old is the Earth?", "small"),
    ("How many planets are in the solar system?", "small"),
    ("Define photosynthesis.", "small"),
    ("Tell me the time.", "small"),
    ("What is quantum physics?", "small"),  # short enough for small
    ("Who wrote Romeo and Juliet?", "small"),
    ("What is the largest ocean?", "small"),
    ("When did World War II end?", "small"),
    ("How many bones are in the human body?", "small"),
    ("What is the speed of light?", "small"),
    
    # Medium tier (explanations, comparisons, moderate length)
    ("Explain how REST APIs work and why they are popular.", "medium"),
    ("Compare REST and GraphQL for building APIs.", "medium"),
    ("Describe the process of how a web browser renders a page.", "medium"),
    ("What are the differences between Python and JavaScript?", "medium"),
    ("Explain how a database index works.", "medium"),
    ("Describe the steps to deploy a web application.", "medium"),
    ("What are the main features of Kubernetes?", "medium"),
    ("Compare SQL and NoSQL databases.", "medium"),
    ("Explain how caching improves application performance.", "medium"),
    ("Describe the difference between HTTP and HTTPS.", "medium"),
    
    # Large tier (complex, long, analysis, code generation)
    ("Write a comprehensive analysis of the economic impacts of artificial intelligence on the global workforce, including specific examples from manufacturing, healthcare, and software development industries. Consider both short-term disruptions and long-term structural changes.", "large"),
    ("Implement a red-black tree in Python with insert, delete, and search operations. Include unit tests and explain the time complexity of each operation in detail.", "large"),
    ("Write a detailed essay exploring the philosophical implications of artificial general intelligence, covering ethics, consciousness, and the future of humanity. Provide multiple perspectives and conclude with your own argument.", "large"),
    ("Debug and optimize this Python function that calculates Fibonacci numbers using a recursive approach. Explain why the original is inefficient and provide a complete optimized solution with benchmark results.", "large"),
    ("Create a comprehensive technical report comparing microservices architecture with monolithic architecture. Include diagrams, trade-offs, use cases, and migration strategies with real-world examples.", "large"),
    ("Write a complete machine learning pipeline for predicting housing prices using Python, scikit-learn, and pandas. Include data preprocessing, feature engineering, model selection, hyperparameter tuning, and evaluation metrics.", "large"),
    ("Explain the theory of relativity in detail, including special and general relativity, with mathematical derivations and practical examples of time dilation and gravitational effects.", "large"),
    ("Develop a distributed consensus algorithm similar to Raft. Describe the protocol, provide pseudocode, explain failure scenarios, and analyze performance characteristics.", "large"),
    ("Write a comprehensive review of cloud computing platforms (AWS, Azure, GCP) comparing their services, pricing, strengths, and weaknesses for different use cases.", "large"),
    ("Create a detailed tutorial on building a real-time chat application using WebSockets, including server architecture, client implementation, scalability considerations, and security best practices.", "large"),
]


async def test_single_prompt(client: httpx.AsyncClient, prompt: str, expected_tier: str) -> bool:
    """Test a single prompt and return True if routed correctly."""
    payload = {
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 10,
        "temperature": 0.1,
    }
    
    try:
        response = await client.post(
            f"{ROUTER_URL}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        
        actual_tier = data.get("router_metadata", {}).get("tier", "unknown")
        return actual_tier == expected_tier, actual_tier
    except Exception as e:
        print(f"  ERROR: {e}")
        return False, "error"


async def main():
    print("=" * 60)
    print("LLM Semantic Router - Integration Test")
    print("=" * 60)
    print(f"Router URL: {ROUTER_URL}")
    print(f"Total prompts: {len(CORPUS)}")
    print()
    
    async with httpx.AsyncClient() as client:
        results = []
        
        for i, (prompt, expected_tier) in enumerate(CORPUS, 1):
            print(f"[{i:2d}/{len(CORPUS)}] Testing: '{prompt[:60]}...' -> expected: {expected_tier}")
            
            start = time.time()
            success, actual_tier = await test_single_prompt(client, prompt, expected_tier)
            elapsed = time.time() - start
            
            status = "✓ PASS" if success else "✗ FAIL"
            print(f"       {status} (actual: {actual_tier}, {elapsed:.2f}s)")
            
            results.append({
                "prompt": prompt[:80],
                "expected": expected_tier,
                "actual": actual_tier,
                "success": success,
                "elapsed": elapsed,
            })
    
    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["success"])
    failed = total - passed
    accuracy = passed / total * 100
    
    print()
    print("=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Total:     {total}")
    print(f"Passed:    {passed}")
    print(f"Failed:    {failed}")
    print(f"Accuracy:  {accuracy:.1f}%")
    print()
    
    # By tier breakdown
    for tier in ["small", "medium", "large"]:
        tier_results = [r for r in results if r["expected"] == tier]
        tier_passed = sum(1 for r in tier_results if r["success"])
        tier_total = len(tier_results)
        tier_accuracy = tier_passed / tier_total * 100 if tier_total > 0 else 0
        print(f"  {tier:8s}: {tier_passed}/{tier_total} ({tier_accuracy:.1f}%)")
    
    print()
    
    if accuracy >= 90:
        print(f"✓ Target met: {accuracy:.1f}% >= 90%")
        return 0
    else:
        print(f"✗ Target missed: {accuracy:.1f}% < 90%")
        # Show failures
        for r in results:
            if not r["success"]:
                print(f"  - Expected {r['expected']}, got {r['actual']}: {r['prompt']}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
