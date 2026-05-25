"""Tests for pure-function math helpers in render.py.

These exist to catch the timing regressions that have happened repeatedly:
 - source_sec direction (multiply vs divide by slowdown)
 - row assignment with mixed explicit/auto rows
 - effective lengths with/without fill_initial_bot_gap
 - output start times (especially that bot row really starts at t=0 when fill_gap is on)
"""
from __future__ import annotations

from car_show_editor.project import Segment
from car_show_editor.render import (
    _assign_rows,
    _atempo_chain,
    _effective_lengths,
    _effective_slowdown,
    _output_start_times,
    _segment_audio_filter,
    _segment_video_filter,
    _split_rows,
)


def s(length: float = 4, row: str | None = None, in_time: float = 0.0, **kw: object) -> Segment:
    return Segment(clip_path="/c.mp4", in_time=in_time, length_beats=length, row=row, **kw)   # type: ignore[arg-type]


# ----------------------------- _assign_rows -----------------------------


def test_assign_rows_explicit_kept() -> None:
    rows = _assign_rows([s(row="top"), s(row="bottom"), s(row="top")])
    assert rows == ["top", "bottom", "top"]


def test_assign_rows_auto_alternate_starts_top() -> None:
    rows = _assign_rows([s(), s(), s(), s()])
    assert rows == ["top", "bottom", "top", "bottom"]


def test_assign_rows_mixed_auto_counter_only_advances_on_auto() -> None:
    # explicit segs don't move the auto pointer; auto resumes alternation
    rows = _assign_rows([s(), s(row="top"), s(), s(row="bottom"), s()])
    # auto: 0->top, 1->bot, 2->top
    assert rows == ["top", "top", "bottom", "bottom", "top"]


# ----------------------------- _split_rows -----------------------------


def test_split_rows_groups_by_assignment() -> None:
    segs = [s(row="top"), s(row="bottom"), s(row="top"), s(row="bottom")]
    top, bot = _split_rows(segs)
    assert [i for i, _ in top] == [0, 2]
    assert [i for i, _ in bot] == [1, 3]


# ----------------------------- _effective_lengths -----------------------------


def test_effective_lengths_no_fill_unchanged() -> None:
    segs = [s(length=4, row="top"), s(length=6, row="bottom"), s(length=4, row="bottom")]
    rows = _assign_rows(segs)
    lens = _effective_lengths(segs, rows, row_offset_beats=2, fill_gap=False)
    assert lens == [4, 6, 4]


def test_effective_lengths_fill_does_not_override_first_bot() -> None:
    """Regression: dragging a 10-beat clip into the first bot slot must NOT get auto-shortened."""
    segs = [s(length=4, row="top"), s(length=10, row="bottom"), s(length=4, row="bottom")]
    rows = _assign_rows(segs)
    lens = _effective_lengths(segs, rows, row_offset_beats=2, fill_gap=True)
    assert lens == [4, 10, 4]


def test_effective_lengths_fill_with_no_bot_no_op() -> None:
    segs = [s(length=4, row="top"), s(length=4, row="top")]
    rows = _assign_rows(segs)
    lens = _effective_lengths(segs, rows, row_offset_beats=2, fill_gap=True)
    assert lens == [4, 4]


def test_effective_lengths_fill_with_zero_offset_no_op() -> None:
    segs = [s(length=4, row="top"), s(length=4, row="bottom")]
    rows = _assign_rows(segs)
    lens = _effective_lengths(segs, rows, row_offset_beats=0, fill_gap=True)
    assert lens == [4, 4]


def test_effective_lengths_accepts_fractional_beats() -> None:
    """Half-beat (0.5) lengths must survive through the helper unchanged."""
    segs = [s(length=0.5, row="top"), s(length=4, row="bottom"), s(length=1.5, row="top")]
    rows = _assign_rows(segs)
    lens = _effective_lengths(segs, rows, row_offset_beats=2, fill_gap=True)
    assert lens == [0.5, 4, 1.5]


