# Project: Laptop-Scale LLM Serving Platform on KServe

A production-pattern LLM inference platform running entirely on a MacBook M3 Pro (18GB unified memory), demonstrating frontier AI infrastructure skills: KServe custom runtimes, semantic request routing, metrics-driven autoscaling, canary rollouts with automated eval-based promotion, and full observability.

---

## Instructions for the Coding Agent

Read this section fully before writing any code.

### Operating rules

1. **Work phase by phase, in order.** Do not start a phase until the previous phase's acceptance criteria all pass. Each phase ends with a git commit.
2. **Verify before proceeding.** Every phase has explicit acceptance criteria with commands. Run them. Paste output into the phase's section of `PROGRESS.md` (create this file at repo root, append-only log).
3. **Respect the memory budget** (see below). Before adding any component, state its expected memory cost in `PROGRESS.md`. If the VM would exceed budget, stop and ask the human.
4. **Ask the human** before: installing or upgrading any host-level software (all required host tools are pre-installed — see "Host environment"), granting network access, or resolving anything marked `[HUMAN DECISION]`.
5. **Prefer boring, pinned versions.** Pin all image tags, Helm chart versions, and model file URLs. No `:latest`.
6. **All configuration is declarative and committed.** No `kubectl edit` or imperative changes that aren't captured in manifests. The repo must be able to recreate the cluster from scratch via `make bootstrap`.
7. **Small commits, conventional commit messages** (`feat:`, `fix:`, `docs:`, `chore:`).
8. **When something fails, capture it.** Failure modes and their fixes go in `docs/troubleshooting.md`. This document is a deliverable, not an afterthought.

### Host environment (pre-installed — do not install host software)

The human has already installed all required host tooling. Do **not** run `brew install` or install anything at the host level; if a host tool appears to be missing or broken, stop and ask the human.

Available on the host:

- **Homebrew-managed:** `colima`, `docker` (CLI, daemon provided by Colima), `docker-buildx` (registered as a CLI plugin), `k3d`, `kubectl`, `helm`, `k6`, `jq`, `yq`
- **Python:** interpreters are managed by **pyenv** — never `uv python install`, never rely on system Python. `uv` is available for project/dependency management and is expected to use the pyenv-provided interpreter.
- **Model downloads:** `hf` CLI (`huggingface_hub`) is available for pulling GGUF files with resumable downloads.

Python convention for all Python components in this repo (router, canary controller, eval harness, perf-report scripts):

1. In Phase 0, run `pyenv versions`, pick the newest installed 3.11+ (ask the human if none exists), and pin it at repo root with `pyenv local <version>` (commit the resulting `.python-version`).
2. Each Python component is a uv project: `uv init` / `uv add`, with `uv` resolving the interpreter from `.python-version` (`uv run` for execution). Commit `pyproject.toml` and `uv.lock` for every component.
3. All Python invocations in the `Makefile` and CI-style scripts go through `uv run` — never bare `python` or `pip`.

In Phase 0, record the exact versions of every tool above in `cluster/versions.md` (`colima version`, `docker version`, `k3d version`, `kubectl version --client`, `helm version`, `k6 version`, `pyenv version-name`, `uv --version`, `hf version`).

### Hard constraints

- Host: macOS, Apple Silicon (M3 Pro), 18GB unified memory. No NVIDIA GPU, no Metal passthrough into containers → **all in-cluster inference is CPU-only**.
- Linux VM (Colima) memory cap: **11GB**, 6 CPUs. Never raise without human approval.
- vLLM, TensorRT, Triton GPU backends: **out of scope**. Runtime is llama.cpp (`llama-server`).
- KServe must run in **RawDeployment mode** (no Knative, no Istio — too heavy for this budget).
- All model files are GGUF quantized. Total resident model memory across all running pods must stay ≤ 4.5GB.

### Memory budget (VM = 11GB)

| Component | Budget |
|---|---|
| k3s system + kube-system | 1.5 GB |
| KServe controller + cert-manager | 0.7 GB |
| kube-prometheus-stack (trimmed) + KEDA | 1.5 GB |
| Router service | 0.3 GB |
| Model pods (all replicas combined) | 4.5 GB |
| Headroom (scaling bursts, image pulls) | 2.5 GB |

