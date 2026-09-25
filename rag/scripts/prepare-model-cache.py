#!/usr/bin/env python3
"""固定リビジョンのモデルとリモートコードをWSLキャッシュへ取得する。"""

from __future__ import annotations

import json
import os
from pathlib import Path

# 定数: スクリプトと同じragディレクトリに置くモデル固定情報。
RAG_ROOT = Path(__file__).resolve().parents[1]
MODEL_PINS_PATH = RAG_ROOT / "model-cache.json"
# 定数: Hugging Faceのキャッシュを保存するWSL仮想ディスク内の場所。
RUNTIME_ROOT = Path("/home/rag/rag-runtime")
HF_HOME = RUNTIME_ROOT / "cache" / "huggingface"
HUB_CACHE = HF_HOME / "hub"
# 定数: 推論に必要な設定・tokenizer・モデル重みだけを取得する。
DOWNLOAD_PATTERNS = {
    "checkpoint": [
        "config.json",
        "config_sentence_transformers.json",
        "modules.json",
        "custom_st.py",
        "model.safetensors",
        "preprocessor_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ],
    "clip_implementation": ["*.py", "*.json"],
    "text_checkpoint": ["*.json", "*.txt", "*.model"],
    "text_implementation": ["*.py", "*.json"],
}

# キャッシュ取得時はオンラインを許可し、保存先だけをプロジェクト内VHDXに固定する。
os.environ["RAG_RUNTIME_DIR"] = str(RUNTIME_ROOT)
os.environ["HF_HOME"] = str(HF_HOME)
os.environ["TORCH_HOME"] = str(RUNTIME_ROOT / "cache" / "torch")
os.environ["XDG_CACHE_HOME"] = str(RUNTIME_ROOT / "cache" / "xdg")
os.environ["PIP_CACHE_DIR"] = str(RUNTIME_ROOT / "cache" / "pip")
os.environ["TMPDIR"] = str(RUNTIME_ROOT / "tmp")
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ.pop("HF_HUB_OFFLINE", None)

from huggingface_hub import snapshot_download


def prepare_model_cache() -> None:
    """固定コミットを取得し、未指定revisionで参照されるmainも同じ版に固定する。"""
    pins = json.loads(MODEL_PINS_PATH.read_text(encoding="utf-8"))
    for role, entry in pins.items():
        repo_id = entry["repo_id"]
        revision = entry["revision"]
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            allow_patterns=DOWNLOAD_PATTERNS[role],
        )

        repo_cache = HUB_CACHE / f"models--{repo_id.replace('/', '--')}"
        refs_dir = repo_cache / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        (refs_dir / "main").write_text(revision, encoding="ascii")
        print(f"固定キャッシュ準備済み: {repo_id}@{revision}", flush=True)


if __name__ == "__main__":
    prepare_model_cache()
