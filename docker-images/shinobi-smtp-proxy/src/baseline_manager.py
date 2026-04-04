import asyncio
import logging
import time
from collections import deque

import httpx

log = logging.getLogger('smtp-proxy')

SNAPSHOT_PATH = '/cgi-bin/snapshot.cgi?channel=1'


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

    async def start(self):
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())
        self._baseline_task = asyncio.create_task(self._baseline_loop())
        log.info("Camera manager started: %d cameras, %ds snapshots, %ds baseline",
                 len(self.cameras), self.snapshot_interval, self.baseline_interval)

    async def stop(self):
        for task in (self._snapshot_task, self._baseline_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # -- Snapshot loop: grab JPEGs into ring buffers --

    async def _snapshot_loop(self):
        while True:
            await self._snapshot_all()
            await asyncio.sleep(self.snapshot_interval)

    async def _snapshot_all(self):
        tasks = [self._grab_snapshot(cam_id, ip)
                 for cam_id, ip in self.cameras.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _grab_snapshot(self, camera_id, ip):
        try:
            url = f'http://{ip}{SNAPSHOT_PATH}'
            auth = httpx.DigestAuth(self.username, self.password)
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, auth=auth)
                resp.raise_for_status()
            self.buffers[camera_id].add(resp.content)
        except Exception:
            log.debug("Snapshot failed for %s", camera_id, exc_info=True)

    # -- Baseline loop: run YOLO periodically on quiet frames --

    async def _baseline_loop(self):
        # Wait one full interval before first baseline so buffers fill
        await asyncio.sleep(self.baseline_interval)
        while True:
            for camera_id, ip in self.cameras.items():
                try:
                    await self._update_baseline(camera_id, ip)
                except Exception:
                    log.exception("Baseline update failed for %s", camera_id)
            await asyncio.sleep(self.baseline_interval)

    async def _update_baseline(self, camera_id, ip):
        # Use the most recent buffered frame if available, else grab one
        buf = self.buffers.get(camera_id)
        frames = buf.get_recent(seconds=2) if buf else []
        if frames:
            jpeg = frames[-1]
        else:
            url = f'http://{ip}{SNAPSHOT_PATH}'
            auth = httpx.DigestAuth(self.username, self.password)
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, auth=auth)
                resp.raise_for_status()
            jpeg = resp.content

        detections = await self.detector.get_detections(jpeg)
        self.baselines[camera_id] = detections
        if detections:
            log.debug("Baseline for %s: %s", camera_id, detections)

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
