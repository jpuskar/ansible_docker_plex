from __future__ import annotations

from typing import NamedTuple, TypeAlias

from proxy_types.camera import CameraId


class MotionRect(NamedTuple):
    """Normalized motion region: left, top, width, height, novelty."""

    x: float
    y: float
    w: float
    h: float
    novelty: float


MotionRects: TypeAlias = list[MotionRect]


class MotionEvent(NamedTuple):
    """Motion-triggered JPEG frame and its detected motion regions."""

    camera_id: CameraId
    jpeg_bytes: bytes
    motion_rects: MotionRects
