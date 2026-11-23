"""Qdrant Serverのfloat16コレクションとポイント操作。"""

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Datatype,
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointIdsList,
    VectorParams,
)

from .config import (
    COLLECTION_NAME, LEGACY_QDRANT_PATH, QDRANT_SERVER_ROOT,
    QDRANT_URL, VECTOR_DATATYPE, VECTOR_SIZE,
)
from .qdrant_server import ensure_server


def open_client() -> QdrantClient:
    """専用WSLのQdrant Serverを起動し、移行済みデータへ接続する。"""
    ensure_server()
    client = QdrantClient(url=QDRANT_URL, timeout=60)
    try:
        migration_path = QDRANT_SERVER_ROOT / "local-migration.json"
        if (LEGACY_QDRANT_PATH / "meta.json").is_file():
            import json

            if not migration_path.is_file() or json.loads(
                migration_path.read_text(encoding="utf-8")
            ).get("status") != "completed":
                raise RuntimeError(
                    "旧Local DBの移行が未完了です。scripts/prepare-qdrant-server.pyを実行してください。"
                )
        ensure_collection(client)
        return client
    except Exception:
        client.close()
        raise


def ensure_collection(client: QdrantClient) -> None:
    """1024次元・float16のCosine検索用コレクションを用意し、型を検査する。"""
    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
                datatype=Datatype.FLOAT16,
                on_disk=True,
            ),
        )
    vectors = client.get_collection(COLLECTION_NAME).config.params.vectors
    if (
        not isinstance(vectors, VectorParams)
        or vectors.size != VECTOR_SIZE
        or vectors.distance != Distance.COSINE
        or vectors.datatype != Datatype(VECTOR_DATATYPE)
    ):
        raise RuntimeError("Qdrantのベクトル設定が1024次元・Cosine・float16と一致しません")


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
            wait=True,
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
            wait=True,
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
        wait=True,
    )
