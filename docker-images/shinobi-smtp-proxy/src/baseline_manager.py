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

    Motion heatmap: maintains an exponential moving average of per-cell motion
    intensity. Environmental motion (wind, flickering lights) builds up in the
    heatmap so it can be subtracted from instantaneous motion, leaving only
    novel motion (person walking, car arriving).
    """

    # Motion heatmap grid resolution
    _GRID_ROWS = 12
    _GRID_COLS = 16
    _HEATMAP_ALPHA = 0.05  # EMA factor: lower = slower adaptation

    def __init__(
        self,
        camera_id: str,
        rtsp_url: str,
        buf: CameraBuffer,
        target_fps: int = 2,
        motion_queue: queue.Queue[tuple[str, bytes, list[tuple[float, float, float, float, float]]]] | None = None,
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
        # Motion heatmap: EMA of per-cell motion intensity (0-255 scale)
        self._motion_heatmap = np.zeros(
            (self._GRID_ROWS, self._GRID_COLS), dtype=np.float32
        )

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

    def _detect_motion(self, frame: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        """Returns list of motion region rects with novelty scores.

        Each tuple is (x, y, w, h, novelty) in normalized 0-1 coords.
        novelty = how much the motion in this region exceeds the temporal
        background motion (heatmap). High novelty = genuinely new motion
        (person, car). Low novelty = environmental (wind, trees, shadows).
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

        # Compute per-cell mean motion intensity for heatmap update
        fh, fw = gray.shape
        cell_h = fh / self._GRID_ROWS
        cell_w = fw / self._GRID_COLS
        current_grid = np.zeros(
            (self._GRID_ROWS, self._GRID_COLS), dtype=np.float32
        )
        for r in range(self._GRID_ROWS):
            for c in range(self._GRID_COLS):
                y1 = int(r * cell_h)
                y2 = int((r + 1) * cell_h)
                x1 = int(c * cell_w)
                x2 = int((c + 1) * cell_w)
                current_grid[r, c] = delta[y1:y2, x1:x2].mean()

        # Update heatmap with EMA
        self._motion_heatmap = (
            self._motion_heatmap * (1 - self._HEATMAP_ALPHA)
            + current_grid * self._HEATMAP_ALPHA
        )

        thresh = cv2.threshold(delta, self._motion_threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        # Mask to detection zone — ignore motion outside configured regions
        if self._motion_mask is not None:
            thresh = cv2.bitwise_and(thresh, self._motion_mask)
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        motion_rects: list[tuple[float, float, float, float, float]] = []
        for cont in contours:
            if cv2.contourArea(cont) >= self._motion_min_area:
                x, y, w, h = cv2.boundingRect(cont)
                # Compute novelty: mean motion in this rect vs heatmap baseline
                r1 = max(0, int(y / cell_h))
                r2 = min(self._GRID_ROWS, int((y + h) / cell_h) + 1)
                c1 = max(0, int(x / cell_w))
                c2 = min(self._GRID_COLS, int((x + w) / cell_w) + 1)
                current_mean = current_grid[r1:r2, c1:c2].mean()
                heatmap_mean = self._motion_heatmap[r1:r2, c1:c2].mean()
                # Novelty: how much instantaneous exceeds the background
                novelty = max(0.0, current_mean - heatmap_mean) / 255.0
                motion_rects.append((x / fw, y / fh, w / fw, h / fh, novelty))
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
                self._motion_heatmap[:] = 0  # reset heatmap on reconnect
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


# COCO vehicle class IDs that YOLO frequently confuses with each other.
# Treat as equivalent for baseline matching so a robot mower detected as
# car/bus/truck interchangeably can still accumulate hits.
VEHICLE_CLASSES = frozenset({2, 3, 5, 7})  # car, motorcycle, bus, truck


def _same_class_group(a_cls: int, b_cls: int) -> bool:
    """True if both class IDs are identical or both are vehicles."""
    return a_cls == b_cls or (a_cls in VEHICLE_CLASSES and b_cls in VEHICLE_CLASSES)


class BaselineCandidate:
    """Tracks a single detected object across multiple baseline cycles."""

    __slots__ = ("cls_id", "name", "cx", "cy", "w", "h", "hits", "misses", "promoted")

    def __init__(self, det: Detection) -> None:
        self.cls_id = det.cls_id
        self.name = det.name
        self.cx = det.cx
        self.cy = det.cy
        self.w = det.w
        self.h = det.h
        self.hits = 1
        self.misses = 0
        self.promoted = False

    def update(self, det: Detection) -> None:
        """Exponential moving average of position/size."""
        alpha = 0.3
        self.cx = self.cx * (1 - alpha) + det.cx * alpha
        self.cy = self.cy * (1 - alpha) + det.cy * alpha
        self.w = self.w * (1 - alpha) + det.w * alpha
        self.h = self.h * (1 - alpha) + det.h * alpha
        # Update class to latest detection (handles car/bus/truck flip-flop)
        self.cls_id = det.cls_id
        self.name = det.name
        self.hits += 1
        self.misses = 0

    def as_detection(self) -> Detection:
        return Detection(
            cls_id=self.cls_id, name=self.name,
            cx=self.cx, cy=self.cy, w=self.w, h=self.h,
            conf=1.0,
        )

    def __repr__(self) -> str:
        state = "P" if self.promoted else "c"
        return f"{self.name}@({self.cx:.2f},{self.cy:.2f})[{state} h={self.hits} m={self.misses}]"


class BaselineTracker:
    """Hysteresis-based baseline: objects must appear N cycles to enter.
    To leave, a low-confidence verification pass must also fail to find them."""

    def __init__(self, add_threshold: int = 3,
                 tolerance: float = 0.15) -> None:
        self.add_threshold = add_threshold
        self.tolerance = tolerance
        self.candidates: list[BaselineCandidate] = []
        self.cycles = 0
        # Indices of promoted candidates that missed in the latest update()
        self._missed_promoted: list[int] = []

    @property
    def is_warm(self) -> bool:
        return self.cycles >= self.add_threshold

    def _match(self, det: Detection, cand: BaselineCandidate) -> bool:
        return (_same_class_group(cand.cls_id, det.cls_id)
                and abs(cand.cx - det.cx) < self.tolerance
                and abs(cand.cy - det.cy) < self.tolerance)

    def update(self, detections: list[Detection]) -> list[str]:
        """Feed one cycle of normal-confidence detections.
        Returns log messages. Call verify_missed() after if any promoted missed."""
        self.cycles += 1
        return self._feed(detections, is_cycle=True)

    def observe(self, detections: list[Detection]) -> list[str]:
        """Feed observations from motion events into the tracker.
        Does NOT increment cycles or count misses. Creates new candidates
        for objects not yet tracked (so static objects only visible during
        motion can eventually accumulate hits and get promoted)."""
        return self._feed(detections, is_cycle=False)

    def _feed(self, detections: list[Detection], is_cycle: bool) -> list[str]:
        matched_candidates: set[int] = set()
        messages: list[str] = []
        if is_cycle:
            self._missed_promoted = []

        for det in detections:
            best = None
            for i, cand in enumerate(self.candidates):
                if i in matched_candidates:
                    continue
                if self._match(det, cand):
                    best = i
                    break
            if best is not None:
                matched_candidates.add(best)
                self.candidates[best].update(det)
                if not self.candidates[best].promoted and self.candidates[best].hits >= self.add_threshold:
                    self.candidates[best].promoted = True
                    messages.append(f"promoted {self.candidates[best]}")
            else:
                self.candidates.append(BaselineCandidate(det))

        # Track misses — only on baseline cycles
        if is_cycle:
            to_remove = []
            for i, cand in enumerate(self.candidates):
                if i not in matched_candidates:
                    cand.misses += 1
                    if cand.promoted:
                        self._missed_promoted.append(i)
                    elif cand.misses >= 3:
                        to_remove.append(i)

            for i in reversed(to_remove):
                self.candidates.pop(i)
            # Re-index _missed_promoted after removals
            if to_remove:
                removed_set = set(to_remove)
                shift = [sum(1 for r in to_remove if r < i) for i in self._missed_promoted]
                self._missed_promoted = [
                    i - s for i, s in zip(self._missed_promoted, shift)
                    if i not in removed_set
                ]

        return messages

    @property
    def has_missed_promoted(self) -> bool:
        return len(self._missed_promoted) > 0

    def verify_missed(self, low_conf_detections: list[Detection]) -> list[str]:
        """Check missed promoted candidates against a low-confidence detection pass.
        Found → keep (reset misses). Not found → demote and remove."""
        messages: list[str] = []
        to_remove = []

        for i in self._missed_promoted:
            cand = self.candidates[i]
            found = any(self._match(d, cand) for d in low_conf_detections)
            if found:
                cand.misses = 0
                messages.append(f"verified at low-conf {cand}")
            else:
                messages.append(f"demoted (gone) {cand}")
                cand.promoted = False
                to_remove.append(i)

        for i in reversed(sorted(to_remove)):
            self.candidates.pop(i)

        self._missed_promoted = []
        return messages

    def get_baseline(self) -> list[Detection]:
        """Promoted candidates only — the stable baseline."""
        return [c.as_detection() for c in self.candidates if c.promoted]

    def get_all_seen(self) -> list[Detection]:
        """All candidates seen at least once — used during warmup."""
        return [c.as_detection() for c in self.candidates if c.hits > 0]


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
        self._scene_change_threshold = 0.05  # fraction of edge pixels that must differ

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

    def _patch_edge_change(self, camera_id: str, jpeg_bytes: bytes,
                           det: Detection, margin: float = 0.03) -> float | None:
        """Compare edge maps (Canny) of a detection's patch between the
        baseline reference frame and the current frame.

        Returns 0.0-1.0: fraction of edge pixels that are new (present in
        current but not in reference).  Edges are lighting-invariant — a
        cloud passing or dusk shift changes brightness but not object
        contours.  A static trashcan has the same edges in both frames
        (≈ 0%).  A person standing where there was none before introduces
        many new edge pixels (10-40%)."""
        ref = self._reference_frames.get(camera_id)
        if ref is None:
            return None

        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        cur = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if cur is None:
            return None

        h, w = cur.shape
        rh, rw = ref.shape
        if (h, w) != (rh, rw):
            return None

        # Crop patch with small margin
        x1 = max(0, int((det.cx - det.w / 2 - margin) * w))
        y1 = max(0, int((det.cy - det.h / 2 - margin) * h))
        x2 = min(w, int((det.cx + det.w / 2 + margin) * w))
        y2 = min(h, int((det.cy + det.h / 2 + margin) * h))

        if x2 - x1 < 8 or y2 - y1 < 8:
            return None

        ref_patch = ref[y1:y2, x1:x2]
        cur_patch = cur[y1:y2, x1:x2]

        # Canny edge detection on both patches
        ref_edges = cv2.Canny(ref_patch, 50, 150)
        cur_edges = cv2.Canny(cur_patch, 50, 150)

        # New edges = present in current but not in reference.
        # Dilate reference edges slightly so minor alignment jitter
        # (compression artifacts, sub-pixel shifts) doesn't count.
        kernel = np.ones((3, 3), dtype=np.uint8)
        ref_dilated = cv2.dilate(ref_edges, kernel, iterations=1)
        new_edges = cv2.bitwise_and(cur_edges, cv2.bitwise_not(ref_dilated))

        total_pixels = ref_patch.size
        new_edge_count = np.count_nonzero(new_edges)
        return new_edge_count / total_pixels if total_pixels > 0 else 0.0

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

                # Zone filter — ignore detections outside configured regions
                before_zone = len(detections)
                detections = self._filter_by_zone(camera_id=camera_id, detections=detections)
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
                            "Motion %s: %s novelty=%.3f < %.3f (environmental)",
                            camera_id, d.name, novelty, self._min_motion_novelty,
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
                            "Motion %s: ignoring (baseline not yet initialized)",
                            camera_id,
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

                # Visual similarity filter — compare edge maps (Canny) of
                # each "new" detection's patch against the baseline reference.
                # Edges are lighting-invariant: a cloud or dusk shift changes
                # brightness but not object contours.  A static trashcan has
                # the same edges in both frames (~0% new edges).  A person
                # introduces many new edge pixels (5-30%).
                if new and camera_id in self._reference_frames:
                    visually_new = []
                    for d in new:
                        edge_frac = self._patch_edge_change(camera_id, jpeg_bytes, d)
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
                    best_dets = detections
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
                                best_dets = delayed_dets
                                best_new = delayed_new
                            else:
                                log.debug(
                                    "Motion %s: keeping original frame (conf %.0f%% >= delayed %.0f%%)",
                                    camera_id, best_conf * 100, delayed_conf * 100,
                                )

                    annotated = self._annotate_frame(
                        jpeg_bytes=best_jpeg, detections=best_dets,
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