### Model tiers

| Tier | Model | Quant | Approx RAM | Role |
|---|---|---|---|---|
| small | Qwen2.5-0.5B-Instruct | Q4_K_M | ~0.6 GB | always-warm, default route |
| medium | Llama-3.2-1B-Instruct | Q4_K_M | ~1.0 GB | mid-complexity route |
| large | Qwen2.5-3B-Instruct | Q4_K_M | ~2.2 GB | complex route, scale-to-zero |

Download GGUF files from Hugging Face using the pre-installed `hf` CLI (`hf download <repo> <filename>` — resumable); pin exact repo + filename + SHA256 in `models/manifest.yaml`. All three models are available as ungated community GGUF uploads; `[HUMAN DECISION]` only if a chosen repo turns out to be gated and requires an HF token.

### Repository layout (create in Phase 0)

```
.
├── Makefile                  # bootstrap, teardown, test, load-test targets
├── PROGRESS.md               # append-only agent log
├── README.md                 # written incrementally, finalized Phase 8
├── cluster/                  # k3d config, Colima config notes
├── platform/                 # KServe, cert-manager, KEDA, monitoring (Helm values + kustomize)
├── runtime/                  # custom llama.cpp ServingRuntime: Dockerfile, entrypoint, manifest
├── models/                   # InferenceService manifests, model download manifest + script
├── router/                   # FastAPI semantic router: src, Dockerfile, manifests, tests
├── canary/                   # canary controller/job: eval sets, promotion logic, manifests
├── observability/            # Grafana dashboards (JSON), PrometheusRules, ServiceMonitors
├── loadtest/                 # k6 scripts, prompt corpora, results/
└── docs/
    ├── architecture.md       # with Mermaid diagrams
    ├── performance.md        # analysis write-up (Phase 7)
    └── troubleshooting.md
```

---

## Phase 0 — Host setup and cluster bootstrap

**Goal:** Reproducible k3d cluster inside Colima with pinned versions and a `make bootstrap` that goes from nothing → running cluster.

Tasks:
1. Verify the pre-installed host tooling (see "Host environment" above): run the version commands for colima, docker, docker-buildx (`docker buildx version`), k3d, kubectl, helm, k6, jq, yq, pyenv, uv, and hf, and write the output to `cluster/versions.md`. If any tool is missing or errors, stop and ask the human — do not install anything.
2. Pin the Python interpreter: `pyenv versions`, select the newest installed 3.11+, `pyenv local <version>` at repo root, commit `.python-version`. If no 3.11+ interpreter is installed, ask the human to run `pyenv install <version>`.
3. Create Colima profile: 11GB memory, 6 CPU, VZ virtualization, disk ≥ 40GB. Commit the invocation in `Makefile` (`make vm-up`). Note: the first `colima start` downloads the VM image and may take several minutes; subsequent starts are fast. After start, confirm `docker context ls` shows the colima context active and `docker ps` succeeds.
4. Create k3d cluster (1 server, 1 agent) with a config file in `cluster/k3d-config.yaml`. Disable Traefik (we'll use KServe's ingress via NodePort or a lightweight ingress later; decide in Phase 1 and document).
5. Write `make bootstrap` (vm-up → cluster-create → platform install stub) and `make teardown`.
6. Initialize git repo, first commit.

**Acceptance criteria:**
- `cluster/versions.md` committed with real command output for all twelve tools.
- `.python-version` committed; `uv run python -V` from repo root prints the pinned version.
- `kubectl get nodes` shows 2 Ready nodes.
- `colima list` shows the profile with 11GB/6CPU.
- `make teardown && make bootstrap` completes cleanly twice in a row.
- Record VM baseline memory usage (`colima ssh -- free -m`) in `PROGRESS.md`.

---

## Phase 1 — KServe in RawDeployment mode

**Goal:** KServe installed minimally; a trivial sklearn or dummy InferenceService round-trips a request.

