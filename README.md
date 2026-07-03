# KSRouter

A Kubernetes-native LLM serving platform built for a single laptop (M3 Pro, 18GB unified memory). Runs three tiers of models — small (0.5B), medium (1B), and large (3B) — with a semantic router, KEDA autoscaling, Prometheus/Grafana observability, and canary traffic splitting.

**CLI name:** `ksrouter`

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Request                          │
│              (OpenAI-compatible /v1/chat/completions)        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     KSRouter (FastAPI)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Heuristic   │→│  Classifier  │→│  Canary Split    │  │
│  │  Routing     │  │  (0.5B LLM)  │  │  (10%→50%→100%) │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Large Model Cold-Start: 503 + Retry-After + fun msg  │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────┬──────────────┬──────────────┬──────────────────┬────┘
           │              │              │                  │
           ▼              ▼              ▼                  ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │   Small     │ │   Medium    │ │   Large     │ │  Prometheus │
    │  Qwen 0.5B  │ │ Llama 3.2 1B│ │  Qwen 3B    │ │   + Grafana │
    │  1-3 pods   │ │   1 pod     │ │ 0-1 pods    │ │  Dashboard  │
    │   KEDA      │ │   static    │ │   KEDA      │ │             │
    └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

### Technology Stack

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| VM Runtime | Colima | latest | macOS Linux VM (VZ, 11GB, 6CPU) |
| Container Runtime | Docker | v27.4.0 | Image building + registry |
| Kubernetes | k3d (k3s) | v5.8.3 (v1.35.5+k3s1) | 1 server + 1 agent cluster |
| Inference Server | KServe | v0.18.0 | RawDeployment mode |
| Inference Runtime | llama.cpp | server | GGUF model serving |
| Autoscaler | KEDA | v2.20.0 | Prometheus-triggered scaling |
| Monitoring | kube-prometheus-stack | v0.92.1 | Prometheus + Grafana + Alertmanager |
| Router | FastAPI | v0.139.0 | Semantic routing + canary |
| Build Tool | uv | 0.6.12 | Python package management |

## Quick Start

### Prerequisites

