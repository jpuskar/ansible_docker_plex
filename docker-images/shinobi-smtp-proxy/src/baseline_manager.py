import asyncio
import logging
import queue
import threading
import time
from collections import deque

import cv2
import numpy as np

log = logging.getLogger('smtp-proxy')

# RTSP sub-stream template — MJPEG at 704x480 2fps
RTSP_SUBSTREAM = 'rtsp://{user}:{passwd}@{ip}:554/cam/realmonitor?channel=1&subtype=1'

# HTTP snapshot fallback (not used by default)
SNAPSHOT_PATH = '/cgi-bin/snapshot.cgi?channel=1&subtype=1'

# Motion detection defaults
MOTION_THRESHOLD = 25       # pixel difference threshold (0-255)
MOTION_MIN_AREA = 500       # minimum contour area in pixels to count as motion
MOTION_COOLDOWN = 2.0       # seconds between motion events per camera


class CameraBuffer:
    """Thread-safe ring buffer of raw JPEG snapshots for one camera."""

    def __init__(self, maxlen):
        self.frames = deque(maxlen=maxlen)  # each entry: (timestamp, jpeg_bytes)
        self._lock = threading.Lock()

    def add(self, jpeg_bytes):
        with self._lock:
            self.frames.append((time.monotonic(), jpeg_bytes))

    def get_recent(self, seconds=None):
        with self._lock:
            if seconds is None or not self.frames:
                return [f[1] for f in self.frames]
            cutoff = time.monotonic() - seconds
            return [data for ts, data in self.frames if ts >= cutoff]

    def evict_stale(self, max_age):
        with self._lock:
            cutoff = time.monotonic() - max_age
            while self.frames and self.frames[0][0] < cutoff:
                self.frames.popleft()

    def total(self):
        with self._lock:
            return len(self.frames)


