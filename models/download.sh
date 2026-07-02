#!/usr/bin/env bash
# models/download.sh
# Download GGUF models from Hugging Face with SHA256 verification
# Usage: ./models/download.sh [model_name]
#   model_name: qwen-0_5b | llama-1b | qwen-3b | all

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${SCRIPT_DIR}/gguf"
MANIFEST="${SCRIPT_DIR}/manifest.yaml"

mkdir -p "${MODELS_DIR}"

# Parse manifest with yq
download_model() {
    local name="$1"
    local repo=$(yq e ".models[] | select(.name == \"${name}\") | .repo" "${MANIFEST}")
    local filename=$(yq e ".models[] | select(.name == \"${name}\") | .filename" "${MANIFEST}")
    local expected_sha=$(yq e ".models[] | select(.name == \"${name}\") | .sha256" "${MANIFEST}")

    if [ -z "${repo}" ] || [ -z "${filename}" ]; then
        echo "Error: model '${name}' not found in manifest"
        exit 1
    fi

    local dest="${MODELS_DIR}/${filename}"

    echo "Downloading ${name} from ${repo}/${filename}..."

    if [ -f "${dest}" ]; then
        echo "  File exists, verifying SHA256..."
        local actual_sha=$(shasum -a 256 "${dest}" | awk '{print $1}')
        if [ "${actual_sha}" = "${expected_sha}" ] || [ "${expected_sha}" = "TBD" ]; then
            echo "  ✓ SHA256 matches (or TBD)"
            return 0
        else
            echo "  ✗ SHA256 mismatch! Expected: ${expected_sha}, Got: ${actual_sha}"
            echo "  Re-downloading..."
            rm -f "${dest}"
        fi
    fi

    # Use hf CLI for resumable downloads
    hf download "${repo}" "${filename}" --local-dir "${MODELS_DIR}"

    echo "  Computing SHA256..."
    local actual_sha=$(shasum -a 256 "${dest}" | awk '{print $1}')
    echo "  SHA256: ${actual_sha}"

    if [ "${expected_sha}" != "TBD" ] && [ "${actual_sha}" != "${expected_sha}" ]; then
        echo "  ✗ SHA256 mismatch! Expected: ${expected_sha}, Got: ${actual_sha}"
        exit 1
    fi

    echo "  ✓ ${name} ready at ${dest}"
}

case "${1:-all}" in
    qwen-0_5b)
        download_model qwen-0_5b
        ;;
    llama-1b)
        download_model llama-1b
        ;;
    qwen-3b)
        download_model qwen-3b
        ;;
    all)
        download_model qwen-0_5b
        download_model llama-1b
        download_model qwen-3b
        ;;
    *)
        echo "Usage: $0 [qwen-0_5b|llama-1b|qwen-3b|all]"
        exit 1
        ;;
esac

echo "Done."