- macOS with Apple Silicon (tested on M3 Pro 18GB)
- [Homebrew](https://brew.sh) for tool installation
- Docker Desktop or Colima installed
- Tools installed via `brew install k3d helm jq yq k6` (versions pinned in `cluster/versions.md`)

### One-Command Bootstrap

```bash
make bootstrap          # Create VM + cluster
make deploy-all         # Install all platform components
```

### Verify Everything Works

```bash
make test               # Check model readiness and router status
```

### Send a Request

```bash
# Automatic tier routing (heuristic + classifier)
curl http://localhost:8126/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "What is 2+2?"}]}'

# Force a specific tier
# x-route-tier: small | medium | large
```

### Teardown

```bash
make teardown           # Destroy cluster + VM
```

## Components

### 1. Model Tiers

All models are GGUF format served by `llama.cpp` server via KServe InferenceServices.

| Tier | Model | Parameters | Quantization | File Size | CPU Limit | Memory Limit | Replicas |
|---|---|---|---|---|---|---|---|
| Small | Qwen2.5-0.5B-Instruct | 0.5B | Q4_K_M | ~491 MB | 2 cores | 1 GiB | 1-3 (KEDA) |
| Medium | Llama-3.2-1B-Instruct | 1B | Q4_K_M | ~620 MB | 4 cores | 2 GiB | 1 (static) |
| Large | Qwen2.5-3B-Instruct | 3B | Q4_K_M | ~1.7 GB | 4 cores | 3 GiB | 0-1 (KEDA) |

**Model delivery:** Pre-downloaded GGUF files are copied to k3d nodes via `docker cp` at `/mnt/models`, then mounted via `hostPath` volumes. No in-cluster downloads.

### 2. KSRouter

A FastAPI service that routes requests to the appropriate model tier based on prompt complexity.

**Routing Pipeline:**
1. **Explicit header** (`x-route-tier: small|medium|large`) — highest priority
2. **Heuristic analysis** — keyword matching, prompt length, code blocks
3. **Model-as-classifier** — 0.5B model classifies as simple/moderate/complex (timeout: 2s)
4. **Fallback** — return heuristic result if classifier fails

**Canary Traffic Splitting:**
- KServe RawDeployment doesn't support native canary
- Router implements weighted random selection: `roll(1-100) <= weight`
- Configured per tier via env vars: `CANARY_SMALL_WEIGHT`, `CANARY_SMALL_URL`, etc.
- Progression: 10% → 50% → 100%

**Cold-Start Handling:**
- Large model is configured for scale-to-zero (`minReplicaCount: 0`)
- When cold, router returns `503 Service Unavailable` with:
  - `Retry-After: 30` header
  - Fun message: "Our large brain is taking a power nap. Give it 30 seconds to wake up and grab some coffee! ☕"
- Client should retry after the specified duration

**Router Metrics:**
- `router_route_decisions_total` — routing decisions by tier and method
- `router_upstream_latency_seconds` — upstream request latency by tier
- `router_time_to_first_token_seconds` — TTFT for streaming
- `router_recent_requests` — sliding-window gauge (60s) for KEDA scaling
- `router_canary_routing_total` — canary vs stable routing decisions

### 3. KEDA Autoscaling

| Model | Trigger | Metric | Threshold | Min | Max | Scale-Down Stabilization |
|---|---|---|---|---|---|---|
| Small | Prometheus | `router_recent_requests` | 10 | 1 | 3 | 120s |
| Large | Prometheus | `router_recent_requests_by_tier{tier="large"}` | 1 | 0 | 1 | 120s |

**Why `router_recent_requests` instead of CPU?** The 0.5B model is so fast that CPU usage is a series of sharp spikes (<1s). KEDA's Prometheus scrape interval (15s) misses them. A sliding-window gauge that counts requests started in the last 60s provides a stable, decaying signal.

**KServe Integration:** InferenceServices use `serving.kserve.io/autoscalerClass: external` so KServe doesn't create its own HPA or fight KEDA for replica control.

### 4. Observability

**Prometheus Metrics:**
- Model-level: `llamacpp:prompt_tokens_total`, `llamacpp:generated_tokens_total`, `llamacpp:kv_cache_usage_ratio`
- Router-level: latency, routing decisions, errors, TTFT, token throughput
- System-level: CPU, memory, pod count via kube-prometheus-stack

**Grafana Dashboard:** 14 panels provisioned via ConfigMap
- Requests/sec by tier, routing method distribution, latency percentiles
- KV cache usage, model load time, token throughput
- KEDA scaling events, VM memory pressure, cold-start frequency

**Alerts:** 5 PrometheusRules
- `ModelMemoryPressure`, `ModelEndpointDown`, `VMMemoryPressure`, `HighLatency`, `LowThroughput`

### 5. Evaluation Scripts

| Script | Purpose | Command |
|---|---|---|
| `evals/canary_eval.py` | Verify canary traffic split ratios | `python evals/canary_eval.py --stage 50` |
| `evals/coldstart_timing.py` | Measure large model cold-start time | `python evals/coldstart_timing.py --trials 3` |
| `evals/chaos_test.py` | Kill pod mid-generation, verify recovery | `python evals/chaos_test.py --tier small` |

## Performance Metrics

### Latency (Single Request, Warm)

| Tier | TTFT | Total Latency | Example Prompt |
|---|---|---|---|
| Small | <0.01s | 0.17s | "What is the capital of France?" |
| Medium | <0.01s | 0.30s | "What is the capital of France?" |
| Large | <0.01s | 0.66s | "What is the capital of France?" |

### Cold-Start (Scale-to-Zero → First Response)

- **KEDA pod scheduling:** ~30s
- **Model loading (3B Q4_K_M):** ~15-30s
- **Total user-visible:** ~60s (with automatic retry)

### Router Overhead

- **Heuristic routing:** ~31μs
- **Classifier routing:** ~0.5-1.5s (with 2s timeout)
- **Classifier accuracy:** 91.4% on 35-prompt corpus

### KEDA Scaling

- **Scale-up (1→3):** Triggered within 15s of burst start
- **Scale-down (3→1):** 120s stabilization window prevents flapping
- **Metric decay:** Natural 60s sliding-window cooldown

### VM Memory Budget

| Phase | Used | Budget | Headroom |
|---|---|---|---|
| Idle (k3s only) | ~1.0 GB | 11 GB | 10 GB |
| Platform + 3 models | ~6.2 GB | 8.5 GB | 2.3 GB |

## Project Structure

```
.
├── Makefile                          # One-command lifecycle management
├── cluster/versions.md               # Pinned tool versions
├── docs/
│   └── troubleshooting.md            # Common issues and fixes
├── evals/                            # Automated evaluation scripts
│   ├── canary_eval.py
│   ├── coldstart_timing.py
│   └── chaos_test.py
├── models/                           # Model configs and manifests
│   ├── download.sh                   # Download + SHA256 verify GGUFs
│   ├── gguf/                         # Pre-downloaded model files
│   ├── qwen-0_5b.yaml                # Small InferenceService
│   ├── llama-1b.yaml                 # Medium InferenceService
│   ├── qwen-3b.yaml                  # Large InferenceService
│   ├── qwen-0-5b-scaledobject.yaml   # KEDA autoscaling config (small)
│   ├── llama-1b-scaledobject.yaml    # KEDA autoscaling config (medium)
│   └── qwen-3b-scaledobject.yaml     # KEDA autoscaling config (large)
├── observability/                    # Prometheus + Grafana config
│   ├── podmonitor-llm-models.yaml
│   ├── prometheusrules.yaml
│   └── grafana-configmap.yaml
├── platform/                         # Helm values and configs
│   ├── cert-manager/values.yaml
│   ├── kserve/values.yaml
│   ├── kserve/clusterservingruntimes.yaml
│   └── monitoring/values.yaml
├── router/                           # Semantic router service
│   ├── src/
│   │   ├── main.py                   # FastAPI app
│   │   ├── routing.py                # Tier determination + canary
│   │   └── metrics.py                # Prometheus metrics
│   └── manifests/
│       └── deployment.yaml
├── runtime/
│   └── clusterservingruntime.yaml    # Custom llama.cpp runtime
└── scripts/
    └── fix-dns.sh                    # CoreDNS DNS patch automation
```

## Known Limitations

1. **KEDA Prometheus trigger latency:** 15s polling + scrape interval means scale-up is not instantaneous. The sliding-window gauge mitigates this.
2. **KServe RawDeployment lacks native traffic splitting:** Canary is implemented in the router instead.
3. **Single-node cluster:** All pods run on one VM. No real node affinity or zone awareness.
4. **No GPU:** All inference is CPU-bound. Larger models (>7B) are not feasible.
5. **Model loading on every pod start:** HostPath is fast but still requires ~15-30s for the 3B model.

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for:
- CoreDNS DNS resolution failures
- KServe HPA vs KEDA conflicts
- Volume mount conflicts at `/mnt/models`
- k3d kubeconfig port mismatch
- Image pull issues on k3d nodes

## Git Workflow

All commits follow conventional commits:
- `feat:` — new feature or capability
- `fix:` — bug fix or correction
- `docs:` — documentation changes
- `chore:` — maintenance, tooling, cleanup

## License

MIT
