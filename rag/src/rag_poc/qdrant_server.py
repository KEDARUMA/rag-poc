"""専用WSL内のQdrant Serverを配置し、ローカル接続用に起動する。"""

import hashlib
import json
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import urlopen

from .config import QDRANT_SERVER_ROOT, QDRANT_URL, RUNTIME_ROOT, TEMP_ROOT

# 定数: Qdrant Serverの公式配布物と固定チェックサム。
SERVER_VERSION = "1.19.1"
SERVER_URL = (
    f"https://github.com/qdrant/qdrant/releases/download/v{SERVER_VERSION}/"
    "qdrant-x86_64-unknown-linux-gnu.tar.gz"
)
SERVER_SHA256 = "eef986e769d4d3e806dd2d546e1b4ecdd416211e54d34b4ed764fac7c58e1085"
# 定数: 専用サーバーの実行ファイル、設定、プロセス情報。
SERVER_BINARY = QDRANT_SERVER_ROOT / "bin" / "qdrant"
SERVER_CONFIG = QDRANT_SERVER_ROOT / "config.yaml"
SERVER_PID = QDRANT_SERVER_ROOT / "server.pid"


@contextmanager
def server_lock(name: str) -> Iterator[None]:
    """WSL内の起動・移行処理をプロセス間で直列化する。"""
    import fcntl

    QDRANT_SERVER_ROOT.mkdir(parents=True, exist_ok=True)
    with (QDRANT_SERVER_ROOT / f"{name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def install_server() -> None:
    """固定版の公式バイナリを検証して配置する。"""
    with server_lock("install"):
        if SERVER_BINARY.is_file():
            result = subprocess.run(
                [str(SERVER_BINARY), "--version"], capture_output=True, text=True, check=True
            )
            if result.stdout.strip() != f"qdrant {SERVER_VERSION}":
                raise RuntimeError(f"Qdrant Serverの版が一致しません: {result.stdout.strip()}")
            return
        with tempfile.TemporaryDirectory(prefix="qdrant-install-", dir=TEMP_ROOT) as directory:
            archive_path = Path(directory) / "qdrant.tar.gz"
            with urlopen(SERVER_URL, timeout=120) as source, archive_path.open("wb") as output:
                shutil.copyfileobj(source, output)
            with archive_path.open("rb") as archive_file:
                digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
            if digest != SERVER_SHA256:
                raise RuntimeError("Qdrant ServerのSHA256が一致しません")
            with tarfile.open(archive_path) as archive:
                candidates = [item for item in archive.getmembers() if item.isfile() and Path(item.name).name == "qdrant"]
                if len(candidates) != 1:
                    raise RuntimeError("配布物内のQdrant実行ファイルを特定できません")
                with archive.extractfile(candidates[0]) as source:
                    target = Path(directory) / "qdrant"
                    with target.open("wb") as output:
                        shutil.copyfileobj(source, output)
            target.chmod(0o755)
            SERVER_BINARY.parent.mkdir(parents=True, exist_ok=True)
            target.replace(SERVER_BINARY)


def _managed_pid() -> int | None:
    """PIDが専用サーバーの実行ファイルを指しているか確認する。"""
    if SERVER_PID.is_file():
        pid = int(SERVER_PID.read_text().strip())
        executable = Path(f"/proc/{pid}/exe")
        if executable.exists() and executable.resolve() == SERVER_BINARY.resolve():
            return pid
    return None


def _ready() -> bool:
    """専用サーバーの準備完了と版を確認する。"""
    try:
        with urlopen(f"{QDRANT_URL}/readyz", timeout=1) as response:
            if response.status != 200:
                return False
        with urlopen(QDRANT_URL, timeout=1) as response:
            info = json.load(response)
        if info.get("version") != SERVER_VERSION:
            raise RuntimeError("接続先Qdrant Serverの版が一致しません")
        return True
    except OSError:
        return False


def ensure_server() -> None:
    """配置済みサーバーを起動し、利用可能になるまで待つ。"""
    with server_lock("startup"):
        if _managed_pid() is not None:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if _ready():
                    return
                time.sleep(0.2)
            raise RuntimeError("起動済みQdrant Serverの準備を確認できません")
        if not SERVER_BINARY.is_file():
            raise RuntimeError("Qdrant Serverが未配置です。scripts/prepare-qdrant-server.pyを実行してください。")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 6333)) == 0:
                raise RuntimeError("ポート6333は専用Qdrant以外のプロセスが使用しています")
        # JSONはYAMLとしても読めるため、パスを安全にエスケープして出力する。
        config = {
            "storage": {
                "storage_path": str(QDRANT_SERVER_ROOT / "storage"),
                "snapshots_path": str(QDRANT_SERVER_ROOT / "snapshots"),
                "temp_path": str(TEMP_ROOT / "qdrant"),
            },
            "service": {"host": "127.0.0.1", "http_port": 6333, "grpc_port": None},
            "telemetry_disabled": True,
        }
        SERVER_CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
        with (RUNTIME_ROOT / "logs" / "qdrant-server.log").open("a") as log:
            process = subprocess.Popen(
                [str(SERVER_BINARY), "--config-path", str(SERVER_CONFIG)],
                cwd=QDRANT_SERVER_ROOT, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
        SERVER_PID.write_text(str(process.pid), encoding="ascii")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Qdrant Serverが終了しました。logs/qdrant-server.logを確認してください。")
            if _ready():
                return
            time.sleep(0.2)
        raise RuntimeError("Qdrant Serverの起動待ちがタイムアウトしました")
