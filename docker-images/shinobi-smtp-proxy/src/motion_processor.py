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
    from camera_runtime import CameraState, CameraStates
    from frame_selector import BestFrameSelector
    from pipeline import DetectionPipeline

log = logging.getLogger("smtp-proxy")


class MotionEventProcessor:
    """Processes queued motion events and post-alert follow-up scans.

    RTSP reader threads only enqueue MotionEvent objects. This processor and the
    collaborators it mutates all run on the asyncio event loop.
    """

    def __init__(
        self,
        cameras: CameraStates,
        scheduler: InferenceScheduler,
        baseline_service: BaselineService,
        frame_selector: BestFrameSelector,
        alert_dispatcher: AlertDispatcher,
        motion_pre: DetectionPipeline,
        motion_post: DetectionPipeline,
        followup_pre: DetectionPipeline,
        followup_post: DetectionPipeline,
        followup_interval: float,
        followup_duration: float,
    ) -> None:
        self.cameras = cameras
        self.scheduler = scheduler
        self.baseline_service = baseline_service
        self.frame_selector = frame_selector
        self.alert_dispatcher = alert_dispatcher
        self.motion_pre = motion_pre
        self.motion_post = motion_post
        self.followup_pre = followup_pre
        self.followup_post = followup_post
        self.followup_interval = followup_interval
        self.followup_duration = followup_duration

    async def process_motion_event(self, event: MotionEvent) -> None:
        try:
            camera = self.cameras[event.camera_id]
            camera.last_motion_time = time.monotonic()
            detections = await self._detect_motion_objects(event)
            if not detections:
                return

            all_yolo_detections = list(detections)
            ctx = self._motion_context(event, camera)
            new, baseline = self._filter_motion_detections(detections, ctx)
            if not new:
                return

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
                f"Motion: {names}",
                event.jpeg_bytes,
                all_yolo_detections,
                new,
            )
            self.baseline_service.record_alert(event.camera_id, new)
            self._start_follow_up_if_idle(event.camera_id)
        except Exception:
            log.warning("Motion processing error for %s", event.camera_id, exc_info=True)

    async def follow_up_scan(self, camera_id: str) -> None:
        camera = self.cameras[camera_id]
        if camera.followup_active:
            return
        camera.followup_active = True
        deadline = time.monotonic() + self.followup_duration
        scan_num = 0
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(self.followup_interval)
                if time.monotonic() >= deadline:
                    break
                scan_num += 1
                extended = await self._run_follow_up_scan(camera_id, scan_num)
                if extended:
                    deadline = time.monotonic() + self.followup_duration
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
            recent_alerts=self.baseline_service.active_alerts(event.camera_id),
        )

    def _filter_motion_detections(
        self,
        detections: list[Detection],
        ctx: FilterContext,
    ) -> tuple[list[Detection], list[Detection]]:
        detections = self.motion_pre.run(detections, ctx)
        if not detections:
            return [], []

        comparison = self.baseline_service.compare(
            detections,
            ctx.camera_id,
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

        new = self.motion_post.run(new, ctx)
        if not new:
            log.debug(
                "Motion %s: no new objects after confirmation filters",
                ctx.camera_id,
            )
        return new, baseline

    async def _run_follow_up_scan(self, camera_id: str, scan_num: int) -> bool:
        camera = self.cameras[camera_id]
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
        new = self._filter_follow_up_detections(detections, ctx, scan_num)
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
            f"FollowUp: {names}",
            jpeg_bytes,
            detections,
            new,
        )
        self.baseline_service.record_alert(camera_id, new)
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
            recent_alerts=self.baseline_service.active_alerts(camera_id),
        )

    def _filter_follow_up_detections(
        self,
        detections: list[Detection],
        ctx: FilterContext,
        scan_num: int,
    ) -> list[Detection]:
        detections = self.followup_pre.run(detections, ctx)
        if not detections:
            return []

        comparison = self.baseline_service.compare(
            detections,
            ctx.camera_id,
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

        new = self.followup_post.run(new, ctx)
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

    def _start_follow_up_if_idle(self, camera_id: str) -> None:
        if not self.cameras[camera_id].followup_active:
            asyncio.create_task(self.follow_up_scan(camera_id))
