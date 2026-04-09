"""Tests for the Detection dataclass in object_detector.py."""
from object_detector import Detection


def _det(cls_id=0, name="person", cx=0.5, cy=0.5, w=0.1, h=0.2, conf=0.9):
    return Detection(cls_id=cls_id, name=name, cx=cx, cy=cy, w=w, h=h, conf=conf)


# --- is_near ---

class TestIsNear:
    def test_same_class_close(self):
        a = _det(cls_id=0, cx=0.50, cy=0.50)
        b = _det(cls_id=0, cx=0.55, cy=0.55)
        assert a.is_near(b)

    def test_same_class_far(self):
        a = _det(cls_id=0, cx=0.1, cy=0.1)
        b = _det(cls_id=0, cx=0.9, cy=0.9)
        assert not a.is_near(b)

    def test_different_class_close(self):
        a = _det(cls_id=0, name="person", cx=0.5, cy=0.5)
        b = _det(cls_id=16, name="dog", cx=0.5, cy=0.5)
        assert not a.is_near(b)

    def test_vehicle_group_car_truck(self):
        """Car (2) and truck (7) are in the same vehicle group."""
        a = _det(cls_id=2, name="car", cx=0.5, cy=0.5)
        b = _det(cls_id=7, name="truck", cx=0.55, cy=0.55)
        assert a.is_near(b)

    def test_vehicle_group_car_bus(self):
        a = _det(cls_id=2, name="car", cx=0.5, cy=0.5)
        b = _det(cls_id=5, name="bus", cx=0.45, cy=0.48)
        assert a.is_near(b)

    def test_vehicle_group_motorcycle_truck(self):
        a = _det(cls_id=3, name="motorcycle", cx=0.5, cy=0.5)
        b = _det(cls_id=7, name="truck", cx=0.5, cy=0.5)
        assert a.is_near(b)

    def test_person_not_vehicle_group(self):
        a = _det(cls_id=0, name="person", cx=0.5, cy=0.5)
        b = _det(cls_id=2, name="car", cx=0.5, cy=0.5)
        assert not a.is_near(b)

    def test_tolerance_boundary_exact(self):
        """Just inside default tolerance of 0.15."""
        a = _det(cx=0.50, cy=0.50)
        b = _det(cx=0.649, cy=0.649)
        assert a.is_near(b)

    def test_tolerance_boundary_outside(self):
        """Just outside default tolerance of 0.15."""
        a = _det(cx=0.50, cy=0.50)
        b = _det(cx=0.66, cy=0.50)
        assert not a.is_near(b)

    def test_custom_tolerance(self):
        a = _det(cx=0.50, cy=0.50)
        b = _det(cx=0.70, cy=0.50)
        assert not a.is_near(b, tolerance=0.15)
        assert a.is_near(b, tolerance=0.25)


# --- max_novelty ---

class TestMaxNovelty:
    def test_no_rects(self):
        d = _det(cx=0.5, cy=0.5, w=0.2, h=0.2)
        assert d.max_novelty([]) == 0.0

    def test_overlapping_rect(self):
        d = _det(cx=0.5, cy=0.5, w=0.2, h=0.2)
        # Rect that fully covers the detection
        rects = [(0.3, 0.3, 0.4, 0.4, 0.8)]
        assert d.max_novelty(rects) == 0.8

    def test_non_overlapping_rect(self):
        d = _det(cx=0.1, cy=0.1, w=0.05, h=0.05)
        rects = [(0.8, 0.8, 0.1, 0.1, 0.9)]
        assert d.max_novelty(rects) == 0.0

    def test_picks_max_novelty(self):
        d = _det(cx=0.5, cy=0.5, w=0.3, h=0.3)
        rects = [
            (0.4, 0.4, 0.1, 0.1, 0.3),
            (0.5, 0.5, 0.1, 0.1, 0.7),
        ]
        assert d.max_novelty(rects) == 0.7

    def test_zero_area_detection(self):
        d = _det(cx=0.5, cy=0.5, w=0.0, h=0.0)
        rects = [(0.4, 0.4, 0.2, 0.2, 0.5)]
        assert d.max_novelty(rects) == 0.0

    def test_overlap_below_min_overlap(self):
        """Tiny overlap should not count."""
        d = _det(cx=0.5, cy=0.5, w=0.4, h=0.4)
        # Rect that barely touches corner of detection
        rects = [(0.69, 0.69, 0.02, 0.02, 0.9)]
        result = d.max_novelty(rects, min_overlap=0.1)
        assert result == 0.0


# --- repr ---

class TestRepr:
    def test_format(self):
        d = _det(name="car", cx=0.123, cy=0.456)
        assert repr(d) == "car@(0.12,0.46)"
