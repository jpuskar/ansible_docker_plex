from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from baseline_tracker import BaselineTracker
from object_detector import Detection
from proxy_types.alerts import RecentAlerts
from proxy_types.camera import CameraConfig, CameraId, GrayFrame
from rtsp_reader import CameraBuffer, RTSPReader


@dataclass
class CameraState:
    """Runtime state owned by one configured camera.

    RTSPReader mutates its own thread-local internals and pushes frames into the
    thread-safe buffer. The remaining fields are owned by the asyncio event loop.
    """

    config: CameraConfig
    buffer: CameraBuffer
    tracker: BaselineTracker
    reader: RTSPReader
    baseline: list[Detection] = field(default_factory=list)
    baseline_initialized: bool = False
    reference_frame: GrayFrame | None = None
    recent_alerts: RecentAlerts = field(default_factory=list)
    last_motion_time: float = 0.0
    followup_active: bool = False
    snap_ok: int = 0
    snap_fail: int = 0
    snap_bytes: int = 0
    motion_events: int = 0


CameraStates: TypeAlias = dict[CameraId, CameraState]
