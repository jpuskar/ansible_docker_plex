"""Tests for the composable detection-filter pipeline (pipeline.py)."""

from object_detector import Detection
from pipeline import (
    CooldownFilter,
    DetectionPipeline,
    EdgeChangeFilter,
    FilterContext,
    MinAreaFilter,
    NoveltyFilter,
    ZoneFilter,
)
from proxy_types.camera import ZonePolygon


def _det(cls_id=0, name="person", cx=0.5, cy=0.5, w=0.1, h=0.2, conf=0.9):
    return Detection(cls_id=cls_id, name=name, cx=cx, cy=cy, w=w, h=h, conf=conf)


class TestZoneFilter:
    def test_no_zones_passes_all(self):
        dets = [_det(cx=0.1, cy=0.1), _det(cx=0.9, cy=0.9)]
        ctx = FilterContext(camera_id="cam")
        assert ZoneFilter().apply(dets, ctx) == dets

    def test_keeps_only_inside_zone(self):
        # Zone covering the left half of the frame
        zone = ZonePolygon.from_points([[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]])
        inside = _det(cx=0.25, cy=0.5)
        outside = _det(cx=0.75, cy=0.5)
        ctx = FilterContext(camera_id="cam", zones=[zone])
        kept = ZoneFilter().apply([inside, outside], ctx)
        assert kept == [inside]


class TestNoveltyFilter:
    def test_high_novelty_kept_low_dropped(self):
        d = _det(cx=0.5, cy=0.5, w=0.2, h=0.2)
        # A motion rect fully overlapping d, with high novelty
        ctx_high = FilterContext(
            camera_id="cam", motion_rects=[(0.4, 0.4, 0.2, 0.2, 0.5)]
        )
        ctx_low = FilterContext(
            camera_id="cam", motion_rects=[(0.4, 0.4, 0.2, 0.2, 0.01)]
        )
        assert NoveltyFilter(0.05).apply([d], ctx_high) == [d]
        assert NoveltyFilter(0.05).apply([d], ctx_low) == []

    def test_no_motion_rects_drops(self):
        d = _det()
        ctx = FilterContext(camera_id="cam", motion_rects=[])
        assert NoveltyFilter(0.05).apply([d], ctx) == []


class TestMinAreaFilter:
    def test_drops_below_area(self):
        big = _det(w=0.2, h=0.2)  # area 0.04
        small = _det(w=0.01, h=0.01)  # area 0.0001
        ctx = FilterContext(camera_id="cam")
        assert MinAreaFilter(0.003).apply([big, small], ctx) == [big]

    def test_zero_threshold_passes_all(self):
        dets = [_det(w=0.001, h=0.001)]
        ctx = FilterContext(camera_id="cam")
        assert MinAreaFilter(0.0).apply(dets, ctx) == dets


class TestEdgeChangeFilter:
    def test_passthrough_without_reference(self):
        dets = [_det()]
        ctx = FilterContext(camera_id="cam", reference_frame=None)
        assert EdgeChangeFilter(0.15).apply(dets, ctx) == dets


class TestCooldownFilter:
    def test_drops_near_recent_alert(self):
        prev = _det(cx=0.5, cy=0.5)
        near = _det(cx=0.52, cy=0.52)
        far = _det(cx=0.1, cy=0.1)
        ctx = FilterContext(camera_id="cam", recent_alerts=[(0.0, prev)])
        kept = CooldownFilter(0.15).apply([near, far], ctx)
        assert kept == [far]

    def test_empty_recent_passes_all(self):
        dets = [_det(cx=0.5, cy=0.5)]
        ctx = FilterContext(camera_id="cam", recent_alerts=[])
        assert CooldownFilter(0.15).apply(dets, ctx) == dets


class TestDetectionPipeline:
    def test_runs_in_order_and_short_circuits(self):
        # min-area removes the small box; cooldown would remove the survivor
        big = _det(cx=0.5, cy=0.5, w=0.2, h=0.2)
        small = _det(cx=0.8, cy=0.8, w=0.01, h=0.01)
        ctx = FilterContext(
            camera_id="cam",
            recent_alerts=[(0.0, _det(cx=0.5, cy=0.5))],  # near big
        )
        pipe = DetectionPipeline([MinAreaFilter(0.003), CooldownFilter(0.15)])
        assert pipe.run([big, small], ctx) == []

    def test_survivor_passes_all_stages(self):
        survivor = _det(cx=0.1, cy=0.1, w=0.2, h=0.2)
        ctx = FilterContext(
            camera_id="cam", recent_alerts=[(0.0, _det(cx=0.9, cy=0.9))]
        )
        pipe = DetectionPipeline([MinAreaFilter(0.003), CooldownFilter(0.15)])
        assert pipe.run([survivor], ctx) == [survivor]

    def test_empty_input(self):
        ctx = FilterContext(camera_id="cam")
        pipe = DetectionPipeline([MinAreaFilter(0.003)])
        assert pipe.run([], ctx) == []
