"""FastAPI app: project CRUD, beat detection, clip listing, media streaming, render."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import beats, media, render
from .config import OUTPUT_DIR, PROJECTS_DIR
from .project import Project, Segment

app = FastAPI(title="Car Show Editor")

STATIC_DIR = Path(__file__).parent / "static"


# -------------------- project endpoints --------------------


class CreateReq(BaseModel):
    name: str
    song_path: str
    clips_folder: str


@app.get("/api/projects")
def list_projects() -> dict[str, list[str]]:
    return {"projects": Project.list_names()}


@app.post("/api/projects")
def create_project(req: CreateReq) -> dict[str, Any]:
    song = Path(req.song_path)
    folder = Path(req.clips_folder)
    if not song.is_file():
        raise HTTPException(400, f"Song file not found: {song}")
    if not folder.is_dir():
        raise HTTPException(400, f"Clips folder not found: {folder}")

    name = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_") or "untitled"
    proj = Project(name=name, song_path=str(song.resolve()))

    # Folder scan: register every video as a known clip. Also seed one default segment per clip
    # so the user can immediately see them in Review; segments can be removed without losing the clip.
    clips = media.scan_clips(folder)
    proj.clips = [c["path"] for c in clips]
    for i, c in enumerate(clips):
        row: Literal["top", "bottom"] = "top" if i % 2 == 0 else "bottom"
        proj.segments.append(Segment(clip_path=c["path"], in_time=0.0, row=row))

    proj.save()
    return {"name": proj.name, "n_clips": len(clips)}


@app.get("/api/projects/{name}")
def get_project(name: str) -> dict[str, Any]:
    try:
        proj = Project.load(name)
    except FileNotFoundError as e:
        raise HTTPException(404, "project not found") from e
    # Migrate older projects that predate the `clips` field: derive from existing segments.
    if not proj.clips and proj.segments:
        seen: set[str] = set()
        derived: list[str] = []
        for seg in proj.segments:
            if seg.clip_path not in seen:
                seen.add(seg.clip_path)
                derived.append(seg.clip_path)
        proj.clips = derived
        proj.save()
    # Migrate older projects where segments rely on auto-alternation (row=None) into explicit rows,
    # so the new 3-column Arrange UI can show + drag-drop them unambiguously.
    if any(s.row is None for s in proj.segments):
        auto_idx = 0
        for s in proj.segments:
            if s.row is None:
                s.row = "top" if auto_idx % 2 == 0 else "bottom"
                auto_idx += 1
        proj.save()
    return proj.model_dump()


@app.put("/api/projects/{name}")
def save_project(name: str, payload: dict[str, Any]) -> dict[str, bool]:
    payload["name"] = name
    proj = Project.model_validate(payload)
    proj.save()
    return {"ok": True}


@app.delete("/api/projects/{name}")
def delete_project(name: str) -> dict[str, bool]:
    p = PROJECTS_DIR / f"{name}.json"
    if p.exists():
        p.unlink()
    return {"ok": True}


# -------------------- analysis endpoints --------------------


@app.post("/api/projects/{name}/detect_beats")
def detect_beats(name: str) -> dict[str, Any]:
    proj = Project.load(name)
    prev_start_t = proj.beat_times[proj.start_beat_index] if proj.beat_times else 0.0
    info = beats.detect(proj.song_path)
    proj.bpm = info["bpm"]
    bt: list[float] = list(info["beat_times"])
    proj.duration = info["duration"]
    # Librosa often stops detecting before the actual end (and may skip the very start). Extend
    # the grid out to song duration using the average of the last few detected intervals so the
    # Beats screen and render have something to lock to all the way through.
    if len(bt) >= 2:
        tail_n = min(8, len(bt) - 1)
        tail_intervals = [bt[i + 1] - bt[i] for i in range(len(bt) - 1 - tail_n, len(bt) - 1)]
        period = sum(tail_intervals) / len(tail_intervals)
    elif proj.bpm > 0:
        period = 60.0 / proj.bpm
    else:
        period = 0.0
    if period > 0 and bt:
        t = bt[-1] + period
        while t < proj.duration:
            bt.append(t)
            t += period
    proj.beat_times = bt
    if proj.beat_times:
        proj.start_beat_index = min(
            range(len(proj.beat_times)),
            key=lambda i: abs(proj.beat_times[i] - prev_start_t),
        )
    else:
        proj.start_beat_index = 0
    proj.save()
    return {"bpm": proj.bpm, "n_beats": len(proj.beat_times), "duration": proj.duration}


@app.get("/api/projects/{name}/clip_info")
def clip_info(name: str, path: str) -> dict[str, float | int]:
    """Return ffprobe info for a single clip in a project."""
    proj = Project.load(name)
    allowed = {seg.clip_path for seg in proj.segments}
    if path not in allowed:
        raise HTTPException(403, "clip not in project")
    return media.video_info(path)


# -------------------- media streaming --------------------


def _is_allowed_media(proj: Project, path: str) -> bool:
    if path == proj.song_path:
        return True
    if path in proj.clips:
        return True
    return any(seg.clip_path == path for seg in proj.segments)


@app.get("/media/{name}")
def stream_media(name: str, path: str, request: Request) -> Response:
    """Stream a project-registered media file with HTTP range support."""
    try:
        proj = Project.load(name)
    except FileNotFoundError as e:
        raise HTTPException(404, "project not found") from e
    if not _is_allowed_media(proj, path):
        raise HTTPException(403, "media not registered to project")
    fp = Path(path)
    if not fp.is_file():
        raise HTTPException(404, "file not found")

    file_size = fp.stat().st_size
    range_header = request.headers.get("range")
    suffix = fp.suffix.lower()
    media_type = {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
        ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
    }.get(suffix, "application/octet-stream")

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not m:
            raise HTTPException(400, "bad range header")
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        def stream() -> Iterator[bytes]:
            with fp.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": media_type,
        }
        return StreamingResponse(stream(), status_code=206, headers=headers)

    return FileResponse(fp, media_type=media_type)


# -------------------- render --------------------


_render_jobs: dict[str, dict[str, Any]] = {}
_render_tasks: set[asyncio.Task[None]] = set()


@app.post("/api/projects/{name}/render")
async def start_render(name: str) -> dict[str, str]:
    proj = Project.load(name)
    job_id = f"{name}-{len(_render_jobs)}"
    _render_jobs[job_id] = {"status": "running", "progress": 0.0, "log": [], "output": None}

    async def _run() -> None:
        try:
            out = await render.render_project(proj, _render_jobs[job_id])
            _render_jobs[job_id]["output"] = str(out)
            _render_jobs[job_id]["status"] = "done"
        except Exception as e:  # noqa: BLE001 — top-level task boundary; surface any failure to the UI
            _render_jobs[job_id]["status"] = "error"
            _render_jobs[job_id]["log"].append(f"ERROR: {e}")

    task = asyncio.create_task(_run())
    _render_tasks.add(task)
    task.add_done_callback(_render_tasks.discard)
    return {"job_id": job_id}


@app.get("/api/render/{job_id}")
def render_status(job_id: str) -> dict[str, Any]:
    if job_id not in _render_jobs:
        raise HTTPException(404, "job not found")
    return _render_jobs[job_id]


@app.get("/api/output/{filename}")
def get_output(filename: str) -> FileResponse:
    fp = OUTPUT_DIR / filename
    if not fp.is_file():
        raise HTTPException(404)
    return FileResponse(fp)


# -------------------- static UI --------------------


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
