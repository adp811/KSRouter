.PHONY: vm-up vm-down cluster-create cluster-delete bootstrap teardown
.PHONY: fix-dns install-cert-manager install-kserve install-monitoring install-keda install-platform
.PHONY: deploy-models deploy-router apply-scaledobjects deploy-all test
.PHONY: vm-memory cluster-status

# KSRouter — KServe RawDeployment LLM Router
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
	@echo "Fixing CoreDNS for external DNS resolution..."
	./scripts/fix-dns.sh

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
		--wait \
		--timeout 10m

install-kserve:
	@echo "Installing KServe v0.18.0 (Standard/RawDeployment mode)..."
	helm install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd \
		--version v0.18.0 \
		--namespace kserve \
		--create-namespace \
		--wait \
		--timeout 10m
	helm install kserve-resources oci://ghcr.io/kserve/charts/kserve-resources \
		--version v0.18.0 \
		--namespace kserve \
		--values platform/kserve/values.yaml \
		--wait \
		--timeout 10m
	helm install kserve-runtime-configs oci://ghcr.io/kserve/charts/kserve-runtime-configs \
		--version v0.18.0 \
		--namespace kserve \
		--wait \
		--timeout 10m
	kubectl apply -f platform/kserve/clusterservingruntimes.yaml
	kubectl apply -f runtime/clusterservingruntime.yaml

install-monitoring:
	@echo "Installing kube-prometheus-stack (trimmed)..."
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts || true
	helm repo update
	helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
		--namespace monitoring \
		--create-namespace \
		--values platform/monitoring/values.yaml \
		--wait \
		--timeout 10m
	@echo "Applying observability manifests..."
	kubectl apply -f observability/podmonitor-llm-models.yaml
	kubectl apply -f observability/prometheusrules.yaml
	kubectl apply -f observability/grafana-configmap.yaml

install-keda:
	@echo "Installing KEDA v2.20.0..."
	helm repo add kedacore https://kedacore.github.io/charts || true
	helm repo update
	helm install keda kedacore/keda \
		--namespace keda \
		--create-namespace \
		--values platform/keda/values.yaml \
		--wait \
		--timeout 10m

install-platform: install-cert-manager install-kserve install-monitoring install-keda
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
	kubectl apply -f models/llama-1b.yaml
	kubectl apply -f models/qwen-3b.yaml

apply-scaledobjects:
	@echo "Applying KEDA ScaledObjects..."
	kubectl apply -f models/qwen-0-5b-scaledobject.yaml
	kubectl apply -f models/qwen-3b-scaledobject.yaml

build-router:
	@echo "Building router image..."
	docker build -t llm-router:latest router/
	k3d image import llm-router:latest --cluster $(CLUSTER_NAME)

deploy-router: build-router
	@echo "Deploying router..."
	kubectl apply -f router/manifests/deployment.yaml
	kubectl apply -f router/manifests/podmonitor.yaml

deploy-all: install-platform deploy-models deploy-router apply-scaledobjects
	@echo "All components deployed."

# --- Testing ---

test:
	@echo "Running smoke tests..."
	@echo "Testing all three models..."
	@kubectl get inferenceservices -n default
	@echo "Testing router..."
	@kubectl get pods -n default -l app=llm-router

# --- Utilities ---

vm-memory:
	colima ssh -- free -m

cluster-status:
	kubectl get nodes
	kubectl get pods -A
