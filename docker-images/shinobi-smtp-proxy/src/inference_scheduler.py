"""Priority-based inference scheduler for GPU access.

All YOLO inference goes through a single worker that services a priority queue.
Motion events (priority 0) always run before baseline scans (priority 1),
guaranteeing worst-case motion latency of ~one inference cycle (~310ms)
instead of being blocked behind a full 9-camera baseline sweep (~2.8s).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from object_detector import Detection, ObjectDetector

log = logging.getLogger("smtp-proxy")

# Priority levels (lower = higher priority)
PRIORITY_MOTION = 0
PRIORITY_BASELINE = 1


@dataclass(order=True)
class _InferRequest:
    priority: int
    seq: int  # tiebreaker for FIFO within same priority
    image_data: bytes = field(compare=False)
    confidence_override: float | None = field(default=None, compare=False)
    future: asyncio.Future = field(compare=False, default=None)
    camera_id: str = field(default="", compare=False)


class InferenceScheduler:
    """Serializes all GPU inference through a priority queue.

    Usage:
        scheduler = InferenceScheduler(detector)
        await scheduler.start()

        # Motion (high priority):
        dets = await scheduler.infer(image, priority=PRIORITY_MOTION)

        # Baseline (low priority, cancellable):
        dets = await scheduler.infer(image, priority=PRIORITY_BASELINE)
    """

    def __init__(self, detector: ObjectDetector) -> None:
        self.detector = detector
        self._queue: asyncio.PriorityQueue[_InferRequest] = asyncio.PriorityQueue()
        self._seq = 0
        self._worker_task: asyncio.Task | None = None
        # Track in-flight baseline requests so motion can cancel queued ones
        self._pending_baseline: dict[str, _InferRequest] = {}

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def infer(
        self,
        image_data: bytes,
        priority: int = PRIORITY_MOTION,
        confidence_override: float | None = None,
        camera_id: str = "",
    ) -> list[Detection]:
        """Submit inference request and await result.

        Args:
            image_data: JPEG bytes
            priority: PRIORITY_MOTION (0) or PRIORITY_BASELINE (1)
            confidence_override: Override confidence threshold
            camera_id: Camera ID (used for baseline preemption tracking)

        Returns:
            List of Detection objects
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        self._seq += 1
        req = _InferRequest(
            priority=priority,
            seq=self._seq,
            image_data=image_data,
            confidence_override=confidence_override,
            future=future,
            camera_id=camera_id,
        )

        # If this is a motion request and there's a queued baseline for the same
        # camera, cancel the baseline request — it would just absorb our transient
        # object into the baseline anyway.
        if priority == PRIORITY_MOTION and camera_id:
            pending = self._pending_baseline.pop(camera_id, None)
            if pending and pending.future and not pending.future.done():
                pending.future.set_result([])
                log.debug("Preempted queued baseline for %s", camera_id)

        # Track baseline requests for potential preemption
        if priority == PRIORITY_BASELINE and camera_id:
            self._pending_baseline[camera_id] = req

        await self._queue.put(req)
        return await future

    async def _worker(self) -> None:
        """Single worker coroutine that processes inference requests by priority."""
        while True:
            req = await self._queue.get()

            # Skip if future was already cancelled/resolved (preempted baseline)
            if req.future.done():
                self._queue.task_done()
                continue

            # Clean up baseline tracking
            if req.priority == PRIORITY_BASELINE and req.camera_id:
                self._pending_baseline.pop(req.camera_id, None)

            try:
                t0 = time.monotonic()
                detections = await self.detector.get_detections(
                    req.image_data,
                    confidence_override=req.confidence_override,
                )
                elapsed = (time.monotonic() - t0) * 1000
                if not req.future.done():
                    req.future.set_result(detections)
                log.debug(
                    "Inference done: priority=%d camera=%s %.0fms %d dets",
                    req.priority, req.camera_id, elapsed, len(detections),
                )
            except Exception as exc:
                if not req.future.done():
                    req.future.set_exception(exc)
            finally:
                self._queue.task_done()
