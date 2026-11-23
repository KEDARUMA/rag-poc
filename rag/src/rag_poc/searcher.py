"""テキスト・画像・動画・PDF・ZIPクエリのベクトル検索。"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
from qdrant_client.models import FieldCondition, Filter, MatchValue

from .archive import extract_archive
from .config import COLLECTION_NAME, TEMP_ROOT
from .embedding import EmbeddingProvider
from .pdf import render_pdf_pages
from .storage import open_client
from .video import extract_video_frames

# 定数: 1回の動画クエリで処理する最大フレーム数。
MAX_VIDEO_QUERY_FRAMES = 24
# 定数: ユーザー向け種別とQdrant payload種別の対応。
TYPE_VALUES = {
    "text": {"text"},
    "txt": {"text"},
    "image": {"image"},
    "video": {"video_frame"},
    "pdf": {"pdf_page"},
}


def _normalize_types(
    query_kind: str, type_values: Iterable[str] | None
) -> set[str]:
    """クエリ種別ごとの検索対象を正規化する。"""
    if type_values:
        selected = set()
        for value in type_values:
            selected.update(TYPE_VALUES.get(value.strip().casefold(), set()))
        return selected
    if query_kind == "text":
        return {"text", "image", "video_frame", "pdf_page"}
    if query_kind in {"image", "pdf"}:
        return {"image", "video_frame", "pdf_page"}
    return {"video_frame"}


def _query_vectors(
    query_kind: str, query_value: str, embedder: EmbeddingProvider
) -> list[np.ndarray]:
    """入力形式に合わせて検索ベクトルを作る。"""
    if query_kind == "text":
        return list(embedder.embed_texts([query_value]))
    if query_kind == "image":
        return list(embedder.embed_images([Path(query_value).expanduser()]))
    if query_kind == "pdf":
        with render_pdf_pages(Path(query_value).expanduser(), TEMP_ROOT) as pages:
            return [embedder.embed_images([page.path])[0] for page in pages]

    video_path = Path(query_value).expanduser()
    with extract_video_frames(video_path) as frames:
        if not frames:
            raise ValueError(f"動画からフレームを取得できません: {video_path}")
        if len(frames) > MAX_VIDEO_QUERY_FRAMES:
            step = (len(frames) - 1) / (MAX_VIDEO_QUERY_FRAMES - 1)
            chosen = [
                frames[round(index * step)]
                for index in range(MAX_VIDEO_QUERY_FRAMES)
            ]
        else:
            chosen = frames
        return list(embedder.embed_images([frame.path for frame in chosen]))


@contextmanager
def _expanded_queries(
    queries: list[tuple[str, str]],
) -> Iterator[list[tuple[str, str]]]:
    """ZIPを内部ファイルのクエリへ展開し、検索完了まで一時ファイルを保つ。"""
    from .indexer import _chunk_text

    with ExitStack() as stack:
        expanded = []
        for kind, value in queries:
            if kind != "zip":
                expanded.append((kind, value))
                continue
            members = stack.enter_context(
                extract_archive(Path(value).expanduser(), TEMP_ROOT)
            )
            for member in members:
                if member.kind == "text":
                    text = member.path.read_text(encoding="utf-8-sig", errors="replace")
                    expanded.extend(("text", chunk) for chunk in _chunk_text(text))
                else:
                    expanded.append((member.kind, str(member.path)))
        if not expanded:
            raise ValueError("ZIP内に検索可能な内容がありません")
        yield expanded


def _filter_for_types(types: set[str]) -> Filter:
    """検索対象のpayload種別をQdrantフィルターにする。"""
    return Filter(
        should=[
            FieldCondition(
                key="type",
                match=MatchValue(value=kind),
            )
            for kind in sorted(types)
        ]
    )


def _result_record(point) -> dict | None:
    """QdrantポイントをJSON向けの検索結果に変換する。"""
    payload = point.payload or {}
    kind = payload.get("type")
    common = {"path": payload.get("file"), "score": float(point.score)}
    if payload.get("archive_member"):
        common["archive_member"] = payload["archive_member"]
    if kind == "pdf_page":
        return {
            **common,
            "type": "pdf",
            "page": payload.get("page"),
            "page_count": payload.get("page_count"),
        }
    if kind == "video_frame":
        return {
            **common,
            "type": "video",
            "time": payload.get("time"),
            "start": payload.get("scene_start"),
            "end": payload.get("scene_end"),
        }
    if kind == "image":
        return {
            **common,
            "type": "image",
            "width": payload.get("width"),
            "height": payload.get("height"),
        }
    if kind == "text":
        return {
            **common,
            "type": "text",
            "chunk_index": payload.get("chunk_index"),
            "text": payload.get("text"),
        }
    return None


def _result_key(record: dict, point_id: str) -> tuple:
    """同じ画像・文章・動画シーンの重複検索結果をまとめる。"""
    if record.get("archive_member"):
        return ("zip", record.get("path"))
    if record["type"] == "pdf":
        return ("pdf", record.get("path"))
    if record["type"] == "video":
        return (
            record["type"],
            record.get("path"),
            record.get("start"),
            record.get("end"),
        )
    return (record["type"], point_id)


def search(
    query_kind: str,
    query_value: str,
    limit: int = 10,
    min_score: float = 0.0,
    type_values: Iterable[str] | None = None,
) -> dict:
    """指定クエリを検索し、上位の結果を返す。"""
    if limit < 1:
        raise ValueError("--limit は1以上を指定してください")
    if not 0.0 <= min_score <= 1.0:
        raise ValueError("--min-score は0から1の範囲で指定してください")

    if query_kind == "zip":
        result = search_many(
            [("zip", query_value)], limit, min_score, type_values=type_values
        )
        return {
            "query_type": query_kind,
            "query": query_value,
            "results": result["results"],
        }

    types = _normalize_types(query_kind, type_values)
    if not types:
        raise ValueError("--type に text, image, video, pdf のいずれかを指定してください")

    embedder = EmbeddingProvider()
    vectors = _query_vectors(query_kind, query_value, embedder)
    client = open_client()
    try:
        if not client.collection_exists(COLLECTION_NAME):
            return {
                "query_type": query_kind,
                "query": query_value,
                "results": [],
            }
        query_filter = _filter_for_types(types)
        merged: dict[tuple, dict] = {}
        for vector in vectors:
            response = client.query_points(
                collection_name=COLLECTION_NAME,
                query=vector.tolist(),
                query_filter=query_filter,
                limit=max(limit * 3, limit),
                with_payload=True,
            )
            for point in response.points:
                record = _result_record(point)
                if record is None or record["score"] < min_score:
                    continue
                key = _result_key(record, str(point.id))
                current = merged.get(key)
                if current is None or record["score"] > current["score"]:
                    merged[key] = record
    finally:
        client.close()

    results = sorted(
        merged.values(),
        key=lambda item: item["score"],
        reverse=True,
    )[:limit]
    return {
        "query_type": query_kind,
        "query": query_value,
        "results": results,
    }


def search_many(
    queries: Iterable[tuple[str, str]],
    limit: int = 10,
    min_score: float = 0.0,
    type_values: Iterable[str] | None = None,
) -> dict:
    """複数のクエリを同じモデル・Qdrant接続で検索し、ファイル単位にまとめる。"""
    if limit < 1:
        raise ValueError("limit は1以上を指定してください")
    if not 0.0 <= min_score <= 1.0:
        raise ValueError("min_score は0から1の範囲で指定してください")

    query_items = list(queries)
    if not query_items:
        raise ValueError("検索クエリがありません")
    for query_kind, _ in query_items:
        if query_kind not in {"text", "image", "video", "zip", "pdf"}:
            raise ValueError(f"未対応のクエリ種別です: {query_kind}")

    selected_types = tuple(type_values) if type_values is not None else None
    if selected_types and not _normalize_types("text", selected_types):
        raise ValueError("--type に text, image, video, pdf のいずれかを指定してください")

    merged: dict[str, dict] = {}
    with _expanded_queries(query_items) as expanded:
        embedder = EmbeddingProvider()
        client = open_client()
        try:
            if not client.collection_exists(COLLECTION_NAME):
                return {"queries": query_items, "results": []}

            for query_kind, query_value in expanded:
                query_filter = _filter_for_types(
                    _normalize_types(query_kind, selected_types)
                )
                vectors = _query_vectors(query_kind, query_value, embedder)
                for vector in vectors:
                    response = client.query_points(
                        collection_name=COLLECTION_NAME,
                        query=vector.tolist(),
                        query_filter=query_filter,
                        limit=max(limit * 3, limit),
                        with_payload=True,
                    )
                    for point in response.points:
                        record = _result_record(point)
                        if record is None or record["score"] < min_score:
                            continue
                        result_path = record.get("path")
                        if not result_path:
                            continue
                        current = merged.get(result_path)
                        if current is None or record["score"] > current["score"]:
                            merged[result_path] = record
        finally:
            client.close()

    results = sorted(
        merged.values(),
        key=lambda item: item["score"],
        reverse=True,
    )[:limit]
    return {"queries": query_items, "results": results}
