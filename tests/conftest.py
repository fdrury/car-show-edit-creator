"""Shared pytest fixtures."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from car_show_editor.project import Segment


def seg(**kwargs: object) -> Segment:
    """Build a Segment with sensible defaults, override fields by keyword."""
    defaults: dict[str, object] = {"clip_path": "/fake/clip.mp4", "in_time": 0.0, "length_beats": 4}
    defaults.update(kwargs)
    return Segment(**defaults)   # type: ignore[arg-type]


@pytest.fixture
def make_seg() -> Callable[..., Segment]:
    return seg
