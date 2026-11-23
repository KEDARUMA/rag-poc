"""Qdrant Serverの配置・起動と、旧Local DBからのfloat16移行を行う。"""

import argparse
import json
import sys
from pathlib import Path

# 定数: 共通実装を読み込むプロジェクトの場所。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rag_poc.migration import migrate_local
from rag_poc.qdrant_server import ensure_server, install_server


def main() -> None:
    """初回は配置と移行を行い、通常起動では準備済みサーバーを起動する。"""
    parser = argparse.ArgumentParser(description="専用WSLのQdrant Serverを準備します")
    parser.add_argument("--start-only", action="store_true", help="配置・移行を行わず起動のみ")
    arguments = parser.parse_args()
    if not arguments.start_only:
        print("固定版Qdrant Serverを配置します。", flush=True)
        install_server()
    ensure_server()
    if not arguments.start_only:
        print(json.dumps(migrate_local(), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
