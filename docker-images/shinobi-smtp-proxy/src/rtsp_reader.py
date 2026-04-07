from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque

import cv2
import numpy as np

import metrics as m

log = logging.getLogger("smtp-proxy")

# RTSP sub-stream template — MJPEG at 704x480 2fps
RTSP_SUBSTREAM = "rtsp://{user}:{passwd}@{ip}:554/cam/realmonitor?channel=1&subtype=1"

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
