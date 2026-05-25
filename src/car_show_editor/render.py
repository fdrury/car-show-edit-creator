"""FFmpeg render pipeline.

Three passes:
  1. Per-segment video intermediates  (1080x960, 60 fps, yuv420p, silent)
  2. Per-row concat (no re-encode) + per-segment audio intermediates (for the
     subset of segments with audio_enabled).
  3. Final mux: vstack the two rows, mix song audio with all segment audios,
     re-encode to deliverable MP4.
"""
from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, Any

from .config import CACHE_DIR, FFMPEG, OUTPUT_DIR

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .project import Project, Segment

Job = dict[str, Any]   # progress/log/output passed by the server task


# ----------------------------- helpers -----------------------------


def _ffmpeg_cmd(args: list[str]) -> list[str]:
    return [FFMPEG, "-hide_banner", "-y", *args]


async def _run(cmd: list[str], log: list[str]) -> None:
    """Run ffmpeg, append a short stderr tail to the log; raise on non-zero exit."""
    log.append("$ " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    text = stderr.decode("utf-8", errors="replace")
    tail = "\n".join(text.splitlines()[-40:])
    log.append(tail)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode})")


def _segment_video_filter(seg: Segment, slowdown: float, w: int, h: int, fps: int, screen_sec: float) -> str:
    """Filter for a single segment: slow, optional rotate/reverse, crop, scale, fps.

    Ends with tpad+trim+setpts so the output file's duration is *exactly* `screen_sec`
    regardless of source-frame rounding — otherwise sub-frame errors accumulate across
    many concatenated segments and the cuts drift off the beat grid.
    """
    parts: list[str] = [f"setpts={1.0/slowdown}*PTS"]
    if seg.reverse:
        parts.append("reverse")
    if seg.rotate_180:
        parts.append("transpose=2,transpose=2")
    parts += [
        f"crop=ih*{w}/{h}:ih",
        f"scale={w}:{h}:flags=lanczos",
        f"fps={fps}",
        "format=yuv420p",
        f"tpad=stop_duration={screen_sec:.6f}:stop_mode=clone",
        f"trim=duration={screen_sec:.6f}",
        "setpts=PTS-STARTPTS",
    ]
    return ",".join(parts)


def _effective_slowdown(seg: Segment, project_slowdown: float) -> float:
    """Per-segment slowdown: explicit override > audio-coupled 1.0 > project default."""
    if seg.slowdown is not None:
        return seg.slowdown
    if seg.audio_enabled:
        return 1.0
    return project_slowdown


def _atempo_chain(target: float) -> str:
    """Build an atempo filter chain covering values outside the single-filter [0.5, 2.0] sweet spot.
    e.g. 0.25 -> 'atempo=0.5,atempo=0.5'; 4.0 -> 'atempo=2.0,atempo=2.0'.
    """
    if target <= 0:
        return "atempo=1.0"
    parts: list[str] = []
    remaining = target
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def _segment_audio_filter(seg: Segment, slowdown: float, screen_sec: float) -> str:
    parts: list[str] = [_atempo_chain(slowdown)]
    if seg.reverse:
        parts.append("areverse")
    if seg.audio_fade_in > 0:
        fi = min(seg.audio_fade_in, screen_sec)
        parts.append(f"afade=t=in:d={fi:.6f}:st=0")
    if seg.audio_fade_out > 0:
        fo = min(seg.audio_fade_out, screen_sec)
        parts.append(f"afade=t=out:d={fo:.6f}:st={max(0.0, screen_sec - fo):.6f}")
    if seg.audio_gain_db:
        parts.append(f"volume={seg.audio_gain_db}dB")
    # Force exact duration to keep audio aligned with video over many segments.
    parts += [
        f"apad=pad_dur={screen_sec:.6f}",
        f"atrim=duration={screen_sec:.6f}",
        "asetpts=PTS-STARTPTS",
    ]
    return ",".join(parts)


