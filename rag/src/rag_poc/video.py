"""PySceneDetect と FFmpeg による動画フレーム抽出。"""

import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from scenedetect import SceneManager, open_video
from scenedetect.detectors import ContentDetector

from .config import TEMP_ROOT, VIDEO_SAMPLE_INTERVAL, VIDEO_SCENE_THRESHOLD


@dataclass(frozen=True)
class VideoFrame:
    """抽出画像と、元動画上の時刻情報を保持する。"""

    path: Path
    time: float
    scene_start: float
    scene_end: float


def _scene_samples(start: float, end: float) -> list[float]:
    """代表フレームと一定間隔のサンプル時刻を返す。"""
    if end <= start:
        return []
    center = (start + end) / 2.0
    samples = {round(center, 3)}
    sample_time = start + VIDEO_SAMPLE_INTERVAL / 2.0
    while sample_time < end:
        samples.add(round(sample_time, 3))
        sample_time += VIDEO_SAMPLE_INTERVAL
    return sorted(samples)


@contextmanager
def extract_video_frames(video_path: Path) -> Iterator[list[VideoFrame]]:
    """場面ごとの代表フレームと5秒間隔フレームを一時抽出する。"""
    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(
        ContentDetector(threshold=VIDEO_SCENE_THRESHOLD)
    )
    scene_manager.detect_scenes(video=video, show_progress=False)
    detected_scenes = scene_manager.get_scene_list(start_in_scene=True)
    duration = video.duration.get_seconds()

    scenes = [
        (start.get_seconds(), end.get_seconds())
        for start, end in detected_scenes
    ]
    if not scenes and duration > 0:
        scenes = [(0.0, duration)]

    frames: list[VideoFrame] = []
    with tempfile.TemporaryDirectory(
        prefix="video_frames_", dir=TEMP_ROOT
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        frame_number = 0
        for scene_start, scene_end in scenes:
            for sample_time in _scene_samples(scene_start, scene_end):
                frame_path = temporary_root / f"frame_{frame_number:06d}.jpg"
                safe_time = max(0.0, min(sample_time, max(0.0, duration - 0.05)))
                command = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{safe_time:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(frame_path),
                ]
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0 or not frame_path.is_file():
                    message = completed.stderr.strip() or "フレームを抽出できません"
                    raise RuntimeError(
                        f"動画フレーム抽出失敗 ({video_path}): {message}"
                    )
                frames.append(
                    VideoFrame(
                        path=frame_path,
                        time=safe_time,
                        scene_start=scene_start,
                        scene_end=scene_end,
                    )
                )
                frame_number += 1
        yield frames
