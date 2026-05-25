"""Locate ffmpeg/ffprobe and define project paths."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECTS_DIR = PROJECT_ROOT / "projects"
OUTPUT_DIR = PROJECT_ROOT / "output"
CACHE_DIR = PROJECT_ROOT / ".cache"

PROJECTS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)


def _find_binary(name: str) -> str:
    """Find ffmpeg/ffprobe — PATH first, then known winget install location."""
    hit = shutil.which(name)
    if hit:
        return hit
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.exists():
        for pkg in winget.glob("Gyan.FFmpeg*"):
            for exe in pkg.rglob(f"{name}.exe"):
                return str(exe)
    raise RuntimeError(f"{name} not found in PATH or known winget install")


FFMPEG = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")
