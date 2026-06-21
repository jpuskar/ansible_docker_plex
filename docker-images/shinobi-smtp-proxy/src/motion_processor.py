from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from inference_scheduler import PRIORITY_MOTION, InferenceScheduler
import metrics as m
from object_detector import Detection
from proxy_types.motion import MotionEvent
from proxy_types.pipeline import FilterContext
from scene_compare import annotate_frame

if TYPE_CHECKING:
    from alert_dispatcher import AlertDispatcher
    from baseline_service import BaselineService
    from camera_runtime import CameraState
    from frame_selector import BestFrameSelector

log = logging.getLogger("smtp-proxy")


class MotionEventProcessor:
    """Processes queued motion events and post-alert follow-up scans.

    RTSP reader threads only enqueue MotionEvent objects. This processor and the
    collaborators it mutates all run on the asyncio event loop.
    """

    def __init__(
        self,
        scheduler: InferenceScheduler,
        baseline_service: BaselineService,
        frame_selector: BestFrameSelector,
        alert_dispatcher: AlertDispatcher,
    ) -> None:
        self.scheduler = scheduler
        self.baseline_service = baseline_service
        self.frame_selector = frame_selector
        self.alert_dispatcher = alert_dispatcher

    async def process_motion_event(
        self,
        event: MotionEvent,
        camera: CameraState,
    ) -> bool:
        try:
            camera.last_motion_time = time.monotonic()
            detections = await self._detect_motion_objects(event)
            if not detections:
                return False

            all_yolo_detections = list(detections)
            ctx = self._motion_context(event, camera)
            new, baseline = self._filter_motion_detections(detections, ctx, camera)
            if not new:
                return False

            names = ", ".join(sorted(set(d.name for d in new)))
            log.info(
                "Motion %s: new objects: %s (det=%s, base=%s)",
                event.camera_id,
                names,
                [repr(d) for d in detections],
                [repr(b) for b in baseline],
            )
            await self._send_detected_alert(
                event.camera_id,
                camera,
                f"Motion: {names}",
                event.jpeg_bytes,
                all_yolo_detections,
                new,
            )
            self.baseline_service.record_alert(camera, new)
            return True
        except Exception:
            log.warning(
                "Motion processing error for %s", event.camera_id, exc_info=True
            )
            return False

    async def follow_up_scan(self, camera_id: str, camera: CameraState) -> None:
        if camera.followup_active:
            return
        camera.followup_active = True
        tuning = camera.config.tuning
        deadline = time.monotonic() + tuning.followup_duration
        scan_num = 0
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(tuning.followup_interval)
                if time.monotonic() >= deadline:
                    break
                scan_num += 1
                extended = await self._run_follow_up_scan(
                    camera_id,
                    camera,
                    scan_num,
                )
                if extended:
                    deadline = time.monotonic() + tuning.followup_duration
        except Exception:
            log.warning("FollowUp scan error for %s", camera_id, exc_info=True)
        finally:
            camera.followup_active = False
            log.debug("FollowUp %s: finished after %d scans", camera_id, scan_num)

    async def _detect_motion_objects(self, event: MotionEvent) -> list[Detection]:
        detections = await self.scheduler.infer(
            event.jpeg_bytes,
            priority=PRIORITY_MOTION,
            camera_id=event.camera_id,
        )
        if not detections:
            log.info("Motion %s: YOLO returned 0 detections", event.camera_id)
            m.motion_filtered_total.labels(
                camera=event.camera_id,
                reason="no_detections",
            ).inc()
            return []

        for d in detections:
            m.detections_total.labels(
                camera=event.camera_id,
                class_name=d.name,
            ).inc()
        log.info(
            "Motion %s: YOLO found %d detections: %s",
            event.camera_id,
            len(detections),
            [
                (
                    d.name,
                    f"{d.conf:.2f}",
                    f"{d.cx:.2f},{d.cy:.2f}",
                    f"{d.w:.3f}x{d.h:.3f}",
                )
                for d in detections
            ],
        )
        return detections

    def _motion_context(
        self,
        event: MotionEvent,
        camera: CameraState,
    ) -> FilterContext:
        return FilterContext(
            camera_id=event.camera_id,
            label="Motion",
            zones=camera.config.zones,
            motion_rects=event.motion_rects,
            jpeg_bytes=event.jpeg_bytes,
            reference_frame=camera.reference_frame,
            recent_alerts=self.baseline_service.active_alerts(camera),
        )

    def _filter_motion_detections(
        self,
        detections: list[Detection],
        ctx: FilterContext,
        camera: CameraState,
    ) -> tuple[list[Detection], list[Detection]]:
        detections = camera.pipelines.motion_pre.run(detections, ctx)
        if not detections:
            return [], []

        comparison = self.baseline_service.compare(
            detections,
            ctx.camera_id,
            camera,
            observe=True,
        )
        new = comparison.new_detections
        baseline = comparison.baseline
        if new is None:
            log.debug(
                "Motion %s: ignoring %d detections (baseline not yet initialized)",
                ctx.camera_id,
                len(detections),
            )
            return [], baseline
        if not new:
            log.debug(
                "Motion %s: all %d detections matched baseline (base=%s)",
                ctx.camera_id,
                len(detections),
                [repr(b) for b in baseline],
            )
            return [], baseline

        new = camera.pipelines.motion_post.run(new, ctx)
        if not new:
            log.debug(
                "Motion %s: no new objects after confirmation filters",
                ctx.camera_id,
            )
        return new, baseline

    async def _run_follow_up_scan(
        self,
        camera_id: str,
        camera: CameraState,
        scan_num: int,
    ) -> bool:
        recent_frames = camera.buffer.get_recent(seconds=1.0)
        if not recent_frames:
            return False
        jpeg_bytes = recent_frames[-1]

        detections = await self.scheduler.infer(
            jpeg_bytes,
            priority=PRIORITY_MOTION,
            camera_id=camera_id,
        )
        if not detections:
            log.debug("FollowUp %s scan %d: no detections", camera_id, scan_num)
            return False

        ctx = self._follow_up_context(camera_id, camera, jpeg_bytes)
        new = self._filter_follow_up_detections(detections, ctx, camera, scan_num)
        if not new:
            return False

        names = ", ".join(sorted(set(d.name for d in new)))
        log.info(
            "FollowUp %s scan %d: new arrivals: %s",
            camera_id,
            scan_num,
            names,
        )
        await self._send_detected_alert(
            camera_id,
            camera,
            f"FollowUp: {names}",
            jpeg_bytes,
            detections,
            new,
        )
        self.baseline_service.record_alert(camera, new)
        m.alerts_total.labels(camera=camera_id, destination="followup").inc()
        return True

    def _follow_up_context(
        self,
        camera_id: str,
        camera: CameraState,
        jpeg_bytes: bytes,
    ) -> FilterContext:
        return FilterContext(
            camera_id=camera_id,
            label="FollowUp",
            zones=camera.config.zones,
            jpeg_bytes=jpeg_bytes,
            reference_frame=camera.reference_frame,
            recent_alerts=self.baseline_service.active_alerts(camera),
        )

    def _filter_follow_up_detections(
        self,
        detections: list[Detection],
        ctx: FilterContext,
        camera: CameraState,
        scan_num: int,
    ) -> list[Detection]:
        detections = camera.pipelines.followup_pre.run(detections, ctx)
        if not detections:
            return []

        comparison = self.baseline_service.compare(
            detections,
            ctx.camera_id,
            camera,
            observe=False,
        )
        new = comparison.new_detections
        if not new:
            log.debug(
                "FollowUp %s scan %d: %d detections all matched baseline",
                ctx.camera_id,
                scan_num,
                len(detections),
            )
            return []

        new = camera.pipelines.followup_post.run(new, ctx)
        if not new:
            log.debug(
                "FollowUp %s scan %d: new detections suppressed by cooldown",
                ctx.camera_id,
                scan_num,
            )
        return new

    async def _send_detected_alert(
        self,
        camera_id: str,
        camera: CameraState,
        description: str,
        jpeg_bytes: bytes,
        all_detections: list[Detection],
        new_detections: list[Detection],
    ) -> None:
        best_frame = await self.frame_selector.select(
            camera_id,
            jpeg_bytes,
            all_detections,
            new_detections,
            position_tolerance=camera.config.tuning.position_tolerance,
        )
        annotated = annotate_frame(
            jpeg_bytes=best_frame.jpeg_bytes,
            detections=best_frame.all_detections,
            new_detections=best_frame.new_detections,
        )
        await self.alert_dispatcher.send(
            camera_id=camera_id,
            description=description,
            jpeg_bytes=annotated,
            detections=best_frame.new_detections,
        )
