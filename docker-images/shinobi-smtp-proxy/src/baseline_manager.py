import asyncio
import logging
import time
from collections import deque

import httpx

log = logging.getLogger('smtp-proxy')

# Digest auth always does 401→retry on new connections; these cameras close
# connections after each response, so every poll shows 401+200.  Noise.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

SNAPSHOT_PATH = '/cgi-bin/snapshot.cgi?channel=1&subtype=1'


class CameraBuffer:
    """Ring buffer of raw JPEG snapshots for one camera."""

    def __init__(self, maxlen):
        self.frames = deque(maxlen=maxlen)  # each entry: (timestamp, jpeg_bytes)

    def add(self, jpeg_bytes):
        self.frames.append((time.monotonic(), jpeg_bytes))

    def get_recent(self, seconds=None):
        """Return list of jpeg bytes from the last N seconds (or all if None)."""
        if seconds is None or not self.frames:
            return [f[1] for f in self.frames]
        cutoff = time.monotonic() - seconds
        return [data for ts, data in self.frames if ts >= cutoff]

    def evict_stale(self, max_age):
        """Remove frames older than max_age seconds."""
        cutoff = time.monotonic() - max_age
        while self.frames and self.frames[0][0] < cutoff:
            self.frames.popleft()

    def total(self):
        return len(self.frames)


