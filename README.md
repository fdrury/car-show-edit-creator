# Car Show Video Editor

Beat-synced editor for stacked-portrait car show videos.

## Quickstart

```powershell
.\.venv\Scripts\python.exe -m car_show_editor
```

Then open http://localhost:8765 in a browser.

## Workflow

1. **Setup** — choose a song file and a folder of video clips
2. **Beats** — confirm/override BPM, pick which beat is "beat 1" of the video
3. **Review** — for each clip: mark in-point, length in beats (2/4/6/8...), rotate 180°, reverse
4. **Arrange + Render** — drag-reorder segments, render to MP4 + project.json

## Assumptions

- Source clips are 60fps landscape; output is 1080×1920 @ 60fps with all clips slowed to 0.5x
- Top and bottom rows are offset by 1 beat (default), so visual cuts alternate between rows
- Center-crop horizontally to fit each clip into a half-portrait slot
