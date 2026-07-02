# Host Tool Versions

Recorded on macOS Apple Silicon (M3 Pro) during Phase 0 bootstrap.

| Tool | Version | Command |
|---|---|---|
| colima | 0.10.3 | `colima version` |
| docker (CLI) | 29.6.1 | `docker version` |
| docker-buildx | v0.35.0 | `docker buildx version` |
| k3d | v5.9.0 | `k3d version` |
| kubectl | v1.36.2 | `kubectl version --client` |
| helm | v4.2.2 | `helm version` |
| k6 | v2.1.0 | `k6 version` |
| jq | 1.8.2 | `jq --version` |
| yq | v4.53.3 | `yq --version` |
| pyenv | 3.12.0 | `pyenv version-name` |
| uv | 0.11.19 | `uv --version` |
| hf | 1.8.0 | `hf version` |

## Docker Daemon Status

Docker daemon is provided by Colima. Before `colima start`, the daemon is unreachable (`unix:///var/run/docker.sock` does not exist). After `colima start`, the `colima` context is active and `docker ps` succeeds.
