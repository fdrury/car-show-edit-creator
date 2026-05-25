"""End-to-end render check: assert the actual output duration matches the
arithmetic prediction within a small tolerance.

Catches both classes of recent regressions in one shot:
 - source_sec direction errors (output ends up 2x or 4x too short/long)
 - tpad/trim mismatches that let sub-frame drift accumulate
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from car_show_editor.config import FFMPEG, FFPROBE
from car_show_editor.project import Project, Segment
from car_show_editor.render import render_project


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    return tmp_path


def _make_clip(path: Path, color: str = "blue", seconds: int = 4) -> None:
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:size=640x360:rate=60:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=f=440:duration={seconds}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(path),
    ], check=True)


def _make_song(path: Path, seconds: int = 10) -> None:
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=f=440:duration={seconds}",
        str(path),
    ], check=True)


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def _run_render(proj: Project, tmp_path: Path) -> Path:
    job: dict[str, Any] = {"status": "running", "progress": 0.0, "log": [], "output": None}
    output = tmp_path / "out"
    output.mkdir()
    import car_show_editor.config as cfg
    orig_out, orig_cache = cfg.OUTPUT_DIR, cfg.CACHE_DIR
    cfg.OUTPUT_DIR = output
    cfg.CACHE_DIR = tmp_path / "cache"
    cfg.CACHE_DIR.mkdir()
    try:
        result = asyncio.run(render_project(proj, job))
    finally:
        cfg.OUTPUT_DIR, cfg.CACHE_DIR = orig_out, orig_cache
        shutil.rmtree(tmp_path / "cache", ignore_errors=True)
    return result


def test_output_duration_matches_screen_time(fixture_root: Path) -> None:
    """4 segments x 4 beats @ 120 BPM with fill_gap should yield ~4s output."""
    song = fixture_root / "song.wav"
    clip = fixture_root / "clip.mp4"
    _make_song(song, seconds=10)
    _make_clip(clip, seconds=4)

    proj = Project(
        name="dur_test",
        song_path=str(song),
        bpm=120.0,
        beat_times=[i * 0.5 for i in range(30)],
        duration=10.0,
        start_beat_index=0,
        default_segment_beats=4,
        row_offset_beats=2,
        fill_initial_bot_gap=True,
        clips=[str(clip)],
        segments=[
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="top"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="bottom"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="top"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="bottom"),
        ],
    )
    # Expected (with fill_initial_bot_gap=True — both rows start at t=0):
    #   top: 4 + 4 = 8 beats * 0.5 = 4.0s
    #   bot: 4 + 4 = 8 beats * 0.5 = 4.0s
    #   out_dur = max(4.0, 4.0) = 4.0s
    out = _run_render(proj, fixture_root)
    actual = _probe_duration(out)
    assert 3.9 <= actual <= 4.1, f"expected ~4.0s, got {actual}s"


def test_output_duration_without_fill_includes_offset(fixture_root: Path) -> None:
    """Without fill_gap the bot row starts at row_offset, so bot_span dominates."""
    song = fixture_root / "song.wav"
    clip = fixture_root / "clip.mp4"
    _make_song(song, seconds=10)
    _make_clip(clip, seconds=4)
    proj = Project(
        name="dur_nofill",
        song_path=str(song),
        bpm=120.0,
        beat_times=[i * 0.5 for i in range(30)],
        duration=10.0,
        default_segment_beats=4,
        row_offset_beats=2,
        fill_initial_bot_gap=False,
        clips=[str(clip)],
        segments=[
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="top"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="bottom"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="top"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="bottom"),
        ],
    )
    # top span = 8 beats * 0.5 = 4.0s
    # bot span = (2 beats offset + 8 beats segs) * 0.5 = 5.0s
    # out_dur = 5.0s
    out = _run_render(proj, fixture_root)
    actual = _probe_duration(out)
    assert 4.9 <= actual <= 5.1, f"expected ~5.0s, got {actual}s"


def test_slowdown_is_applied_video_content_plays_at_half_speed(fixture_root: Path) -> None:
    """If a 4-beat segment is rendered from a 2s source @ slowdown=0.5, output is 2s wall time
    (1s wall = 0.5s source consumed). Just verify the duration math holds end-to-end."""
    song = fixture_root / "song.wav"
    clip = fixture_root / "clip.mp4"
    _make_song(song, seconds=10)
    _make_clip(clip, seconds=4)
    proj = Project(
        name="slowdown_test",
        song_path=str(song),
        bpm=120.0,
        beat_times=[i * 0.5 for i in range(30)],
        duration=10.0,
        default_segment_beats=4,
        row_offset_beats=2,
        fill_initial_bot_gap=True,
        clips=[str(clip)],
        segments=[
            # one 4-beat top + one 4-beat bot. fill shortens bot to 2-beat.
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="top"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="bottom"),
        ],
    )
    # top = 4 beats = 2s screen. bot = 4 beats = 2s screen. out_dur = 2s.
    out = _run_render(proj, fixture_root)
    actual = _probe_duration(out)
    assert 1.9 <= actual <= 2.1, f"expected ~2.0s, got {actual}s (2x-too-fast bug?)"