class RTSPReader(threading.Thread):
    """Background thread that reads from an RTSP stream into a CameraBuffer.

    Reconnects automatically with exponential backoff (cap 30s).
    Encodes each frame to JPEG before storing.
    Optionally performs frame-differencing motion detection and enqueues events.
    """

    def __init__(self, camera_id, rtsp_url, buf, target_fps=2,
                 motion_queue=None, motion_threshold=MOTION_THRESHOLD,
                 motion_min_area=MOTION_MIN_AREA):
        super().__init__(daemon=True, name=f'rtsp-{camera_id}')
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
        self._prev_gray = None
        self._last_motion = 0.0
        self.motion_events = 0  # counter for metrics

    def stop(self):
        self._stop_event.set()

    def _detect_motion(self, frame):
        """Returns True if significant motion is detected vs previous frame."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if self._prev_gray is None:
            self._prev_gray = gray
            return False
        delta = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray
        thresh = cv2.threshold(delta, self._motion_threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            if cv2.contourArea(c) >= self._motion_min_area:
                return True
        return False

    def run(self):
        while not self._stop_event.is_set():
            cap = None
            try:
                cap = cv2.VideoCapture()
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
                cap.open(self.rtsp_url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    raise ConnectionError(f"Cannot open RTSP stream for {self.camera_id}")
                self.connected = True
                if self._backoff > 0:
                    log.info("%s RTSP reconnected (was backed off %ds)", self.camera_id, self._backoff)
                self._backoff = 0
                self._prev_gray = None  # reset motion baseline on reconnect
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
                    ok, jpeg = cv2.imencode('.jpg', frame)
                    if not ok:
                        self.frames_fail += 1
                        continue
                    jpeg_bytes = jpeg.tobytes()
                    self.buf.add(jpeg_bytes)
                    self.frames_ok += 1
                    self.bytes_total += len(jpeg_bytes)

                    # Motion detection (if enabled)
                    if self._motion_queue is not None:
                        if self._detect_motion(frame):
                            if now - self._last_motion >= MOTION_COOLDOWN:
                                self._last_motion = now
                                self.motion_events += 1
                                try:
                                    self._motion_queue.put_nowait(
                                        (self.camera_id, jpeg_bytes))
                                except queue.Full:
                                    pass  # drop if processing is backed up

            except Exception as exc:
                self.connected = False
                self.frames_fail += 1
                self._backoff = min(max(self._backoff * 2, 1), 30)
                log.info("%s RTSP error, reconnect in %ds: %s", self.camera_id, self._backoff, exc)
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

    def __init__(self, cameras, username, password, detector,
                 snapshot_interval=1, buffer_seconds=10,
                 baseline_interval=60, position_tolerance=0.15,
                 strategy='rtsp', discord_notifier=None,
                 motion_detection=True, motion_threshold=MOTION_THRESHOLD,
                 motion_min_area=MOTION_MIN_AREA, detection_zones=None):
        self.cameras = cameras              # {camera_id: ip}
        self.username = username
        self.password = password
        self.detector = detector
        self.snapshot_interval = snapshot_interval
        self.buffer_seconds = buffer_seconds
        self.baseline_interval = baseline_interval
        self.position_tolerance = position_tolerance
        self.strategy = strategy
        self.discord_notifier = discord_notifier
        self.motion_detection = motion_detection

        # Detection zones: {camera_id: [numpy_polygon, ...]}
        # Each polygon is a numpy array of (x,y) pairs in normalized 0.0-1.0 coords
        self._zones = {}
        if detection_zones:
            for cam_id, zones in detection_zones.items():
                self._zones[cam_id] = [
                    np.array(poly, dtype=np.float32) for poly in zones
                ]
            log.info("Detection zones configured for: %s", ', '.join(self._zones.keys()))

        maxlen = max(buffer_seconds * 2, 10)  # 2fps * buffer_seconds
        self.buffers = {cam_id: CameraBuffer(maxlen) for cam_id in cameras}
        self.baselines = {}                 # {camera_id: [Detection, ...]}
        self._baseline_task = None
        self._metrics_task = None
        self._motion_task = None

        # Motion event queue: (camera_id, jpeg_bytes) from RTSP threads
        self._motion_queue = queue.Queue(maxsize=50) if motion_detection else None

        # RTSP readers (strategy='rtsp')
        self._readers = {}
        if strategy == 'rtsp':
            for cam_id, ip in cameras.items():
                url = RTSP_SUBSTREAM.format(user=username, passwd=password, ip=ip)
                self._readers[cam_id] = RTSPReader(
                    cam_id, url, self.buffers[cam_id],
                    motion_queue=self._motion_queue if motion_detection else None,
                    motion_threshold=motion_threshold,
                    motion_min_area=motion_min_area,
                )

        # HTTP snapshot state (strategy='http')
        self._http_clients = {}
        self._snapshot_task = None
        self._backoff = {cam_id: 0 for cam_id in cameras}
        self._next_poll = {cam_id: 0.0 for cam_id in cameras}
        self._BACKOFF_CAP = 10
        if strategy == 'http':
            import httpx
            logging.getLogger('httpx').setLevel(logging.WARNING)
            logging.getLogger('httpcore').setLevel(logging.WARNING)
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

    async def start(self):
        if self.strategy == 'rtsp':
            for reader in self._readers.values():
                reader.start()
            log.info("Camera manager started [rtsp]: %d cameras, %ds buffer, %ds baseline, motion=%s",
                     len(self.cameras), self.buffer_seconds, self.baseline_interval, self.motion_detection)
        elif self.strategy == 'http':
            self._snapshot_task = asyncio.create_task(self._http_snapshot_loop())
            log.info("Camera manager started [http]: %d cameras, %ds snapshots, %ds baseline, path=%s",
                     len(self.cameras), self.snapshot_interval, self.baseline_interval, SNAPSHOT_PATH)
        self._baseline_task = asyncio.create_task(self._baseline_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        if self.motion_detection:
            self._motion_task = asyncio.create_task(self._motion_loop())

    async def stop(self):
        for task in (self._snapshot_task, self._baseline_task, self._metrics_task, self._motion_task):
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

    # ================================================================
    # HTTP snapshot strategy (legacy, strategy='http')
    # ================================================================

    async def _http_snapshot_loop(self):
        while True:
            await self._http_snapshot_all()
            await asyncio.sleep(self.snapshot_interval)

    async def _http_snapshot_all(self):
        import httpx
        now = time.monotonic()
        for cam_id in self.cameras:
            self.buffers[cam_id].evict_stale(self.buffer_seconds)
        tasks = [self._http_grab_snapshot(cam_id, ip)
                 for cam_id, ip in self.cameras.items()
                 if now >= self._next_poll[cam_id]]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _http_grab_snapshot(self, camera_id, ip):
        import httpx
        try:
            url = f'http://{ip}{SNAPSHOT_PATH}'
            client = self._http_clients[camera_id]
            resp = await client.get(url)
            if resp.status_code == 401:
                log.warning("Auth failed (real 401) for %s — check credentials", camera_id)
                self._snap_fail[camera_id] += 1
                return
            resp.raise_for_status()
            self.buffers[camera_id].add(resp.content)
            self._snap_ok[camera_id] += 1
            self._snap_bytes[camera_id] += len(resp.content)
            if self._backoff[camera_id] > 0:
                log.info("%s recovered (was backed off %ds)", camera_id, self._backoff[camera_id])
                self._backoff[camera_id] = 0
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._snap_fail[camera_id] += 1
            prev = self._backoff[camera_id]
            self._backoff[camera_id] = min(max(prev * 2, 1), self._BACKOFF_CAP)
            self._next_poll[camera_id] = time.monotonic() + self._backoff[camera_id]
            log.info("%s timeout, backoff %ds (was %ds): %s",
                     camera_id, self._backoff[camera_id], prev, type(exc).__name__)
        except Exception:
            self._snap_fail[camera_id] += 1
            log.debug("Snapshot failed for %s", camera_id, exc_info=True)

    # ================================================================
    # Metrics loop (works with both strategies)
    # ================================================================

    async def _metrics_loop(self):
        while True:
            await asyncio.sleep(self._metrics_interval)

            if self.strategy == 'rtsp':
                self._collect_rtsp_metrics()

            ok_cams = [c for c, n in self._snap_ok.items() if n > 0]
            fail_cams = [c for c, n in self._snap_fail.items()
                         if n > 0 and self._snap_ok[c] == 0]
            partial = [c for c, n in self._snap_fail.items()
                       if n > 0 and self._snap_ok[c] > 0]

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
            log.info("Frames [%ds]: %s", self._metrics_interval, ' | '.join(parts))

            for cam_id in self.cameras:
                self._snap_ok[cam_id] = 0
                self._snap_fail[cam_id] = 0
                self._snap_bytes[cam_id] = 0

    def _collect_rtsp_metrics(self):
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

    def _filter_by_zone(self, camera_id, detections):
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
    # Motion detection loop (strategy='rtsp', motion_detection=True)
    # ================================================================

    async def _motion_loop(self):
        """Reads motion events from the queue, runs YOLO, compares baseline,
        sends Discord alert if new objects are found."""
        loop = asyncio.get_event_loop()
        while True:
            # Poll the thread-safe queue from async context
            try:
                camera_id, jpeg_bytes = await loop.run_in_executor(
                    None, self._motion_queue.get, True, 1.0)
            except Exception:
                continue

            try:
                detections = await self.detector.get_detections(jpeg_bytes)
                if not detections:
                    continue

                # Zone filter — ignore detections outside configured regions
                detections = self._filter_by_zone(camera_id, detections)
                if not detections:
                    log.debug("Motion %s: detections outside zone", camera_id)
                    continue

                baseline = self.baselines.get(camera_id, [])
                if not baseline:
                    names = ', '.join(d.name for d in detections)
                    log.info("Motion %s: %s (no baseline)", camera_id, names)
                    if self.discord_notifier:
                        await self.discord_notifier.send_alert(
                            camera_id, f"Motion: {names}", jpeg_bytes)
                    continue

                new = [d for d in detections
                       if not any(d.is_near(b, self.position_tolerance) for b in baseline)]
                if new:
                    names = ', '.join(d.name for d in new)
                    log.info("Motion %s: new objects: %s (det=%s, base=%s)",
                             camera_id, names,
                             [repr(d) for d in detections],
                             [repr(b) for b in baseline])
                    if self.discord_notifier:
                        await self.discord_notifier.send_alert(
                            camera_id, f"Motion: {names}", jpeg_bytes)
                else:
                    log.debug("Motion %s: only baseline objects", camera_id)

            except Exception:
                log.warning("Motion processing error for %s", camera_id, exc_info=True)

    # ================================================================
    # Baseline loop (works with both strategies)
    # ================================================================

    async def _baseline_loop(self):
        # Wait briefly for cameras to connect and buffer frames before first scan
        await asyncio.sleep(15)
        while True:
            for camera_id in self.cameras:
                try:
                    await self._update_baseline(camera_id)
                except Exception:
                    log.warning("Baseline update failed for %s", camera_id, exc_info=True)
            await asyncio.sleep(self.baseline_interval)

    async def _update_baseline(self, camera_id):
        buf = self.buffers.get(camera_id)
        recent = buf.get_recent(seconds=2) if buf else []
        total = buf.total() if buf else 0
        if not recent:
            log.debug("Baseline skipped for %s: no recent frames (0 of %d in buffer)", camera_id, total)
            return

        jpeg = recent[-1]
        detections = await self.detector.get_detections(jpeg)
        self.baselines[camera_id] = detections
        if detections:
            log.info("Baseline %s: %s", camera_id, [repr(d) for d in detections])
        log.debug("Baseline for %s: chose previous frame of %d available, %d detections",
                  camera_id, total, len(detections))

    # ================================================================
    # Event analysis (works with both strategies)
    # ================================================================

    async def analyze_event(self, camera_id):
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
            batch_indices = check_order[batch_start:batch_start + batch_size]
            tasks = [self.detector.get_detections(frames[i]) for i in batch_indices]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for idx, detections in zip(batch_indices, results):
                if isinstance(detections, Exception):
                    log.debug("Frame %d YOLO error: %s", idx, detections)
                    continue
                if not detections:
                    continue

                # Zone filter
                detections = self._filter_by_zone(camera_id, detections)
                if not detections:
                    continue

                if not baseline:
                    names = ', '.join(d.name for d in detections)
                    log.info("Frame %d/%d: detected %s (no baseline)", idx + 1, n, names)
                    return True, f"new objects (no baseline): {names}", frames[idx]

                new = [d for d in detections
                       if not any(d.is_near(b, self.position_tolerance) for b in baseline)]
                if new:
                    names = ', '.join(d.name for d in new)
                    log.info("Frame %d/%d: new objects: %s", idx + 1, n, names)
                    return True, f"new objects: {names}", frames[idx]

        log.info("No new objects in %d frames for %s", n, camera_id)
        return False, f"no new objects in {n} frames", None
