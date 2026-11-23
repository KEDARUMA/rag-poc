"""WSL・CUDA・実Qdrantで全形式のfloat16保存と検索を確認する。"""

import argparse
import hashlib
import importlib.util
import json
import logging
import sqlite3
import sys
import tempfile
import threading
from contextlib import ExitStack, closing
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

# 定数: 検証対象のプロジェクトと共通実装。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from rag_poc.config import COLLECTION_NAME, DATA_ROOT, INDEX_STATE_PATH, OUTPUT_ROOT, TEMP_ROOT

import numpy as np
import pypdfium2 as pdfium
import torch
from PIL import Image
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

from rag_poc import indexer, searcher
from rag_poc.embedding import EmbeddingProvider
from rag_poc.pdf import PDF_RENDER_LOCK, render_pdf_pages
from rag_poc.storage import open_client
from ui_server import RAGRequestHandler


def _write_image_pdf(target: Path, image_paths: list[Path]) -> None:
    """文字を含まない検証用PDFを画像だけで作成する。"""
    with ExitStack() as stack:
        pages = []
        for path in image_paths:
            source = stack.enter_context(Image.open(path))
            pages.append(stack.enter_context(source.convert("RGB")))
        pages[0].save(target, format="PDF", save_all=True, append_images=pages[1:], resolution=72)


