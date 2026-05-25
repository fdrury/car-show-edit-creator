"""End-to-end smoke test using synthetic media.

Generates a click track + a handful of test-pattern clips, drives the API
to create a project, detect beats, set up segments, and render.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

from car_show_editor.config import FFMPEG

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
BASE = "http://127.0.0.1:8765"


def http(method: str, path: str, body: Any = None) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return cast("dict[str, Any]", json.loads(r.read()))
    except urllib.error.HTTPError as e:
        print("HTTP error", e.code, e.read().decode())
        raise


def make_fixtures() -> tuple[Path, Path]:
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    FIXTURES.mkdir(parents=True)
    clips_dir = FIXTURES / "clips"
    clips_dir.mkdir()

    # Click track at 120 BPM, 30 seconds — sharp click on every beat
    song = FIXTURES / "song.wav"
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        "sine=f=1000:duration=0.03,apad=whole_dur=0.5",
        "-af", "aloop=loop=-1:size=22050",
        "-t", "30",
        str(song),
    ], check=True)

    # 6 test-pattern clips, 60fps, 1920x1080, 5 sec each, distinct colors + distinct tones
    palette = ["red", "green", "blue", "yellow", "magenta", "cyan"]
    tones = [262, 330, 392, 523, 659, 784]   # C4 E4 G4 C5 E5 G5
    for i, (color, hz) in enumerate(zip(palette, tones, strict=False)):
        out = clips_dir / f"clip_{i}_{color}.mp4"
        subprocess.run([
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60:duration=5",
            "-f", "lavfi", "-i", f"color=c={color}:size=1920x1080:rate=60:duration=5",
            "-f", "lavfi", "-i", f"sine=f={hz}:duration=5",
            "-filter_complex", "[0:v][1:v]blend=all_mode=overlay:all_opacity=0.3[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(out),
        ], check=True)
    return song, clips_dir


def main() -> None:
    song, clips_dir = make_fixtures()
    print("fixtures ready:", song, clips_dir)

    # Project
    proj = http("POST", "/api/projects", {
        "name": "smoke_test",
        "song_path": str(song),
        "clips_folder": str(clips_dir),
    })
    print("created:", proj)

    # Detect beats
    beats = http("POST", "/api/projects/smoke_test/detect_beats")
    print("beats:", beats)
    assert 100 <= beats["bpm"] <= 140, f"expected ~120 BPM, got {beats['bpm']}"

    # Adjust segments: alternate rotate/reverse + enable audio on segments 1 & 4 with fades
    proj_data = http("GET", "/api/projects/smoke_test")
    proj_data["start_beat_index"] = 0
    for i, seg in enumerate(proj_data["segments"]):
        seg["in_time"] = 0.5
        seg["length_beats"] = 2 if i % 3 != 2 else 4
        seg["rotate_180"] = (i % 4 == 1)
        seg["reverse"] = (i % 4 == 3)
        if i in (1, 4):
            seg["audio_enabled"] = True
            seg["audio_fade_in"] = 0.2
            seg["audio_fade_out"] = 0.2
    http("PUT", "/api/projects/smoke_test", proj_data)
    print("segments configured:", len(proj_data["segments"]))

    # Render
    job = http("POST", "/api/projects/smoke_test/render")
    print("render started:", job)
    while True:
        time.sleep(1)
        s = http("GET", f"/api/render/{job['job_id']}")
        print(f"  status={s['status']} progress={s['progress']:.0%}")
        if s["status"] in ("done", "error"):
            break
    if s["status"] != "done":
        print("LAST LOG ENTRIES:")
        for entry in s["log"][-5:]:
            print(entry)
        raise SystemExit(1)
    print("OK render at:", s["output"])


if __name__ == "__main__":
    main()
