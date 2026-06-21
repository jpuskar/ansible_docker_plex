from __future__ import annotations

import asyncio
import logging
import queue
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from alert_dispatcher import AlertDispatcher
from baseline_service import BaselineService
from baseline_tracker import BaselineTracker
from camera_runtime import CameraState, CameraStates
from frame_selector import BestFrameSelector
from inference_scheduler import InferenceScheduler
import metrics as m
from motion_processor import MotionEventProcessor
from pipeline import (
    CooldownFilter,
    DetectionPipeline,
    EdgeChangeFilter,
    MinAreaFilter,
    NoveltyFilter,
    ZoneFilter,
)
from proxy_types.camera import CameraConfig
from proxy_types.motion import MotionEvent
from rtsp_reader import (
    MOTION_MIN_AREA,
    MOTION_THRESHOLD,
    RTSP_SUBSTREAM,
    CameraBuffer,
    RTSPReader,
)

if TYPE_CHECKING:
    from discord_notifier import DiscordNotifier
    from object_detector import ObjectDetector
    from shinobi_notifier import ShinobiNotifier

log = logging.getLogger("smtp-proxy")


class BaselineManager:
    """Coordinates camera readers, baseline refreshes, and motion handling.

    One BaselineManager owns all configured cameras. Per-camera mutable state is
    stored in ``self.cameras``; shared collaborators are event-loop-owned unless
    their class says otherwise.
    """

    def __init__(
        self,
        camera_configs: Sequence[CameraConfig],
        username: str,
        password: str,
        detector: ObjectDetector,
        buffer_seconds: int = 10,
        baseline_interval: int = 60,
        position_tolerance: float = 0.15,
        discord_notifier: DiscordNotifier | None = None,
        motion_detection: bool = True,
        motion_threshold: int = MOTION_THRESHOLD,
        motion_min_area: int = MOTION_MIN_AREA,
        min_detection_area: float = 0.003,
        shinobi_notifier: ShinobiNotifier | None = None,
        baseline_add_threshold: int = 3,
        baseline_verify_confidence: float = 0.15,
        min_motion_novelty: float = 0.05,
    ) -> None:
        self.username = username
        self.password = password
        self.detector = detector
        self.buffer_seconds = buffer_seconds
        self.baseline_interval = baseline_interval
        self.position_tolerance = position_tolerance
        self.discord_notifier = discord_notifier
        self.shinobi_notifier = shinobi_notifier
        self.motion_detection = motion_detection

        self._log_detection_zones(camera_configs)
        log.info(
            "Baseline hysteresis: promote after %d cycles (%ds), verify at %.0f%% confidence",
            baseline_add_threshold,
            baseline_add_threshold * baseline_interval,
            baseline_verify_confidence * 100,
        )

        self._baseline_task: asyncio.Task[None] | None = None
        self._metrics_task: asyncio.Task[None] | None = None
        self._motion_task: asyncio.Task[None] | None = None
        self._metrics_interval = 10
        self._motion_grace_period = 10.0

        self._scheduler = InferenceScheduler(detector)
        self._motion_queue: queue.Queue[MotionEvent] | None = (
            queue.Queue(maxsize=50) if motion_detection else None
        )

        maxlen = max(buffer_seconds * 2, 10)
        self.cameras = self._build_camera_states(
            camera_configs=camera_configs,
            username=username,
            password=password,
            maxlen=maxlen,
            motion_detection=motion_detection,
            motion_threshold=motion_threshold,
            motion_min_area=motion_min_area,
            baseline_add_threshold=baseline_add_threshold,
            position_tolerance=position_tolerance,
        )
        self._wire_services(
            min_detection_area=min_detection_area,
            min_motion_novelty=min_motion_novelty,
            baseline_verify_confidence=baseline_verify_confidence,
        )

    async def start(self) -> None:
        for camera in self.cameras.values():
            camera.reader.start()
        log.info(
            "Camera manager started: %d cameras, %ds buffer, %ds baseline, motion=%s",
            len(self.cameras),
            self.buffer_seconds,
            self.baseline_interval,
            self.motion_detection,
        )
        await self._scheduler.start()
        self._baseline_task = asyncio.create_task(self._baseline_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        if self.motion_detection:
            self._motion_task = asyncio.create_task(self._motion_loop())

    async def stop(self) -> None:
        await self._scheduler.stop()
        for task in (
            self._baseline_task,
            self._metrics_task,
            self._motion_task,
        ):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for camera in self.cameras.values():
            camera.reader.stop()
        if self.discord_notifier:
            await self.discord_notifier.close()
        if self.shinobi_notifier:
            await self.shinobi_notifier.close()

    def _log_detection_zones(
        self,
        camera_configs: Sequence[CameraConfig],
    ) -> None:
        zone_camera_ids = [config.id for config in camera_configs if config.zones]
        if zone_camera_ids:
            log.info(
                "Detection zones configured for: %s",
                ", ".join(zone_camera_ids),
            )

    def _build_camera_states(
        self,
        camera_configs: Sequence[CameraConfig],
        username: str,
        password: str,
        maxlen: int,
        motion_detection: bool,
        motion_threshold: int,
        motion_min_area: int,
        baseline_add_threshold: int,
        position_tolerance: float,
    ) -> CameraStates:
        cameras: CameraStates = {}
        for config in camera_configs:
            buffer = CameraBuffer(maxlen=maxlen)
            tracker = BaselineTracker(
                add_threshold=baseline_add_threshold,
                tolerance=position_tolerance,
            )
            url = RTSP_SUBSTREAM.format(user=username, passwd=password, ip=config.host)
            reader = RTSPReader(
                camera_id=config.id,
                rtsp_url=url,
                buf=buffer,
                motion_queue=self._motion_queue if motion_detection else None,
                motion_threshold=motion_threshold,
                motion_min_area=motion_min_area,
                motion_zone_polygons=config.zones,
            )
            cameras[config.id] = CameraState(
                config=config,
                buffer=buffer,
                tracker=tracker,
                reader=reader,
            )
        return cameras

    def _wire_services(
        self,
        min_detection_area: float,
        min_motion_novelty: float,
        baseline_verify_confidence: float,
    ) -> None:
        scene_change_threshold = 0.15
        alert_cooldown = 300.0
        followup_interval = 3.0
        followup_duration = 15.0

        motion_pre = DetectionPipeline([
            ZoneFilter(),
            NoveltyFilter(min_motion_novelty),
            MinAreaFilter(min_detection_area),
        ])
        motion_post = DetectionPipeline([
            EdgeChangeFilter(scene_change_threshold),
            CooldownFilter(self.position_tolerance),
        ])
        followup_pre = DetectionPipeline([
            ZoneFilter(),
            MinAreaFilter(min_detection_area),
        ])
        followup_post = DetectionPipeline([
            CooldownFilter(self.position_tolerance),
        ])

        alert_dispatcher = AlertDispatcher(self.discord_notifier, self.shinobi_notifier)
        baseline_service = BaselineService(
            cameras=self.cameras,
            scheduler=self._scheduler,
            detector=self.detector,
            position_tolerance=self.position_tolerance,
            verify_confidence=baseline_verify_confidence,
            alert_cooldown=alert_cooldown,
        )
        frame_selector = BestFrameSelector(
            cameras=self.cameras,
            scheduler=self._scheduler,
            position_tolerance=self.position_tolerance,
        )
        self._baseline_service = baseline_service
        self._motion_processor = MotionEventProcessor(
            cameras=self.cameras,
            scheduler=self._scheduler,
            baseline_service=baseline_service,
            frame_selector=frame_selector,
            alert_dispatcher=alert_dispatcher,
            motion_pre=motion_pre,
            motion_post=motion_post,
            followup_pre=followup_pre,
            followup_post=followup_post,
            followup_interval=followup_interval,
            followup_duration=followup_duration,
        )

    async def _metrics_loop(self) -> None:
        while True:
            await asyncio.sleep(self._metrics_interval)
            self._collect_rtsp_metrics()
            self._log_camera_metrics()
            self._reset_camera_metrics()

    def _collect_rtsp_metrics(self) -> None:
        for camera in self.cameras.values():
            metrics = camera.reader.snapshot_metrics()
            camera.snap_ok = metrics.frames_ok
            camera.snap_fail = metrics.frames_fail
            camera.snap_bytes = metrics.bytes_total
            camera.motion_events = metrics.motion_events

    def _log_camera_metrics(self) -> None:
        ok_cams = [c.config.id for c in self.cameras.values() if c.snap_ok > 0]
        fail_cams = [
            c.config.id for c in self.cameras.values()
            if c.snap_fail > 0 and c.snap_ok == 0
        ]
        partial = [
            c.config.id for c in self.cameras.values()
            if c.snap_fail > 0 and c.snap_ok > 0
        ]

        parts = [f"{len(ok_cams)}/{len(self.cameras)} ok"]
        if ok_cams:
            total_bytes = sum(self.cameras[c].snap_bytes for c in ok_cams)
            total_snaps = sum(self.cameras[c].snap_ok for c in ok_cams)
            avg_kb = (total_bytes / total_snaps / 1024) if total_snaps else 0
            parts.append(f"avg {avg_kb:.0f}KB/frame")
        if fail_cams:
            parts.append(f"down: {', '.join(sorted(fail_cams))}")
        if partial:
            parts.append(f"flaky: {', '.join(sorted(partial))}")
        if self.motion_detection:
            motion_total = sum(c.motion_events for c in self.cameras.values())
            parts.append(f"motion: {motion_total}")
        log.info("Frames [%ds]: %s", self._metrics_interval, " | ".join(parts))

    def _reset_camera_metrics(self) -> None:
        for camera in self.cameras.values():
            camera.snap_ok = 0
            camera.snap_fail = 0
            camera.snap_bytes = 0
            camera.motion_events = 0

    async def _motion_loop(self) -> None:
        if self._motion_queue is None:
            return
        loop = asyncio.get_event_loop()
        while True:
            try:
                event = await loop.run_in_executor(
                    None,
                    self._motion_queue.get,
                    True,
                    1.0,
                )
            except Exception:
                continue
            await self._motion_processor.process_motion_event(event)

    async def _baseline_loop(self) -> None:
        await asyncio.sleep(15)
        while True:
            for camera_id, camera in self.cameras.items():
                if self._recent_motion(camera.last_motion_time):
                    self._log_baseline_skip(camera_id, camera.last_motion_time)
                    continue
                try:
                    await self._baseline_service.update(camera_id)
                except Exception:
                    log.warning(
                        "Baseline update failed for %s",
                        camera_id,
                        exc_info=True,
                    )
            await asyncio.sleep(self.baseline_interval)

    def _recent_motion(self, last_motion: float) -> bool:
        return time.monotonic() - last_motion < self._motion_grace_period

    def _log_baseline_skip(self, camera_id: str, last_motion: float) -> None:
        log.debug(
            "Baseline skipped for %s: motion active %.1fs ago",
            camera_id,
            time.monotonic() - last_motion,
        )
        m.baseline_skipped_total.labels(camera=camera_id).inc()

    async def _update_baseline(self, camera_id: str) -> None:
        await self._baseline_service.update(camera_id)
