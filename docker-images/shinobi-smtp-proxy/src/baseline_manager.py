import asyncio
import io
import logging
import queue
import threading
import time
from collections import deque

import cv2
import numpy as np
from PIL import Image, ImageDraw

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

    def __init__(
        self,
        camera_id,
        rtsp_url,
        buf,
        target_fps=2,
        motion_queue=None,
        motion_threshold=MOTION_THRESHOLD,
        motion_min_area=MOTION_MIN_AREA,
    ):
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
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
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
                    raise ConnectionError(
                        f"Cannot open RTSP stream for {self.camera_id}"
                    )
                self.connected = True
                if self._backoff > 0:
                    log.info(
                        "%s RTSP reconnected (was backed off %ds)",
                        self.camera_id,
                        self._backoff,
                    )
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
                    ok, jpeg = cv2.imencode(".jpg", frame)
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
                                        (self.camera_id, jpeg_bytes)
                                    )
                                except queue.Full:
                                    pass  # drop if processing is backed up

            except Exception as exc:
                self.connected = False
                self.frames_fail += 1
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
        cameras,
        username,
        password,
        detector,
        snapshot_interval=1,
        buffer_seconds=10,
        baseline_interval=60,
        position_tolerance=0.15,
        strategy="rtsp",
        discord_notifier=None,
        motion_detection=True,
        motion_threshold=MOTION_THRESHOLD,
        motion_min_area=MOTION_MIN_AREA,
        detection_zones=None,
        confirm_cameras=None,
        min_detection_area=0.003,
        static_baselines=None,
        shinobi_notifier=None,
    ):
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
        from object_detector import Detection

        self._static_baselines = {}
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
        self._sticky_confirmed = {}
        # Low-confidence probe runs every N baseline cycles (e.g. 5 × 60s = 5min)
        # Also runs on cycle 1 (first baseline) so sticky kicks in immediately
        self._sticky_probe_every = 5
        self._sticky_probe_confidence = 0.15
        self._baseline_cycle = 0

        maxlen = max(buffer_seconds * 2, 10)  # 2fps * buffer_seconds
        self.buffers = {cam_id: CameraBuffer(maxlen) for cam_id in cameras}
        self.baselines = {}  # {camera_id: [Detection, ...]}
        self._baseline_initialized = set()  # cameras that have had at least one baseline scan
        self._baseline_task = None
        self._metrics_task = None
        self._motion_task = None

        # Motion event queue: (camera_id, jpeg_bytes) from RTSP threads
        self._motion_queue = queue.Queue(maxsize=50) if motion_detection else None

        # Pending alerts: require new objects to appear in 2 consecutive motion frames
        # before firing a Discord alert (filters single-frame YOLO hallucinations)
        # Only applies to cameras listed in confirm_cameras
        # {camera_id: {'names': str, 'jpeg': bytes, 'time': float}}
        self._pending_alerts = {}
        self._confirm_cameras = set(confirm_cameras or [])
        if self._confirm_cameras:
            log.info(
                "Confirmation required for: %s",
                ", ".join(sorted(self._confirm_cameras)),
            )

        # Minimum detection bounding box area (fraction of frame, 0.0-1.0)
        # Detections smaller than this are discarded as noise/hallucinations
        self._min_detection_area = min_detection_area

        # RTSP readers (strategy='rtsp')
        self._readers = {}
        if strategy == "rtsp":
            for cam_id, ip in cameras.items():
                url = RTSP_SUBSTREAM.format(user=username, passwd=password, ip=ip)
                self._readers[cam_id] = RTSPReader(
                    cam_id,
                    url,
                    self.buffers[cam_id],
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

    async def start(self):
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
        self._baseline_task = asyncio.create_task(self._baseline_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        if self.motion_detection:
            self._motion_task = asyncio.create_task(self._motion_loop())

    async def stop(self):
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

    async def _http_snapshot_loop(self):
        while True:
            await self._http_snapshot_all()
            await asyncio.sleep(self.snapshot_interval)

    async def _http_snapshot_all(self):
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

    async def _http_grab_snapshot(self, camera_id, ip):
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

    async def _metrics_loop(self):
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
    # Image annotation
    # ================================================================

    @staticmethod
    def _annotate_frame(jpeg_bytes, detections, new_detections=None):
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

    async def _send_alert(self, camera_id, description, jpeg_bytes, detections):
        """Send alert to Discord (with annotated image) and Shinobi (timeline event)."""
        if self.discord_notifier:
            await self.discord_notifier.send_alert(camera_id, description, jpeg_bytes)
        if self.shinobi_notifier:
            await self.shinobi_notifier.trigger_event(camera_id, detections)

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
                    None, self._motion_queue.get, True, 1.0
                )
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

                # Min area filter — discard tiny hallucinations from light flashes
                if self._min_detection_area > 0:
                    detections = [
                        d for d in detections if d.w * d.h >= self._min_detection_area
                    ]
                    if not detections:
                        log.debug("Motion %s: detections below min area", camera_id)
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
                    if camera_id in self._confirm_cameras:
                        now = time.monotonic()
                        pending = self._pending_alerts.get(camera_id)
                        if pending and now - pending["time"] < 5.0:
                            del self._pending_alerts[camera_id]
                            log.info(
                                "Motion %s: CONFIRMED %s (no baseline)",
                                camera_id,
                                names,
                            )
                            annotated = self._annotate_frame(jpeg_bytes, detections)
                            await self._send_alert(
                                camera_id, f"Motion: {names}", annotated, detections
                            )
                        else:
                            self._pending_alerts[camera_id] = {
                                "names": names,
                                "jpeg": jpeg_bytes,
                                "time": now,
                            }
                            log.info(
                                "Motion %s: pending %s (no baseline)", camera_id, names
                            )
                    else:
                        log.info("Motion %s: %s (no baseline)", camera_id, names)
                        annotated = self._annotate_frame(jpeg_bytes, detections)
                        await self._send_alert(
                            camera_id, f"Motion: {names}", annotated, detections
                        )
                    continue

                new = [
                    d
                    for d in detections
                    if not any(d.is_near(b, self.position_tolerance) for b in baseline)
                ]
                if new:
                    names = ", ".join(sorted(set(d.name for d in new)))
                    # Cameras with parked vehicles need 2-frame confirmation
                    # to filter single-frame YOLO hallucinations from headlight flashes
                    if camera_id in self._confirm_cameras:
                        now = time.monotonic()
                        pending = self._pending_alerts.get(camera_id)
                        if (
                            pending
                            and pending["names"] == names
                            and now - pending["time"] < 5.0
                        ):
                            del self._pending_alerts[camera_id]
                            log.info(
                                "Motion %s: CONFIRMED new objects: %s (det=%s, base=%s)",
                                camera_id,
                                names,
                                [repr(d) for d in detections],
                                [repr(b) for b in baseline],
                            )
                            annotated = self._annotate_frame(
                                jpeg_bytes, detections, new
                            )
                            await self._send_alert(
                                camera_id, f"Motion: {names}", annotated, new
                            )
                        elif (
                            not pending
                            or pending["names"] != names
                            or now - pending["time"] >= 5.0
                        ):
                            self._pending_alerts[camera_id] = {
                                "names": names,
                                "jpeg": jpeg_bytes,
                                "time": now,
                            }
                            log.info(
                                "Motion %s: pending confirmation: %s (det=%s, base=%s)",
                                camera_id,
                                names,
                                [repr(d) for d in detections],
                                [repr(b) for b in baseline],
                            )
                    else:
                        log.info(
                            "Motion %s: new objects: %s (det=%s, base=%s)",
                            camera_id,
                            names,
                            [repr(d) for d in detections],
                            [repr(b) for b in baseline],
                        )
                        annotated = self._annotate_frame(jpeg_bytes, detections, new)
                        await self._send_alert(
                            camera_id, f"Motion: {names}", annotated, new
                        )
                else:
                    # No new objects — clear any pending alert for this camera
                    self._pending_alerts.pop(camera_id, None)
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
            self._baseline_cycle += 1
            is_probe_cycle = self._static_baselines and (
                self._baseline_cycle == 1
                or self._baseline_cycle % self._sticky_probe_every == 0
            )

            for camera_id in self.cameras:
                try:
                    await self._update_baseline(camera_id, probe=is_probe_cycle)
                except Exception:
                    log.warning(
                        "Baseline update failed for %s", camera_id, exc_info=True
                    )
            await asyncio.sleep(self.baseline_interval)

    async def _update_baseline(self, camera_id, probe=False):
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
        detections = await self.detector.get_detections(
            jpeg, confidence_override=self.detector.confidence_threshold
        )

        # Sticky static baseline probe: periodically run at low confidence
        # to check if expected objects (e.g. parked car) are actually there
        static = self._static_baselines.get(camera_id, [])
        if static:
            if probe:
                low_conf_dets = await self.detector.get_detections(
                    jpeg, confidence_override=self._sticky_probe_confidence
                )
                confirmed = set()
                for i, s in enumerate(static):
                    if any(
                        s.is_near(d, self.position_tolerance) for d in detections
                    ) or any(
                        s.is_near(d, self.position_tolerance) for d in low_conf_dets
                    ):
                        confirmed.add(i)
                prev = self._sticky_confirmed.get(camera_id, set())
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
                if not any(s.is_near(d, self.position_tolerance) for d in detections):
                    detections.append(s)

        self.baselines[camera_id] = detections
        self._baseline_initialized.add(camera_id)
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
            batch_indices = check_order[batch_start : batch_start + batch_size]
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
                    names = ", ".join(d.name for d in detections)
                    log.info(
                        "Frame %d/%d: detected %s (no baseline)", idx + 1, n, names
                    )
                    annotated = self._annotate_frame(frames[idx], detections)
                    return True, f"new objects (no baseline): {names}", annotated

                new = [
                    d
                    for d in detections
                    if not any(d.is_near(b, self.position_tolerance) for b in baseline)
                ]
                if new:
                    names = ", ".join(d.name for d in new)
                    log.info("Frame %d/%d: new objects: %s", idx + 1, n, names)
                    annotated = self._annotate_frame(frames[idx], detections, new)
                    return True, f"new objects: {names}", annotated

        log.info("No new objects in %d frames for %s", n, camera_id)
        return False, f"no new objects in {n} frames", None
