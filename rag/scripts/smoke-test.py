"""一時データを使ってPhase 1の登録・検索を確認する。"""

import json
import shutil
import subprocess
import sys
import tempfile
import sqlite3
from pathlib import Path

# 定数: CLIとランタイム設定のプロジェクト内パス。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from PIL import Image, ImageDraw
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

from rag_poc.config import COLLECTION_NAME, DATA_ROOT, INDEX_STATE_PATH
from rag_poc.indexer import _source_id
from rag_poc.storage import open_client


def _run_cli(arguments: list[str]) -> dict:
    """CLIを実行し、JSON結果を返す。"""
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / arguments[0]), *arguments[1:]],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"{arguments[0]} の実行に失敗しました: {details}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"{arguments[0]} がJSONを返しませんでした: {completed.stdout}"
        ) from error


def _create_test_video(image_path: Path, video_path: Path) -> None:
    """FFmpegで8秒のテスト動画を作る。"""
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "2",
            "-i",
            str(image_path),
            "-t",
            "8",
            "-vf",
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not video_path.is_file():
        raise RuntimeError(
            completed.stderr.strip() or "テスト動画を作成できませんでした"
        )


def _remove_test_records(source_ids: list[str]) -> None:
    """テスト専用QdrantポイントとSQLite行だけを削除する。"""
    client = open_client()
    try:
        if client.collection_exists(COLLECTION_NAME):
            for source_id in source_ids:
                client.delete(
                    collection_name=COLLECTION_NAME,
                    points_selector=FilterSelector(
                        filter=Filter(
                            must=[
                                FieldCondition(
                                    key="source_id",
                                    match=MatchValue(value=source_id),
                                )
                            ]
                        )
                    ),
                )
    finally:
        client.close()

    if INDEX_STATE_PATH.is_file():
        with sqlite3.connect(INDEX_STATE_PATH) as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='indexed_files'"
            ).fetchone()
            if table_exists:
                connection.executemany(
                    "DELETE FROM indexed_files WHERE source_id = ?",
                    [(source_id,) for source_id in source_ids],
                )


def run_smoke_test() -> dict:
    """TXT、画像、動画を登録して、それぞれの検索CLIを確認する。"""
    smoke_root = Path(
        tempfile.mkdtemp(prefix="_codex_smoke_", dir=DATA_ROOT)
    ).resolve()
    text_path = smoke_root / "fixture.txt"
    image_path = smoke_root / "fixture.png"
    video_path = smoke_root / "fixture.mp4"
    source_ids = []

    try:
        text_path.write_text(
            "A red square on a blue background is a RAG smoke-test card.",
            encoding="utf-8",
        )
        image = Image.new("RGB", (320, 240), color=(35, 80, 190))
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 50, 240, 190), fill=(220, 35, 35))
        image.save(image_path)
        _create_test_video(image_path, video_path)

        source_ids = [
            _source_id(path)
            for path in (text_path, image_path, video_path)
        ]

        indexed = _run_cli(
            ["index.py", str(smoke_root), "--type", "txt,image,video"]
        )
        if indexed.get("errors"):
            raise RuntimeError(f"登録エラー: {indexed['errors']}")
        if indexed.get("indexed") != 3 or indexed.get("points", 0) < 3:
            raise RuntimeError(f"登録件数が想定と違います: {indexed}")

        text_results = _run_cli(
            [
                "search.py",
                "--text",
                "red square blue background smoke-test card",
                "--type",
                "text",
                "--limit",
                "100",
                "--json",
            ]
        )["results"]
        image_results = _run_cli(
            [
                "search.py",
                "--image",
                str(image_path),
                "--type",
                "image",
                "--limit",
                "100",
                "--json",
            ]
        )["results"]
        video_results = _run_cli(
            [
                "search.py",
                "--video",
                str(video_path),
                "--type",
                "video",
                "--limit",
                "100",
                "--json",
            ]
        )["results"]

        if not any(result.get("path") == str(text_path) for result in text_results):
            raise RuntimeError("TXT検索結果にテスト文書がありません")
        if not any(result.get("path") == str(image_path) for result in image_results):
            raise RuntimeError("画像検索結果にテスト画像がありません")
        matching_video = next(
            (
                result
                for result in video_results
                if result.get("path") == str(video_path)
            ),
            None,
        )
        if matching_video is None:
            raise RuntimeError("動画検索結果にテスト動画がありません")
        if matching_video.get("start") is None or matching_video.get("end") is None:
            raise RuntimeError("動画検索結果にシーン時刻がありません")

        return {
            "status": "passed",
            "indexed_files": indexed["indexed"],
            "indexed_points": indexed["points"],
            "text_search": "passed",
            "image_search": "passed",
            "video_search": "passed",
            "video_scene": {
                "start": matching_video["start"],
                "end": matching_video["end"],
            },
        }
    finally:
        try:
            if source_ids:
                _remove_test_records(source_ids)
        finally:
            resolved_data_root = DATA_ROOT.resolve()
            if (
                smoke_root.parent == resolved_data_root
                and smoke_root.name.startswith("_codex_smoke_")
            ):
                shutil.rmtree(smoke_root)


if __name__ == "__main__":
    print(json.dumps(run_smoke_test(), ensure_ascii=False))
