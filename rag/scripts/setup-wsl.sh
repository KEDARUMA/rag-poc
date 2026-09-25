#!/usr/bin/env bash
set -Eeuo pipefail

# 定数: 専用WSLユーザーとVHDX内のランタイム配置。
RAG_USER="rag"
RUNTIME_ROOT="/home/${RAG_USER}/rag-runtime"
VENV_ROOT="${RUNTIME_ROOT}/.venv"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "rootユーザーで実行してください。" >&2
    exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RAG_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
LOCK_FILE="${RAG_ROOT}/requirements.lock"

if [[ ! -f "${LOCK_FILE}" ]]; then
    echo "依存関係lockがありません: ${LOCK_FILE}" >&2
    exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
apt-get -qq install -y python3.12-venv ffmpeg

if ! id -u "${RAG_USER}" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "${RAG_USER}"
fi

for directory in \
    "${RUNTIME_ROOT}" \
    "${RUNTIME_ROOT}/cache/huggingface" \
    "${RUNTIME_ROOT}/cache/torch" \
    "${RUNTIME_ROOT}/cache/xdg" \
    "${RUNTIME_ROOT}/cache/pip" \
    "${RUNTIME_ROOT}/qdrant_data" \
    "${RUNTIME_ROOT}/tmp" \
    "${RUNTIME_ROOT}/frames" \
    "${RUNTIME_ROOT}/logs"; do
    install -d -m 0755 -o "${RAG_USER}" -g "${RAG_USER}" "${directory}"
done

if [[ ! -x "${VENV_ROOT}/bin/python" ]]; then
    runuser -u "${RAG_USER}" -- /usr/bin/python3.12 -m venv "${VENV_ROOT}"
fi

PIP_CACHE_DIR="${RUNTIME_ROOT}/cache/pip"
TMPDIR="${RUNTIME_ROOT}/tmp"
PYTHON="${VENV_ROOT}/bin/python"

runuser -u "${RAG_USER}" -- env \
    PIP_CACHE_DIR="${PIP_CACHE_DIR}" \
    TMPDIR="${TMPDIR}" \
    "${PYTHON}" -m pip install --quiet --disable-pip-version-check --upgrade pip==26.2.1

runuser -u "${RAG_USER}" -- env \
    PIP_CACHE_DIR="${PIP_CACHE_DIR}" \
    TMPDIR="${TMPDIR}" \
    "${PYTHON}" -m pip install --quiet \
        --index-url https://download.pytorch.org/whl/cu128 \
        torch==2.11.0+cu128 torchvision==0.26.0+cu128

runuser -u "${RAG_USER}" -- env \
    PIP_CACHE_DIR="${PIP_CACHE_DIR}" \
    TMPDIR="${TMPDIR}" \
    "${PYTHON}" -m pip install --quiet --disable-pip-version-check --requirement "${LOCK_FILE}"

echo "固定リビジョンのJinaモデルと補助コードを取得しています。"
runuser -u "${RAG_USER}" -- env \
    RAG_RUNTIME_DIR="${RUNTIME_ROOT}" \
    HF_HOME="${RUNTIME_ROOT}/cache/huggingface" \
    TORCH_HOME="${RUNTIME_ROOT}/cache/torch" \
    XDG_CACHE_HOME="${RUNTIME_ROOT}/cache/xdg" \
    PIP_CACHE_DIR="${PIP_CACHE_DIR}" \
    TMPDIR="${TMPDIR}" \
    "${PYTHON}" "${RAG_ROOT}/scripts/prepare-model-cache.py"

echo "WSL内のRAG環境構築が完了しました。"
