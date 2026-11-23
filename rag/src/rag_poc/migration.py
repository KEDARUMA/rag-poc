"""旧Local DBを保持して、既存ポイントをServerのfloat16へ移行する。"""

import json
from contextlib import closing
from datetime import datetime, timezone

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, SearchParams, VectorParams

from .config import (
    COLLECTION_NAME, LEGACY_QDRANT_PATH, QDRANT_SERVER_ROOT, QDRANT_URL, VECTOR_SIZE,
)
from .qdrant_server import server_lock
from .storage import ensure_collection


def _save_status(status: dict) -> None:
    """移行状態を一時ファイル経由で保存する。"""
    target = QDRANT_SERVER_ROOT / "local-migration.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def migrate_local() -> dict:
    """IDとメタデータを保って移行し、件数・値・検索結果を照合する。"""
    with server_lock("migration"):
        with closing(QdrantClient(url=QDRANT_URL, timeout=60)) as target:
            ensure_collection(target)
            marker = QDRANT_SERVER_ROOT / "local-migration.json"
            status = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
            source_path = str(LEGACY_QDRANT_PATH)
            if status and status.get("source") != source_path:
                raise RuntimeError("移行記録のLocal DB保存先が一致しません")
            if status.get("status") == "completed":
                return {**status, "already_completed": True}
            if not (LEGACY_QDRANT_PATH / "meta.json").is_file():
                return {"status": "source_absent", "points": 0}
            if not status and target.count(COLLECTION_NAME, exact=True).count:
                raise RuntimeError("移行記録のないServerに既存ポイントがあります。自動上書きは行えません。")
            with closing(QdrantClient(path=str(LEGACY_QDRANT_PATH))) as source:
                expected = 0
                samples = []
                sampled_kinds = {}
                max_error = 0.0
                if source.collection_exists(COLLECTION_NAME):
                    vectors = source.get_collection(COLLECTION_NAME).config.params.vectors
                    if (
                        not isinstance(vectors, VectorParams)
                        or vectors.size != VECTOR_SIZE
                        or vectors.distance != Distance.COSINE
                    ):
                        raise RuntimeError("旧Local DBのベクトル仕様が移行対象と一致しません")
                    expected = source.count(COLLECTION_NAME, exact=True).count
                    _save_status({"status": "running", "source": source_path, "points": expected})
                    offset = None
                    copied = 0
                    while True:
                        records, offset = source.scroll(
                            COLLECTION_NAME, limit=128, offset=offset,
                            with_vectors=True, with_payload=True,
                        )
                        if records:
                            target.upsert(
                                COLLECTION_NAME,
                                points=[PointStruct(id=record.id, vector=record.vector, payload=record.payload) for record in records],
                                wait=True,
                            )
                            restored = {
                                record.id: record for record in target.retrieve(
                                    COLLECTION_NAME, ids=[record.id for record in records],
                                    with_vectors=True, with_payload=True,
                                )
                            }
                            for record in records:
                                restored_record = restored.get(record.id)
                                if restored_record is None or restored_record.payload != record.payload:
                                    raise RuntimeError(f"移行したID・メタデータが一致しません: {record.id}")
                                original = np.asarray(record.vector, dtype=np.float32)
                                converted = np.asarray(restored_record.vector, dtype=np.float32)
                                if original.shape != converted.shape or not np.isfinite(converted).all():
                                    raise RuntimeError(f"移行したベクトルが不正です: {record.id}")
                                if not np.array_equal(converted, converted.astype(np.float16).astype(np.float32)):
                                    raise RuntimeError("Serverから取得した値がfloat16の表現と一致しません")
                                error = float(np.max(np.abs(original - converted)))
                                max_error = max(max_error, error)
                                if error > 0.002:
                                    raise RuntimeError(f"float16化の誤差が想定範囲を超えています: {error}")
                                kind = (record.payload or {}).get("type", "unknown")
                                if sampled_kinds.get(kind, 0) < 3:
                                    samples.append(record)
                                    sampled_kinds[kind] = sampled_kinds.get(kind, 0) + 1
                            copied += len(records)
                            print(f"移行・照合済み: {copied}/{expected}ポイント", flush=True)
                        if offset is None:
                            break
                if target.count(COLLECTION_NAME, exact=True).count != expected:
                    raise RuntimeError("移行前後のポイント件数が一致しません")
                max_score_error = 0.0
                for record in samples:
                    before = source.query_points(
                        COLLECTION_NAME, query=record.vector, limit=5, with_payload=False
                    ).points
                    after = target.query_points(
                        COLLECTION_NAME, query=record.vector, limit=5,
                        search_params=SearchParams(exact=True), with_payload=False,
                    ).points
                    if len(before) != len(after) or (before and abs(before[0].score - after[0].score) > 0.002):
                        raise RuntimeError("移行前後の検索結果に想定以上の差があります")
                    if before:
                        # 同点候補の順位入れ替わりを許し、最上位候補の類似度を照合する。
                        reference = source.retrieve(COLLECTION_NAME, ids=[after[0].id], with_vectors=True)[0]
                        query = np.asarray(record.vector, dtype=np.float64)
                        vector = np.asarray(reference.vector, dtype=np.float64)
                        score = float(np.dot(query, vector) / (np.linalg.norm(query) * np.linalg.norm(vector)))
                        if before[0].score - score > 0.002:
                            raise RuntimeError("float16化後の最上位候補が移行前と一致しません")
                        max_score_error = max(max_score_error, abs(before[0].score - after[0].score))
                result = {
                    "status": "completed", "source": source_path,
                    "points": expected, "datatype": "float16",
                    "verified_queries": len(samples), "max_vector_error": max_error,
                    "max_top_score_error": max_score_error,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                _save_status(result)
                return result