def verify_float16(preview_output: Path | None = None) -> dict:
    """テスト専用ファイルだけを登録し、確認後にDB記録とファイルを削除する。"""
    spec = importlib.util.spec_from_file_location("float16_smoke_helpers", PROJECT_ROOT / "scripts" / "smoke-test.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    with closing(open_client()) as client:
        before_count = client.count(COLLECTION_NAME, exact=True).count
        datatype = client.get_collection(COLLECTION_NAME).config.params.vectors.datatype
    if datatype.value != "float16" or not torch.cuda.is_available():
        raise RuntimeError("float16保存またはCUDAを確認できません")
    source_ids = []
    server = None
    thread = None
    preview_cache = None
    results = {"datatype": datatype.value, "gpu": torch.cuda.get_device_name(0)}
    try:
        with tempfile.TemporaryDirectory(prefix="_float16_smoke_", dir=DATA_ROOT) as directory, tempfile.TemporaryDirectory(prefix="pdf-query-", dir=TEMP_ROOT) as query_directory:
            root = Path(directory).resolve()
            text = root / "fixture.txt"
            image = root / "fixture.png"
            video = root / "fixture.mp4"
            zip_path = root / "fixture.zip"
            pdf_path = root / "fixture.pdf"
            query_pdf = Path(query_directory) / "query.pdf"
            car = DATA_ROOT / "rag-test-data" / "car.jpg"
            dog = DATA_ROOT / "rag-test-data" / "dog.png"
            _write_image_pdf(pdf_path, [car, dog])
            _write_image_pdf(query_pdf, [dog])
            with PDF_RENDER_LOCK, closing(pdfium.PdfDocument(str(pdf_path))) as document:
                for index in range(len(document)):
                    with closing(document[index]) as page, closing(page.get_textpage()) as text_page:
                        if text_page.get_text_range().strip():
                            raise RuntimeError("検証用PDFに文字が含まれています")
            results["image_only_pdf"] = "passed"
            if preview_output is not None:
                preview_output = preview_output.resolve()
                preview_output.relative_to(OUTPUT_ROOT.resolve())
                preview_output.parent.mkdir(parents=True, exist_ok=True)
                with render_pdf_pages(pdf_path, TEMP_ROOT) as pages:
                    for page in pages:
                        if page.number == 2:
                            with Image.open(page.path) as rendered:
                                rendered.save(preview_output)
            text.write_text("A red square on a blue background. FLOAT16 QUARTZ TEST.", encoding="utf-8")
            Image.new("RGB", (320, 240), color=(220, 35, 35)).save(image)
            helpers._create_test_video(image, video)
            with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
                archive.write(text, "docs/fixture.txt")
                archive.write(image, "media/fixture.png")
                archive.write(video, "media/fixture.mp4")
                archive.write(pdf_path, "docs/fixture.pdf")
            source_ids = [indexer._source_id(path) for path in (text, image, video, pdf_path, zip_path)]
            print("CUDA上で実モデルを読み込みます。", flush=True)
            embedder = EmbeddingProvider()
            with ExitStack() as stack:
                # 同じ実モデルを再利用し、繰り返しロードによるGPUメモリ消費を避ける。
                stack.enter_context(patch.object(indexer, "EmbeddingProvider", return_value=embedder))
                stack.enter_context(patch.object(searcher, "EmbeddingProvider", return_value=embedder))
                indexed = indexer.index_directory(root)
                if indexed["indexed"] != 5 or indexed["errors"]:
                    raise RuntimeError(f"全形式の登録に失敗しました: {indexed}")
                results["indexed_files"] = indexed["indexed"]
                results["indexed_points"] = indexed["points"]
                # 旧版のZIP登録を再現し、PDFポイントの不足を通常更新で補えるか確認する。
                with closing(open_client()) as client:
                    client.delete(
                        COLLECTION_NAME,
                        points_selector=FilterSelector(filter=Filter(must=[
                            FieldCondition(key="source_id", match=MatchValue(value=source_ids[-1])),
                            FieldCondition(key="type", match=MatchValue(value="pdf_page")),
                        ])),
                        wait=True,
                    )
                with closing(sqlite3.connect(INDEX_STATE_PATH)) as connection:
                    connection.execute("UPDATE indexed_files SET index_version = '' WHERE source_id = ?", (source_ids[-1],))
                    connection.commit()
                refreshed = indexer.index_directory(root, type_values=["zip"])
                if refreshed["indexed"] != 1 or refreshed["errors"]:
                    raise RuntimeError("旧版のZIP登録を通常更新で再処理できません")
                results["legacy_zip_refresh"] = "passed"
                print("全形式を登録しました。float16の実値を確認します。", flush=True)
                with closing(open_client()) as client:
                    for source_id in source_ids:
                        records, offset = client.scroll(
                            COLLECTION_NAME, limit=100,
                            scroll_filter=Filter(must=[FieldCondition(key="source_id", match=MatchValue(value=source_id))]),
                            with_vectors=True, with_payload=True,
                        )
                        if not records or offset is not None:
                            raise RuntimeError("検証用ポイントを取得できません")
                        for record in records:
                            vector = np.asarray(record.vector, dtype=np.float32)
                            if vector.size != 1024 or not np.array_equal(vector, vector.astype(np.float16).astype(np.float32)):
                                raise RuntimeError("保存ベクトルが1024次元のfloat16ではありません")
                for kind, value, target, member in (
                    ("text", text.read_text(encoding="utf-8"), text, "docs/fixture.txt"),
                    ("image", str(image), image, "media/fixture.png"),
                    ("video", str(video), video, "media/fixture.mp4"),
                ):
                    found = searcher.search(kind, value, limit=100, type_values=[kind])["results"]
                    if not any(item["path"] == str(target) for item in found):
                        raise RuntimeError(f"通常ファイルの{kind}検索に失敗しました")
                    if not any(item["path"] == str(zip_path) and item.get("archive_member") == member for item in found):
                        raise RuntimeError(f"ZIP内部の{kind}検索に失敗しました")
                    results[f"{kind}_search"] = "passed"
                    print(f"{kind}検索: 成功", flush=True)
                found = searcher.search("zip", str(zip_path), limit=100)["results"]
                if not any(item["path"] == str(zip_path) for item in found):
                    raise RuntimeError("ZIP検索入力の処理に失敗しました")
                results["zip_query"] = "passed"
                for kind, value in (("text", "a photograph of a dog"), ("image", str(dog)), ("pdf", str(query_pdf))):
                    found = searcher.search(kind, value, limit=100, type_values=["pdf"])["results"]
                    matches = {item["path"]: item for item in found}
                    for target in (pdf_path, zip_path):
                        item = matches.get(str(target))
                        if item is None or item.get("page") != 2 or item.get("page_count") != 2:
                            raise RuntimeError(f"画像PDFの{kind}検索・ページ位置が一致しません: {found}")
                    results[f"pdf_{kind}_search"] = "passed"
                print("画像PDFのテキスト・画像・PDF検索と2ページ目の一致: 成功", flush=True)
                server = HTTPServer(("127.0.0.1", 0), RAGRequestHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://127.0.0.1:{server.server_port}"
                for member, expected in (("docs/fixture.txt", text.read_bytes()), ("media/fixture.png", image.read_bytes()), ("docs/fixture.pdf", pdf_path.read_bytes())):
                    query = urlencode({"path": str(zip_path), "member": member})
                    with urlopen(f"{base_url}/api/file?{query}", timeout=120) as response:
                        if response.read() != expected:
                            raise RuntimeError("ZIP内部ファイルのプレビュー内容が一致しません")
                        if member.endswith(".pdf") and response.headers.get_content_type() != "application/pdf":
                            raise RuntimeError("PDFの配信形式がapplication/pdfではありません")
                with urlopen(f"{base_url}/api/file?{urlencode({'path': str(pdf_path)})}", timeout=120) as response:
                    if response.headers.get_content_type() != "application/pdf" or response.read() != pdf_path.read_bytes():
                        raise RuntimeError("通常PDFのプレビュー内容が一致しません")
                stat = zip_path.stat()
                identity = f"{zip_path}:{stat.st_size}:{stat.st_mtime_ns}:media/fixture.mp4"
                preview_cache = TEMP_ROOT / "webm_preview_cache" / f"{hashlib.sha256(identity.encode()).hexdigest()}.webm"
                query = urlencode({"path": str(zip_path), "member": "media/fixture.mp4", "format": "webm"})
                with urlopen(Request(f"{base_url}/api/file?{query}", headers={"Range": "bytes=0-31"}), timeout=120) as response:
                    if response.status != 206 or len(response.read()) != 32:
                        raise RuntimeError("ZIP動画の変換・Range配信に失敗しました")
                # 定数: 実際のアップロードAPIへ送るmultipart境界。
                boundary = "rag-float16-verify-boundary"
                body = (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="fixture.zip"\r\n'
                    "Content-Type: application/zip\r\n\r\n"
                ).encode() + zip_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
                request = Request(
                    f"{base_url}/api/search", data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                )
                with urlopen(request, timeout=180) as response:
                    found = json.load(response)["results"]
                if not any(item["path"] == str(zip_path) for item in found):
                    raise RuntimeError("画面のZIPアップロード検索に失敗しました")
                pdf_body = (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="query.pdf"\r\n'
                    "Content-Type: application/pdf\r\n\r\n"
                ).encode() + query_pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
                with urlopen(Request(
                    f"{base_url}/api/search", data=pdf_body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                ), timeout=180) as response:
                    pdf_results = json.load(response)["results"]
                if not any(item["path"] == str(pdf_path) and item.get("page") == 2 for item in pdf_results):
                    raise RuntimeError("画像PDFアップロード検索に失敗しました")
                results["pdf_http_upload_and_preview"] = "passed"
                results["http_upload_and_previews"] = "passed"
                print("HTTP検索・TXT/画像/動画プレビュー: 成功", flush=True)
                with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
                    archive.writestr("updated.txt", "Updated float16 archive document.")
                updated = indexer.index_directory(root, type_values=["zip"])
                skipped = indexer.index_directory(root, type_values=["zip"])
                if updated["indexed"] != 1 or updated["errors"] or skipped["skipped"] != 1:
                    raise RuntimeError("ZIP更新・未変更判定に失敗しました")
                with closing(open_client()) as client:
                    records, _ = client.scroll(
                        COLLECTION_NAME, limit=100, with_payload=True,
                        scroll_filter=Filter(must=[FieldCondition(key="source_id", match=MatchValue(value=source_ids[-1]))]),
                    )
                if not records or any(record.payload.get("archive_member") != "updated.txt" for record in records):
                    raise RuntimeError("更新前のZIP内部ポイントが残っています")
                results["zip_update_and_skip"] = "passed"
                original_pdf = pdf_path.read_bytes()
                pdf_path.write_bytes(b"invalid pdf fixture")
                failed = indexer.index_directory(root, type_values=["pdf"])
                if len(failed["errors"]) != 1:
                    raise RuntimeError("破損PDFの更新を拒否できません")
                with closing(open_client()) as client:
                    retained, _ = client.scroll(
                        COLLECTION_NAME, limit=100, with_payload=True,
                        scroll_filter=Filter(must=[FieldCondition(key="source_id", match=MatchValue(value=source_ids[-2]))]),
                    )
                if len(retained) != 2:
                    raise RuntimeError("破損PDFの更新で既存ポイントが失われました")
                pdf_path.write_bytes(original_pdf)
                _write_image_pdf(pdf_path, [car])
                updated_pdf = indexer.index_directory(root, type_values=["pdf"])
                skipped_pdf = indexer.index_directory(root, type_values=["pdf"])
                with closing(open_client()) as client:
                    replaced, _ = client.scroll(
                        COLLECTION_NAME, limit=100, with_payload=True,
                        scroll_filter=Filter(must=[FieldCondition(key="source_id", match=MatchValue(value=source_ids[-2]))]),
                    )
                if updated_pdf["indexed"] != 1 or skipped_pdf["skipped"] != 1 or len(replaced) != 1 or replaced[0].payload.get("page_count") != 1:
                    raise RuntimeError("PDFの更新・旧ページ削除・未変更判定に失敗しました")
                results["pdf_update_skip_and_failure_retention"] = "passed"
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        if source_ids:
            helpers._remove_test_records(source_ids)
        if preview_cache is not None:
            preview_cache.unlink(missing_ok=True)
    with closing(open_client()) as client:
        if client.count(COLLECTION_NAME, exact=True).count != before_count:
            raise RuntimeError("テスト用ポイントの後片付けを確認できません")
    with closing(sqlite3.connect(INDEX_STATE_PATH)) as connection:
        remaining = connection.execute(
            f"SELECT count(*) FROM indexed_files WHERE source_id IN ({','.join('?' for _ in source_ids)})", source_ids
        ).fetchone()[0]
    if remaining:
        raise RuntimeError("テスト用SQLite行が残っています")
    results["cleanup"] = "passed"
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全形式のfloat16保存を実環境で確認します")
    parser.add_argument("--preview-output", type=Path, help="目視確認用のPDFページPNGをoutput配下へ保存")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(verify_float16(arguments.preview_output), ensure_ascii=False), flush=True)
