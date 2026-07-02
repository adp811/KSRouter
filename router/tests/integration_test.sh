#!/usr/bin/env bash
# router/tests/integration_test.sh
# Integration test: sends labeled prompts and verifies routing decisions

set -euo pipefail

ROUTER_URL="${ROUTER_URL:-http://localhost:8090}"

echo "=== LLM Router Integration Test ==="
echo "Router URL: $ROUTER_URL"
echo ""

# Test corpus: prompt -> expected_tier
# We test heuristic routing (no classifier) by using short timeouts
declare -a TESTS=(
  "What is 2+2?|small"
  "Who is the president?|small"
  "When was the moon landing?|small"
  "Explain how REST APIs work|medium"
  "Compare REST and GraphQL|medium"
  "Write a comprehensive analysis of AI economic impacts|large"
  "Implement a red-black tree in Python|large"
)

PASS=0
FAIL=0

for test in "${TESTS[@]}"; do
  IFS='|' read -r prompt expected_tier <<< "$test"
  
  echo "Testing: '$prompt' -> expected: $expected_tier"
  
  response=$(curl -s "$ROUTER_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"auto\", \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}], \"max_tokens\": 10, \"temperature\": 0.1}")
  
  actual_tier=$(echo "$response" | jq -r '.router_metadata.tier // empty')
  
  if [ "$actual_tier" = "$expected_tier" ]; then
    echo "  ✓ PASS (tier: $actual_tier)"
    ((PASS++)) || true
  else
    echo "  ✗ FAIL (expected: $expected_tier, got: $actual_tier)"
    echo "  Response: $response"
    ((FAIL++)) || true
  fi
done

echo ""
echo "=== Results ==="
echo "Pass: $PASS"
echo "Fail: $FAIL"
echo "Total: $((PASS + FAIL))"

if [ $FAIL -eq 0 ]; then
  echo "All tests passed!"
  exit 0
else
  echo "Some tests failed."
  exit 1
fi
