"""Regression tests for the SHAPE of ffmpeg commands the render pipeline builds.

The 'everything plays 2x too fast' bug was caused by placing `-t source_sec`
AFTER `-i` in the per-segment commands — that made `-t` an output-duration cap
rather than an input-read limit, and chopped the slowed output in half.

These tests assert the argument order by running render_project against tiny
synthetic media and capturing the commands via the job log (each command is
logged as a single `$ ...` line).
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from car_show_editor.config import FFMPEG
from car_show_editor.project import Project, Segment
from car_show_editor.render import render_project


@pytest.fixture
def tiny_media(tmp_path: Path) -> tuple[Path, Path]:
    """Generate one short audio + one short video clip via ffmpeg."""
    audio = tmp_path / "song.wav"
    clip = tmp_path / "clip.mp4"
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=f=440:duration=5",
        str(audio),
    ], check=True)
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=60:duration=5",
        "-f", "lavfi", "-i", "sine=f=440:duration=5",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(clip),
    ], check=True)
    return audio, clip


def _build_project(audio: Path, clip: Path) -> Project:
    return Project(
        name="cmdtest",
        song_path=str(audio),
        bpm=120.0,
        beat_times=[i * 0.5 for i in range(20)],
        duration=5.0,
        start_beat_index=0,
        default_segment_beats=4,
        row_offset_beats=2,
        fill_initial_bot_gap=True,
        clips=[str(clip)],
        segments=[
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="top"),
            Segment(clip_path=str(clip), in_time=0.0, length_beats=4, row="bottom"),
        ],
    )


def _run_render_capture(proj: Project, tmp_path: Path) -> dict[str, Any]:
    job: dict[str, Any] = {"status": "running", "progress": 0.0, "log": [], "output": None}
    # Redirect output so we don't pollute the real output dir.
    output = tmp_path / "out"
    output.mkdir()
    import car_show_editor.config as cfg
    orig_out, orig_cache = cfg.OUTPUT_DIR, cfg.CACHE_DIR
    cfg.OUTPUT_DIR = output
    cfg.CACHE_DIR = tmp_path / "cache"
    cfg.CACHE_DIR.mkdir()
    try:
        asyncio.run(render_project(proj, job))
    finally:
        cfg.OUTPUT_DIR, cfg.CACHE_DIR = orig_out, orig_cache
        shutil.rmtree(tmp_path / "cache", ignore_errors=True)
    return job


def _command_lines(log: list[str]) -> list[str]:
    return [line for line in log if line.startswith("$ ")]


def test_per_segment_video_cmd_has_t_before_i(tiny_media: tuple[Path, Path], tmp_path: Path) -> None:
    """Regression: -t must be an INPUT option (before -i), not an output cap.

    Output-side -t would clip the already-slowed video to source_sec - 2x too fast.
    """
    audio, clip = tiny_media
    proj = _build_project(audio, clip)
    job = _run_render_capture(proj, tmp_path)
    # Per-segment video commands have a single -i and write to a seg_XXXX.mp4 file.
    seg_cmds = [
        c for c in _command_lines(job["log"])
        if "setpts=" in c and "seg_" in c and "[0:v]" in c and c.count(" -i ") == 1
    ]
    assert seg_cmds, "expected at least one per-segment video command"
    for cmd in seg_cmds:
        # position of " -t " must be earlier than " -i " in the same command string
        t_pos = cmd.find(" -t ")
        i_pos = cmd.find(" -i ")
        assert t_pos > 0 and i_pos > 0, f"missing -t or -i in: {cmd}"
        assert t_pos < i_pos, (
            f"-t comes AFTER -i in segment cmd - output-duration cap bug; full cmd:\n{cmd}"
        )


def test_per_segment_audio_cmd_has_t_before_i(tiny_media: tuple[Path, Path], tmp_path: Path) -> None:
    """Same -t-placement check for per-segment audio rendering."""
    audio, clip = tiny_media
    proj = _build_project(audio, clip)
    # enable audio on one segment so the audio path runs
    proj.segments[0].audio_enabled = True
    job = _run_render_capture(proj, tmp_path)
    aud_cmds = [c for c in _command_lines(job["log"]) if "-vn" in c and "atempo" in c]
    assert aud_cmds, "expected at least one per-segment audio command"
    for cmd in aud_cmds:
        t_pos = cmd.find(" -t ")
        i_pos = cmd.find(" -i ")
        assert t_pos > 0 and i_pos > 0, f"missing -t or -i in: {cmd}"
        assert t_pos < i_pos, (
            f"-t comes AFTER -i in segment-audio cmd — output-duration cap bug; full cmd:\n{cmd}"
        )


def test_final_mux_cmd_has_t_as_output(tiny_media: tuple[Path, Path], tmp_path: Path) -> None:
    """For the FINAL mux, `-t out_dur` is correctly an OUTPUT cap (after all inputs).

    Pinning this so the opposite mistake (moving -t in front of an input) doesn't slip in.
    """
    audio, clip = tiny_media
    proj = _build_project(audio, clip)
    job = _run_render_capture(proj, tmp_path)
    final = [c for c in _command_lines(job["log"]) if "[outv]" in c and "vstack" in c]
    assert final, "expected a final mux command containing vstack"
    cmd = final[0]
    # final command structure: ... -i top -i bot -ss X -i song -filter_complex ... -t OUT ...
    fc_pos = cmd.find("-filter_complex")
    t_pos = cmd.find(" -t ")
    assert fc_pos > 0 and t_pos > fc_pos, (
        f"final-mux -t must come AFTER -filter_complex (i.e., be an output cap); cmd:\n{cmd}"
    )
