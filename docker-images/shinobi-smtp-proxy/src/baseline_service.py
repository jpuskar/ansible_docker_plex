from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

from inference_scheduler import PRIORITY_BASELINE, InferenceScheduler
import metrics as m
from object_detector import Detection
from proxy_types.alerts import RecentAlert, RecentAlerts
from proxy_types.pipeline import BaselineComparison

if TYPE_CHECKING:
    from camera_runtime import CameraState
    from object_detector import ObjectDetector

log = logging.getLogger("smtp-proxy")


class BaselineService:
    """Owns per-camera baseline tracking and alert cooldown history.

    All methods are called from the asyncio event loop. BaselineTracker itself is
    per-camera and is not touched by RTSP reader threads.
    """

    def __init__(
        self,
        scheduler: InferenceScheduler,
        detector: ObjectDetector,
    ) -> None:
        self.scheduler = scheduler
        self.detector = detector

    def active_alerts(self, camera: CameraState) -> RecentAlerts:
        """Return this camera's recent alerts with expired entries purged."""
        now = time.monotonic()
        return [
            alert
            for alert in camera.recent_alerts
            if now - alert.timestamp < camera.config.tuning.alert_cooldown_seconds
        ]

    def record_alert(self, camera: CameraState, detections: list[Detection]) -> None:
        """Record alerted detections for cooldown, dropping expired entries."""
        recent = self.active_alerts(camera)
        now = time.monotonic()
        recent.extend(RecentAlert(timestamp=now, detection=d) for d in detections)
        camera.recent_alerts = recent

    def compare(
        self,
        detections: list[Detection],
        camera_id: str,
        camera: CameraState,
        observe: bool,
    ) -> BaselineComparison:
        """Diff detections against the camera's baseline.

        ``new_detections`` is ``None`` to signal "baseline not yet initialized",
        which callers use to suppress alerts until warmup completes.
        """
        baseline = camera.baseline
        tracker = camera.tracker

        if observe:
            obs_msgs = tracker.observe(detections)
            for msg in obs_msgs:
                log.info("Baseline %s: %s (via motion observe)", camera_id, msg)
            if obs_msgs:
                baseline = tracker.get_baseline()
                camera.baseline = baseline

        if not baseline:
            if not camera.baseline_initialized:
                return BaselineComparison(new_detections=None, baseline=[])
            if not tracker.is_warm:
                baseline = tracker.get_all_seen()

        new = (
            [
                d
                for d in detections
                if not any(
                    d.is_near(
                        other=b,
                        tolerance=camera.config.tuning.position_tolerance,
                    )
                    for b in baseline
                )
            ]
            if baseline
            else detections
        )
        return BaselineComparison(new_detections=new, baseline=baseline)

    async def update(self, camera_id: str, camera: CameraState) -> None:
        recent = camera.buffer.get_recent(seconds=2)
        total = camera.buffer.total()
        if not recent:
            log.debug(
                "Baseline skipped for %s: no recent frames (0 of %d in buffer)",
                camera_id,
                total,
            )
            return

        jpeg = recent[-1]
        detections = await self.scheduler.infer(
            jpeg,
            priority=PRIORITY_BASELINE,
            confidence_override=self.detector.confidence_threshold,
            camera_id=camera_id,
        )
        self._store_reference_frame(camera, jpeg)

        tracker = camera.tracker
        messages = tracker.update(detections)
        if tracker.has_missed_promoted:
            low_conf_dets = await self.scheduler.infer(
                jpeg,
                priority=PRIORITY_BASELINE,
                confidence_override=camera.config.tuning.baseline_verify_confidence,
                camera_id=camera_id,
            )
            messages += tracker.verify_missed(low_conf_dets)

        for msg in messages:
            log.info("Baseline %s: %s", camera_id, msg)

        baseline = tracker.get_baseline()
        camera.baseline = baseline
        camera.baseline_initialized = True
        m.baseline_objects.labels(camera=camera_id).set(len(baseline))
        if baseline:
            log.info("Baseline %s: %s", camera_id, [repr(d) for d in baseline])
        log.debug(
            "Baseline for %s: cycle %d, %d detections, %d candidates, %d promoted",
            camera_id,
            tracker.cycles,
            len(detections),
            len(tracker.candidates),
            len(baseline),
        )

    def _store_reference_frame(self, camera: CameraState, jpeg: bytes) -> None:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if gray is not None:
            camera.reference_frame = gray