def _assign_rows(segments: list[Segment]) -> list[str]:
    """["top" | "bottom"] per segment index. Honors row override; auto-alternates the rest."""
    out: list[str] = []
    auto_idx = 0
    for s in segments:
        if s.row == "top":
            out.append("top")
        elif s.row == "bottom":
            out.append("bottom")
        else:
            out.append("top" if auto_idx % 2 == 0 else "bottom")
            auto_idx += 1
    return out


def _split_rows(segments: list[Segment]) -> tuple[list[tuple[int, Segment]], list[tuple[int, Segment]]]:
    rows = _assign_rows(segments)
    top = [(i, s) for i, (s, r) in enumerate(zip(segments, rows, strict=True)) if r == "top"]
    bot = [(i, s) for i, (s, r) in enumerate(zip(segments, rows, strict=True)) if r == "bottom"]
    return top, bot


def _effective_lengths(segments: list[Segment], rows: list[str], row_offset_beats: int, fill_gap: bool) -> list[float]:  # noqa: ARG001 — args kept for API stability
    """Return user-set length-in-beats per segment, unmodified.

    (Earlier versions auto-shortened the first bot segment to `row_offset_beats` when fill_gap
    was on, but that was a surprising override when the user wanted a long opener. The bot row's
    starting position is still controlled by fill_gap in _output_start_times.)
    """
    return [s.length_beats for s in segments]


def _output_time_at_beat(beat_times: list[float], start_beat_index: int, beat_dur: float, n_beats: float) -> float:
    """Output-timeline seconds for `n_beats` past the project's start beat.

    Uses linear interpolation between adjacent entries of `beat_times` for fractional beats,
    so cuts land on the *actual* detected beats (which aren't perfectly uniform). Extrapolates
    past the last beat using `beat_dur` as a fallback. With no detected beats, falls back to
    the uniform grid entirely.
    """
    if not beat_times:
        return n_beats * beat_dur
    base = beat_times[start_beat_index]
    abs_beat = start_beat_index + n_beats
    n_known = len(beat_times)
    if abs_beat <= 0:
        return abs_beat * beat_dur - base
    if abs_beat >= n_known - 1:
        # extrapolate past the last detected beat
        last = beat_times[-1]
        extra = (abs_beat - (n_known - 1)) * beat_dur
        return last + extra - base
    lo = int(abs_beat)
    frac = abs_beat - lo
    interp = beat_times[lo] + frac * (beat_times[lo + 1] - beat_times[lo])
    return interp - base


def _output_start_times(segments: list[Segment], beat_to_time: Callable[[float], float],
                        row_offset_beats: int, fill_gap: bool) -> list[float]:
    """Return each segment's start time (seconds) on the output timeline."""
    rows = _assign_rows(segments)
    lens = _effective_lengths(segments, rows, row_offset_beats, fill_gap)
    top_beat = 0.0
    bot_beat = 0.0 if fill_gap else float(row_offset_beats)
    starts: list[float] = []
    for i, r in enumerate(rows):
        if r == "top":
            starts.append(beat_to_time(top_beat))
            top_beat += lens[i]
        else:
            starts.append(beat_to_time(bot_beat))
            bot_beat += lens[i]
    return starts


def _write_concat_list(files: list[Path], path: Path) -> None:
    path.write_text("\n".join(f"file '{f.as_posix()}'" for f in files))


# ----------------------------- passes -----------------------------


