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
	colima delete --profile $(VM_PROFILE) --force || true

# --- Cluster lifecycle ---

cluster-create:
	@echo "Creating k3d cluster '$(CLUSTER_NAME)' (1 server, 1 agent, Traefik disabled)..."
	# Ensure any stale cluster record is cleaned up first
	-k3d cluster delete $(CLUSTER_NAME) >/dev/null 2>&1 || true
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
	-k3d cluster delete $(CLUSTER_NAME) || true
	@echo "Pruning any leftover k3d networks/volumes..."
	-docker network rm k3d-$(CLUSTER_NAME) >/dev/null 2>&1 || true

# --- Bootstrap / Teardown ---

bootstrap: vm-up cluster-create
	@echo "Bootstrap complete. Cluster ready for platform installation."

teardown: cluster-delete vm-delete
	@echo "Teardown complete."

# --- Platform install ---

install-cert-manager:
	@echo "Installing cert-manager v1.20.3..."
	helm install cert-manager oci://quay.io/jetstack/charts/cert-manager \
		--version v1.20.3 \
		--namespace cert-manager \
		--create-namespace \
		--values platform/cert-manager/values.yaml \
		--wait

install-kserve:
	@echo "Installing KServe v0.18.0 (Standard/RawDeployment mode)..."
	helm install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd \
		--version v0.18.0 \
		--namespace kserve \
		--create-namespace \
		--wait
	helm install kserve-resources oci://ghcr.io/kserve/charts/kserve-resources \
		--version v0.18.0 \
		--namespace kserve \
		--values platform/kserve/values.yaml \
		--wait
	helm install kserve-runtime-configs oci://ghcr.io/kserve/charts/kserve-runtime-configs \
		--version v0.18.0 \
		--namespace kserve \
		--wait
	kubectl apply -f platform/kserve/clusterservingruntimes.yaml
	kubectl apply -f runtime/clusterservingruntime.yaml

install-platform: install-cert-manager install-kserve
	@echo "Platform installation complete."

# --- Model deployment ---

copy-models-to-nodes:
	@echo "Copying GGUF models to k3d nodes..."
	# Copy to server node
	docker exec k3d-$(CLUSTER_NAME)-server-0 mkdir -p /mnt/models || true
	docker cp models/gguf/. k3d-$(CLUSTER_NAME)-server-0:/mnt/models/
	# Copy to agent node
	docker exec k3d-$(CLUSTER_NAME)-agent-0 mkdir -p /mnt/models || true
	docker cp models/gguf/. k3d-$(CLUSTER_NAME)-agent-0:/mnt/models/

deploy-models: copy-models-to-nodes
	@echo "Deploying model InferenceServices..."
	kubectl apply -f models/qwen-0_5b.yaml

deploy-all: install-platform deploy-models
	@echo "All components deployed."

# --- Testing ---

test:
	@echo "Running smoke tests..."
	@echo "See test scripts in individual component directories."

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
