"""Project data model and JSON save/load.

A Project holds the song reference, beat grid, and an ordered list of Segments.
Each Segment is one clip-instance that will appear in either the top or bottom
row of the stacked output. By default segments alternate top/bot in list order;
a Segment may override its row.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .config import PROJECTS_DIR

if TYPE_CHECKING:
    from pathlib import Path


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _empty_floats() -> list[float]:
    return []


def _empty_segments() -> list[Segment]:
    return []


def _empty_strs() -> list[str]:
    return []


class Segment(BaseModel):
    id: str = Field(default_factory=_new_id)
    clip_path: str
    in_time: float                  # seconds into source clip (before slowdown)
    length_beats: float = 2.0       # screen duration in beats; fractional values (e.g. 0.5) are allowed
    rotate_180: bool = False
    reverse: bool = False
    row: Literal["top", "bottom"] | None = None   # None = auto-alternate by index
    # audio: clips are muted by default; enable to mix this segment's audio
    # over the song (with fades). audio honors slowdown and reverse.
    audio_enabled: bool = False
    audio_fade_in: float = 0.0      # seconds of fade-in at segment start
    audio_fade_out: float = 0.0     # seconds of fade-out at segment end
    audio_gain_db: float = 0.0      # +/- dB applied after fades
    # Per-segment playback rate. None = auto (1.0 when audio_enabled else project.slowdown).
    # Slowed audio sounds awful, so audio-enabled segments default to real-time.
    slowdown: float | None = None


class Project(BaseModel):
    name: str
    song_path: str
    bpm: float = 0.0
    beat_times: list[float] = Field(default_factory=_empty_floats)
    duration: float = 0.0                              # song duration (seconds)
    start_beat_index: int = 0                          # which detected beat is "beat 1" of video
    default_segment_beats: float = 4.0                 # default length for new segments (0.5 increments)
    row_offset_beats: int = 2                          # how many beats bot is offset from top
    # If True, start the bottom row at t=0 (no leading black) and shorten the *first* bot
    # segment to exactly `row_offset_beats` so the stagger pattern continues from the next swap.
    fill_initial_bot_gap: bool = True
    source_fps: int = 60                               # all clips assumed 60 fps
    slowdown: float = 0.5                              # 0.5x = half speed
    output_width: int = 1080
    output_height: int = 1920
    output_fps: int = 60
    song_fade_out_beats: int = 2     # fade the song over the last N beats if video ends before song does
    # All source clips known to this project (from initial folder scan).
    # Lives independently of `segments` so a clip can remain in the Review list with 0 segments.
    clips: list[str] = Field(default_factory=_empty_strs)
    segments: list[Segment] = Field(default_factory=_empty_segments)

    # Note: `fill_initial_bot_gap` only controls whether the bot row starts at t=0 or at row_offset_beats
    # of leading silence. It does NOT shorten any segments — first-bot length is always honored.

    # convenience
    def beat_duration(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.0

    def save(self) -> Path:
        path = PROJECTS_DIR / f"{self.name}.json"
        path.write_text(self.model_dump_json(indent=2))
        return path

    @classmethod
    def load(cls, name: str) -> Project:
        path = PROJECTS_DIR / f"{name}.json"
        return cls.model_validate_json(path.read_text())

    @classmethod
    def list_names(cls) -> list[str]:
        return sorted(p.stem for p in PROJECTS_DIR.glob("*.json"))
