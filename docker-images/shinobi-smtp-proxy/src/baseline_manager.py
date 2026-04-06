from __future__ import annotations

import asyncio
import io
import logging
import queue
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inference_scheduler import PRIORITY_BASELINE, PRIORITY_MOTION, InferenceScheduler
import metrics as m
from object_detector import Detection

if TYPE_CHECKING:
    from discord_notifier import DiscordNotifier
    from object_detector import ObjectDetector
    from shinobi_notifier import ShinobiNotifier

log = logging.getLogger("smtp-proxy")

# RTSP sub-stream template — MJPEG at 704x480 2fps
RTSP_SUBSTREAM = "rtsp://{user}:{passwd}@{ip}:554/cam/realmonitor?channel=1&subtype=1"

# HTTP snapshot fallback (not used by default)
SNAPSHOT_PATH = "/cgi-bin/snapshot.cgi?channel=1&subtype=1"

# Motion detection defaults
MOTION_THRESHOLD = 25  # pixel difference threshold (0-255)
MOTION_MIN_AREA = 500  # minimum contour area in pixels to count as motion
MOTION_COOLDOWN = 2.0  # seconds between motion events per camera


class CameraBuffer:
    """Thread-safe ring buffer of raw JPEG snapshots for one camera."""

    def __init__(self, maxlen: int) -> None:
        self.frames: deque[tuple[float, bytes]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            self.frames.append((time.monotonic(), jpeg_bytes))

    def get_recent(self, seconds: float | None = None) -> list[bytes]:
        with self._lock:
            if seconds is None or not self.frames:
                return [f[1] for f in self.frames]
            cutoff = time.monotonic() - seconds
            return [data for ts, data in self.frames if ts >= cutoff]

    def evict_stale(self, max_age: float) -> None:
        with self._lock:
            cutoff = time.monotonic() - max_age
            while self.frames and self.frames[0][0] < cutoff:
                self.frames.popleft()

    def total(self) -> int:
        with self._lock:
            return len(self.frames)


class RTSPReader(threading.Thread):
    """Background thread that reads from an RTSP stream into a CameraBuffer.

    Reconnects automatically with exponential backoff (cap 30s).
    Encodes each frame to JPEG before storing.
    Optionally performs frame-differencing motion detection and enqueues events.
    """

    def __init__(
        self,
        camera_id: str,
        rtsp_url: str,
        buf: CameraBuffer,
        target_fps: int = 2,
        motion_queue: queue.Queue[tuple[str, bytes, list[tuple[float, float, float, float]]]] | None = None,
        motion_threshold: int = MOTION_THRESHOLD,
        motion_min_area: int = MOTION_MIN_AREA,
        motion_zone_polygons: list[np.ndarray] | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"rtsp-{camera_id}")
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.buf = buf
        self.target_fps = target_fps
        self._stop_event = threading.Event()
        # Counters for metrics (read from async side)
        self.frames_ok = 0
        self.frames_fail = 0
        self.bytes_total = 0
        self.connected = False
        self._backoff = 0
        # Motion detection
        self._motion_queue = motion_queue
        self._motion_threshold = motion_threshold
        self._motion_min_area = motion_min_area
        self._motion_zone_polygons = motion_zone_polygons  # normalized 0-1 coords
        self._motion_mask = None  # built on first frame when we know resolution
        self._prev_gray = None
        self._last_motion = 0.0
        self.motion_events = 0  # counter for metrics

    def stop(self) -> None:
        self._stop_event.set()

    def _build_motion_mask(self, h: int, w: int) -> np.ndarray | None:
        """Build a binary mask from zone polygons scaled to pixel coords."""
        if not self._motion_zone_polygons:
            return None
        mask = np.zeros((h, w), dtype=np.uint8)
        for poly in self._motion_zone_polygons:
            pts = (poly * np.array([w, h])).astype(np.int32)
            cv2.fillPoly(mask, [pts], 255)
        return mask

    def _detect_motion(self, frame: np.ndarray) -> list[tuple[float, float, float, float]]:
        """Returns list of motion region rects (normalized x, y, w, h) if motion detected.

        Returns empty list if no significant motion. Each rect represents a
        contour bounding box in 0-1 normalized coordinates.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if self._prev_gray is None:
            self._prev_gray = gray
            # Build motion mask on first frame now that we know resolution
            if self._motion_mask is None and self._motion_zone_polygons:
                h, w = gray.shape
                self._motion_mask = self._build_motion_mask(h, w)
            return []
        delta = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray
        thresh = cv2.threshold(delta, self._motion_threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        # Mask to detection zone — ignore motion outside configured regions
        if self._motion_mask is not None:
            thresh = cv2.bitwise_and(thresh, self._motion_mask)
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        fh, fw = gray.shape
        motion_rects: list[tuple[float, float, float, float]] = []
        for c in contours:
            if cv2.contourArea(c) >= self._motion_min_area:
                x, y, w, h = cv2.boundingRect(c)
                motion_rects.append((x / fw, y / fh, w / fw, h / fh))
        return motion_rects

    def run(self) -> None:
        while not self._stop_event.is_set():
            cap = None
            try:
                cap = cv2.VideoCapture()
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
                cap.open(self.rtsp_url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    raise ConnectionError(
                        f"Cannot open RTSP stream for {self.camera_id}"
                    )
                self.connected = True
                m.camera_connected.labels(camera=self.camera_id).set(1)
                if self._backoff > 0:
                    log.info(
                        "%s RTSP reconnected (was backed off %ds)",
                        self.camera_id,
                        self._backoff,
                    )
                self._backoff = 0
                self._prev_gray = None  # reset motion baseline on reconnect
                self._warmup_frames = 10  # skip motion detection for first N frames
                frame_interval = 1.0 / self.target_fps
                last_frame = 0.0

                while not self._stop_event.is_set():
                    grabbed = cap.grab()
                    if not grabbed:
                        raise ConnectionError(f"RTSP stream lost for {self.camera_id}")
                    now = time.monotonic()
                    if now - last_frame < frame_interval:
                        continue
                    last_frame = now
                    ret, frame = cap.retrieve()
                    if not ret:
                        self.frames_fail += 1
                        continue
                    ok, jpeg = cv2.imencode(".jpg", frame)
                    if not ok:
                        self.frames_fail += 1
                        continue
                    jpeg_bytes = jpeg.tobytes()
                    self.buf.add(jpeg_bytes)
                    self.frames_ok += 1
                    self.bytes_total += len(jpeg_bytes)
                    m.camera_frames_total.labels(camera=self.camera_id, status="ok").inc()
                    m.camera_bytes_total.labels(camera=self.camera_id).inc(len(jpeg_bytes))

                    # Motion detection (if enabled)
                    if self._motion_queue is not None:
                        if self._warmup_frames > 0:
                            self._warmup_frames -= 1
                        else:
                            motion_rects = self._detect_motion(frame)
                            if motion_rects:
                                if now - self._last_motion >= MOTION_COOLDOWN:
                                    self._last_motion = now
                                    self.motion_events += 1
                                    m.motion_events_total.labels(camera=self.camera_id).inc()
                                    try:
                                        self._motion_queue.put_nowait(
                                            (self.camera_id, jpeg_bytes, motion_rects)
                                        )
                                    except queue.Full:
                                        pass  # drop if processing is backed up

            except Exception as exc:
                self.connected = False
                self.frames_fail += 1
                m.camera_frames_total.labels(camera=self.camera_id, status="fail").inc()
                m.camera_connected.labels(camera=self.camera_id).set(0)
                self._backoff = min(max(self._backoff * 2, 1), 30)
                log.info(
                    "%s RTSP error, reconnect in %ds: %s",
                    self.camera_id,
                    self._backoff,
                    exc,
                )
                self._stop_event.wait(self._backoff)
            finally:
                if cap is not None:
                    cap.release()


class BaselineManager:
    """Manages camera frame buffers, periodic baselines, and event analysis.

    Strategies for receiving frames:
      - 'rtsp' (default): persistent RTSP sub-stream per camera, background threads
      - 'http': HTTP snapshot polling with async loop (legacy, kept for fallback)

    When motion_detection=True and strategy='rtsp', frame-differencing in each
    RTSPReader thread triggers YOLO analysis + Discord alerts autonomously.
    """

    # COCO class name → ID mapping for static baselines
    COCO_NAME_TO_ID = {
        "person": 0,
        "bicycle": 1,
        "car": 2,
        "motorcycle": 3,
        "bus": 5,
        "truck": 7,
        "cat": 15,
        "dog": 16,
    }

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
        static_baselines: dict[str, list[dict[str, str | float]]] | None = None,
        shinobi_notifier: ShinobiNotifier | None = None,
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

        # Static baselines: {camera_id: [Detection, ...]}
        # Known positions where objects are expected (e.g. parked car in driveway).
        # Periodically probed at low confidence to verify presence.
        # "Sticky" — once confirmed, stays in baseline until a probe says it's gone.
        self._static_baselines: dict[str, list[Detection]] = {}
        if static_baselines:
            for cam_id, entries in static_baselines.items():
                dets = []
                for entry in entries:
                    name = entry["name"]
                    cls_id = self.COCO_NAME_TO_ID.get(name, -1)
                    dets.append(
                        Detection(
                            cls_id=cls_id,
                            name=name,
                            cx=entry["cx"],
                            cy=entry["cy"],
                            w=entry.get("w", 0.15),
                            h=entry.get("h", 0.10),
                            conf=1.0,
                        )
                    )
                self._static_baselines[cam_id] = dets
            log.info(
                "Static baselines configured for: %s",
                ", ".join(
                    f"{k} ({len(v)} objects)" for k, v in self._static_baselines.items()
                ),
            )

        # Sticky state: tracks which static entries are confirmed present
        # {camera_id: set of indices into _static_baselines[camera_id]}
        self._sticky_confirmed: dict[str, set[int]] = {}
        # Consecutive probe failure counter per (camera_id, index)
        # Only drop a confirmed entry after N consecutive failed probes
        self._sticky_miss_count: dict[tuple[str, int], int] = {}
        self._sticky_miss_threshold = 3  # consecutive failures before drop
        # Low-confidence probe runs every baseline cycle (60s)
        self._sticky_probe_every = 1
        self._sticky_probe_confidence = 0.15
        self._baseline_cycle = 0

        maxlen = max(buffer_seconds * 2, 10)  # 2fps * buffer_seconds
        self.buffers: dict[str, CameraBuffer] = {
            cam_id: CameraBuffer(maxlen=maxlen) for cam_id in cameras
        }
        self.baselines: dict[str, list[Detection]] = {}
        self._baseline_initialized: set[str] = set()
        self._baseline_task = None
        self._metrics_task = None
        self._motion_task = None

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
    # Zone filtering
    # ================================================================

    def _filter_by_zone(self, camera_id: str, detections: list[Detection]) -> list[Detection]:
        """Return only detections whose center falls inside a configured zone.
        If no zones are configured for this camera, all detections pass through."""
        zones = self._zones.get(camera_id)
        if not zones:
            return detections
        result = []
        for d in detections:
            pt = (d.cx, d.cy)
            for poly in zones:
                if cv2.pointPolygonTest(poly, pt, False) >= 0:
                    result.append(d)
                    break
        return result

    # ================================================================
    # Image annotation
    # ================================================================

    @staticmethod
    def _annotate_frame(jpeg_bytes: bytes, detections: list[Detection],
                        new_detections: list[Detection] | None = None) -> bytes:
        """Draw bounding boxes and labels on a JPEG frame. Returns annotated JPEG bytes.
        Green boxes for new/alerting detections, gray for baseline-matched ones."""
        try:
            img = Image.open(io.BytesIO(jpeg_bytes))
            draw = ImageDraw.Draw(img)
            w, h = img.size
            new_set = set(id(d) for d in (new_detections or detections))

            for d in detections:
                x1 = int((d.cx - d.w / 2) * w)
                y1 = int((d.cy - d.h / 2) * h)
                x2 = int((d.cx + d.w / 2) * w)
                y2 = int((d.cy + d.h / 2) * h)
                is_new = id(d) in new_set
                color = (0, 255, 0) if is_new else (128, 128, 128)
                draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
                label = f"{d.name} {d.conf:.0%}"
                # Label background
                bbox = draw.textbbox((x1, y1 - 14), label)
                draw.rectangle(
                    [bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1], fill=color
                )
                draw.text((x1, y1 - 14), label, fill=(0, 0, 0))

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception:
            log.debug("Annotation failed, sending raw frame", exc_info=True)
            return jpeg_bytes

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

        Only considers YOLO detections that overlap with where motion actually
        occurred in the frame — static objects (e.g. trashcan misclassified as
        person) are ignored because there's no motion at their position.
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

                # Zone filter — ignore detections outside configured regions
                before_zone = len(detections)
                detections = self._filter_by_zone(camera_id=camera_id, detections=detections)
                if not detections:
                    log.info("Motion %s: all %d detections outside zone", camera_id, before_zone)
                    m.motion_filtered_total.labels(camera=camera_id, reason="outside_zone").inc()
                    continue

                # Motion co-location filter — only keep detections that overlap
                # with where motion was actually detected in the frame.
                # Static objects (trashcan, etc.) won't have motion at their position.
                before_count = len(detections)
                detections = [
                    d for d in detections
                    if d.overlaps_any_rect(rects=motion_rects)
                ]
                if not detections:
                    if before_count > 0:
                        log.info(
                            "Motion %s: %d detections filtered (no motion at their position)",
                            camera_id, before_count,
                        )
                    m.motion_filtered_total.labels(camera=camera_id, reason="no_motion_overlap").inc()
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
                if not baseline:
                    # Suppress alerts until baseline has run at least once
                    if camera_id not in self._baseline_initialized:
                        log.debug(
                            "Motion %s: ignoring (baseline not yet initialized)",
                            camera_id,
                        )
                        continue
                    names = ", ".join(d.name for d in detections)
                    log.info("Motion %s: %s (no baseline)", camera_id, names)
                    annotated = self._annotate_frame(jpeg_bytes=jpeg_bytes, detections=detections)
                    await self._send_alert(
                        camera_id=camera_id, description=f"Motion: {names}",
                        jpeg_bytes=annotated, detections=detections,
                    )
                    continue

                new = [
                    d
                    for d in detections
                    if not any(
                        d.is_near(other=b, tolerance=self.position_tolerance)
                        for b in baseline
                    )
                ]
                if new:
                    names = ", ".join(sorted(set(d.name for d in new)))
                    log.info(
                        "Motion %s: new objects: %s (det=%s, base=%s)",
                        camera_id,
                        names,
                        [repr(d) for d in detections],
                        [repr(b) for b in baseline],
                    )
                    annotated = self._annotate_frame(
                        jpeg_bytes=jpeg_bytes, detections=detections,
                        new_detections=new,
                    )
                    await self._send_alert(
                        camera_id=camera_id, description=f"Motion: {names}",
                        jpeg_bytes=annotated, detections=new,
                    )
                else:
                    log.debug("Motion %s: only baseline objects", camera_id)

            except Exception:
                log.warning("Motion processing error for %s", camera_id, exc_info=True)

    # ================================================================
    # Baseline loop (works with both strategies)
    # ================================================================

    async def _baseline_loop(self) -> None:
        # Wait briefly for cameras to connect and buffer frames before first scan
        await asyncio.sleep(15)
        while True:
            self._baseline_cycle += 1
            is_probe_cycle = self._static_baselines and (
                self._baseline_cycle == 1
                or self._baseline_cycle % self._sticky_probe_every == 0
            )

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
                    await self._update_baseline(camera_id, probe=is_probe_cycle)
                except Exception:
                    log.warning(
                        "Baseline update failed for %s", camera_id, exc_info=True
                    )
            await asyncio.sleep(self.baseline_interval)

    async def _update_baseline(self, camera_id: str, probe: bool = False) -> None:
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

        # Sticky static baseline probe: periodically run at low confidence
        # to check if expected objects (e.g. parked car) are actually there
        static = self._static_baselines.get(camera_id, [])
        if static:
            if probe:
                low_conf_dets = await self._scheduler.infer(
                    jpeg,
                    priority=PRIORITY_BASELINE,
                    confidence_override=self._sticky_probe_confidence,
                    camera_id=camera_id,
                )
                prev = self._sticky_confirmed.get(camera_id, set())
                confirmed = set()
                for i, s in enumerate(static):
                    seen = any(
                        s.is_near(other=d, tolerance=self.position_tolerance)
                        for d in detections
                    ) or any(
                        s.is_near(other=d, tolerance=self.position_tolerance)
                        for d in low_conf_dets
                    )
                    key = (camera_id, i)
                    if seen:
                        confirmed.add(i)
                        self._sticky_miss_count.pop(key, None)
                    elif i in prev:
                        # Was confirmed — count consecutive misses
                        misses = self._sticky_miss_count.get(key, 0) + 1
                        self._sticky_miss_count[key] = misses
                        if misses < self._sticky_miss_threshold:
                            confirmed.add(i)  # keep it confirmed
                            log.info(
                                "Sticky probe %s: %r missed %d/%d",
                                camera_id, static[i], misses,
                                self._sticky_miss_threshold,
                            )
                        else:
                            log.info(
                                "Sticky probe %s: dropping %r after %d misses",
                                camera_id, static[i], misses,
                            )
                            self._sticky_miss_count.pop(key, None)
                self._sticky_confirmed[camera_id] = confirmed
                if confirmed != prev:
                    names = [repr(static[i]) for i in sorted(confirmed)]
                    log.info(
                        "Sticky probe %s: confirmed %s", camera_id, names or "none"
                    )

            # Merge confirmed sticky entries into baseline
            confirmed = self._sticky_confirmed.get(camera_id, set())
            for i in confirmed:
                s = static[i]
                if not any(
                    s.is_near(other=d, tolerance=self.position_tolerance)
                    for d in detections
                ):
                    detections.append(s)

        self.baselines[camera_id] = detections
        self._baseline_initialized.add(camera_id)
        m.baseline_objects.labels(camera=camera_id).set(len(detections))
        if detections:
            log.info("Baseline %s: %s", camera_id, [repr(d) for d in detections])
        log.debug(
            "Baseline for %s: chose previous frame of %d available, %d detections",
            camera_id,
            total,
            len(detections),
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
                detections = self._filter_by_zone(camera_id=camera_id, detections=detections)
                if not detections:
                    continue

                if not baseline:
                    names = ", ".join(d.name for d in detections)
                    log.info(
                        "Frame %d/%d: detected %s (no baseline)", idx + 1, n, names
                    )
                    annotated = self._annotate_frame(jpeg_bytes=frames[idx], detections=detections)
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
                    annotated = self._annotate_frame(
                        jpeg_bytes=frames[idx], detections=detections,
                        new_detections=new,
                    )
                    return True, f"new objects: {names}", annotated

        log.info("No new objects in %d frames for %s", n, camera_id)
        return False, f"no new objects in {n} frames", None