# ----------------------------- _output_start_times -----------------------------


def _uniform_grid(n: float) -> float:
    return n * 0.5


def test_output_start_times_fill_gap_starts_both_at_zero() -> None:
    """With fill_gap=True the bot row starts at t=0 — first-bot length is honored as-is, no auto-shorten."""
    segs = [s(length=4, row="top"), s(length=4, row="bottom"), s(length=4, row="top"), s(length=4, row="bottom")]
    starts = _output_start_times(segs, _uniform_grid, row_offset_beats=2, fill_gap=True)
    # top: 0, then 4 beats * 0.5 = 2.0
    # bot: 0 (full 4 beats), then 4 beats * 0.5 = 2.0
    assert starts[0] == 0.0
    assert starts[1] == 0.0
    assert starts[2] == 2.0
    assert starts[3] == 2.0


def test_output_start_times_no_fill_bot_starts_at_offset() -> None:
    segs = [s(length=4, row="top"), s(length=4, row="bottom"), s(length=4, row="top"), s(length=4, row="bottom")]
    starts = _output_start_times(segs, _uniform_grid, row_offset_beats=2, fill_gap=False)
    # top: 0, 2
    # bot: 1.0 (offset), 1.0 + 2.0 = 3.0
    assert starts[0] == 0.0
    assert starts[1] == 1.0
    assert starts[2] == 2.0
    assert starts[3] == 3.0


def test_output_start_times_with_nonuniform_beats() -> None:
    """Beat-times that aren't perfectly even should make per-segment screen times also non-even."""
    from car_show_editor.render import _output_time_at_beat
    # Synthetic beat grid where the 2nd interval is half the 1st (tempo speeds up briefly).
    beat_times = [0.0, 0.5, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25]

    def beat_to_time(n: float) -> float:
        return _output_time_at_beat(beat_times, 0, 0.5, n)

    segs = [s(length=2, row="top"), s(length=2, row="top")]
    starts = _output_start_times(segs, beat_to_time, row_offset_beats=2, fill_gap=True)
    # seg 0 spans beats [0,2] -> t=0..0.75
    # seg 1 spans beats [2,4] -> t=0.75..1.75
    assert starts[0] == 0.0
    assert starts[1] == 0.75


# ----------------------------- _segment_video_filter -----------------------------


def test_segment_video_filter_setpts_inverse_of_slowdown() -> None:
    """At slowdown=0.5 (half-speed playback) the PTS factor must be 2.0, not 0.5."""
    flt = _segment_video_filter(s(), slowdown=0.5, w=1080, h=960, fps=60, screen_sec=2.0)
    assert "setpts=2.0*PTS" in flt


def test_segment_video_filter_caps_to_screen_sec_via_trim() -> None:
    """Filter must end with tpad+trim+setpts so file duration is exactly screen_sec."""
    flt = _segment_video_filter(s(), slowdown=0.5, w=1080, h=960, fps=60, screen_sec=1.234)
    # Both pad (for too-short source) and trim (for too-long source) must be present.
    assert "tpad=stop_duration=1.234000:stop_mode=clone" in flt
    assert "trim=duration=1.234000" in flt
    assert flt.endswith("setpts=PTS-STARTPTS")


def test_segment_video_filter_rotate_180() -> None:
    flt = _segment_video_filter(s(rotate_180=True), slowdown=0.5, w=1080, h=960, fps=60, screen_sec=1.0)
    assert "transpose=2,transpose=2" in flt


def test_segment_video_filter_reverse() -> None:
    flt = _segment_video_filter(s(reverse=True), slowdown=0.5, w=1080, h=960, fps=60, screen_sec=1.0)
    assert "reverse" in flt


def test_segment_video_filter_no_rotate_when_off() -> None:
    flt = _segment_video_filter(s(rotate_180=False), slowdown=0.5, w=1080, h=960, fps=60, screen_sec=1.0)
    assert "transpose" not in flt