async def _pass1_segments(proj: Project, work: Path, job: Job) -> list[Path]:
    slot_w, slot_h = proj.output_width, proj.output_height // 2
    rows = _assign_rows(proj.segments)
    lens = _effective_lengths(proj.segments, rows, proj.row_offset_beats, proj.fill_initial_bot_gap)
    beat_to_time = _project_beat_to_time(proj)
    # Walk per-row beat cursors so each segment's screen_sec reflects the *actual* time between
    # its starting beat and ending beat (interpolated from librosa's beat_times). Cuts then land on
    # real beats instead of a uniform grid average — prevents drift across long songs.
    top_beat = 0.0
    bot_beat = 0.0 if proj.fill_initial_bot_gap else float(proj.row_offset_beats)
    seg_files: list[Path] = []
    n = len(proj.segments)
    for i, seg in enumerate(proj.segments):
        if rows[i] == "top":
            start_beat, end_beat = top_beat, top_beat + lens[i]
            top_beat = end_beat
        else:
            start_beat, end_beat = bot_beat, bot_beat + lens[i]
            bot_beat = end_beat
        screen_sec = beat_to_time(end_beat) - beat_to_time(start_beat)
        slowdown = _effective_slowdown(seg, proj.slowdown)
        source_sec = screen_sec * slowdown
        out = work / f"seg_{i:04d}.mp4"
        flt = _segment_video_filter(seg, slowdown, slot_w, slot_h, proj.output_fps, screen_sec)
        # -ss and -t MUST come before -i so they're input options (trim the read).
        # If -t is placed after -i it becomes an output-duration cap, which clips the
        # already-slowed output to source_sec — i.e., everything plays 2x too fast.
        await _run(_ffmpeg_cmd([
            "-ss", f"{seg.in_time:.6f}",
            "-t", f"{source_sec:.6f}",
            "-i", seg.clip_path,
            "-filter_complex", f"[0:v]{flt}[v]",
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(proj.output_fps),
            str(out),
        ]), job["log"])
        seg_files.append(out)
        job["progress"] = 0.7 * ((i + 1) / n)
    return seg_files


async def _pass2_concat_rows(
    seg_files: list[Path],
    top_rows: list[tuple[int, Segment]],
    bot_rows: list[tuple[int, Segment]],
    work: Path,
    job: Job,
) -> tuple[Path | None, Path | None]:
    top_mp4: Path | None = None
    bot_mp4: Path | None = None
    if top_rows:
        top_mp4 = work / "_top.mp4"
        lst = work / "_top.txt"
        _write_concat_list([seg_files[i] for i, _ in top_rows], lst)
        await _run(_ffmpeg_cmd(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(top_mp4)]), job["log"])
    if bot_rows:
        bot_mp4 = work / "_bot.mp4"
        lst = work / "_bot.txt"
        _write_concat_list([seg_files[i] for i, _ in bot_rows], lst)
        await _run(_ffmpeg_cmd(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(bot_mp4)]), job["log"])
    job["progress"] = 0.80
    return top_mp4, bot_mp4


async def _pass2b_segment_audio(proj: Project, work: Path, job: Job) -> list[tuple[float, Path]]:
    """Render the audio side of each segment that has audio_enabled."""
    rows = _assign_rows(proj.segments)
    lens = _effective_lengths(proj.segments, rows, proj.row_offset_beats, proj.fill_initial_bot_gap)
    beat_to_time = _project_beat_to_time(proj)
    starts = _output_start_times(proj.segments, beat_to_time, proj.row_offset_beats, proj.fill_initial_bot_gap)
    # Walk per-row beat cursors to compute each segment's screen_sec from real beat positions.
    top_beat = 0.0
    bot_beat = 0.0 if proj.fill_initial_bot_gap else float(proj.row_offset_beats)
    screen_secs: dict[int, float] = {}
    for i in range(len(proj.segments)):
        if rows[i] == "top":
            screen_secs[i] = beat_to_time(top_beat + lens[i]) - beat_to_time(top_beat)
            top_beat += lens[i]
        else:
            screen_secs[i] = beat_to_time(bot_beat + lens[i]) - beat_to_time(bot_beat)
            bot_beat += lens[i]
    out_audio: list[tuple[float, Path]] = []
    for i, seg in enumerate(proj.segments):
        if not seg.audio_enabled:
            continue
        slowdown = _effective_slowdown(seg, proj.slowdown)
        screen_sec = screen_secs[i]
        source_sec = screen_sec * slowdown
        aout = work / f"seg_{i:04d}.wav"
        afilters = _segment_audio_filter(seg, slowdown, screen_sec)
        try:
            # See _pass1_segments — -t must precede -i to act as an input-read limit.
            await _run(_ffmpeg_cmd([
                "-ss", f"{seg.in_time:.6f}",
                "-t", f"{source_sec:.6f}",
                "-i", seg.clip_path,
                "-vn", "-af", afilters,
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                str(aout),
            ]), job["log"])
            out_audio.append((starts[i], aout))
        except RuntimeError as e:
            # Source might have no audio stream — skip rather than fail the render.
            job["log"].append(f"(skipping seg {i} audio: {e})")
    job["progress"] = 0.85
    return out_audio


