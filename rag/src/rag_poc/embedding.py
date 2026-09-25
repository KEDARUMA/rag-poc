"""Jina CLIP v2 によるテキスト・画像Embedding。"""

import logging
import time
from pathlib import Path
from typing import Sequence

# 環境変数を外部ライブラリが読む前に設定する。
from .config import MODEL_ID, MODEL_REVISION, VECTOR_SIZE

import numpy as np
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer

# 定数: Embedding処理の診断ログ。
LOGGER = logging.getLogger(__name__)


class EmbeddingProvider:
    """テキストと画像を同じベクトル空間へ変換する。"""

    def __init__(self, device: str = "cuda") -> None:
        """モデルを読み込み、指定デバイスを選ぶ。"""
        model_started = time.perf_counter()
        LOGGER.info(
            "embedding.model.load.started device=%s model=%s revision=%s",
            device,
            MODEL_ID,
            MODEL_REVISION,
        )
        if device == "cuda" and not torch.cuda.is_available():
            LOGGER.error("embedding.model.load.failed reason=cuda_unavailable")
            raise RuntimeError(
                "CUDA GPUを利用できません。WSL内のGPU設定を確認してください。"
            )
        self.device = device
        try:
            self.model = SentenceTransformer(
                MODEL_ID,
                revision=MODEL_REVISION,
                trust_remote_code=True,
                device=device,
            )
        except Exception:
            LOGGER.exception("embedding.model.load.failed device=%s", device)
            raise
        LOGGER.info(
            "embedding.model.load.completed elapsed_seconds=%.3f",
            time.perf_counter() - model_started,
        )

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        """テキスト一覧を正規化済みベクトルへ変換する。"""
        if not texts:
            return np.empty((0, VECTOR_SIZE), dtype=np.float32)
        encode_started = time.perf_counter()
        LOGGER.info("embedding.text.encode.started item_count=%d", len(texts))
        try:
            vectors = self.model.encode(
                list(texts),
                batch_size=1,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception:
            LOGGER.exception(
                "embedding.text.encode.failed item_count=%d", len(texts)
            )
            raise
        LOGGER.info(
            "embedding.text.encode.completed item_count=%d elapsed_seconds=%.3f",
            len(texts),
            time.perf_counter() - encode_started,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_images(self, image_paths: Sequence[Path]) -> np.ndarray:
        """画像ファイル一覧を正規化済みベクトルへ変換する。"""
        if not image_paths:
            return np.empty((0, VECTOR_SIZE), dtype=np.float32)
        decode_started = time.perf_counter()
        LOGGER.info("embedding.image.decode.started image_count=%d", len(image_paths))
        images = []
        try:
            for image_index, image_path in enumerate(image_paths, start=1):
                LOGGER.info(
                    "embedding.image.decode.item_started index=%d extension=%s",
                    image_index,
                    image_path.suffix.casefold(),
                )
                with Image.open(image_path) as image:
                    image_format = image.format or "unknown"
                    width, height = image.size
                    images.append(image.convert("RGB"))
                LOGGER.info(
                    "embedding.image.decode.item_completed index=%d format=%s width=%d height=%d",
                    image_index,
                    image_format,
                    width,
                    height,
                )
        except Exception:
            LOGGER.exception(
                "embedding.image.decode.failed image_count=%d", len(image_paths)
            )
            raise
        LOGGER.info(
            "embedding.image.decode.completed image_count=%d elapsed_seconds=%.3f",
            len(image_paths),
            time.perf_counter() - decode_started,
        )
        encode_started = time.perf_counter()
        LOGGER.info("embedding.image.encode.started image_count=%d", len(images))
        try:
            vectors = self.model.encode(
                images,
                batch_size=1,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception:
            LOGGER.exception(
                "embedding.image.encode.failed image_count=%d", len(images)
            )
            raise
        LOGGER.info(
            "embedding.image.encode.completed image_count=%d elapsed_seconds=%.3f",
            len(images),
            time.perf_counter() - encode_started,
        )
        return np.asarray(vectors, dtype=np.float32)
