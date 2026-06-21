from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from proxy_types.alerts import RecentAlerts
from proxy_types.camera import ReferenceFrames
from proxy_types.motion import MotionRects

if TYPE_CHECKING:
    from object_detector import Detection


class BaselineComparison(NamedTuple):
    """Detections after baseline diff, plus the baseline used for comparison."""

    new_detections: list["Detection"] | None
    baseline: list["Detection"]


class BestFrameSelection(NamedTuple):
    """Best JPEG frame and the detection sets to draw/alert for it."""

    jpeg_bytes: bytes
    all_detections: list["Detection"]
    new_detections: list["Detection"]


@dataclass
class FilterContext:
    """Per-event data any filter stage might need."""

    camera_id: str
    label: str = "Motion"
    motion_rects: MotionRects = field(default_factory=list)
    jpeg_bytes: bytes = b""
    reference_frames: ReferenceFrames = field(default_factory=dict)
    recent_alerts: RecentAlerts = field(default_factory=list)
