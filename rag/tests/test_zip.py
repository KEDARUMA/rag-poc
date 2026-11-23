"""実モデル・Qdrant環境を変更せず、ZIP対応の処理経路を確認する。"""

import importlib
import importlib.util
import io
import json
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

# 定数: テスト対象と、作業ファイルの保存先。
RAG_ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, **attributes) -> ModuleType:
    """外部依存の代替に使うモジュールを作る。"""
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


class FakeVector(list):
    """外部ライブラリなしでベクトルの受け渡しを確認する。"""

    def tolist(self) -> list:
        """登録・検索用の通常のリストへ変換する。"""
        return list(self)


class FakeEmbedder:
    """Embeddingの代替として、入力が利用できることだけを確認する。"""

    def embed_texts(self, texts) -> list:
        """テキストごとに固定のテストベクトルを返す。"""
        return [FakeVector([1.0]) for _ in texts]

    def embed_images(self, paths) -> list:
        """検索実行時まで一時ファイルが存在することを確認する。"""
        for path in paths:
            if not Path(path).is_file():
                raise AssertionError("Embedding前に一時ファイルが削除されています")
        return [FakeVector([1.0]) for _ in paths]


@contextmanager
def _fake_image(path):
    """画像デコーダーを代替し、登録の分岐とメタデータを確認する。"""
    if not Path(path).is_file():
        raise AssertionError("画像ファイルがありません")
    yield SimpleNamespace(size=(8, 6))


@contextmanager
def _fake_video(path):
    """動画処理を代替し、フレームとシーン情報の引き継ぎを確認する。"""
    if not Path(path).is_file():
        raise AssertionError("動画ファイルがありません")
    yield [
        SimpleNamespace(path=path, time=time, scene_start=0.0, scene_end=8.0)
        for time in (1.0, 6.0)
    ]


