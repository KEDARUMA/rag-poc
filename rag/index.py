"""Phase 1のファイル登録CLI。"""

import argparse
import json
import sys
from pathlib import Path

# 定数: src配下の実装をCLIから読み込むためのパス。
PROJECT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIRECTORY / "src"))

from rag_poc.config import DATA_ROOT
from rag_poc.indexer import index_directory


def main() -> int:
    """引数を読み、登録結果をJSONで標準出力へ返す。"""
    parser = argparse.ArgumentParser(
        description="TXT・画像・動画をQdrant Localへ登録します"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=str(DATA_ROOT),
        help="登録対象のフォルダー。省略時はこのプロジェクトのdata/",
    )
    parser.add_argument(
        "--type",
        default="txt,image,video",
        help="登録する種別。txt,image,videoをカンマ区切りで指定",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Qdrantコレクションを消去して全件再構築",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="互換用オプション。出力は常にJSONです",
    )
    arguments = parser.parse_args()

    try:
        summary = index_directory(
            root=Path(arguments.directory),
            type_values=arguments.type.split(","),
            rebuild=arguments.rebuild,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if summary["errors"] else 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