def _project_beat_to_time(proj: Project) -> Callable[[float], float]:
    """Return a callable beat_to_time(n) for this project — interpolates beat_times if present,
    else falls back to uniform beat_dur."""
    beat_dur = proj.beat_duration()
    beat_times = list(proj.beat_times)
    start = proj.start_beat_index
    return lambda n: _output_time_at_beat(beat_times, start, beat_dur, n)


def _compute_durations(proj: Project, top_rows: list[tuple[int, Segment]],
                       bot_rows: list[tuple[int, Segment]]) -> tuple[float, float, float, float, float]:
    """Return (top_span, bot_span, out_dur, song_start_t, song_avail) — all in seconds."""
    rows = _assign_rows(proj.segments)
    lens = _effective_lengths(proj.segments, rows, proj.row_offset_beats, proj.fill_initial_bot_gap)
    beat_to_time = _project_beat_to_time(proj)
    top_total_beats = sum(lens[i] for i, _ in top_rows)
    bot_total_beats = sum(lens[i] for i, _ in bot_rows)
    top_span = beat_to_time(top_total_beats)
    bot_start_beat = 0.0 if proj.fill_initial_bot_gap else float(proj.row_offset_beats)
    bot_span = beat_to_time(bot_start_beat + bot_total_beats)
    out_dur = max(top_span, bot_span)
    song_start_t = proj.beat_times[proj.start_beat_index] if proj.beat_times else 0.0
    song_avail = max(0.0, proj.duration - song_start_t)
    if song_avail > 0:
        out_dur = min(out_dur, song_avail)
    return top_span, bot_span, max(out_dur, 0.1), song_start_t, song_avail


def _build_video_filtergraph(
    proj: Project, out_dur: float, top_span: float, bot_span: float,
    top_mp4: Path | None, bot_mp4: Path | None,
) -> tuple[list[str], list[str], int]:
    """Return (inputs, filtergraph_parts, next_input_idx)."""
    slot_w, slot_h = proj.output_width, proj.output_height // 2
    # If fill_initial_bot_gap is off, the bot row gets a leading silence equal to the actual
    # song-time spanned by row_offset_beats (interpolated, not uniform beat_dur — keeps the
    # first bot segment landing on the correct beat).
    beat_to_time = _project_beat_to_time(proj)
    bot_start_pad = 0.0 if proj.fill_initial_bot_gap else beat_to_time(float(proj.row_offset_beats))
    inputs: list[str] = []
    fg: list[str] = []
    idx = 0
    if top_mp4 is not None:
        inputs += ["-i", str(top_mp4)]
        fg.append(
            f"[{idx}:v]tpad=stop_mode=add:stop_duration={max(0.0, out_dur - top_span):.6f}:color=black,"
            f"trim=duration={out_dur:.6f},setpts=PTS-STARTPTS[topv]"
        )
        idx += 1
    else:
        fg.append(f"color=c=black:s={slot_w}x{slot_h}:r={proj.output_fps}:d={out_dur:.6f}[topv]")
    if bot_mp4 is not None:
        inputs += ["-i", str(bot_mp4)]
        end_pad = max(0.0, out_dur - bot_span)
        fg.append(
            f"[{idx}:v]tpad=start_duration={bot_start_pad:.6f}:start_mode=add:color=black,"
            f"tpad=stop_duration={end_pad:.6f}:stop_mode=add:color=black,"
            f"trim=duration={out_dur:.6f},setpts=PTS-STARTPTS[botv]"
        )
        idx += 1
    else:
        fg.append(f"color=c=black:s={slot_w}x{slot_h}:r={proj.output_fps}:d={out_dur:.6f}[botv]")
    fg.append("[topv][botv]vstack=inputs=2[outv]")
    return inputs, fg, idx


