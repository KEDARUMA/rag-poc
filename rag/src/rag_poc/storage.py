"""Qdrant Local のコレクションとポイント操作。"""

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointIdsList,
    VectorParams,
)

from .config import COLLECTION_NAME, QDRANT_PATH, VECTOR_SIZE


def open_client() -> QdrantClient:
    """WSL仮想ディスク内の永続Qdrantデータを開く。"""
    return QdrantClient(path=str(QDRANT_PATH))


def ensure_collection(client: QdrantClient) -> None:
    """1024次元Cosine検索用コレクションを用意する。"""
    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )


def clear_collection(client: QdrantClient) -> None:
    """PoCコレクションを作り直す。"""
    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)
    ensure_collection(client)


def upsert_points(client: QdrantClient, points: list) -> None:
    """ポイントを128件ずつQdrantへ登録する。"""
    batch_size = 128
    for offset in range(0, len(points), batch_size):
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points[offset : offset + batch_size],
        )


def delete_old_source_points(
    client: QdrantClient, source_id: str, current_hash: str
) -> None:
    """更新後に古い内容のポイントだけ削除する。"""
    source_filter = Filter(
        must=[
            FieldCondition(
                key="source_id",
                match=MatchValue(value=source_id),
            )
        ]
    )
    offset = None
    stale_ids = []
    while True:
        records, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=source_filter,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        stale_ids.extend(
            record.id
            for record in records
            if (record.payload or {}).get("file_hash") != current_hash
        )
        if offset is None:
            break
    if stale_ids:
        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=stale_ids),
        )


def source_hash_exists(
    client: QdrantClient, source_id: str, file_hash: str
) -> bool:
    """同一パス・同一ハッシュの登録済みポイントがあるか調べる。"""
    result, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="source_id",
                    match=MatchValue(value=source_id),
                ),
                FieldCondition(
                    key="file_hash",
                    match=MatchValue(value=file_hash),
                ),
            ]
        ),
        limit=1,
        with_payload=False,
        with_vectors=False,
    )
    return bool(result)


def delete_all_points(client: QdrantClient) -> None:
    """コレクション内の全ポイントを削除する。"""
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(filter=Filter()),
    )