Tasks:
1. Install cert-manager (pinned Helm chart) → `platform/cert-manager/`.
2. Install KServe with `deploymentMode: RawDeployment` and Gateway/Ingress config appropriate for k3d (NodePort exposure is fine; document the choice in `docs/architecture.md`).
3. Deploy KServe's example sklearn iris InferenceService as a smoke test. Curl a prediction through the exposed endpoint.
4. Delete the smoke-test service after verification (memory discipline).

**Acceptance criteria:**
- `kubectl get pods -n kserve` all Running; controller memory < 500Mi (`kubectl top`).
- Smoke-test InferenceService returns a valid prediction via curl from the host (paste request + response in `PROGRESS.md`).
- Total VM memory used ≤ 3.5GB at idle.

---

## Phase 2 — Custom llama.cpp ServingRuntime + first model

**Goal:** A custom KServe `ServingRuntime` wrapping `llama-server`, serving Qwen2.5-0.5B with an OpenAI-compatible API through KServe.

Tasks:
1. Write `runtime/Dockerfile`: build or pull a pinned llama.cpp release (`ghcr.io/ggml-org/llama.cpp:server` pinned by digest, or build from a pinned tag for ARM64). Entrypoint must map KServe's model mount path (`/mnt/models`) to `llama-server --model ...` flags, and pass through env-configurable flags: context size, parallel slots (`--parallel`), threads, `--metrics`.
2. Write the `ServingRuntime` manifest in `runtime/`: container spec, ports (llama-server on 8080), protocol, resource requests/limits (limit: 1Gi for the small model), and readiness probe against `/health`.
3. Model delivery: simplest robust option — a model-download initContainer or a hostPath/PVC pre-populated by `models/download.sh` (verify SHA256). `[HUMAN DECISION]` only if HF token needed. Document the chosen mechanism and why in `docs/architecture.md`.
4. Deploy `InferenceService` for `qwen-0_5b` using the custom runtime.
5. Verify OpenAI-compatible chat completions endpoint works end-to-end through KServe's ingress path.

**Acceptance criteria:**
- `curl` to the InferenceService's `/v1/chat/completions` with a short prompt returns a coherent completion. Record time-to-first-token and total latency for 3 sample prompts in `PROGRESS.md`.
- `llama-server` `/metrics` endpoint is reachable from inside the cluster.
- Pod memory at rest ≤ 800Mi; under a single request ≤ 1Gi.
- `make teardown && make bootstrap && make deploy-models` reproduces this state.

---

## Phase 3 — All three tiers + observability stack

**Goal:** small/medium/large models deployed; Prometheus scraping llama.cpp metrics; Grafana dashboard showing LLM-specific metrics.

Tasks:
1. Deploy medium (Llama-3.2-1B) and large (Qwen2.5-3B) InferenceServices. Large tier: replicas may be 0 initially (KEDA handles activation in Phase 5 — until then keep `minReplicas: 1` and note memory cost).
2. Install kube-prometheus-stack, trimmed: 6h retention, no alertmanager HA, reduced scrape interval only where needed. Values in `platform/monitoring/values.yaml`.
3. ServiceMonitors (or PodMonitors) for each model pod's llama.cpp `/metrics`.
4. Build a Grafana dashboard (`observability/dashboards/llm-serving.json`, provisioned declaratively): tokens/sec per model, prompt-processing vs generation throughput, KV cache usage, requests in flight, queue/slot utilization, p50/p95/p99 request latency (via router later; llama.cpp metrics now), pod memory/CPU.
5. Add PrometheusRule alerts: pod memory > 90% of limit, model endpoint down, VM-level memory pressure.

**Acceptance criteria:**
- All three models answer a chat completion via curl.
- Grafana dashboard renders live data during a 10-request manual test.
- Total VM memory with all three models idle ≤ 8.5GB (`colima ssh -- free -m` output logged).
- All monitoring config is declarative (no clicking in Grafana UI to create the dashboard).

---

## Phase 4 — Semantic router

**Goal:** A FastAPI gateway exposing one OpenAI-compatible endpoint that classifies each request and routes to the appropriate model tier.

