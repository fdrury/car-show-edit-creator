"""BPM and beat-time detection via librosa."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import librosa
import numpy as np

from .config import CACHE_DIR

BeatInfo = dict[str, Any]   # {bpm: float, beat_times: list[float], duration: float}


def _cache_key(path: str | Path) -> Path:
    p = Path(path)
    stat = p.stat()
    sig = f"{p.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return CACHE_DIR / (hashlib.sha256(sig.encode()).hexdigest()[:16] + ".beats.json")


def detect(path: str | Path) -> BeatInfo:
    """Return {bpm, beat_times, duration}. Cached on disk."""
    cache = _cache_key(path)
    if cache.exists():
        return cast("BeatInfo", json.loads(cache.read_text()))

    y, sr = librosa.load(str(path), sr=22050, mono=True)
    duration = float(len(y) / sr)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    bpm = float(np.atleast_1d(tempo)[0])

    result: BeatInfo = {"bpm": bpm, "beat_times": beat_times, "duration": duration}
    cache.write_text(json.dumps(result))
    return result