class BaselineManager:
    """Continuously snapshots cameras, maintains rolling buffers and periodic baselines.

    - Every snapshot_interval seconds: grabs a JPEG from each camera into the ring buffer
    - Every baseline_interval seconds: runs YOLO on one frame per camera to build baseline
    - On event: analyzes buffered pre-event frames + grabs post-event frames from camera
    """

    def __init__(self, cameras, username, password, detector,
                 snapshot_interval=1, buffer_seconds=10,
                 baseline_interval=60, position_tolerance=0.15):
        self.cameras = cameras              # {camera_id: ip}
        self.username = username
        self.password = password
        self.detector = detector
        self.snapshot_interval = snapshot_interval
        self.buffer_seconds = buffer_seconds
        self.baseline_interval = baseline_interval
        self.position_tolerance = position_tolerance

        maxlen = max(buffer_seconds // max(snapshot_interval, 1), 5)
        self.buffers = {cam_id: CameraBuffer(maxlen) for cam_id in cameras}
        self.baselines = {}                 # {camera_id: [Detection, ...]}
        self._snapshot_task = None
        self._baseline_task = None
        self._metrics_task = None

        # Persistent httpx clients per camera — reuses TCP connections and
        # digest auth nonces, avoiding a 401 challenge on every request.
        self._clients = {}
        for cam_id, ip in cameras.items():
            transport = httpx.AsyncHTTPTransport(retries=1)
            self._clients[cam_id] = httpx.AsyncClient(
                auth=httpx.DigestAuth(username, password),
                timeout=1,
                transport=transport,
            )

        # Per-camera snapshot counters (reset each metrics interval)
        self._snap_ok = {cam_id: 0 for cam_id in cameras}
        self._snap_fail = {cam_id: 0 for cam_id in cameras}
        self._snap_bytes = {cam_id: 0 for cam_id in cameras}
        self._metrics_interval = 10

        # Per-camera exponential backoff on timeout (cap 10s)
        self._backoff = {cam_id: 0 for cam_id in cameras}      # current backoff seconds
        self._next_poll = {cam_id: 0.0 for cam_id in cameras}  # monotonic time
        self._BACKOFF_CAP = 10

    async def start(self):
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())
        self._baseline_task = asyncio.create_task(self._baseline_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        log.info("Camera manager started: %d cameras, %ds snapshots, %ds baseline, path=%s",
                 len(self.cameras), self.snapshot_interval, self.baseline_interval, SNAPSHOT_PATH)

    async def stop(self):
        for task in (self._snapshot_task, self._baseline_task, self._metrics_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for client in self._clients.values():
            await client.aclose()

    # -- Snapshot loop: grab JPEGs into ring buffers --

    async def _snapshot_loop(self):
        while True:
            await self._snapshot_all()
            await asyncio.sleep(self.snapshot_interval)

    async def _snapshot_all(self):
        now = time.monotonic()
        # Evict stale frames from backed-off cameras so we don't hold old data
        for cam_id in self.cameras:
            self.buffers[cam_id].evict_stale(self.buffer_seconds)
        tasks = [self._grab_snapshot(cam_id, ip)
                 for cam_id, ip in self.cameras.items()
                 if now >= self._next_poll[cam_id]]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _grab_snapshot(self, camera_id, ip):
        try:
            url = f'http://{ip}{SNAPSHOT_PATH}'
            client = self._clients[camera_id]
            resp = await client.get(url)
            if resp.status_code == 401:
                log.warning("Auth failed (real 401) for %s — check credentials", camera_id)
                self._snap_fail[camera_id] += 1
                return
            resp.raise_for_status()
            self.buffers[camera_id].add(resp.content)
            self._snap_ok[camera_id] += 1
            self._snap_bytes[camera_id] += len(resp.content)
            # Success — reset backoff if we were backed off
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

    # -- Metrics loop: periodic snapshot health summary --

    async def _metrics_loop(self):
        while True:
            await asyncio.sleep(self._metrics_interval)
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
            log.info("Snapshots [%ds]: %s", self._metrics_interval, ' | '.join(parts))

            # Reset counters
            for cam_id in self.cameras:
                self._snap_ok[cam_id] = 0
                self._snap_fail[cam_id] = 0
                self._snap_bytes[cam_id] = 0

    # -- Baseline loop: run YOLO periodically on quiet frames --

    async def _baseline_loop(self):
        # Wait one full interval before first baseline so buffers fill
        await asyncio.sleep(self.baseline_interval)
        while True:
            for camera_id, ip in self.cameras.items():
                try:
                    await self._update_baseline(camera_id, ip)
                except (httpx.TimeoutException, httpx.ConnectError):
                    log.debug("Baseline update skipped for %s (timeout)", camera_id)
                except Exception:
                    log.warning("Baseline update failed for %s", camera_id, exc_info=True)
            await asyncio.sleep(self.baseline_interval)

    async def _update_baseline(self, camera_id, ip):
        # Use the most recent buffered frame if available, else grab one
        buf = self.buffers.get(camera_id)
        recent = buf.get_recent(seconds=2) if buf else []
        total = buf.total() if buf else 0
        if recent:
            jpeg = recent[-1]
            source = "previous frame"
        else:
            url = f'http://{ip}{SNAPSHOT_PATH}'
            client = self._clients[camera_id]
            resp = await client.get(url)
            resp.raise_for_status()
            jpeg = resp.content
            source = "live fetch"

        detections = await self.detector.get_detections(jpeg)
        self.baselines[camera_id] = detections
        log.debug("Baseline for %s: chose %s of %d available frames, %d detections",
                  camera_id, source, total, len(detections))

    # -- Event analysis: check buffered frames in batches, middle-out --

    async def analyze_event(self, camera_id):
        """Called when an event email arrives. Runs YOLO on buffered frames
        in batches of 3, middle-out order. Returns as soon as a new object is found."""

        buf = self.buffers.get(camera_id)
        if not buf:
            log.warning("No buffer for camera %s", camera_id)
            return True, "unknown camera, allowing"

        frames = buf.get_recent(seconds=self.buffer_seconds)
        if not frames:
            return True, "no frames buffered, allowing"

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

                if not baseline:
                    names = ', '.join(d.name for d in detections)
                    log.info("Frame %d/%d: detected %s (no baseline)", idx + 1, n, names)
                    return True, f"new objects (no baseline): {names}"

                new = [d for d in detections
                       if not any(d.is_near(b, self.position_tolerance) for b in baseline)]
                if new:
                    names = ', '.join(d.name for d in new)
                    log.info("Frame %d/%d: new objects: %s", idx + 1, n, names)
                    return True, f"new objects: {names}"

        log.info("No new objects in %d frames for %s", n, camera_id)
        return False, f"no new objects in {n} frames"
