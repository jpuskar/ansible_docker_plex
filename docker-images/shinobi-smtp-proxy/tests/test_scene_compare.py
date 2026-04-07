"""Tests for scene_compare.py — zone filtering, annotation, edge comparison."""
import io

import cv2
import numpy as np
from PIL import Image

from object_detector import Detection
from scene_compare import filter_by_zone, annotate_frame, patch_edge_change


def _det(cls_id=0, name="person", cx=0.5, cy=0.5, w=0.1, h=0.2, conf=0.9):
    return Detection(cls_id=cls_id, name=name, cx=cx, cy=cy, w=w, h=h, conf=conf)


def _make_jpeg(width=100, height=100, color=(128, 128, 128)):
    """Create a solid-color JPEG as bytes."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _make_gray_jpeg(width=100, height=100, value=128):
    """Create a solid grayscale JPEG using cv2 (matches how reference frames are built)."""
    gray = np.full((height, width), value, dtype=np.uint8)
    _, encoded = cv2.imencode(".jpg", gray)
    return encoded.tobytes()


# --- filter_by_zone ---

class TestFilterByZone:
    def test_no_zones_passes_all(self):
        dets = [_det(cx=0.1, cy=0.1), _det(cx=0.9, cy=0.9)]
        result = filter_by_zone("cam1", dets, zones={})
        assert len(result) == 2

    def test_inside_zone_passes(self):
        # Square zone covering center: (0.2,0.2) to (0.8,0.8)
        poly = np.array([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]], dtype=np.float32)
        zones = {"cam1": [poly]}
        d_inside = _det(cx=0.5, cy=0.5)
        d_outside = _det(cx=0.1, cy=0.1)
        result = filter_by_zone("cam1", [d_inside, d_outside], zones)
        assert len(result) == 1
        assert result[0].cx == 0.5

    def test_different_camera_no_zones(self):
        poly = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32)
        zones = {"cam1": [poly]}
        result = filter_by_zone("cam2", [_det()], zones)
        assert len(result) == 1  # no zones for cam2, passes through


# --- annotate_frame ---

class TestAnnotateFrame:
    def test_returns_valid_jpeg(self):
        jpeg = _make_jpeg(200, 200)
        d = _det(cx=0.5, cy=0.5, w=0.3, h=0.3)
        result = annotate_frame(jpeg, [d])
        # Should be valid JPEG
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"

    def test_green_box_for_new(self):
        jpeg = _make_jpeg(100, 100, color=(255, 255, 255))
        d = _det(cx=0.5, cy=0.5, w=0.4, h=0.4)
        result = annotate_frame(jpeg, [d], new_detections=[d])
        # Just verify it doesn't crash and output is different from input
        assert result != jpeg

    def test_handles_empty_detections(self):
        jpeg = _make_jpeg()
        result = annotate_frame(jpeg, [])
        img = Image.open(io.BytesIO(result))
        assert img.size == (100, 100)

    def test_invalid_jpeg_returns_raw(self):
        bad_data = b"not a jpeg at all"
        result = annotate_frame(bad_data, [_det()])
        assert result == bad_data


# --- patch_edge_change ---

class TestPatchEdgeChange:
    def _gray_np(self, width=100, height=100, value=128):
        """Create a numpy grayscale array (simulates a reference frame)."""
        return np.full((height, width), value, dtype=np.uint8)

    def test_no_reference_returns_none(self):
        jpeg = _make_gray_jpeg()
        result = patch_edge_change("cam1", jpeg, _det(), reference_frames={})
        assert result is None

    def test_identical_frames_low_change(self):
        """Same image for both reference and current → ~0 edge change."""
        gray = self._gray_np(200, 200, value=100)
        # Draw some edges so there's something to compare
        cv2.rectangle(gray, (50, 50), (150, 150), 200, 2)

        ref_frames = {"cam1": gray.copy()}
        _, encoded = cv2.imencode(".jpg", gray)
        jpeg = encoded.tobytes()

        d = _det(cx=0.5, cy=0.5, w=0.6, h=0.6)
        frac = patch_edge_change("cam1", jpeg, d, ref_frames)
        assert frac is not None
        assert frac < 0.2  # should be very low for identical images

    def test_new_object_high_change(self):
        """Adding a strong shape to the current frame should produce high edge change."""
        ref = self._gray_np(200, 200, value=128)
        ref_frames = {"cam1": ref.copy()}

        # Current frame: add a bright rectangle (simulates person-shaped object)
        cur = ref.copy()
        cv2.rectangle(cur, (70, 30), (130, 170), 255, -1)  # filled rect
        cv2.rectangle(cur, (70, 30), (130, 170), 0, 3)      # strong border

        _, encoded = cv2.imencode(".jpg", cur)
        jpeg = encoded.tobytes()

        d = _det(cx=0.5, cy=0.5, w=0.5, h=0.8)
        frac = patch_edge_change("cam1", jpeg, d, ref_frames)
        assert frac is not None
        assert frac > 0.3  # significant new edges

    def test_mismatched_resolution_returns_none(self):
        ref_frames = {"cam1": self._gray_np(200, 200)}
        jpeg = _make_gray_jpeg(100, 100)  # different resolution
        d = _det()
        result = patch_edge_change("cam1", jpeg, d, ref_frames)
        assert result is None

    def test_tiny_patch_returns_none(self):
        """Detection too small → patch < 8px → returns None."""
        ref_frames = {"cam1": self._gray_np(100, 100)}
        jpeg = _make_gray_jpeg(100, 100)
        d = _det(cx=0.5, cy=0.5, w=0.01, h=0.01)  # tiny
        result = patch_edge_change("cam1", jpeg, d, ref_frames)
        assert result is None