class ZipSupportTests(unittest.TestCase):
    """ZIPの登録、検索、プレビューと既存形式の回帰を確認する。"""

    @classmethod
    def setUpClass(cls):
        """設定と外部依存を隔離して実装モジュールを読み込む。"""
        cls.resources = ExitStack()
        cls.scratch = Path(cls.resources.enter_context(
            tempfile.TemporaryDirectory(prefix="zip-tests-", dir=RAG_ROOT / "output")
        ))
        config = _module(
            "rag_poc.config", COLLECTION_NAME="test", DATA_ROOT=cls.scratch,
            INDEX_STATE_PATH=cls.scratch / "state.sqlite3", MODEL_ID="test-model",
            MODEL_REVISION="test-revision", TEMP_ROOT=cls.scratch,
            TEXT_CHUNK_SIZE=1000, TEXT_CHUNK_OVERLAP=100,
        )
        models = _module(
            "qdrant_client.models", PointStruct=SimpleNamespace,
            FieldCondition=SimpleNamespace, Filter=SimpleNamespace, MatchValue=SimpleNamespace,
        )
        storage = _module("rag_poc.storage", **{
            name: Mock() for name in (
                "clear_collection", "delete_old_source_points", "ensure_collection",
                "open_client", "source_hash_exists", "upsert_points",
            )
        })
        cls.resources.enter_context(patch.dict(sys.modules, {
            "rag_poc.config": config,
            "rag_poc.embedding": _module("rag_poc.embedding", EmbeddingProvider=FakeEmbedder),
            "rag_poc.storage": storage,
            "rag_poc.video": _module("rag_poc.video", extract_video_frames=_fake_video),
            "qdrant_client": _module("qdrant_client", models=models),
            "qdrant_client.models": models,
            "numpy": _module("numpy", ndarray=list),
            "PIL": _module("PIL", Image=SimpleNamespace(open=_fake_image)),
        }))
        cls.resources.enter_context(patch.object(sys, "path", [str(RAG_ROOT / "src"), *sys.path]))
        cls.archive = importlib.import_module("rag_poc.archive")
        cls.indexer = importlib.import_module("rag_poc.indexer")
        cls.searcher = importlib.import_module("rag_poc.searcher")
        spec = importlib.util.spec_from_file_location("zip_test_ui", RAG_ROOT / "ui_server.py")
        cls.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.ui)
        spec = importlib.util.spec_from_file_location("zip_test_cli", RAG_ROOT / "search.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    @classmethod
    def tearDownClass(cls):
        """テスト用モジュールと作業ファイルを片付ける。"""
        cls.resources.close()

    def setUp(self):
        """テストごとに独立した入力と一時領域を用意する。"""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=self.scratch)))
        self.data = self.root / "data"
        self.temporary = self.root / "tmp"
        self.data.mkdir()
        self.temporary.mkdir()
        self.zip_path = self.data / "資料.zip"
        self.state = self.root / "state.sqlite3"
        self.enterContext(patch.multiple(self.indexer, TEMP_ROOT=self.temporary, INDEX_STATE_PATH=self.state))
        self.enterContext(patch.object(self.searcher, "TEMP_ROOT", self.temporary))
        self.enterContext(patch.multiple(self.ui, DATA_ROOT=self.data, TEMP_ROOT=self.temporary))

    def write_zip(self, members):
        """指定した内部パスと内容でZIPを作成する。"""
        with ZipFile(self.zip_path, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in members:
                archive.writestr(name, content)

    def points(self):
        """実際の登録処理を使ってZIPのポイントを作る。"""
        return self.indexer._create_points(
            self.zip_path, "zip", self.indexer._source_id(self.zip_path),
            self.indexer._file_hash(self.zip_path), "now", FakeEmbedder(),
        )

    def handler(self, range_value=None):
        """ネットワーク接続なしでHTTPハンドラーの出力を取得する。"""
        handler = object.__new__(self.ui.RAGRequestHandler)
        handler.headers = {"Range": range_value} if range_value else {}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.send_error = Mock()
        return handler

    def test_extract_supported_members_and_cleanup(self):
        """日本語の内部パスと対応形式を保ち、未対応形式を除外する。"""
        self.write_zip([
            ("./", ""), ("文書/", ""),
            ("文書/説明.txt", "説明文"), ("写真.PNG", b"image"),
            ("映像.mp4", b"video"), ("nested.zip", b"ignored"), ("note.docx", b"ignored"),
        ])
        with self.archive.extract_archive(self.zip_path, self.temporary) as members:
            self.assertEqual([item.name for item in members], ["文書/説明.txt", "写真.PNG", "映像.mp4"])
            paths = [item.path for item in members]
            self.assertEqual(paths[0].read_text(encoding="utf-8"), "説明文")
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_unsafe_paths_and_symlinks_rejected(self):
        """展開先外への参照とシンボリックリンクを拒否する。"""
        for name in ("../outside.txt", "/absolute.txt", "C:/drive.txt", "..\\outside.txt"):
            with self.subTest(name=name):
                self.write_zip([(name, "bad")])
                with self.assertRaises(ValueError):
                    with self.archive.extract_archive(self.zip_path, self.temporary):
                        self.fail("不正なZIPを受け付けました")
        info = ZipInfo("link.txt")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.write_zip([(info, "target")])
        with self.assertRaisesRegex(ValueError, "シンボリックリンク"):
            self.points()
        self.assertFalse((self.root / "outside.txt").exists())

    def test_limits_and_duplicate_paths_rejected(self):
        """サイズ・件数制限と正規化後の内部パス重複を確認する。"""
        self.write_zip([("a.txt", "1234"), ("b.txt", "5678")])
        for setting, value in (("MAX_MEMBER_BYTES", 3), ("MAX_ARCHIVE_BYTES", 7), ("MAX_ARCHIVE_ENTRIES", 1)):
            with self.subTest(setting=setting), patch.object(self.archive, setting, value):
                with self.assertRaises(ValueError):
                    with self.archive.extract_archive(self.zip_path, self.temporary):
                        self.fail("上限を超えたZIPを受け付けました")
        self.write_zip([("dir/a.txt", "first"), ("dir\\a.txt", "second")])
        with self.assertRaisesRegex(ValueError, "重複"):
            self.points()
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_encrypted_archive_rejected(self):
        """暗号化フラグ付きのZIPを展開前に拒否する。"""
        self.write_zip([("a.txt", "text")])
        data = bytearray(self.zip_path.read_bytes())
        local = data.index(b"PK\x03\x04")
        central = data.index(b"PK\x01\x02")
        data[local + 6] |= 1
        data[central + 8] |= 1
        self.zip_path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "暗号化"):
            self.points()

    def test_empty_unsupported_and_corrupt_archives_rejected(self):
        """検索・登録できないZIPを明示的なエラーにする。"""
        for members in ([], [("a.docx", "unsupported")], [("empty.txt", " ")]):
            with self.subTest(members=members):
                self.write_zip(members)
                with self.assertRaises(ValueError):
                    self.points()
        self.zip_path.write_bytes(b"not a zip")
        with self.assertRaisesRegex(ValueError, "ZIPを展開できません"):
            self.points()
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_points_keep_parent_metadata_and_distinct_ids(self):
        """内部ファイルのID衝突を防ぎ、親ZIPと種別情報を保持する。"""
        self.write_zip([("a.txt", "first"), ("b.txt", "second"), ("image.png", b"image"), ("video.mp4", b"video")])
        points = self.points()
        self.assertEqual(len(points), 5)
        self.assertEqual(len({point.id for point in points}), 5)
        self.assertEqual([point.id for point in points], [point.id for point in self.points()])
        self.assertEqual({point.payload["file"] for point in points}, {str(self.zip_path.resolve())})
        self.assertEqual({point.payload["source_id"] for point in points}, {self.indexer._source_id(self.zip_path)})
        self.assertEqual({point.payload["type"] for point in points}, {"text", "image", "video_frame"})
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_incremental_update_replaces_all_old_members(self):
        """SQLiteの親一行でスキップを判定し、更新時に旧内部ファイルを除去する。"""
        self.write_zip([("old.txt", "old"), ("removed.txt", "removed")])
        stored = {}

        def upsert(client, points):
            """登録されたポイントをテスト用のメモリーへ保存する。"""
            stored.update((point.id, point) for point in points)

        def remove_old(client, source_id, file_hash):
            """同じZIPに属する旧ハッシュのポイントを除去する。"""
            for point_id, point in list(stored.items()):
                if point.payload["source_id"] == source_id and point.payload["file_hash"] != file_hash:
                    del stored[point_id]

        def source_exists(client, source_id, file_hash):
            """登録済みの親IDとハッシュが一致するか確認する。"""
            return any(point.payload["source_id"] == source_id and point.payload["file_hash"] == file_hash for point in stored.values())

        with patch.multiple(
            self.indexer, open_client=Mock(return_value=Mock()),
            upsert_points=upsert, delete_old_source_points=remove_old,
            source_hash_exists=source_exists,
        ):
            first = self.indexer.index_directory(self.data)
            with closing(sqlite3.connect(self.state)) as connection:
                connection.execute("UPDATE indexed_files SET index_version = ''")
                connection.commit()
            legacy = self.indexer.index_directory(self.data)
            self.assertEqual(legacy["indexed"], 1)
            second = self.indexer.index_directory(self.data)
            self.assertEqual((first["indexed"], second["skipped"]), (1, 1))
            self.write_zip([("new.txt", "updated")])
            updated = self.indexer.index_directory(self.data)
            self.assertEqual(updated["indexed"], 1)
            self.assertEqual({point.payload["archive_member"] for point in stored.values()}, {"new.txt"})
            self.zip_path.write_bytes(b"invalid")
            failed = self.indexer.index_directory(self.data)
            self.assertEqual(len(failed["errors"]), 1)
            self.assertEqual({point.payload["archive_member"] for point in stored.values()}, {"new.txt"})
        with closing(sqlite3.connect(self.state)) as connection:
            rows = connection.execute("SELECT file_path FROM indexed_files").fetchall()
        self.assertEqual(rows, [(str(self.zip_path.resolve()),)])

    def test_search_groups_archive_and_keeps_regular_results(self):
        """ZIP内の複数ヒットをまとめ、通常ファイルの検索を維持する。"""
        self.write_zip([("a.txt", "first"), ("b.txt", "second")])
        points = self.points()
        scored = [SimpleNamespace(id=point.id, payload=point.payload, score=score) for point, score in zip(points, (0.6, 0.9))]
        scored.append(SimpleNamespace(id="regular", payload={"file": "normal.txt", "type": "text", "text": "normal"}, score=0.8))
        client = Mock()
        client.query_points.return_value = SimpleNamespace(points=scored)
        with patch.object(self.searcher, "open_client", return_value=client):
            for result in (self.searcher.search("text", "query"), self.searcher.search_many([("text", "query")])):
                self.assertEqual(len(result["results"]), 2)
                self.assertEqual(result["results"][0]["archive_member"], "b.txt")
                self.assertEqual(result["results"][1]["path"], "normal.txt")

    def test_zip_queries_preserve_media_filters_and_cleanup(self):
        """ZIPの各入力種別に従う検索フィルターと一時ファイル寿命を確認する。"""
        self.write_zip([("a.txt", "x" * 1100), ("image.png", b"image"), ("video.mp4", b"video")])
        client = Mock()
        client.query_points.return_value = SimpleNamespace(points=[])
        with patch.object(self.searcher, "open_client", return_value=client):
            result = self.searcher.search("zip", str(self.zip_path))
        self.assertEqual(result["query_type"], "zip")
        filters = [
            {condition.match.value for condition in call.kwargs["query_filter"].should}
            for call in client.query_points.call_args_list
        ]
        self.assertEqual(filters, [
            {"text", "image", "video_frame", "pdf_page"}, {"text", "image", "video_frame", "pdf_page"},
            {"image", "video_frame", "pdf_page"}, {"video_frame"}, {"video_frame"},
        ])
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_zip_query_type_override_and_failure_cleanup(self):
        """CLIの対象指定を維持し、検索エラー時も一時領域を解放する。"""
        self.write_zip([("image.png", b"image")])
        client = Mock()
        client.query_points.return_value = SimpleNamespace(points=[])
        with patch.object(self.searcher, "open_client", return_value=client):
            self.searcher.search("zip", str(self.zip_path), type_values=["image"])
            selected = client.query_points.call_args.kwargs["query_filter"].should
            self.assertEqual({condition.match.value for condition in selected}, {"image"})
            client.query_points.side_effect = RuntimeError("expected query failure")
            with self.assertRaisesRegex(RuntimeError, "expected query failure"):
                self.searcher.search("zip", str(self.zip_path))
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_preview_member_range_and_regular_file(self):
        """ZIP内部と通常ファイルの取得、およびRange配信を確認する。"""
        self.write_zip([("文書/note.txt", "0123456789"), ("image.png", b"png-data")])
        handler = self.handler("bytes=2-5")
        handler._serve_data_file(str(self.zip_path), member_name="文書/note.txt")
        handler.send_response.assert_called_with(206)
        self.assertEqual(handler.wfile.getvalue(), b"2345")
        self.assertEqual(list(self.temporary.iterdir()), [])
        handler = self.handler()
        handler._serve_data_file(str(self.zip_path), member_name="image.png")
        self.assertEqual(handler.wfile.getvalue(), b"png-data")
        regular = self.data / "normal.txt"
        regular.write_bytes(b"normal")
        handler = self.handler()
        handler._serve_data_file(str(regular))
        handler.send_response.assert_called_with(200)
        self.assertEqual(handler.wfile.getvalue(), b"normal")

    def test_preview_rejects_invalid_member_and_outside_archive(self):
        """不正な内部パスとdata外のZIPへのアクセスを拒否する。"""
        self.write_zip([("a.txt", "text")])
        for name in ("../outside.txt", "missing.txt"):
            handler = self.handler()
            handler._serve_data_file(str(self.zip_path), member_name=name)
            handler.send_response.assert_called_with(400)
            self.assertIn("error", json.loads(handler.wfile.getvalue()))
        outside = self.root / "outside.zip"
        outside.write_bytes(self.zip_path.read_bytes())
        handler = self.handler()
        handler._serve_data_file(str(outside), member_name="a.txt")
        handler.send_error.assert_called_with(404)
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_video_preview_cache_survives_reextraction(self):
        """再展開で一時パスが変わっても、動画変換キャッシュを再利用する。"""
        self.write_zip([("clip.mp4", b"video")])

        def convert(command, **kwargs):
            """FFmpeg出力を代替して変換処理の呼び出し回数を確認する。"""
            Path(command[-1]).write_bytes(b"webm")
            return SimpleNamespace(returncode=0)

        with patch.object(self.ui.subprocess, "run", side_effect=convert) as run:
            for _ in range(2):
                handler = self.handler()
                handler._serve_data_file(str(self.zip_path), "webm", "clip.mp4")
                handler.send_response.assert_called_with(200)
                self.assertEqual(handler.wfile.getvalue(), b"webm")
            self.assertEqual(run.call_count, 1)

    @contextmanager
    def fake_pdf_pages(self, path, temporary_root):
        """PDF描画を代替し、ページ情報を登録・検索へ渡す。"""
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            pages = []
            for number in (1, 2):
                image = Path(directory) / f"{number}.jpg"
                image.write_bytes(b"page image")
                pages.append(SimpleNamespace(path=image, number=number, page_count=2))
            yield iter(pages)

    def test_pdf_pages_and_archive_metadata(self):
        """PDFのページ番号と、ZIP内PDFの親情報を保持する。"""
        pdf_path = self.data / "report.pdf"
        pdf_path.write_bytes(b"pdf fixture")
        with patch.object(self.indexer, "render_pdf_pages", self.fake_pdf_pages):
            points = self.indexer._create_points(pdf_path, "pdf", "pdf-source", "hash", "now", FakeEmbedder())
            self.assertEqual([point.payload["page"] for point in points], [1, 2])
            self.assertEqual({point.payload["page_count"] for point in points}, {2})
            self.assertEqual(len({point.id for point in points}), 2)
            self.write_zip([("docs/report.pdf", pdf_path.read_bytes())])
            archived = self.points()
            self.assertEqual({point.payload["archive_member"] for point in archived}, {"docs/report.pdf"})
            self.assertEqual({point.payload["file"] for point in archived}, {str(self.zip_path.resolve())})
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_pdf_query_and_best_page(self):
        """PDFを画像クエリとして使い、結果をPDF単位で最良ページへまとめる。"""
        pdf_path = self.data / "report.pdf"
        pdf_path.write_bytes(b"pdf fixture")
        client = Mock()
        client.query_points.return_value = SimpleNamespace(points=[
            SimpleNamespace(id="first", score=0.5, payload={"file": str(pdf_path), "type": "pdf_page", "page": 1, "page_count": 2}),
            SimpleNamespace(id="second", score=0.9, payload={"file": str(pdf_path), "type": "pdf_page", "page": 2, "page_count": 2}),
        ])
        with patch.object(self.searcher, "render_pdf_pages", self.fake_pdf_pages), patch.object(
            self.searcher, "open_client", return_value=client
        ):
            result = self.searcher.search("pdf", str(pdf_path))
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["page"], 2)
            self.assertEqual(result["results"][0]["type"], "pdf")
            conditions = client.query_points.call_args.kwargs["query_filter"].should
            self.assertEqual({condition.match.value for condition in conditions}, {"image", "video_frame", "pdf_page"})
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_pdf_preview_and_upload(self):
        """PDFの配信形式と、アップロードを画像PDF検索へ渡す経路を確認する。"""
        pdf_path = self.data / "report.pdf"
        pdf_path.write_bytes(b"%PDF-test")
        handler = self.handler()
        handler._serve_data_file(str(pdf_path))
        self.assertEqual(handler.wfile.getvalue(), pdf_path.read_bytes())
        handler.send_header.assert_any_call("Content-Type", "application/pdf")
        handler = self.handler()
        handler.path = "/api/search"
        handler._read_multipart = Mock(return_value=("", [("report.pdf", pdf_path.read_bytes())]))
        with patch.object(self.ui, "search_many", return_value={"results": []}) as search_many:
            handler.do_POST()
            self.assertEqual(search_many.call_args.args[0][0][0], "pdf")
        output = io.StringIO()
        with patch.object(sys, "argv", ["search.py", "--pdf", str(pdf_path)]), patch.object(
            self.cli, "search", return_value={"results": []}
        ) as search, patch.object(sys, "stdout", output):
            self.assertEqual(self.cli.main(), 0)
            self.assertEqual(search.call_args.kwargs["query_kind"], "pdf")

    def test_ui_upload_and_cli_accept_zip(self):
        """画面のアップロードとCLIのZIP引数を検索処理へ渡す。"""
        self.write_zip([("a.txt", "query")])
        handler = self.handler()
        handler.path = "/api/search"
        handler._read_multipart = Mock(return_value=("", [("query.zip", self.zip_path.read_bytes())]))
        with patch.object(self.ui, "search_many", return_value={"results": []}) as search_many:
            handler.do_POST()
            self.assertEqual(search_many.call_args.args[0][0][0], "zip")
            handler.send_response.assert_called_with(200)
        output = io.StringIO()
        with patch.object(sys, "argv", ["search.py", "--zip", str(self.zip_path)]), patch.object(
            self.cli, "search", return_value={"results": []}
        ) as search, patch.object(sys, "stdout", output):
            self.assertEqual(self.cli.main(), 0)
            self.assertEqual(search.call_args.kwargs["query_kind"], "zip")
        self.assertEqual(list(self.temporary.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
