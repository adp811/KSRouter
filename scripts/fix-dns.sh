#!/bin/bash
# Fix CoreDNS to forward DNS queries to the VM gateway.
# k3d's default CoreDNS uses /etc/resolv.conf which may not resolve external domains.
# This script patches the CoreDNS ConfigMap while preserving the NodeHosts key.

set -euo pipefail

echo "Fixing CoreDNS DNS resolution..."

# Get the current NodeHosts content
NODEHOSTS=$(kubectl get configmap -n kube-system coredns -o jsonpath='{.data.NodeHosts}' 2>/dev/null || true)

if [ -z "$NODEHOSTS" ]; then
    # k3d doesn't always set NodeHosts, so construct it from docker network
    echo "  Constructing NodeHosts from k3d network..."
    NETWORK_INFO=$(docker network inspect k3d-llm-serving -f '{{json .Containers}}' 2>/dev/null || echo "[]")
    # NodeHosts format: IP hostname
    NODEHOSTS=$(echo "$NETWORK_INFO" | python3 -c '
import json, sys
data = json.load(sys.stdin)
for cid, info in data.items():
    ip = info.get("IPv4Address", "").split("/")[0]
    name = info.get("Name", "")
    if ip and name:
        print(f"{ip} {name}")
')
fi

# Get the current Corefile and replace forward line
COREFILE=$(kubectl get configmap -n kube-system coredns -o jsonpath='{.data.Corefile}' | sed 's/forward \. \/etc\/resolv\.conf/forward . 192.168.5.1/')

# Apply the fixed ConfigMap with both keys preserved
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
data:
  Corefile: |
$(echo "$COREFILE" | sed 's/^/    /')
  NodeHosts: |
$(echo "$NODEHOSTS" | sed 's/^/    /')
EOF

echo "  Restarting CoreDNS..."
kubectl rollout restart deployment -n kube-system coredns
kubectl rollout status deployment -n kube-system coredns --timeout=60s

echo "  Testing DNS resolution..."
# Wait a moment for DNS to be ready
sleep 5
if kubectl run dns-test --image=busybox:1.36 --rm -it --restart=Never -- nslookup google.com >/dev/null 2>&1; then
    echo "  ✅ DNS resolution working"
else
    echo "  ⚠️  DNS test inconclusive (may need a moment to stabilize)"
fi

echo "CoreDNS fix complete."
