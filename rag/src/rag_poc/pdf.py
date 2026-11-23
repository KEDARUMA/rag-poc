"""PDFをページごとの画像に変換し、画像主体の資料を検索対象にする。"""

import tempfile
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# 定数: ページ画像の最大辺と、PDFiumを同時に呼び出さないためのロック。
PDF_MAX_IMAGE_SIDE = 1600
PDF_RENDER_LOCK = threading.RLock()


@dataclass(frozen=True)
class PdfPage:
    """検索用のページ画像と、元PDF上のページ位置を保持する。"""

    path: Path
    number: int
    page_count: int


def _render_pages(document, directory: Path) -> Iterator[PdfPage]:
    """ページを一枚ずつ画像化し、利用済みの画像を削除する。"""
    page_count = len(document)
    if page_count == 0:
        raise ValueError("PDFにページがありません")
    for index in range(page_count):
        target = directory / f"page_{index + 1:06d}.jpg"
        with closing(document[index]) as page:
            width, height = page.get_size()
            if width <= 0 or height <= 0:
                raise ValueError(f"PDFのページサイズが不正です: {index + 1}")
            scale = min(2.0, PDF_MAX_IMAGE_SIDE / max(width, height))
            with closing(page.render(scale=scale, limit_image_cache=True)) as bitmap:
                with bitmap.to_pil() as image, image.convert("RGB") as rgb:
                    rgb.save(target, format="JPEG", quality=90)
        try:
            yield PdfPage(path=target, number=index + 1, page_count=page_count)
        finally:
            target.unlink(missing_ok=True)


@contextmanager
def render_pdf_pages(path: Path, temporary_root: Path) -> Iterator[Iterator[PdfPage]]:
    """PDFのページ画像を順に提供し、終了時にPDFと一時領域を解放する。"""
    import pypdfium2 as pdfium

    with PDF_RENDER_LOCK:
        with tempfile.TemporaryDirectory(prefix="rag-pdf-", dir=temporary_root) as directory:
            try:
                with closing(pdfium.PdfDocument(str(path))) as document:
                    with closing(_render_pages(document, Path(directory))) as pages:
                        yield pages
            except pdfium.PdfiumError as error:
                raise ValueError(f"PDFを読み込めません（破損・暗号化など）: {error}") from error
