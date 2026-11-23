"""テキスト・画像・動画・PDF・ZIP検索CLI。"""

import argparse
import json
import sys
from pathlib import Path

# 定数: src配下の実装をCLIから読み込むためのパス。
PROJECT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIRECTORY / "src"))

from rag_poc.searcher import search


def main() -> int:
    """検索引数を処理し、結果をJSONで標準出力へ返す。"""
    parser = argparse.ArgumentParser(
        description="Jina CLIP v2とQdrant Serverで類似検索します"
    )
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--text", help="テキスト検索クエリ")
    query_group.add_argument("--image", help="画像ファイルのパス")
    query_group.add_argument("--video", help="動画ファイルのパス")
    query_group.add_argument("--zip", help="内部のTXT・画像・動画・PDFを検索入力にするZIPのパス")
    query_group.add_argument("--pdf", help="全ページの画像を検索入力にするPDFのパス")
    parser.add_argument("--limit", type=int, default=10, help="最大結果数")
    parser.add_argument(
        "--type",
        help="対象をtext,image,video,pdfからカンマ区切りで指定",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="表示する最低類似度",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="互換用オプション。出力は常にJSONです",
    )
    arguments = parser.parse_args()

    if arguments.text is not None:
        query_kind = "text"
        query_value = arguments.text
    elif arguments.image is not None:
        query_kind = "image"
        query_value = arguments.image
    elif arguments.video is not None:
        query_kind = "video"
        query_value = arguments.video
    elif arguments.zip is not None:
        query_kind = "zip"
        query_value = arguments.zip
    else:
        query_kind = "pdf"
        query_value = arguments.pdf

    try:
        result = search(
            query_kind=query_kind,
            query_value=query_value,
            limit=arguments.limit,
            min_score=arguments.min_score,
            type_values=(
                arguments.type.split(",") if arguments.type is not None else None
            ),
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
