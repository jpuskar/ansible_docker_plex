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
from rtsp_reader import (
    MOTION_MIN_AREA,
    MOTION_THRESHOLD,
    RTSP_SUBSTREAM,
    CameraBuffer,
    RTSPReader,
)
from scene_compare import annotate_frame, filter_by_zone, patch_edge_change

if TYPE_CHECKING:
    from discord_notifier import DiscordNotifier
    from object_detector import ObjectDetector
    from shinobi_notifier import ShinobiNotifier

log = logging.getLogger("smtp-proxy")

# HTTP snapshot fallback (not used by default)
SNAPSHOT_PATH = "/cgi-bin/snapshot.cgi?channel=1&subtype=1"


class BaselineManager:
    """Manages camera frame buffers, periodic baselines, and event analysis.

    Strategies for receiving frames:
      - 'rtsp' (default): persistent RTSP sub-stream per camera, background threads
      - 'http': HTTP snapshot polling with async loop (legacy, kept for fallback)

    When motion_detection=True and strategy='rtsp', frame-differencing in each
    RTSPReader thread triggers YOLO analysis + Discord alerts autonomously.
    """

    def __init__(
        self,
        cameras: dict[str, str],
        username: str,
        password: str,
        detector: ObjectDetector,
        snapshot_interval: int = 1,
        buffer_seconds: int = 10,
        baseline_interval: int = 60,
        position_tolerance: float = 0.15,
        strategy: str = "rtsp",
        discord_notifier: DiscordNotifier | None = None,
        motion_detection: bool = True,
        motion_threshold: int = MOTION_THRESHOLD,
        motion_min_area: int = MOTION_MIN_AREA,
        detection_zones: dict[str, list[list[list[float]]]] | None = None,
        min_detection_area: float = 0.003,
        shinobi_notifier: ShinobiNotifier | None = None,
        baseline_add_threshold: int = 3,
        baseline_verify_confidence: float = 0.15,
        min_motion_novelty: float = 0.05,
    ) -> None:
        self.cameras = cameras  # {camera_id: ip}
        self.username = username
        self.password = password
        self.detector = detector
        self.snapshot_interval = snapshot_interval
        self.buffer_seconds = buffer_seconds
        self.baseline_interval = baseline_interval
        self.position_tolerance = position_tolerance
        self.strategy = strategy
        self.discord_notifier = discord_notifier
        self.shinobi_notifier = shinobi_notifier
        self.motion_detection = motion_detection

        # Detection zones: {camera_id: [numpy_polygon, ...]}
        # Each polygon is a numpy array of (x,y) pairs in normalized 0.0-1.0 coords
        self._zones = {}
        if detection_zones:
            for cam_id, zones in detection_zones.items():
                self._zones[cam_id] = [
                    np.array(poly, dtype=np.float32) for poly in zones
                ]
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
        self._baseline_task = None
        self._metrics_task = None
        self._motion_task = None

        # Reference frames for visual similarity comparison.
        # Stored during each baseline cycle — a calm frame with no motion.
        self._reference_frames: dict[str, np.ndarray] = {}  # camera -> grayscale numpy
        self._scene_change_threshold = 0.15  # fraction of current edges that must be new

        # Priority inference scheduler — all GPU access goes through here
        self._scheduler = InferenceScheduler(detector)

        # Motion event queue: (camera_id, jpeg_bytes) from RTSP threads
        self._motion_queue = queue.Queue(maxsize=50) if motion_detection else None

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
        self._recent_alerts: dict[str, list[tuple[float, Detection]]] = {}  # camera -> [(time, det)]

        # Follow-up scan: after an alert, periodically re-check the camera
        # for additional arrivals (e.g. kid walking behind parent).
        self._followup_interval = 3.0   # seconds between follow-up scans
        self._followup_duration = 15.0  # total follow-up window after alert
        self._active_followups: set[str] = set()  # cameras with running follow-up tasks

        # RTSP readers (strategy='rtsp')
        self._readers = {}
        if strategy == "rtsp":
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

        # HTTP snapshot state (strategy='http')
        self._http_clients = {}
        self._snapshot_task = None
        self._backoff = {cam_id: 0 for cam_id in cameras}
        self._next_poll = {cam_id: 0.0 for cam_id in cameras}
        self._BACKOFF_CAP = 10
        if strategy == "http":
            import httpx

            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)
            for cam_id, ip in cameras.items():
                transport = httpx.AsyncHTTPTransport(retries=1)
                self._http_clients[cam_id] = httpx.AsyncClient(
                    auth=httpx.DigestAuth(username, password),
                    timeout=1,
                    transport=transport,
                )

        # Per-camera snapshot counters (reset each metrics interval)
        self._snap_ok = {cam_id: 0 for cam_id in cameras}
        self._snap_fail = {cam_id: 0 for cam_id in cameras}
        self._snap_bytes = {cam_id: 0 for cam_id in cameras}
        self._metrics_interval = 10

    async def start(self) -> None:
        if self.strategy == "rtsp":
            for reader in self._readers.values():
                reader.start()
            log.info(
                "Camera manager started [rtsp]: %d cameras, %ds buffer, %ds baseline, motion=%s",
                len(self.cameras),
                self.buffer_seconds,
                self.baseline_interval,
                self.motion_detection,
            )
        elif self.strategy == "http":
            self._snapshot_task = asyncio.create_task(self._http_snapshot_loop())
            log.info(
                "Camera manager started [http]: %d cameras, %ds snapshots, %ds baseline, path=%s",
                len(self.cameras),
                self.snapshot_interval,
                self.baseline_interval,
                SNAPSHOT_PATH,
            )
        await self._scheduler.start()
        self._baseline_task = asyncio.create_task(self._baseline_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        if self.motion_detection:
            self._motion_task = asyncio.create_task(self._motion_loop())

    async def stop(self) -> None:
        await self._scheduler.stop()
        for task in (
            self._snapshot_task,
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
        for client in self._http_clients.values():
            await client.aclose()
        if self.discord_notifier:
            await self.discord_notifier.close()
        if self.shinobi_notifier:
            await self.shinobi_notifier.close()

    # ================================================================
    # HTTP snapshot strategy (legacy, strategy='http')
    # ================================================================

    async def _http_snapshot_loop(self) -> None:
        while True:
            await self._http_snapshot_all()
            await asyncio.sleep(self.snapshot_interval)

    async def _http_snapshot_all(self) -> None:
        import httpx

        now = time.monotonic()
        for cam_id in self.cameras:
            self.buffers[cam_id].evict_stale(self.buffer_seconds)
        tasks = [
            self._http_grab_snapshot(cam_id, ip)
            for cam_id, ip in self.cameras.items()
            if now >= self._next_poll[cam_id]
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _http_grab_snapshot(self, camera_id: str, ip: str) -> None:
        import httpx

        try:
            url = f"http://{ip}{SNAPSHOT_PATH}"
            client = self._http_clients[camera_id]
            resp = await client.get(url)
            if resp.status_code == 401:
                log.warning(
                    "Auth failed (real 401) for %s — check credentials", camera_id
                )
                self._snap_fail[camera_id] += 1
                return
            resp.raise_for_status()
            self.buffers[camera_id].add(resp.content)
            self._snap_ok[camera_id] += 1
            self._snap_bytes[camera_id] += len(resp.content)
            if self._backoff[camera_id] > 0:
                log.info(
                    "%s recovered (was backed off %ds)",
                    camera_id,
                    self._backoff[camera_id],
                )
                self._backoff[camera_id] = 0
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._snap_fail[camera_id] += 1
            prev = self._backoff[camera_id]
            self._backoff[camera_id] = min(max(prev * 2, 1), self._BACKOFF_CAP)
            self._next_poll[camera_id] = time.monotonic() + self._backoff[camera_id]
            log.info(
                "%s timeout, backoff %ds (was %ds): %s",
                camera_id,
                self._backoff[camera_id],
                prev,
                type(exc).__name__,
            )
        except Exception:
            self._snap_fail[camera_id] += 1
            log.debug("Snapshot failed for %s", camera_id, exc_info=True)

    # ================================================================
    # Metrics loop (works with both strategies)
    # ================================================================

    async def _metrics_loop(self) -> None:
        while True:
            await asyncio.sleep(self._metrics_interval)

            if self.strategy == "rtsp":
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
    # Motion detection loop (strategy='rtsp', motion_detection=True)
    # ================================================================

    async def _motion_loop(self) -> None:
        """Reads motion events from the queue, runs YOLO, compares baseline,
        sends Discord alert if new objects are found.

        Only considers YOLO detections where the overlapping motion has high
        novelty — i.e. the motion significantly exceeds the temporal background
        for that region. Environmental motion (trees, shadows) has low novelty
        because the heatmap has learned it as normal.
        """
        loop = asyncio.get_event_loop()
        while True:
            # Poll the thread-safe queue from async context
            try:
                camera_id, jpeg_bytes, motion_rects = await loop.run_in_executor(
                    None, self._motion_queue.get, True, 1.0
                )
            except Exception:
                continue

            try:
                self._last_motion_time[camera_id] = time.monotonic()
                detections = await self._scheduler.infer(
                    jpeg_bytes, priority=PRIORITY_MOTION, camera_id=camera_id,
                )
                if not detections:
                    log.info("Motion %s: YOLO returned 0 detections", camera_id)
                    m.motion_filtered_total.labels(camera=camera_id, reason="no_detections").inc()
                    continue

                for d in detections:
                    m.detections_total.labels(camera=camera_id, class_name=d.name).inc()

                log.info(
                    "Motion %s: YOLO found %d detections: %s",
                    camera_id,
                    len(detections),
                    [(d.name, f"{d.conf:.2f}", f"{d.cx:.2f},{d.cy:.2f}", f"{d.w:.3f}x{d.h:.3f}") for d in detections],
                )

                # Keep a snapshot of ALL YOLO detections before any filtering.
                # Used later by annotate_frame so baseline/filtered objects
                # still appear (gray boxes) even when they don't trigger alerts.
                all_yolo_detections = list(detections)

                # Zone filter — ignore detections outside configured regions
                before_zone = len(detections)
                detections = filter_by_zone(camera_id=camera_id, detections=detections, zones=self._zones)
                if not detections:
                    log.info("Motion %s: all %d detections outside zone", camera_id, before_zone)
                    m.motion_filtered_total.labels(camera=camera_id, reason="outside_zone").inc()
                    continue

                # Motion novelty filter — only keep detections where the
                # overlapping motion is significantly above the temporal
                # background (heatmap). Trees swaying = low novelty,
                # person walking = high novelty.
                before_count = len(detections)
                novel_detections = []
                for d in detections:
                    novelty = d.max_novelty(rects=motion_rects)
                    if novelty >= self._min_motion_novelty:
                        novel_detections.append(d)
                    else:
                        log.debug(
                            "Motion %s: %s@(%.2f,%.2f) novelty=%.3f < %.3f (filtered as environmental)",
                            camera_id, d.name, d.cx, d.cy, novelty, self._min_motion_novelty,
                        )
                detections = novel_detections
                if not detections:
                    if before_count > 0:
                        log.info(
                            "Motion %s: %d detections filtered (environmental motion, novelty too low)",
                            camera_id, before_count,
                        )
                    m.motion_filtered_total.labels(camera=camera_id, reason="low_novelty").inc()
                    continue

                # Min area filter — discard tiny hallucinations from light flashes
                if self._min_detection_area > 0:
                    before_area = len(detections)
                    detections = [
                        d for d in detections if d.w * d.h >= self._min_detection_area
                    ]
                    if not detections:
                        log.info("Motion %s: %d detections below min area %.4f", camera_id, before_area, self._min_detection_area)
                        m.motion_filtered_total.labels(camera=camera_id, reason="below_min_area").inc()
                        continue

                baseline = self.baselines.get(camera_id, [])
                tracker = self._trackers[camera_id]

                # Feed all surviving detections into the tracker FIRST so
                # persistent objects seen during motion events accumulate
                # hits toward promotion. If a promotion happens, the
                # baseline comparison below will already include it.
                obs_msgs = tracker.observe(detections)
                for msg in obs_msgs:
                    log.info("Baseline %s: %s (via motion observe)", camera_id, msg)
                if obs_msgs:
                    baseline = tracker.get_baseline()
                    self.baselines[camera_id] = baseline

                if not baseline:
                    # Before first baseline cycle: suppress all
                    if camera_id not in self._baseline_initialized:
                        log.debug(
                            "Motion %s: ignoring %d detections (baseline not yet initialized)",
                            camera_id, len(detections),
                        )
                        continue
                    # During warmup: use all candidates seen so far
                    if not tracker.is_warm:
                        baseline = tracker.get_all_seen()

                # Compare detections against baseline (promoted, or all-seen during warmup)
                new = [
                    d
                    for d in detections
                    if not any(
                        d.is_near(other=b, tolerance=self.position_tolerance)
                        for b in baseline
                    )
                ] if baseline else detections

                if not new and detections:
                    log.debug(
                        "Motion %s: all %d detections matched baseline (base=%s)",
                        camera_id, len(detections),
                        [repr(b) for b in baseline],
                    )
                    continue

                # Visual similarity filter — compare edge maps (Canny) of
                # each "new" detection's patch against the baseline reference.
                # Edges are lighting-invariant: a cloud or dusk shift changes
                # brightness but not object contours.  A static trashcan has
                # the same edges in both frames (~0% new-edge fraction).  A
                # person introduces many new contour edges (40-80% of current
                # edges are new).
                if new and camera_id in self._reference_frames:
                    visually_new = []
                    for d in new:
                        edge_frac = patch_edge_change(camera_id, jpeg_bytes, d, self._reference_frames)
                        if edge_frac is not None and edge_frac < self._scene_change_threshold:
                            log.info(
                                "Motion %s: %s@(%.2f,%.2f) suppressed (edges unchanged, %.2f%% new edges)",
                                camera_id, d.name, d.cx, d.cy, edge_frac * 100,
                            )
                            m.motion_filtered_total.labels(camera=camera_id, reason="scene_unchanged").inc()
                        else:
                            if edge_frac is not None:
                                log.info(
                                    "Motion %s: %s@(%.2f,%.2f) scene changed (%.2f%% new edges)",
                                    camera_id, d.name, d.cx, d.cy, edge_frac * 100,
                                )
                            visually_new.append(d)
                    new = visually_new

                # Alert cooldown — suppress repeat alerts for the same
                # object at the same position within the cooldown window.
                if new:
                    now = time.monotonic()
                    recent = self._recent_alerts.get(camera_id, [])
                    # Purge expired entries
                    recent = [(t, d) for t, d in recent if now - t < self._alert_cooldown]
                    truly_new = [
                        d for d in new
                        if not any(
                            d.is_near(prev_d, tolerance=self.position_tolerance)
                            for _, prev_d in recent
                        )
                    ]
                    if truly_new != new:
                        suppressed = len(new) - len(truly_new)
                        log.info(
                            "Motion %s: %d detections suppressed (alert cooldown)",
                            camera_id, suppressed,
                        )
                        m.motion_filtered_total.labels(camera=camera_id, reason="alert_cooldown").inc()
                    new = truly_new

                if new:
                    names = ", ".join(sorted(set(d.name for d in new)))
                    log.info(
                        "Motion %s: new objects: %s (det=%s, base=%s)",
                        camera_id,
                        names,
                        [repr(d) for d in detections],
                        [repr(b) for b in baseline],
                    )

                    # Best-frame selection: wait briefly for a clearer frame
                    # where the person/object is more visible, then pick the
                    # frame with the highest confidence for the new detections.
                    best_jpeg = jpeg_bytes
                    best_all_dets = all_yolo_detections
                    best_new = new
                    best_conf = max(d.conf for d in new)

                    await asyncio.sleep(1.5)

                    buf = self.buffers.get(camera_id)
                    delayed_frames = buf.get_recent(seconds=2) if buf else []
                    if delayed_frames:
                        delayed_jpeg = delayed_frames[-1]
                        delayed_dets = await self._scheduler.infer(
                            delayed_jpeg,
                            priority=PRIORITY_MOTION,
                            camera_id=camera_id,
                        )
                        # Check only detections near the original new objects
                        delayed_new = [
                            d for d in delayed_dets
                            if any(d.is_near(n, tolerance=self.position_tolerance) for n in new)
                        ]
                        if delayed_new:
                            delayed_conf = max(d.conf for d in delayed_new)
                            if delayed_conf > best_conf:
                                log.info(
                                    "Motion %s: using delayed frame (conf %.0f%% > %.0f%%)",
                                    camera_id, delayed_conf * 100, best_conf * 100,
                                )
                                best_jpeg = delayed_jpeg
                                best_all_dets = delayed_dets
                                best_new = delayed_new
                            else:
                                log.debug(
                                    "Motion %s: keeping original frame (conf %.0f%% >= delayed %.0f%%)",
                                    camera_id, best_conf * 100, delayed_conf * 100,
                                )

                    annotated = annotate_frame(
                        jpeg_bytes=best_jpeg, detections=best_all_dets,
                        new_detections=best_new,
                    )
                    await self._send_alert(
                        camera_id=camera_id, description=f"Motion: {names}",
                        jpeg_bytes=annotated, detections=best_new,
                    )
                    # Record alerted detections for cooldown
                    now = time.monotonic()
                    recent = self._recent_alerts.get(camera_id, [])
                    recent = [(t, d) for t, d in recent if now - t < self._alert_cooldown]
                    recent.extend((now, d) for d in new)
                    self._recent_alerts[camera_id] = recent

                    # Kick off follow-up scans to catch additional arrivals
                    # (e.g. kid walking behind parent, second car pulling in).
                    if camera_id not in self._active_followups:
                        asyncio.create_task(self._follow_up_scan(camera_id))
                else:
                    log.debug("Motion %s: no new objects after all filters", camera_id)

            except Exception:
                log.warning("Motion processing error for %s", camera_id, exc_info=True)

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

                # Zone filter
                detections = filter_by_zone(
                    camera_id=camera_id, detections=detections, zones=self._zones,
                )
                if not detections:
                    continue

                # Min area filter
                if self._min_detection_area > 0:
                    detections = [
                        d for d in detections if d.w * d.h >= self._min_detection_area
                    ]
                    if not detections:
                        continue

                # Baseline comparison
                baseline = self.baselines.get(camera_id, [])
                tracker = self._trackers[camera_id]
                if not baseline and not tracker.is_warm:
                    baseline = tracker.get_all_seen()
                new = [
                    d for d in detections
                    if not any(
                        d.is_near(b, tolerance=self.position_tolerance) for b in baseline
                    )
                ] if baseline else detections

                if not new:
                    log.debug(
                        "FollowUp %s scan %d: %d detections all matched baseline",
                        camera_id, scan_num, len(detections),
                    )
                    continue

                # Cooldown comparison — filter out already-alerted positions
                now = time.monotonic()
                recent_alerts = self._recent_alerts.get(camera_id, [])
                recent_alerts = [
                    (t, d) for t, d in recent_alerts if now - t < self._alert_cooldown
                ]
                truly_new = [
                    d for d in new
                    if not any(
                        d.is_near(prev_d, tolerance=self.position_tolerance)
                        for _, prev_d in recent_alerts
                    )
                ]
                if not truly_new:
                    log.debug(
                        "FollowUp %s scan %d: %d new detections suppressed by cooldown",
                        camera_id, scan_num, len(new),
                    )
                    continue

                # Found genuinely new arrivals — best-frame selection
                names = ", ".join(sorted(set(d.name for d in truly_new)))
                log.info(
                    "FollowUp %s scan %d: new arrivals: %s",
                    camera_id, scan_num, names,
                )

                best_jpeg = jpeg_bytes
                best_dets = detections
                best_new = truly_new
                best_conf = max(d.conf for d in truly_new)

                await asyncio.sleep(1.5)

                delayed_frames = buf.get_recent(seconds=2)
                if delayed_frames:
                    delayed_jpeg = delayed_frames[-1]
                    delayed_dets = await self._scheduler.infer(
                        delayed_jpeg, priority=PRIORITY_MOTION, camera_id=camera_id,
                    )
                    delayed_new = [
                        d for d in delayed_dets
                        if any(
                            d.is_near(n, tolerance=self.position_tolerance)
                            for n in truly_new
                        )
                    ]
                    if delayed_new:
                        delayed_conf = max(d.conf for d in delayed_new)
                        if delayed_conf > best_conf:
                            log.info(
                                "FollowUp %s: using delayed frame (conf %.0f%% > %.0f%%)",
                                camera_id, delayed_conf * 100, best_conf * 100,
                            )
                            best_jpeg = delayed_jpeg
                            best_dets = delayed_dets
                            best_new = delayed_new

                annotated = annotate_frame(
                    jpeg_bytes=best_jpeg, detections=best_dets,
                    new_detections=best_new,
                )
                await self._send_alert(
                    camera_id=camera_id,
                    description=f"FollowUp: {names}",
                    jpeg_bytes=annotated,
                    detections=best_new,
                )

                # Record for cooldown + extend deadline so further arrivals
                # still get caught (e.g. third person walking up)
                now = time.monotonic()
                recent_alerts = self._recent_alerts.get(camera_id, [])
                recent_alerts = [
                    (t, d) for t, d in recent_alerts if now - t < self._alert_cooldown
                ]
                recent_alerts.extend((now, d) for d in truly_new)
                self._recent_alerts[camera_id] = recent_alerts
                deadline = now + self._followup_duration
                m.alerts_total.labels(camera=camera_id, destination="followup").inc()

        except Exception:
            log.warning("FollowUp scan error for %s", camera_id, exc_info=True)
        finally:
            self._active_followups.discard(camera_id)
            log.debug("FollowUp %s: finished after %d scans", camera_id, scan_num)

    # ================================================================
    # Baseline loop (works with both strategies)
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

    # ================================================================
    # Event analysis (works with both strategies)
    # ================================================================

    async def analyze_event(self, camera_id: str) -> tuple[bool, str, bytes | None]:
        """Called when an event email arrives. Runs YOLO on buffered frames
        in batches of 3, middle-out order. Returns as soon as a new object is found.

        Returns: (has_new_objects: bool, reason: str, frame_jpeg: bytes|None)
        """

        buf = self.buffers.get(camera_id)
        if not buf:
            log.warning("No buffer for camera %s", camera_id)
            return True, "unknown camera, allowing", None

        frames = buf.get_recent(seconds=self.buffer_seconds)
        if not frames:
            return True, "no frames buffered, allowing", None

        log.info("Event for %s: analyzing %d buffered frames", camera_id, len(frames))

        # Build middle-out index order:
        # e.g. 10 frames [0..9] → 5, 4, 6, 3, 7, 2, 8, 1, 9, 0
        n = len(frames)
        mid = n // 2
        check_order = []
        lo, hi = mid, mid + 1
        while lo >= 0 or hi < n:
            if lo >= 0:
                check_order.append(lo)
                lo -= 1
            if hi < n:
                check_order.append(hi)
                hi += 1

        baseline = self.baselines.get(camera_id, [])
        batch_size = 3

        for batch_start in range(0, len(check_order), batch_size):
            batch_indices = check_order[batch_start : batch_start + batch_size]
            tasks = [
                self._scheduler.infer(
                    frames[i], priority=PRIORITY_MOTION, camera_id=camera_id,
                )
                for i in batch_indices
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for idx, detections in zip(batch_indices, results):
                if isinstance(detections, Exception):
                    log.debug("Frame %d YOLO error: %s", idx, detections)
                    continue
                if not detections:
                    continue

                # Zone filter
                detections = filter_by_zone(camera_id=camera_id, detections=detections, zones=self._zones)
                if not detections:
                    continue

                if not baseline:
                    names = ", ".join(d.name for d in detections)
                    log.info(
                        "Frame %d/%d: detected %s (no baseline)", idx + 1, n, names
                    )
                    annotated = annotate_frame(jpeg_bytes=frames[idx], detections=detections)
                    return True, f"new objects (no baseline): {names}", annotated

                new = [
                    d
                    for d in detections
                    if not any(
                        d.is_near(other=b, tolerance=self.position_tolerance)
                        for b in baseline
                    )
                ]
                if new:
                    names = ", ".join(d.name for d in new)
                    log.info("Frame %d/%d: new objects: %s", idx + 1, n, names)
                    annotated = annotate_frame(
                        jpeg_bytes=frames[idx], detections=detections,
                        new_detections=new,
                    )
                    return True, f"new objects: {names}", annotated

        log.info("No new objects in %d frames for %s", n, camera_id)
        return False, f"no new objects in {n} frames", None