Tasks:
1. `router/src/`: FastAPI app with `/v1/chat/completions` (streaming and non-streaming). Routing decision logic, in order of preference:
   - Heuristic tier: prompt length, presence of code blocks, task keywords → cheap deterministic baseline.
   - Classifier tier: call the small model itself with a constrained classification prompt ("respond with exactly one of: simple|moderate|complex") with a strict timeout and fallback to heuristics. This "model-as-router" pattern is a headline feature — implement carefully with timeout + fallback.
   - Explicit override: honor a `x-route-tier` header for testing.
2. Router emits Prometheus metrics: route decisions by tier, upstream latency histograms, TTFT, tokens streamed, fallback counts, upstream errors.
3. Structured JSON logging with request IDs propagated to upstream calls.
4. Unit tests for routing logic (pytest, no cluster needed). Integration test script that sends labeled prompts and asserts tier distribution.
5. Containerize (ARM64), deploy with manifests in `router/manifests/`, resource limit 256Mi.
6. Wire router ServiceMonitor + extend Grafana dashboard with routing panels.

**Acceptance criteria:**
- `pytest router/` passes.
- Integration script: ≥ 90% of a 30-prompt labeled corpus routes to the expected tier; results table in `PROGRESS.md`.
- Streaming works end-to-end through the router (verify chunked response with curl).
- Router adds < 50ms p95 overhead for heuristic-routed requests (measure and record).

---

## Phase 5 — Autoscaling with KEDA

**Goal:** Metrics-driven autoscaling on real inference signals; scale-to-zero for the large model with measured cold starts.

Tasks:
1. Install KEDA (pinned Helm chart) → `platform/keda/`.
2. ScaledObject for the **small** model: scale 1→3 replicas on Prometheus query of llama.cpp requests-in-flight / slot utilization. Tune thresholds so a k6 burst visibly triggers scaling.
3. ScaledObject (or KEDA HTTP add-on / activation strategy) for the **large** model: scale 0→1 on incoming demand. If request-buffering during activation is impractical in this setup, implement router-side behavior: on large-tier cold start, return a clear 503-with-retry-after OR degrade to medium tier with a response header noting degradation. `[HUMAN DECISION]`: pick buffering vs. degrade-with-header after presenting tradeoffs in `PROGRESS.md`.
4. Measure and record cold-start breakdown for the large model: pod schedule → container start → model load → first token. Optimize what's cheap (image pull policy, model on pre-populated volume) and record before/after.
5. Chaos check: `kubectl delete pod` on a model mid-generation; document observed behavior and router error handling; fix router to retry idempotent cases.

**Acceptance criteria:**
- k6 burst against small tier: replicas observably scale 1→3 and back down; Grafana screenshot/export saved to `loadtest/results/`.
- Large model scales 0→1 on demand; cold-start timing table (≥ 3 trials) in `PROGRESS.md`.
- Memory ceiling never exceeded during scaling (alert from Phase 3 must not fire; verify).
- Pod-kill chaos test documented in `docs/troubleshooting.md`.

---

## Phase 6 — Canary rollouts with automated eval-based promotion

**Goal:** KServe traffic-splitting canary between quantization variants, with an automated eval job that promotes or rolls back.

Tasks:
1. Prepare a canary candidate: Qwen2.5-0.5B at **Q8_0** as a new InferenceService revision (or second isolated service if RawDeployment traffic splitting proves limited — investigate KServe canary support in RawDeployment mode first; if unsupported, implement traffic splitting **in the router** with weighted upstream selection, which is an equally strong story. Document findings in `docs/architecture.md`).
2. Build `canary/evalset.jsonl`: 25–40 prompts with programmatically checkable expectations (exact-match QA, JSON-format compliance, contains-answer checks). No LLM-as-judge (no external API dependency).
3. Canary controller (a Kubernetes CronJob or simple operator script in `canary/`):
   - Shifts 10% traffic to canary.
   - Runs evalset against both baseline and canary; collects quality score + p95 latency + tokens/sec from Prometheus.
   - Promotion rule (codified in config): quality ≥ baseline − 2 points AND p95 latency ≤ baseline × 1.25 → shift to 50%, re-run, then 100%. Any violation → rollback to 0% and write a report.
