.PHONY: vm-up vm-down cluster-create cluster-delete bootstrap teardown
.PHONY: deploy-models deploy-all test

# Laptop-Scale LLM Serving Platform
# Host: macOS Apple Silicon (M3 Pro), 18GB unified memory
# VM: Colima 11GB/6CPU/VZ, k3d cluster (1 server + 1 agent)

VM_PROFILE ?= default
CLUSTER_NAME ?= llm-serving
KUBECONFIG_PATH ?= $(HOME)/.kube/config

# --- VM lifecycle ---

vm-up:
	@echo "Starting Colima VM (11GB RAM, 6 CPU, VZ, 40GB disk)..."
	colima start --cpu 6 --memory 11 --disk 40 --vm-type vz --profile $(VM_PROFILE)
	@echo "Docker context:"
	docker context ls

vm-down:
	@echo "Stopping Colima VM..."
	colima stop --profile $(VM_PROFILE)

vm-delete:
	@echo "Deleting Colima VM..."
	colima delete --profile $(VM_PROFILE)

# --- Cluster lifecycle ---

cluster-create:
	@echo "Creating k3d cluster '$(CLUSTER_NAME)' (1 server, 1 agent, Traefik disabled)..."
	k3d cluster create $(CLUSTER_NAME) \
		--servers 1 \
		--agents 1 \
		--k3s-arg "--disable=traefik@server:*" \
		--k3s-arg "--disable=metrics-server@server:*" \
		--k3s-arg "--disable=servicelb@server:*" \
		--port "6443:6443@server:*"
	@echo "Fixing kubeconfig port to 6443..."
	# k3d sometimes writes a random LB port; force it to 6443
	sed -i '' 's/0\.0\.0\.0:[0-9]*/0.0.0.0:6443/g' $(KUBECONFIG_PATH) || true
	@echo "Cluster nodes:"
	kubectl get nodes

cluster-delete:
	@echo "Deleting k3d cluster '$(CLUSTER_NAME)'..."
	k3d cluster delete $(CLUSTER_NAME)

# --- Bootstrap / Teardown ---

bootstrap: vm-up cluster-create
	@echo "Bootstrap complete. Cluster ready for platform installation."

teardown: cluster-delete vm-delete
	@echo "Teardown complete."

# --- Platform install stubs (filled in later phases) ---

install-platform:
	@echo "Installing platform components (KServe, cert-manager, monitoring, KEDA)..."
	@echo "See platform/ directory for Helm values and manifests."

# --- Model deployment stubs (filled in Phase 2+) ---

deploy-models:
	@echo "Deploying model InferenceServices..."
	@echo "See models/ directory for manifests."

deploy-all: install-platform deploy-models
	@echo "All components deployed."

# --- Testing ---

test:
	@echo "Running smoke tests..."
	@echo "See test scripts in individual component directories."

# --- Utilities ---

vm-memory:
	colima ssh -- free -m

cluster-status:
	kubectl get nodes
	kubectl get pods -A
