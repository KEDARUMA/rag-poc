"""プロジェクト内の保存先とモデル設定。"""

import json
import os
from pathlib import Path

# 定数: rag サブディレクトリの絶対パス。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 定数: 取得するモデルとリモートコードの固定リビジョン。
MODEL_PINS = json.loads((PROJECT_ROOT / "model-cache.json").read_text(encoding="utf-8"))
# 定数: 入力ファイルの保存先。
DATA_ROOT = PROJECT_ROOT / "data"
# 定数: 任意の出力ファイルの保存先。
OUTPUT_ROOT = PROJECT_ROOT / "output"
# 定数: Linux 側のランタイム保存先。WSL 仮想ディスク内に置く。
# 定数: 保存場所を専用WSL仮想ディスク内に固定する。
RUNTIME_ROOT = Path("/home/rag/rag-runtime")
# 定数: Qdrant Local の永続データ保存先。
QDRANT_PATH = RUNTIME_ROOT / "qdrant_data"
# 定数: 更新判定用 SQLite ファイル。
INDEX_STATE_PATH = RUNTIME_ROOT / "index_state.sqlite3"
# 定数: 動画フレームなどの一時ファイル保存先。
TEMP_ROOT = RUNTIME_ROOT / "tmp"
# 定数: Hugging Face モデルの固定リビジョン。
MODEL_ID = MODEL_PINS["checkpoint"]["repo_id"]
MODEL_REVISION = MODEL_PINS["checkpoint"]["revision"]
# 定数: Qdrant コレクションとベクトルの仕様。
COLLECTION_NAME = "rag_multimodal"
VECTOR_SIZE = 1024
# 定数: 初期PoC用のテキスト分割幅と重複幅。
TEXT_CHUNK_SIZE = 1000
TEXT_CHUNK_OVERLAP = 100
# 定数: 動画のフレーム抽出間隔とシーン検出感度。
VIDEO_SAMPLE_INTERVAL = 5.0
VIDEO_SCENE_THRESHOLD = 27.0

# 定数: キャッシュと一時ファイルをWSL仮想ディスク内に集約する。
os.environ["RAG_RUNTIME_DIR"] = str(RUNTIME_ROOT)
os.environ["HF_HOME"] = str(RUNTIME_ROOT / "cache" / "huggingface")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TORCH_HOME"] = str(RUNTIME_ROOT / "cache" / "torch")
os.environ["XDG_CACHE_HOME"] = str(RUNTIME_ROOT / "cache" / "xdg")
os.environ["PIP_CACHE_DIR"] = str(RUNTIME_ROOT / "cache" / "pip")
os.environ["TMPDIR"] = str(TEMP_ROOT)


def prepare_directories() -> None:
    """プロジェクトとWSL仮想ディスク内の作業ディレクトリを作る。"""
    for directory in (
        DATA_ROOT,
        OUTPUT_ROOT,
        QDRANT_PATH,
        TEMP_ROOT,
        RUNTIME_ROOT / "frames",
        RUNTIME_ROOT / "logs",
        RUNTIME_ROOT / "cache" / "huggingface",
        RUNTIME_ROOT / "cache" / "torch",
        RUNTIME_ROOT / "cache" / "xdg",
        RUNTIME_ROOT / "cache" / "pip",
    ):
        directory.mkdir(parents=True, exist_ok=True)


prepare_directories()