4. Emit canary state and decisions as Prometheus metrics + a Grafana panel.
5. Run one full promotion and one forced rollback (e.g., artificially degrade the canary via tiny context size) and save both reports to `canary/reports/`.

**Acceptance criteria:**
- One successful automated promotion and one automated rollback, both with machine-generated reports committed.
- Canary decisions visible in Grafana.
- Eval harness runs in < 10 minutes on this hardware.

---

## Phase 7 — Load testing and performance analysis

**Goal:** A rigorous performance characterization producing `docs/performance.md`.

Tasks:
1. k6 scripts in `loadtest/`: realistic prompt-length distribution (mix drawn from a committed corpus), streaming-aware TTFT measurement, scenarios: steady-state per tier, burst, mixed-tier workload through router, soak (20 min).
2. Sweep concurrency (1, 2, 4, 8, 16) per tier; capture TTFT, inter-token latency, total throughput (tokens/sec aggregate), and error rates. Export raw results as JSON to `loadtest/results/`.
3. Analysis in `docs/performance.md` with charts (generate via a Python script, matplotlib, committed): latency vs. concurrency curves, throughput ceilings per tier, effect of llama.cpp `--parallel` slots (test 2 settings), scaling event impact on p95, cost framing (tokens/sec per CPU core; extrapolate to cloud instance pricing for a "what this costs at scale" section).
4. One deliberate saturation test: drive the system past its ceiling, document degradation behavior and where request shedding/queueing happens.

**Acceptance criteria:**
- `docs/performance.md` complete with ≥ 4 generated charts and a findings summary.
- Raw data + generation script committed; charts reproducible via `make perf-report`.

---

## Phase 8 — Documentation, reproducibility, polish

**Goal:** The repo reads like a portfolio piece and rebuilds from scratch with one command.

Tasks:
1. `README.md`: hook ("production-pattern LLM serving platform, fully reproducible on a laptop"), architecture Mermaid diagram, feature list, quickstart (`make bootstrap && make deploy-all`), dashboard screenshots, link to performance write-up, "how this maps to real GPU infrastructure" section (swap ServingRuntime → vLLM, models → full-size; config change not architecture change).
2. `docs/architecture.md`: request flow diagrams (normal, cold-start, canary), design decisions with rationale (RawDeployment, KEDA over HPA, router-side vs KServe canary — whichever was chosen), memory budget table with actuals vs. budget.
3. Full clean-room test: `make teardown`, delete images, `make bootstrap && make deploy-all && make test`. Fix anything that breaks.
4. `make test` target: smoke tests for every component (models answer, router routes, metrics scraped, KEDA objects healthy).
5. Final pass on `docs/troubleshooting.md` — this should have accumulated real content across phases.

**Acceptance criteria:**
- Clean-room rebuild succeeds end-to-end; total wall-clock time recorded in README.
- `make test` passes.
- README renders correctly with all diagrams and screenshots.

---

## Definition of done (whole project)

- [ ] All 8 phases' acceptance criteria pass and are evidenced in `PROGRESS.md`.
- [ ] Clean-room `make bootstrap && make deploy-all && make test` succeeds.
- [ ] Three model tiers served via custom llama.cpp ServingRuntime on KServe RawDeployment.
- [ ] Semantic router with model-as-classifier + heuristic fallback, fully instrumented.
- [ ] KEDA autoscaling on inference metrics; scale-to-zero with measured cold starts.
- [ ] Automated canary promotion and rollback demonstrated with committed reports.
- [ ] `docs/performance.md` with reproducible charts and saturation analysis.
- [ ] VM memory never exceeded 11GB during any recorded test.

## Explicitly out of scope

GPU inference, vLLM/Triton, multi-node clusters, Knative/Istio serverless mode, authentication/multi-tenancy, fine-tuning, LLM-as-judge evals, external API dependencies at runtime.
