from __future__ import annotations

import asyncio
import logging
import queue
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

from baseline_tracker import BaselineTracker
from inference_scheduler import PRIORITY_BASELINE, PRIORITY_MOTION, InferenceScheduler
import metrics as m
from object_detector import Detection
from pipeline import (
    CooldownFilter,
    DetectionPipeline,
    EdgeChangeFilter,
    MinAreaFilter,
    NoveltyFilter,
    ZoneFilter,
)
from proxy_types.alerts import RecentAlert, RecentAlerts
from proxy_types.camera import (
    CameraHosts,
    RawZonesByCamera,
    ReferenceFrames,
    ZonesByCamera,
    build_zones_by_camera,
)
from proxy_types.motion import MotionEvent
from proxy_types.pipeline import BaselineComparison, BestFrameSelection, FilterContext
from rtsp_reader import (
    MOTION_MIN_AREA,
    MOTION_THRESHOLD,
    RTSP_SUBSTREAM,
    CameraBuffer,
    RTSPReader,
)
from scene_compare import annotate_frame

if TYPE_CHECKING:
    from discord_notifier import DiscordNotifier
    from object_detector import ObjectDetector
    from shinobi_notifier import ShinobiNotifier

log = logging.getLogger("smtp-proxy")


class BaselineManager:
    """Manages camera frame buffers, periodic baselines, and event analysis.

    Receives frames via a persistent RTSP sub-stream per camera (background
    threads). When motion_detection=True, frame-differencing in each
    RTSPReader thread triggers YOLO analysis + Discord alerts autonomously.
    """

    def __init__(
        self,
        cameras: CameraHosts,
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
        detection_zones: RawZonesByCamera | None = None,
        min_detection_area: float = 0.003,
        shinobi_notifier: ShinobiNotifier | None = None,
        baseline_add_threshold: int = 3,
        baseline_verify_confidence: float = 0.15,
        min_motion_novelty: float = 0.05,
    ) -> None:
        self.cameras: CameraHosts = cameras
        self.username = username
        self.password = password
        self.detector = detector
        self.buffer_seconds = buffer_seconds
        self.baseline_interval = baseline_interval
        self.position_tolerance = position_tolerance
        self.discord_notifier = discord_notifier
        self.shinobi_notifier = shinobi_notifier
        self.motion_detection = motion_detection

        self._zones: ZonesByCamera = build_zones_by_camera(detection_zones)
        if self._zones:
            log.info(
                "Detection zones configured for: %s", ", ".join(self._zones.keys())
            )

        # Hysteresis baseline trackers — one per camera
        self._trackers: dict[str, BaselineTracker] = {
            cam_id: BaselineTracker(
                add_threshold=baseline_add_threshold,
                tolerance=position_tolerance,
            )
            for cam_id in cameras
        }
        self._verify_confidence = baseline_verify_confidence
        log.info(
            "Baseline hysteresis: promote after %d cycles (%ds), verify at %.0f%% confidence",
            baseline_add_threshold,
            baseline_add_threshold * baseline_interval,
            baseline_verify_confidence * 100,
        )

        maxlen = max(buffer_seconds * 2, 10)  # 2fps * buffer_seconds
        self.buffers: dict[str, CameraBuffer] = {
            cam_id: CameraBuffer(maxlen=maxlen) for cam_id in cameras
        }
        self.baselines: dict[str, list[Detection]] = {}
        self._baseline_initialized: set[str] = set()
        self._baseline_task: asyncio.Task[None] | None = None
        self._metrics_task: asyncio.Task[None] | None = None
        self._motion_task: asyncio.Task[None] | None = None

        # Reference frames for visual similarity comparison.
        # Stored during each baseline cycle — a calm frame with no motion.
        self._reference_frames: ReferenceFrames = {}
        self._scene_change_threshold = 0.15  # fraction of current edges that must be new

        # Priority inference scheduler — all GPU access goes through here
        self._scheduler = InferenceScheduler(detector)

        # Motion event queue: (camera_id, jpeg_bytes) from RTSP threads
        self._motion_queue: queue.Queue[MotionEvent] | None = (
            queue.Queue(maxsize=50) if motion_detection else None
        )

        # Track when each camera last had motion, so baseline skips active cameras
        self._last_motion_time: dict[str, float] = {}
        self._motion_grace_period = 10.0  # seconds to suppress baseline after motion

        # Minimum detection bounding box area (fraction of frame, 0.0-1.0)
        # Detections smaller than this are discarded as noise/hallucinations
        self._min_detection_area = min_detection_area

        # Minimum motion novelty score for a detection to be considered
        # genuinely moving (vs environmental: wind, shadows, tree sway)
        self._min_motion_novelty = min_motion_novelty

        # Alert cooldown: suppress repeat alerts for the same object at the
        # same position within this window. Gives observe() time to promote
        # persistent static objects into the baseline.
        self._alert_cooldown = 300.0  # seconds (5 minutes)
        self._recent_alerts: dict[str, RecentAlerts] = {}

        # Follow-up scan: after an alert, periodically re-check the camera
        # for additional arrivals (e.g. kid walking behind parent).
        self._followup_interval = 3.0   # seconds between follow-up scans
        self._followup_duration = 15.0  # total follow-up window after alert
        self._active_followups: set[str] = set()  # cameras with running follow-up tasks

        # Detection filter pipelines. Each path declares exactly which stages
        # it runs, in order — adjust a filter or its placement here in one
        # spot. Baseline diff happens BETWEEN pre and post (it mutates the
        # tracker, so it's a method, not a stage).
        #   motion:   zone → novelty → min-area | baseline | edge → cooldown
        #   followup: zone → min-area           | baseline | cooldown
        # Follow-up skips novelty + edge on purpose: the scene is actively
        # changing, so those would wrongly suppress real new arrivals.
        self._motion_pre = DetectionPipeline([
            ZoneFilter(self._zones),
            NoveltyFilter(self._min_motion_novelty),
            MinAreaFilter(self._min_detection_area),
        ])
        self._motion_post = DetectionPipeline([
            EdgeChangeFilter(self._scene_change_threshold),
            CooldownFilter(self.position_tolerance),
        ])
        self._followup_pre = DetectionPipeline([
            ZoneFilter(self._zones),
            MinAreaFilter(self._min_detection_area),
        ])
        self._followup_post = DetectionPipeline([
            CooldownFilter(self.position_tolerance),
        ])

        # RTSP readers — one persistent sub-stream thread per camera
        self._readers: dict[str, RTSPReader] = {}
        for cam_id, ip in cameras.items():
            url = RTSP_SUBSTREAM.format(user=username, passwd=password, ip=ip)
            self._readers[cam_id] = RTSPReader(
                camera_id=cam_id,
                rtsp_url=url,
                buf=self.buffers[cam_id],
                motion_queue=self._motion_queue if motion_detection else None,
                motion_threshold=motion_threshold,
                motion_min_area=motion_min_area,
                motion_zone_polygons=self._zones.get(cam_id),
            )

        # Per-camera frame counters (reset each metrics interval)
        self._snap_ok = {cam_id: 0 for cam_id in cameras}
        self._snap_fail = {cam_id: 0 for cam_id in cameras}
        self._snap_bytes = {cam_id: 0 for cam_id in cameras}
        self._metrics_interval = 10

    async def start(self) -> None:
        for reader in self._readers.values():
            reader.start()
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
        for reader in self._readers.values():
            reader.stop()
        if self.discord_notifier:
            await self.discord_notifier.close()
        if self.shinobi_notifier:
            await self.shinobi_notifier.close()

    # ================================================================
    # Metrics loop
    # ================================================================

    async def _metrics_loop(self) -> None:
        while True:
            await asyncio.sleep(self._metrics_interval)

            self._collect_rtsp_metrics()

            ok_cams = [c for c, n in self._snap_ok.items() if n > 0]
            fail_cams = [
                c for c, n in self._snap_fail.items() if n > 0 and self._snap_ok[c] == 0
            ]
            partial = [
                c for c, n in self._snap_fail.items() if n > 0 and self._snap_ok[c] > 0
            ]

            parts = [f"{len(ok_cams)}/{len(self.cameras)} ok"]
            if ok_cams:
                total_bytes = sum(self._snap_bytes[c] for c in ok_cams)
                total_snaps = sum(self._snap_ok[c] for c in ok_cams)
                avg_kb = (total_bytes / total_snaps / 1024) if total_snaps else 0
                parts.append(f"avg {avg_kb:.0f}KB/frame")
            if fail_cams:
                parts.append(f"down: {', '.join(sorted(fail_cams))}")
            if partial:
                parts.append(f"flaky: {', '.join(sorted(partial))}")
            if self.motion_detection:
                motion_total = sum(r.motion_events for r in self._readers.values())
                parts.append(f"motion: {motion_total}")
                for r in self._readers.values():
                    r.motion_events = 0
            log.info("Frames [%ds]: %s", self._metrics_interval, " | ".join(parts))

            for cam_id in self.cameras:
                self._snap_ok[cam_id] = 0
                self._snap_fail[cam_id] = 0
                self._snap_bytes[cam_id] = 0

    def _collect_rtsp_metrics(self) -> None:
        """Harvest counters from RTSP reader threads into the shared metrics dicts."""
        for cam_id, reader in self._readers.items():
            self._snap_ok[cam_id] = reader.frames_ok
            self._snap_fail[cam_id] = reader.frames_fail
            self._snap_bytes[cam_id] = reader.bytes_total
            reader.frames_ok = 0
            reader.frames_fail = 0
            reader.bytes_total = 0

    # ================================================================
    # Alert dispatch (Discord + Shinobi)
    # ================================================================

    async def _send_alert(self, camera_id: str, description: str,
                          jpeg_bytes: bytes, detections: list[Detection]) -> None:
        """Send alert to Discord (with annotated image) and Shinobi (timeline event)."""
        if self.discord_notifier:
            await self.discord_notifier.send_alert(
                camera_id=camera_id, description=description, jpeg_bytes=jpeg_bytes,
            )
            m.alerts_total.labels(camera=camera_id, destination="discord").inc()
        if self.shinobi_notifier:
            await self.shinobi_notifier.trigger_event(
                camera_id=camera_id, detections=detections,
            )
            m.alerts_total.labels(camera=camera_id, destination="shinobi").inc()

    # ================================================================
    # Shared pipeline helpers (used by both motion and follow-up loops)
    # ================================================================

    def _active_alerts(self, camera_id: str) -> RecentAlerts:
        """Return this camera's recent alerts with expired entries purged."""
        now = time.monotonic()
        return [
            alert for alert in self._recent_alerts.get(camera_id, [])
            if now - alert.timestamp < self._alert_cooldown
        ]

    def _record_alert(self, camera_id: str, detections: list[Detection]) -> None:
        """Record alerted detections for cooldown, dropping expired entries."""
        recent = self._active_alerts(camera_id)
        now = time.monotonic()
        recent.extend(RecentAlert(timestamp=now, detection=d) for d in detections)
        self._recent_alerts[camera_id] = recent

    def _compare_baseline(
        self, detections: list[Detection], camera_id: str, observe: bool,
    ) -> BaselineComparison:
        """Diff detections against the camera's baseline.

        ``new_detections`` is ``None`` to signal "baseline not yet initialized
        — suppress everything" (distinct from an empty list, which means all
        detections matched the baseline).

        When ``observe`` is True (motion path), detections are fed into the
        tracker first so persistent objects accumulate hits toward promotion.
        """
        baseline = self.baselines.get(camera_id, [])
        tracker = self._trackers[camera_id]

        if observe:
            obs_msgs = tracker.observe(detections)
            for msg in obs_msgs:
                log.info("Baseline %s: %s (via motion observe)", camera_id, msg)
            if obs_msgs:
                baseline = tracker.get_baseline()
                self.baselines[camera_id] = baseline

        if not baseline:
            if camera_id not in self._baseline_initialized:
                return BaselineComparison(new_detections=None, baseline=[])
            if not tracker.is_warm:
                baseline = tracker.get_all_seen()

        new = [
            d for d in detections
            if not any(
                d.is_near(other=b, tolerance=self.position_tolerance) for b in baseline
            )
        ] if baseline else detections
        return BaselineComparison(new_detections=new, baseline=baseline)

    async def _select_best_frame(
        self, camera_id: str, jpeg_bytes: bytes,
        all_detections: list[Detection], new: list[Detection],
    ) -> BestFrameSelection:
        """Wait briefly for a clearer frame and pick whichever has the
        highest-confidence view of the new objects.
        """
        best_frame = BestFrameSelection(
            jpeg_bytes=jpeg_bytes,
            all_detections=all_detections,
            new_detections=new,
        )
        best_conf = max(d.conf for d in best_frame.new_detections)

        await asyncio.sleep(1.5)

        buf = self.buffers.get(camera_id)
        delayed_frames = buf.get_recent(seconds=2) if buf else []
        if delayed_frames:
            delayed_jpeg = delayed_frames[-1]
            delayed_dets = await self._scheduler.infer(
                delayed_jpeg, priority=PRIORITY_MOTION, camera_id=camera_id,
            )
            delayed_new = [
                d for d in delayed_dets
                if any(d.is_near(n, tolerance=self.position_tolerance) for n in new)
            ]
            if delayed_new:
                delayed_conf = max(d.conf for d in delayed_new)
                if delayed_conf > best_conf:
                    log.info(
                        "BestFrame %s: using delayed frame (conf %.0f%% > %.0f%%)",
                        camera_id, delayed_conf * 100, best_conf * 100,
                    )
                    best_frame = BestFrameSelection(
                        jpeg_bytes=delayed_jpeg,
                        all_detections=delayed_dets,
                        new_detections=delayed_new,
                    )
        return best_frame

    # ================================================================
    # Motion detection loop (motion_detection=True)
    # ================================================================

    async def _motion_loop(self) -> None:
        """Reads motion events from the queue, runs YOLO, filters down to
        genuinely new objects, and sends an alert when any survive.

        Filter order (see __init__): zone → novelty → min-area, then baseline
        diff, then edge-change → cooldown. Novelty rejects environmental motion
        (trees, shadows) whose intensity doesn't exceed the learned heatmap.
        """
        if self._motion_queue is None:
            return
        loop = asyncio.get_event_loop()
        while True:
            # Poll the thread-safe queue from async context
            try:
                event = await loop.run_in_executor(
                    None, self._motion_queue.get, True, 1.0
                )
            except Exception:
                continue

            try:
                self._last_motion_time[event.camera_id] = time.monotonic()
                detections = await self._scheduler.infer(
                    event.jpeg_bytes, priority=PRIORITY_MOTION, camera_id=event.camera_id,
                )
                if not detections:
                    log.info("Motion %s: YOLO returned 0 detections", event.camera_id)
                    m.motion_filtered_total.labels(camera=event.camera_id, reason="no_detections").inc()
                    continue

                for d in detections:
                    m.detections_total.labels(camera=event.camera_id, class_name=d.name).inc()

                log.info(
                    "Motion %s: YOLO found %d detections: %s",
                    event.camera_id,
                    len(detections),
                    [(d.name, f"{d.conf:.2f}", f"{d.cx:.2f},{d.cy:.2f}", f"{d.w:.3f}x{d.h:.3f}") for d in detections],
                )

                # Keep ALL YOLO detections (pre-filter) for annotation, so
                # baseline/filtered objects still render as gray boxes.
                all_yolo_detections = list(detections)
                ctx = FilterContext(
                    camera_id=event.camera_id,
                    label="Motion",
                    motion_rects=event.motion_rects,
                    jpeg_bytes=event.jpeg_bytes,
                    reference_frames=self._reference_frames,
                    recent_alerts=self._active_alerts(event.camera_id),
                )

                # Noise filters: zone → novelty → min-area
                detections = self._motion_pre.run(detections, ctx)
                if not detections:
                    continue

                # Baseline diff (feeds the tracker via observe)
                comparison = self._compare_baseline(detections, event.camera_id, observe=True)
                new = comparison.new_detections
                baseline = comparison.baseline
                if new is None:
                    log.debug(
                        "Motion %s: ignoring %d detections (baseline not yet initialized)",
                        event.camera_id, len(detections),
                    )
                    continue
                if not new:
                    log.debug(
                        "Motion %s: all %d detections matched baseline (base=%s)",
                        event.camera_id, len(detections), [repr(b) for b in baseline],
                    )
                    continue

                # Confirmation filters: edge-change → cooldown
                new = self._motion_post.run(new, ctx)
                if not new:
                    log.debug("Motion %s: no new objects after confirmation filters", event.camera_id)
                    continue

                names = ", ".join(sorted(set(d.name for d in new)))
                log.info(
                    "Motion %s: new objects: %s (det=%s, base=%s)",
                    event.camera_id, names,
                    [repr(d) for d in detections], [repr(b) for b in baseline],
                )

                best_frame = await self._select_best_frame(
                    event.camera_id, event.jpeg_bytes, all_yolo_detections, new,
                )
                annotated = annotate_frame(
                    jpeg_bytes=best_frame.jpeg_bytes,
                    detections=best_frame.all_detections,
                    new_detections=best_frame.new_detections,
                )
                await self._send_alert(
                    camera_id=event.camera_id, description=f"Motion: {names}",
                    jpeg_bytes=annotated, detections=best_frame.new_detections,
                )
                self._record_alert(event.camera_id, new)

                # Kick off follow-up scans to catch additional arrivals
                # (e.g. kid walking behind parent, second car pulling in).
                if event.camera_id not in self._active_followups:
                    asyncio.create_task(self._follow_up_scan(event.camera_id))

            except Exception:
                log.warning("Motion processing error for %s", event.camera_id, exc_info=True)

    # ================================================================
    # Follow-up scan — catch additional arrivals after an alert
    # ================================================================

    async def _follow_up_scan(self, camera_id: str) -> None:
        """After an alert, periodically re-scan the camera for new objects.

        Runs every _followup_interval seconds for _followup_duration seconds.
        Grabs the latest buffered frame, runs YOLO, and checks for detections
        that aren't in the cooldown list (already alerted) and aren't baseline.
        Skips novelty and edge filters — the scene is actively changing.
        """
        self._active_followups.add(camera_id)
        deadline = time.monotonic() + self._followup_duration
        scan_num = 0
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(self._followup_interval)
                if time.monotonic() >= deadline:
                    break
                scan_num += 1
                buf = self.buffers.get(camera_id)
                if not buf:
                    break
                recent_frames = buf.get_recent(seconds=1.0)
                if not recent_frames:
                    continue
                jpeg_bytes = recent_frames[-1]

                detections = await self._scheduler.infer(
                    jpeg_bytes, priority=PRIORITY_MOTION, camera_id=camera_id,
                )
                if not detections:
                    log.debug("FollowUp %s scan %d: no detections", camera_id, scan_num)
                    continue

                ctx = FilterContext(
                    camera_id=camera_id,
                    label="FollowUp",
                    jpeg_bytes=jpeg_bytes,
                    reference_frames=self._reference_frames,
                    recent_alerts=self._active_alerts(camera_id),
                )

                # Noise filters: zone → min-area (no novelty during follow-up)
                detections = self._followup_pre.run(detections, ctx)
                if not detections:
                    continue

                # Baseline diff (no observe — don't promote transient arrivals)
                comparison = self._compare_baseline(detections, camera_id, observe=False)
                new = comparison.new_detections
                if not new:
                    log.debug(
                        "FollowUp %s scan %d: %d detections all matched baseline",
                        camera_id, scan_num, len(detections),
                    )
                    continue

                # Confirmation filter: cooldown only (no edge during follow-up)
                new = self._followup_post.run(new, ctx)
                if not new:
                    log.debug(
                        "FollowUp %s scan %d: new detections suppressed by cooldown",
                        camera_id, scan_num,
                    )
                    continue

                names = ", ".join(sorted(set(d.name for d in new)))
                log.info(
                    "FollowUp %s scan %d: new arrivals: %s",
                    camera_id, scan_num, names,
                )

                best_frame = await self._select_best_frame(
                    camera_id, jpeg_bytes, detections, new,
                )
                annotated = annotate_frame(
                    jpeg_bytes=best_frame.jpeg_bytes,
                    detections=best_frame.all_detections,
                    new_detections=best_frame.new_detections,
                )
                await self._send_alert(
                    camera_id=camera_id,
                    description=f"FollowUp: {names}",
                    jpeg_bytes=annotated,
                    detections=best_frame.new_detections,
                )

                # Record for cooldown + extend deadline so further arrivals
                # still get caught (e.g. third person walking up)
                self._record_alert(camera_id, new)
                deadline = time.monotonic() + self._followup_duration
                m.alerts_total.labels(camera=camera_id, destination="followup").inc()

        except Exception:
            log.warning("FollowUp scan error for %s", camera_id, exc_info=True)
        finally:
            self._active_followups.discard(camera_id)
            log.debug("FollowUp %s: finished after %d scans", camera_id, scan_num)

    # ================================================================
    # Baseline loop
    # ================================================================

    async def _baseline_loop(self) -> None:
        # Wait briefly for cameras to connect and buffer frames before first scan
        await asyncio.sleep(15)
        while True:
            for camera_id in self.cameras:
                # Skip cameras with recent motion to avoid absorbing
                # transient objects (e.g. person walking) into baseline
                last_motion = self._last_motion_time.get(camera_id, 0)
                if time.monotonic() - last_motion < self._motion_grace_period:
                    log.debug(
                        "Baseline skipped for %s: motion active %.1fs ago",
                        camera_id,
                        time.monotonic() - last_motion,
                    )
                    m.baseline_skipped_total.labels(camera=camera_id).inc()
                    continue

                try:
                    await self._update_baseline(camera_id)
                except Exception:
                    log.warning(
                        "Baseline update failed for %s", camera_id, exc_info=True
                    )
            await asyncio.sleep(self.baseline_interval)

    async def _update_baseline(self, camera_id: str) -> None:
        buf = self.buffers.get(camera_id)
        recent = buf.get_recent(seconds=2) if buf else []
        total = buf.total() if buf else 0
        if not recent:
            log.debug(
                "Baseline skipped for %s: no recent frames (0 of %d in buffer)",
                camera_id,
                total,
            )
            return

        jpeg = recent[-1]
        detections = await self._scheduler.infer(
            jpeg,
            priority=PRIORITY_BASELINE,
            confidence_override=self.detector.confidence_threshold,
            camera_id=camera_id,
        )

        # Store reference frame (grayscale) for visual similarity comparison.
        # This is a calm frame with no motion — ideal for comparing patches later.
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if gray is not None:
            self._reference_frames[camera_id] = gray

        tracker = self._trackers[camera_id]
        messages = tracker.update(detections)

        # If any promoted candidates missed at normal confidence,
        # verify at low confidence before dropping them
        if tracker.has_missed_promoted:
            low_conf_dets = await self._scheduler.infer(
                jpeg,
                priority=PRIORITY_BASELINE,
                confidence_override=self._verify_confidence,
                camera_id=camera_id,
            )
            messages += tracker.verify_missed(low_conf_dets)

        for msg in messages:
            log.info("Baseline %s: %s", camera_id, msg)

        baseline = tracker.get_baseline()
        self.baselines[camera_id] = baseline
        self._baseline_initialized.add(camera_id)
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
