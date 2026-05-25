"""ffprobe wrappers and file scanning."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from .config import FFPROBE

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}

ProbeJSON = dict[str, Any]
VideoInfo = dict[str, float | int]
ClipEntry = dict[str, Any]


def probe(path: str | Path) -> ProbeJSON:
    """Return ffprobe JSON for a media file."""
    out = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return cast("ProbeJSON", json.loads(out.stdout))


def video_info(path: str | Path) -> VideoInfo:
    """Return {duration, width, height, fps} for a video file."""
    data = probe(path)
    vstream = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in vstream["r_frame_rate"].split("/"))
    fps = num / den if den else 0.0
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(vstream["width"]),
        "height": int(vstream["height"]),
        "fps": fps,
    }


def audio_duration(path: str | Path) -> float:
    return float(probe(path)["format"]["duration"])


def scan_clips(folder: str | Path) -> list[ClipEntry]:
    """List video files in a folder with their info."""
    folder = Path(folder)
    clips: list[ClipEntry] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            try:
                info = video_info(p)
            except (subprocess.CalledProcessError, OSError, KeyError, ValueError) as e:
                # ffprobe failure or malformed metadata: skip but report on stderr
                sys.stderr.write(f"skip {p.name}: {e}\n")
                continue
            clips.append({"path": str(p), "name": p.name, **info})
    return clips
