"""TXT・画像・動画をQdrant Localへ登録する処理。"""

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from qdrant_client.models import PointStruct

from .config import (
    COLLECTION_NAME,
    DATA_ROOT,
    INDEX_STATE_PATH,
    MODEL_ID,
    MODEL_REVISION,
)
from .embedding import EmbeddingProvider
from .storage import (
    clear_collection,
    delete_old_source_points,
    ensure_collection,
    open_client,
    source_hash_exists,
    upsert_points,
)
from .video import extract_video_frames

# 定数: Phase 1で登録するファイル形式。
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
TYPE_NAMES = {"txt": "text", "text": "text", "image": "image", "video": "video"}


def _file_hash(path: Path) -> str:
    """ファイル内容のSHA256を計算する。"""
    digest = hashlib.sha256()
    with path.open("rb") as file_stream:
        for block in iter(lambda: file_stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_id(path: Path) -> str:
    """絶対パスから、ファイル単位で安定した識別子を作る。"""
    normalized_path = path.resolve().as_posix().casefold()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, normalized_path))


def _point_id(source_id: str, kind: str, unit_key: str) -> str:
    """ファイル内の単位に対応する安定したQdrant IDを作る。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{kind}:{unit_key}"))


def _chunk_text(text: str) -> list[str]:
    """長いTXTを重なり付きの文字チャンクへ分割する。"""
    from .config import TEXT_CHUNK_OVERLAP, TEXT_CHUNK_SIZE

    normalized_text = text.strip()
    if not normalized_text:
        return []
    if len(normalized_text) <= TEXT_CHUNK_SIZE:
        return [normalized_text]
    step = TEXT_CHUNK_SIZE - TEXT_CHUNK_OVERLAP
    return [
        normalized_text[start : start + TEXT_CHUNK_SIZE]
        for start in range(0, len(normalized_text), step)
    ]


def _file_kind(path: Path) -> str | None:
    """拡張子からPhase 1のファイル種別を返す。"""
    suffix = path.suffix.casefold()
    if suffix == ".txt":
        return "text"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _selected_kinds(type_values: Iterable[str] | None) -> set[str]:
    """CLI指定を内部のファイル種別へ正規化する。"""
    if not type_values:
        return {"text", "image", "video"}
    selected = set()
    for value in type_values:
        normalized = TYPE_NAMES.get(value.strip().casefold())
        if normalized:
            selected.add(normalized)
    return selected


def _base_payload(
    path: Path,
    source_id: str,
    file_hash: str,
    indexed_at: str,
) -> dict:
    """全ポイントで共通する検索用metadataを作る。"""
    stat = path.stat()
    return {
        "source_id": source_id,
        "type": "",
        "file": str(path.resolve()),
        "file_hash": file_hash,
        "modified_time": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "embedding_model": MODEL_ID,
        "embedding_version": MODEL_REVISION,
        "indexed_at": indexed_at,
    }


def _create_points(
    path: Path,
    kind: str,
    source_id: str,
    file_hash: str,
    indexed_at: str,
    embedder: EmbeddingProvider,
) -> list[PointStruct]:
    """1ファイルのEmbeddingとQdrantポイントを作る。"""
    base_payload = _base_payload(path, source_id, file_hash, indexed_at)
    vectors = []
    records = []

    if kind == "text":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        chunks = _chunk_text(text)
        vectors = embedder.embed_texts(chunks)
        records = [
            (
                "text",
                str(index),
                {"text": chunk, "chunk_index": index},
            )
            for index, chunk in enumerate(chunks)
        ]
    elif kind == "image":
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        vectors = embedder.embed_images([path])
        records = [
            (
                "image",
                "image",
                {"width": width, "height": height},
            )
        ]
    else:
        with extract_video_frames(path) as frames:
            vectors = embedder.embed_images([frame.path for frame in frames])
            records = [
                (
                    "video_frame",
                    f"{frame.time:.3f}",
                    {
                        "time": frame.time,
                        "scene_start": frame.scene_start,
                        "scene_end": frame.scene_end,
                    },
                )
                for frame in frames
            ]

    points = []
    for vector, (point_kind, unit_key, extra_payload) in zip(
        vectors, records, strict=True
    ):
        payload = dict(base_payload)
        payload["type"] = point_kind
        payload.update(extra_payload)
        points.append(
            PointStruct(
                id=_point_id(source_id, point_kind, unit_key),
                vector=vector.tolist(),
                payload=payload,
            )
        )
    return points


def _prepare_manifest(connection: sqlite3.Connection) -> None:
    """SQLiteにファイルごとの完了状態を用意する。"""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS indexed_files (
            source_id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def index_directory(
    root: Path = DATA_ROOT,
    type_values: Iterable[str] | None = None,
    rebuild: bool = False,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    """対応ファイルを登録し、重複・更新状態を管理する。"""
    index_root = root.expanduser().resolve()
    if not index_root.is_dir():
        raise FileNotFoundError(f"入力ディレクトリがありません: {index_root}")

    selected_kinds = _selected_kinds(type_values)
    source_files = sorted(
        path
        for path in index_root.rglob("*")
        if path.is_file() and _file_kind(path) in selected_kinds
    )
    summary = {
        "root": str(index_root),
        "indexed": 0,
        "skipped": 0,
        "points": 0,
        "errors": [],
    }

    def report_progress(event: str, **details: object) -> None:
        """登録処理の進捗を呼び出し元へ通知する。"""
        if progress_callback is not None:
            progress_callback({"event": event, **details})

    report_progress(
        "started",
        processed=0,
        total=len(source_files),
        message=f"インデックス対象ファイル: {len(source_files)}件",
    )
    if not source_files and not rebuild:
        return summary

    embedder = EmbeddingProvider() if source_files else None
    client = open_client()
    connection = sqlite3.connect(INDEX_STATE_PATH)
    try:
        _prepare_manifest(connection)
        if rebuild:
            clear_collection(client)
            connection.execute("DELETE FROM indexed_files")
            connection.commit()
        else:
            ensure_collection(client)

        if not source_files:
            return summary

        if embedder is None:
            raise RuntimeError("EmbeddingProviderを初期化できませんでした")

        for file_number, path in enumerate(source_files, start=1):
            kind = _file_kind(path)
            if kind not in selected_kinds:
                continue
            report_progress(
                "file_started",
                processed=file_number - 1,
                total=len(source_files),
                path=str(path.resolve()),
                message=f"処理中: {path.name}",
            )
            result = "skipped"
            error_message = ""
            try:
                source_id = _source_id(path)
                file_hash = _file_hash(path)
                prior = connection.execute(
                    "SELECT file_hash FROM indexed_files WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
                if (
                    prior is not None
                    and prior[0] == file_hash
                    and source_hash_exists(client, source_id, file_hash)
                ):
                    summary["skipped"] += 1
                    result = "skipped"
                else:
                    indexed_at = datetime.now(timezone.utc).isoformat()
                    points = _create_points(
                        path,
                        kind,
                        source_id,
                        file_hash,
                        indexed_at,
                        embedder,
                    )
                    if points:
                        upsert_points(client, points)
                        delete_old_source_points(client, source_id, file_hash)
                        connection.execute(
                            """
                            INSERT INTO indexed_files
                                (source_id, file_path, file_hash, indexed_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(source_id) DO UPDATE SET
                                file_path = excluded.file_path,
                                file_hash = excluded.file_hash,
                                indexed_at = excluded.indexed_at
                            """,
                            (source_id, str(path.resolve()), file_hash, indexed_at),
                        )
                        connection.commit()
                        summary["indexed"] += 1
                        summary["points"] += len(points)
                        result = "indexed"
                    else:
                        summary["skipped"] += 1
            except Exception as error:
                connection.rollback()
                summary["errors"].append(
                    {"path": str(path.resolve()), "error": str(error)}
                )
                result = "error"
                error_message = str(error)

            if result == "indexed":
                message = f"登録完了: {path.name}"
            elif result == "error":
                message = f"エラー: {path.name} - {error_message}"
            else:
                message = f"スキップ: {path.name}"
            report_progress(
                "file_completed",
                processed=file_number,
                total=len(source_files),
                path=str(path.resolve()),
                result=result,
                error=error_message,
                message=message,
            )
    finally:
        connection.close()
        client.close()
    return summary
