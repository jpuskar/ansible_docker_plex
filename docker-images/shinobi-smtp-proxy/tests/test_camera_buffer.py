"""Tests for CameraBuffer in rtsp_reader.py."""
import time
from unittest.mock import patch

from rtsp_reader import CameraBuffer


class TestCameraBuffer:
    def test_add_and_total(self):
        buf = CameraBuffer(maxlen=5)
        assert buf.total() == 0
        buf.add(b"frame1")
        buf.add(b"frame2")
        assert buf.total() == 2

    def test_ring_buffer_evicts_oldest(self):
        buf = CameraBuffer(maxlen=2)
        buf.add(b"frame1")
        buf.add(b"frame2")
        buf.add(b"frame3")
        assert buf.total() == 2
        frames = buf.get_recent()
        assert frames == [b"frame2", b"frame3"]

    def test_get_recent_all(self):
        buf = CameraBuffer(maxlen=10)
        buf.add(b"a")
        buf.add(b"b")
        buf.add(b"c")
        assert buf.get_recent() == [b"a", b"b", b"c"]

    def test_get_recent_by_time(self):
        buf = CameraBuffer(maxlen=10)
        # Use monotonic timestamps; mock time to control them
        buf.add(b"old")  # default timestamp = time.monotonic()
        # Manually add a frame with an old timestamp
        with buf._lock:
            buf.frames.clear()
            now = time.monotonic()
            buf.frames.append((now - 10, b"old"))
            buf.frames.append((now - 0.5, b"recent1"))
            buf.frames.append((now, b"recent2"))
        result = buf.get_recent(seconds=2.0)
        assert result == [b"recent1", b"recent2"]

    def test_get_recent_empty(self):
        buf = CameraBuffer(maxlen=5)
        assert buf.get_recent() == []
        assert buf.get_recent(seconds=1.0) == []

    def test_evict_stale(self):
        buf = CameraBuffer(maxlen=10)
        with buf._lock:
            now = time.monotonic()
            buf.frames.append((now - 100, b"very_old"))
            buf.frames.append((now - 50, b"old"))
            buf.frames.append((now - 1, b"recent"))
        buf.evict_stale(max_age=10)
        assert buf.total() == 1
        assert buf.get_recent() == [b"recent"]
