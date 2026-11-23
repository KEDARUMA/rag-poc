"""ZIP内の対応ファイルを検査し、一時ディレクトリへ取り出す。"""

import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator
from zipfile import BadZipFile, ZipFile

# 定数: ZIP内で登録・検索できるファイル形式。
MEMBER_TYPES = {
    ".pdf": "pdf",
    ".txt": "text",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".avi": "video",
    ".webm": "video",
}
# 定数: ZIPのエントリ数、単一ファイル、全体の展開サイズ上限。
MAX_ARCHIVE_ENTRIES = 1000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveMember:
    """ZIP内のパスと、一時ファイルの種別・保存先を保持する。"""

    name: str
    kind: str
    path: Path


def _member_name(name: str) -> str:
    """ZIP内の相対パスを検査し、区切り文字を正規化する。"""
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or ".." in path.parts
        or any(":" in part for part in path.parts)
        or not path.name
    ):
        raise ValueError(f"ZIP内のパスが不正です: {name!r}")
    return path.as_posix()


@contextmanager
def extract_archive(
    path: Path, temporary_root: Path, member_name: str | None = None
) -> Iterator[list[ArchiveMember]]:
    """検査済みの対応ファイルを取り出し、利用後に一時ファイルを削除する。"""
    if member_name is not None:
        member_name = _member_name(member_name)
    with tempfile.TemporaryDirectory(prefix="rag-zip-", dir=temporary_root) as directory:
        try:
            members = _extract_members(path, Path(directory), member_name)
        except (BadZipFile, NotImplementedError, RuntimeError) as error:
            raise ValueError(f"ZIPを展開できません: {error}") from error
        yield members


def _extract_members(
    path: Path, directory: Path, member_name: str | None
) -> list[ArchiveMember]:
    """ZIP全体を検査してから、対象のファイルだけを一時保存する。"""
    with ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("ZIP内のエントリ数が上限1000件を超えています")
        total_size = 0
        selected = []
        names = set()
        for info in infos:
            if info.is_dir() and PurePosixPath(info.orig_filename.replace("\\", "/")) == PurePosixPath("."):
                continue
            name = _member_name(info.orig_filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"ZIP内のシンボリックリンクは扱えません: {name}")
            if info.is_dir():
                continue
            total_size += info.file_size
            if info.file_size > MAX_MEMBER_BYTES or total_size > MAX_ARCHIVE_BYTES:
                raise ValueError("ZIPの展開サイズが上限（単体512MiB・合計1GiB）を超えています")
            if info.flag_bits & 1:
                raise ValueError(f"暗号化されたZIPは扱えません: {name}")
            kind = MEMBER_TYPES.get(PurePosixPath(name).suffix.casefold())
            if kind is None:
                continue
            if name in names:
                raise ValueError(f"ZIP内のファイルパスが重複しています: {name}")
            names.add(name)
            if member_name is None or name == member_name:
                selected.append((info, name, kind))
        if not selected:
            raise ValueError("ZIP内に対象のTXT・画像・動画・PDFがありません")

        members = []
        for index, (info, name, kind) in enumerate(selected):
            # 定数: 内部パスを直接使わない、一時ファイルの保存先。
            target = directory / f"{index:06d}{PurePosixPath(name).suffix}"
            with archive.open(info) as source, target.open("wb") as output:
                # ZIPの申告サイズを超えた展開も制限する。
                remaining = info.file_size
                while block := source.read(min(64 * 1024, remaining + 1)):
                    remaining -= len(block)
                    if remaining < 0:
                        raise ValueError(f"ZIP内の展開サイズが不正です: {name}")
                    output.write(block)
            members.append(ArchiveMember(name=name, kind=kind, path=target))
        return members
