# AGENTS.md — Project Context for Coding Agents

## Project: Laptop-Scale LLM Serving Platform

## Quick Reference

```bash
# Full lifecycle
make teardown && make bootstrap && make deploy-all

# Common checks
kubectl get pods -A                    # All pods
kubectl get inferenceservice -n default # Model status
kubectl get hpa -n default              # KEDA autoscaling
make vm-memory                          # VM memory usage

# Router test
kubectl port-forward svc/llm-router -n default 8126:80
curl -s http://localhost:8126/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "What is 2+2?"}]}'
```

## Architecture Decisions (Do Not Change Without Discussion)

1. **KServe RawDeployment mode** — No Istio/Knative. Simpler, lighter, no native canary.
2. **HostPath model delivery** — `docker cp` to k3d nodes at `/mnt/models`, then `hostPath` mounts. No PVC, no initContainers, no in-cluster downloads.
3. **KEDA + external autoscalerClass** — KServe `autoscalerClass: external` lets KEDA control replicas. Never use `none` (KServe v0.18 resets replicas to 1).
4. **Sliding-window gauge for KEDA** — `router_recent_requests` persists for 60s. Do not use `router_active_requests` (too ephemeral) or `increase()` (KEDA evaluates after burst ends).
5. **Router-side canary** — Weighted random in Python. KServe RawDeployment has no native traffic splitting.
6. **Model-as-classifier only for long prompts** — 0.5B model is unreliable for short prompts. Heuristic only for prompts ≤80 words.

## Common Pitfalls

### CoreDNS DNS Resolution

k3d's default CoreDNS forwards to `/etc/resolv.conf` which often fails on macOS. The fix is in `scripts/fix-dns.sh` (called automatically by `make bootstrap`). If DNS is broken, run:

```bash
./scripts/fix-dns.sh
```

**Do not** manually edit the CoreDNS ConfigMap without preserving the `NodeHosts` key — losing it causes CoreDNS to crashloop with `configmap references non-existent config key: NodeHosts`.

### KEDA vs KServe HPA Fighting

If you see `ScalingReplicaSet` events scaling up then immediately down, check:

```bash
kubectl get inferenceservice -n default -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.serving\.kserve\.io/autoscalerClass}{"\n"}{end}'
```

All must be `external`. If `none` or missing, KServe will recreate its own HPA and fight KEDA.

### Helm Install Timeouts

All helm installs in the Makefile use `--timeout 10m`. If a helm install fails with "context canceled", the pod was still starting (likely pulling images). Check:

```bash
kubectl get pods -n <namespace>
helm list -n <namespace>   # Check if release is "failed" or "deployed"
```

If "failed", uninstall and retry: `helm uninstall <release> -n <namespace> && make deploy-all`

### Image Pull on k3d Nodes

Images built on the host (like `llm-router:latest`) are NOT automatically available in k3d. The Makefile handles this via `k3d image import`, but if you build images manually, run:

```bash
k3d image import <image>:<tag> --cluster llm-serving
```

### VM Memory Pressure

If the VM is under memory pressure (Colima OOMs), check:

```bash
make vm-memory
```

If `available` < 500MB, scale down large model to 0 replicas: `kubectl scale deployment -n default qwen-3b-predictor --replicas=0`

## Testing Checklist (Before Any Commit)

1. `make test` — all pods Running, all InferenceServices Ready
2. `curl` to router — at least one tier returns a valid response
3. `kubectl get hpa -n default` — KEDA HPAs exist and show metrics
4. `kubectl get pods -n monitoring` — Prometheus + Grafana Running
5. `git diff` — only intended files changed

## File Conventions

- **YAML configs:** 2-space indentation, no tabs
- **Python:** ruff-compatible, type hints where helpful, async/await for I/O
- **Conventional commits:** `feat:`, `fix:`, `docs:`, `chore:` prefixes
- **Versions:** All pinned (Helm charts, images, tools). No `:latest` tags.

## Evaluation Scripts

| Script | What it tests | How long |
|---|---|---|
| `evals/canary_eval.py` | Canary traffic split accuracy | ~30s |
| `evals/coldstart_timing.py` | Large model cold-start from 0 | ~2-3min |
| `evals/chaos_test.py` | Pod kill + recovery | ~30s |

All scripts are self-contained Python with only stdlib + `urllib` (no external deps).