def test_segment_video_filter_crop_aspect() -> None:
    """Center-crop math must be ih*9/8:ih when slot is 1080x960."""
    flt = _segment_video_filter(s(), slowdown=0.5, w=1080, h=960, fps=60, screen_sec=1.0)
    # 1080/960 reduces to 9/8 -> the formula keeps the literal w/h substituted
    assert "crop=ih*1080/960:ih" in flt


# ----------------------------- _segment_audio_filter -----------------------------


def test_segment_audio_filter_atempo_matches_slowdown() -> None:
    flt = _segment_audio_filter(s(audio_enabled=True), slowdown=0.5, screen_sec=2.0)
    assert "atempo=0.5" in flt


def test_segment_audio_filter_fades_when_set() -> None:
    flt = _segment_audio_filter(
        s(audio_enabled=True, audio_fade_in=0.2, audio_fade_out=0.3), slowdown=0.5, screen_sec=2.0,
    )
    assert "afade=t=in:d=0.200000:st=0" in flt
    assert "afade=t=out:d=0.300000:st=1.700000" in flt


def test_segment_audio_filter_no_fades_when_zero() -> None:
    flt = _segment_audio_filter(
        s(audio_enabled=True, audio_fade_in=0, audio_fade_out=0), slowdown=0.5, screen_sec=2.0,
    )
    assert "afade" not in flt


def test_segment_audio_filter_caps_to_screen_sec() -> None:
    flt = _segment_audio_filter(s(audio_enabled=True), slowdown=0.5, screen_sec=1.234)
    assert "apad=pad_dur=1.234000" in flt
    assert "atrim=duration=1.234000" in flt


def test_segment_audio_filter_reverse() -> None:
    flt = _segment_audio_filter(s(audio_enabled=True, reverse=True), slowdown=0.5, screen_sec=1.0)
    assert "areverse" in flt


# ----------------------------- _effective_slowdown -----------------------------


def test_effective_slowdown_explicit_override_wins() -> None:
    """An explicit per-seg slowdown beats every other rule."""
    assert _effective_slowdown(s(slowdown=0.25), project_slowdown=0.5) == 0.25
    assert _effective_slowdown(s(slowdown=1.0, audio_enabled=True), project_slowdown=0.5) == 1.0
    assert _effective_slowdown(s(slowdown=2.0), project_slowdown=0.5) == 2.0


def test_effective_slowdown_audio_enabled_defaults_to_realtime() -> None:
    """Audio-enabled with slowdown=None must be 1.0 — slowed audio sounds awful."""
    assert _effective_slowdown(s(audio_enabled=True), project_slowdown=0.5) == 1.0


def test_effective_slowdown_falls_back_to_project_default() -> None:
    assert _effective_slowdown(s(), project_slowdown=0.5) == 0.5
    assert _effective_slowdown(s(), project_slowdown=0.25) == 0.25


# ----------------------------- _atempo_chain -----------------------------


def test_atempo_chain_in_range_one_filter() -> None:
    """0.5 ≤ x ≤ 2.0 fits in a single atempo, no chaining."""
    assert _atempo_chain(1.0) == "atempo=1.000000"
    assert _atempo_chain(0.5) == "atempo=0.500000"
    assert _atempo_chain(2.0) == "atempo=2.000000"
    assert _atempo_chain(1.5) == "atempo=1.500000"


def test_atempo_chain_below_half_chains() -> None:
    """0.25 requires atempo=0.5 then atempo=0.5 (compound 0.5 × 0.5 = 0.25)."""
    chain = _atempo_chain(0.25)
    assert chain.count("atempo=") == 2
    assert chain.startswith("atempo=0.5,")
    assert chain.endswith("atempo=0.500000")


def test_atempo_chain_above_two_chains() -> None:
    """4.0 requires atempo=2.0,atempo=2.0."""
    chain = _atempo_chain(4.0)
    assert chain.count("atempo=") == 2
    assert chain.startswith("atempo=2.0,")
    assert chain.endswith("atempo=2.000000")
