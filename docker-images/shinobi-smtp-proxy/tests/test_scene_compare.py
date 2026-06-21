"""Tests for scene_compare.py — zone filtering, annotation, edge comparison."""

import io

import cv2
import numpy as np
import pytest
from PIL import Image

from object_detector import Detection
from proxy_types.camera import CameraTuning, ZonePolygon, build_camera_configs
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


class TestBuildCameraConfigs:
    def test_none_returns_empty_configs(self):
        assert build_camera_configs(None) == []

    def test_requires_camera_list(self):
        with pytest.raises(ValueError, match="cameras must be a list"):
            build_camera_configs({"id": "cam1"})

    def test_requires_camera_mapping(self):
        with pytest.raises(ValueError, match=r"cameras\[0\] must be a mapping"):
            build_camera_configs(["cam1"])

    def test_rejects_duplicate_camera_ids(self):
        with pytest.raises(ValueError, match="duplicates 'cam1'"):
            build_camera_configs(
                [
                    {"id": "cam1", "host": "192.0.2.10"},
                    {"id": "cam1", "host": "192.0.2.11"},
                ]
            )

    def test_requires_non_empty_host(self):
        with pytest.raises(ValueError, match="host must be a non-empty string"):
            build_camera_configs([{"id": "cam1", "host": ""}])

    def test_requires_zone_points(self):
        with pytest.raises(ValueError, match="must define points"):
            build_camera_configs([{"id": "cam1", "host": "192.0.2.10", "zones": [{}]}])

    def test_converts_raw_config_to_camera_owned_zones(self):
        cameras = build_camera_configs(
            [
                {
                    "id": "cam1",
                    "host": "192.0.2.10",
                    "zones": [
                        {"points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]},
                    ],
                },
            ]
        )

        assert len(cameras) == 1
        assert cameras[0].id == "cam1"
        assert cameras[0].host == "192.0.2.10"
        assert len(cameras[0].zones) == 1
        assert cameras[0].zones[0].points.dtype == np.float32
        assert cameras[0].zones[0].points.shape == (3, 2)
        assert cameras[0].tuning == CameraTuning()

    def test_applies_default_camera_tuning(self):
        default_tuning = CameraTuning(
            min_motion_novelty=0.08,
            min_detection_area=0.01,
            position_tolerance=0.2,
            alert_cooldown_seconds=120.0,
        )

        cameras = build_camera_configs(
            [{"id": "cam1", "host": "192.0.2.10"}],
            default_tuning=default_tuning,
        )

        assert cameras[0].tuning == default_tuning

    def test_camera_tuning_overrides_defaults(self):
        default_tuning = CameraTuning(
            min_motion_novelty=0.08,
            min_detection_area=0.01,
            motion_threshold=30,
        )

        cameras = build_camera_configs(
            [
                {
                    "id": "cam1",
                    "host": "192.0.2.10",
                    "tuning": {
                        "min_detection_area": 0.002,
                        "motion_threshold": 20,
                        "followup_interval_seconds": 2.0,
                    },
                }
            ],
            default_tuning=default_tuning,
        )

        assert cameras[0].tuning.min_motion_novelty == 0.08
        assert cameras[0].tuning.min_detection_area == 0.002
        assert cameras[0].tuning.motion_threshold == 20
        assert cameras[0].tuning.followup_interval_seconds == 2.0

    def test_requires_camera_tuning_mapping(self):
        with pytest.raises(ValueError, match=r"cameras\[0\]\.tuning must be a mapping"):
            build_camera_configs(
                [{"id": "cam1", "host": "192.0.2.10", "tuning": "fast"}]
            )

    def test_requires_numeric_camera_tuning_value(self):
        with pytest.raises(
            ValueError,
            match=r"cameras\[0\]\.tuning\.min_motion_novelty must be numeric",
        ):
            build_camera_configs(
                [
                    {
                        "id": "cam1",
                        "host": "192.0.2.10",
                        "tuning": {"min_motion_novelty": "high"},
                    }
                ]
            )

    def test_requires_at_least_three_points(self):
        with pytest.raises(ValueError, match="at least 3 points"):
            build_camera_configs(
                [
                    {
                        "id": "cam1",
                        "host": "192.0.2.10",
                        "zones": [{"points": [[0.0, 0.0], [1.0, 0.0]]}],
                    }
                ]
            )

    def test_requires_xy_points(self):
        with pytest.raises(ValueError, match="must be \\[x, y\\]"):
            build_camera_configs(
                [
                    {
                        "id": "cam1",
                        "host": "192.0.2.10",
                        "zones": [{"points": [[0.0, 0.0], [1.0], [1.0, 1.0]]}],
                    }
                ]
            )

    def test_requires_normalized_coordinates(self):
        with pytest.raises(ValueError, match="normalized"):
            build_camera_configs(
                [
                    {
                        "id": "cam1",
                        "host": "192.0.2.10",
                        "zones": [{"points": [[0.0, 0.0], [1.2, 0.0], [1.0, 1.0]]}],
                    }
                ]
            )


class TestFilterByZone:
    def test_no_zones_passes_all(self):
        dets = [_det(cx=0.1, cy=0.1), _det(cx=0.9, cy=0.9)]
        result = filter_by_zone(dets, zones=[])
        assert len(result) == 2

    def test_inside_zone_passes(self):
        # Square zone covering center: (0.2,0.2) to (0.8,0.8)
        zone = ZonePolygon.from_points([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])
        d_inside = _det(cx=0.5, cy=0.5)
        d_outside = _det(cx=0.1, cy=0.1)
        result = filter_by_zone([d_inside, d_outside], zones=[zone])
        assert len(result) == 1
        assert result[0].cx == 0.5


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

    def test_identical_frames_low_change(self):
        """Same image for both reference and current → ~0 edge change."""
        gray = self._gray_np(200, 200, value=100)
        # Draw some edges so there's something to compare
        cv2.rectangle(gray, (50, 50), (150, 150), 200, 2)

        _, encoded = cv2.imencode(".jpg", gray)
        jpeg = encoded.tobytes()

        d = _det(cx=0.5, cy=0.5, w=0.6, h=0.6)
        frac = patch_edge_change("cam1", jpeg, d, gray.copy())
        assert frac is not None
        assert frac < 0.2  # should be very low for identical images

    def test_new_object_high_change(self):
        """Adding a strong shape to the current frame should produce high edge change."""
        ref = self._gray_np(200, 200, value=128)

        # Current frame: add a bright rectangle (simulates person-shaped object)
        cur = ref.copy()
        cv2.rectangle(cur, (70, 30), (130, 170), 255, -1)  # filled rect
        cv2.rectangle(cur, (70, 30), (130, 170), 0, 3)  # strong border

        _, encoded = cv2.imencode(".jpg", cur)
        jpeg = encoded.tobytes()

        d = _det(cx=0.5, cy=0.5, w=0.5, h=0.8)
        frac = patch_edge_change("cam1", jpeg, d, ref.copy())
        assert frac is not None
        assert frac > 0.3  # significant new edges

    def test_mismatched_resolution_returns_none(self):
        reference_frame = self._gray_np(200, 200)
        jpeg = _make_gray_jpeg(100, 100)  # different resolution
        d = _det()
        result = patch_edge_change("cam1", jpeg, d, reference_frame)
        assert result is None

    def test_tiny_patch_returns_none(self):
        """Detection too small → patch < 8px → returns None."""
        reference_frame = self._gray_np(100, 100)
        jpeg = _make_gray_jpeg(100, 100)
        d = _det(cx=0.5, cy=0.5, w=0.01, h=0.01)  # tiny
        result = patch_edge_change("cam1", jpeg, d, reference_frame)
        assert result is None