def _build_audio_filtergraph(
    inputs: list[str], fg: list[str], idx: int,
    proj: Project, song_start_t: float, song_avail: float,
    seg_audio: list[tuple[float, Path]], out_dur: float,
) -> None:
    """Append song + per-segment audios to inputs/fg, ending in [outa].

    If video ends before the song would naturally end, fade the final mix over
    the last `proj.song_fade_out_beats` beats for a clean musical ending.
    """
    inputs += ["-ss", f"{song_start_t:.6f}", "-i", proj.song_path]
    song_idx = idx
    idx += 1
    labels: list[str] = []
    for k, (start_sec, path) in enumerate(seg_audio):
        inputs += ["-i", str(path)]
        delay_ms = round(start_sec * 1000)
        fg.append(
            f"[{idx}:a]aresample=48000,adelay={delay_ms}|{delay_ms},apad=pad_dur={out_dur:.6f}[sa{k}]"
        )
        labels.append(f"[sa{k}]")
        idx += 1
    fg.append(f"[{song_idx}:a]aresample=48000[song]")
    all_audio = ["[song]", *labels]

    # Compute fade duration from real beat positions at the end of the output. We can't know
    # the exact end-beat without knowing total beats consumed, so approximate using the
    # last `song_fade_out_beats` from the last detected beat, or fall back to uniform beat_dur.
    if proj.beat_times and len(proj.beat_times) > proj.song_fade_out_beats:
        n = len(proj.beat_times)
        fade_dur = proj.beat_times[n - 1] - proj.beat_times[n - 1 - proj.song_fade_out_beats]
    else:
        fade_dur = proj.song_fade_out_beats * proj.beat_duration()
    needs_fade = fade_dur > 0 and out_dur < song_avail - 0.05 and out_dur > fade_dur
    if needs_fade:
        fg.append(
            f"{''.join(all_audio)}amix=inputs={len(all_audio)}:normalize=0:dropout_transition=0[outa_raw]"
        )
        fg.append(
            f"[outa_raw]afade=t=out:st={out_dur - fade_dur:.6f}:d={fade_dur:.6f}[outa]"
        )
    else:
        fg.append(
            f"{''.join(all_audio)}amix=inputs={len(all_audio)}:normalize=0:dropout_transition=0[outa]"
        )


async def render_project(proj: Project, job: Job) -> Path:
    if not proj.bpm:
        raise RuntimeError("BPM not detected — go back to the Beats step first")
    if not proj.segments:
        raise RuntimeError("No segments to render")

    work = CACHE_DIR / f"render_{proj.name}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    seg_files = await _pass1_segments(proj, work, job)
    top_rows, bot_rows = _split_rows(proj.segments)
    top_mp4, bot_mp4 = await _pass2_concat_rows(seg_files, top_rows, bot_rows, work, job)
    seg_audio = await _pass2b_segment_audio(proj, work, job)

    top_span, bot_span, out_dur, song_start_t, song_avail = _compute_durations(proj, top_rows, bot_rows)

    output_file = OUTPUT_DIR / f"{proj.name}.mp4"
    (OUTPUT_DIR / f"{proj.name}.json").write_text(proj.model_dump_json(indent=2))

    inputs, fg, idx = _build_video_filtergraph(proj, out_dur, top_span, bot_span, top_mp4, bot_mp4)
    _build_audio_filtergraph(inputs, fg, idx, proj, song_start_t, song_avail, seg_audio, out_dur)

    await _run(_ffmpeg_cmd([
        *inputs,
        "-filter_complex", ";".join(fg),
        "-map", "[outv]",
        "-map", "[outa]",
        "-t", f"{out_dur:.6f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_file),
    ]), job["log"])
    job["progress"] = 1.0
    return output_file
