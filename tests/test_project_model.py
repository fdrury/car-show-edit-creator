"""Tests for the Pydantic project model + migration logic.

Old project JSON files must continue to load with sensible defaults for fields
that have been added over time (clips, fill_initial_bot_gap, audio_*, etc.).
"""
from __future__ import annotations

import json

from car_show_editor.project import Project, Segment


def test_minimal_legacy_project_loads_with_defaults() -> None:
    """A bare-bones JSON with just name + song_path should load with all defaults."""
    raw = json.dumps({"name": "legacy", "song_path": "C:/song.mp3"})
    p = Project.model_validate_json(raw)
    assert p.name == "legacy"
    assert p.song_path == "C:/song.mp3"
    assert p.bpm == 0.0
    assert p.beat_times == []
    assert p.segments == []
    assert p.clips == []
    # New-ish defaults that must NOT crash old projects:
    assert p.default_segment_beats == 4
    assert p.row_offset_beats == 2
    assert p.fill_initial_bot_gap is True
    assert p.song_fade_out_beats == 2
    assert p.slowdown == 0.5
    assert p.output_width == 1080
    assert p.output_height == 1920
    assert p.output_fps == 60


def test_segment_loads_with_audio_defaults() -> None:
    """Old segments lacking audio fields should default to muted/no-fade."""
    raw = json.dumps({"clip_path": "/x.mp4", "in_time": 0.0, "length_beats": 4})
    seg = Segment.model_validate_json(raw)
    assert seg.audio_enabled is False
    assert seg.audio_fade_in == 0.0
    assert seg.audio_fade_out == 0.0
    assert seg.audio_gain_db == 0.0
    assert seg.rotate_180 is False
    assert seg.reverse is False
    assert seg.row is None


def test_segment_explicit_row_preserved() -> None:
    seg = Segment(clip_path="/x.mp4", in_time=0.0, length_beats=4, row="bottom")
    assert seg.row == "bottom"
    # round-trip through JSON
    seg2 = Segment.model_validate_json(seg.model_dump_json())
    assert seg2.row == "bottom"


def test_segment_id_is_unique() -> None:
    a = Segment(clip_path="/x.mp4", in_time=0.0)
    b = Segment(clip_path="/x.mp4", in_time=0.0)
    assert a.id != b.id


def test_beat_duration_zero_when_bpm_zero() -> None:
    p = Project(name="x", song_path="x", bpm=0)
    assert p.beat_duration() == 0.0


def test_beat_duration_at_120_bpm() -> None:
    p = Project(name="x", song_path="x", bpm=120.0)
    assert p.beat_duration() == 0.5
