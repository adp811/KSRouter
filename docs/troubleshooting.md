# Troubleshooting

Real issues encountered during project execution and their fixes.

---

## k3d cluster DNS resolution failure

**Symptom:** Pods cannot resolve external DNS names (e.g., `storage.googleapis.com`, `google.com`). CoreDNS logs show:
```
[ERROR] plugin/errors: 2 google.com. A: read udp 10.42.0.6:48980->8.8.8.8:53: i/o timeout
```

**Root cause:** k3d clusters inside Colima VMs inherit the VM's `/etc/resolv.conf` which contains `nameserver 192.168.5.1` (the VM host/gateway). CoreDNS forwards queries to this address by default, but from inside k3d containers, external DNS servers (1.1.1.1, 8.8.8.8) are also unreachable. The actual issue is that the default `forward . /etc/resolv.conf` in CoreDNS doesn't work properly because the resolv.conf inside k3d nodes points to a local resolver that isn't accessible from pod network.

**Fix:** Patch CoreDNS ConfigMap to forward directly to the VM's DNS gateway:
```bash
kubectl patch configmap coredns -n kube-system --type merge -p '{"data":{"Corefile":".:53 {\n    errors\n    health\n    ready\n    kubernetes cluster.local in-addr.arpa ip6.arpa {\n      pods insecure\n      fallthrough in-addr.arpa ip6.arpa\n    }\n    hosts /etc/coredns/NodeHosts {\n      ttl 60\n      reload 15s\n      fallthrough\n    }\n    prometheus :9153\n    cache 30\n    loop\n    reload\n    loadbalance\n    import /etc/coredns/custom/*.override\n    forward . 192.168.5.1\n}\nimport /etc/coredns/custom/*.server\n"}}'
kubectl rollout restart deployment coredns -n kube-system
```

**Verification:**
```bash
kubectl run dns-test --rm -i --restart=Never --image=busybox:1.36 -- nslookup google.com
```

**Phase encountered:** Phase 1 (KServe sklearn iris smoke test failed to download model from GCS due to DNS failure)

---

## k3d kubeconfig writes random API server port

**Symptom:** After `k3d cluster create`, `kubectl get nodes` fails with:
```
The connection to the server 0.0.0.0:56828 was refused
```

**Root cause:** k3d writes a random high port (from the loadbalancer) to kubeconfig instead of the exposed 6443 port.

**Fix:** Force the kubeconfig to use port 6443 after cluster creation:
```bash
sed -i '' 's/0\.0\.0\.0:[0-9]*/0.0.0.0:6443/g' ~/.kube/config
```

This is now automated in the `Makefile` `cluster-create` target.

**Phase encountered:** Phase 0

---

## k3d cluster delete leaves stale registry/network record

**Symptom:** `k3d cluster create` fails with:
```
Failed to create cluster 'llm-serving' because a cluster with that name already exists
```

**Root cause:** Previous `k3d cluster delete` can leave stale records in k3d's state even if no actual nodes exist.

**Fix:** Run a pre-flight delete before create, and prune leftover Docker networks:
```bash
-k3d cluster delete $(CLUSTER_NAME) >/dev/null 2>&1 || true
docker network rm k3d-$(CLUSTER_NAME) >/dev/null 2>&1 || true
```

This is now automated in the `Makefile` `cluster-create` and `cluster-delete` targets.

**Phase encountered:** Phase 0

---

## KServe RawDeployment mode requires ClusterServingRuntime

**Symptom:** InferenceService stays in `Unknown` state; events show:
```
Warning   InternalError  inferenceservice/sklearn-iris  no runtime found to support predictor with model type: {sklearn <nil>}
```

**Root cause:** In RawDeployment (Standard) mode, KServe does not automatically install ClusterServingRuntimes. You must explicitly create them or install the `kserve-runtime-configs` Helm chart.

**Fix:** Create the required ClusterServingRuntime manifest:
```bash
kubectl apply -f platform/kserve/clusterservingruntimes.yaml
```

This manifest defines the sklearn/xgboost/lightgbm runtime with the `kserve/sklearnserver:v0.18.0` image.

**Phase encountered:** Phase 1

---

## Colima VM delete prompts for confirmation

**Symptom:** `make teardown` hangs waiting for interactive confirmation:
```
are you sure you want to delete colima and all settings? [y/N]
```

**Fix:** Use the `--force` flag:
```bash
colima delete --profile default --force
```

This is now automated in the `Makefile` `vm-delete` target.

**Phase encountered:** Phase 0

---

## `kubectl top` unavailable

**Symptom:** `kubectl top pods` fails with:
```
error: Metrics API not available
```

**Root cause:** `metrics-server` is disabled during k3d cluster creation to save memory (1.5GB k3s budget). We install kube-prometheus-stack in Phase 3 instead.

**Workaround:** Check resource usage via `kubectl describe pod` or `colima ssh -- free -m` for VM-level metrics.

**Phase encountered:** Phase 1 (will be resolved in Phase 3 with Prometheus)

---
