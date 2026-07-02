# PROGRESS.md

Append-only log of project execution.

---

## Phase 0 — Host setup and cluster bootstrap

**Status:** COMPLETED ✅

**Date:** 2026-07-02

### Acceptance Criteria Verification

| Criterion | Result | Evidence |
|---|---|---|
| `cluster/versions.md` committed with real command output for all twelve tools | ✅ PASS | File committed with versions for colima, docker, docker-buildx, k3d, kubectl, helm, k6, jq, yq, pyenv, uv, hf |
| `.python-version` committed; `uv run python -V` prints pinned version | ✅ PASS | Python 3.12.0 |
| `kubectl get nodes` shows 2 Ready nodes | ✅ PASS | 2 nodes Ready (1 server, 1 agent) |
| `colima list` shows profile with 11GB/6CPU | ✅ PASS | 6 CPUs, 11GiB memory, 40GiB disk, aarch64, VZ |
| `make teardown && make bootstrap` completes cleanly twice in a row | ✅ PASS | Verified 3 consecutive cycles (teardown → bootstrap → teardown → bootstrap → teardown → bootstrap) |
| VM baseline memory usage recorded | ✅ PASS | `free -m` output logged below |

### VM Baseline Memory

```
               total        used        free      shared  buff/cache   available
Mem:           10927         982        9374           1         740        9945
Swap:              0           0           0
```

Baseline: ~982 MB used at idle (k3s system only, no workloads yet).

### Notes

- k3d's kubeconfig sometimes writes a random load-balancer port instead of 6443. Makefile now forces `0.0.0.0:6443` via `sed` after every cluster create.
- k3d cluster delete can leave a stale registry/network record. Makefile now runs a pre-flight `k3d cluster delete` before create and prunes leftover Docker networks.
- First Colima start downloads the VM image (~2 min); subsequent starts are ~15–20 seconds.
- Docker daemon unreachable before `colima start`; `colima` context becomes active immediately after.

### Commits

- `chore: bootstrap repo structure, cluster config, and tool versions` (root commit)

---

