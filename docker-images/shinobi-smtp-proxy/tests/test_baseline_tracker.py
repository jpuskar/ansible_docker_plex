"""Tests for BaselineTracker and related functions in baseline_tracker.py."""
from object_detector import Detection
from baseline_tracker import _same_class_group, BaselineCandidate, BaselineTracker


def _det(cls_id=0, name="person", cx=0.5, cy=0.5, w=0.1, h=0.2, conf=0.9):
    return Detection(cls_id=cls_id, name=name, cx=cx, cy=cy, w=w, h=h, conf=conf)


# --- _same_class_group ---

class TestSameClassGroup:
    def test_identical(self):
        assert _same_class_group(0, 0)

    def test_different_non_vehicle(self):
        assert not _same_class_group(0, 16)

    def test_car_truck(self):
        assert _same_class_group(2, 7)

    def test_car_bus(self):
        assert _same_class_group(2, 5)

    def test_motorcycle_bus(self):
        assert _same_class_group(3, 5)

    def test_person_car(self):
        assert not _same_class_group(0, 2)


# --- BaselineCandidate ---

class TestBaselineCandidate:
    def test_initial_state(self):
        d = _det(cls_id=2, name="car", cx=0.3, cy=0.4)
        c = BaselineCandidate(d)
        assert c.cls_id == 2
        assert c.hits == 1
        assert c.misses == 0
        assert not c.promoted

    def test_ema_update(self):
        d = _det(cx=0.0, cy=0.0, w=0.1, h=0.1)
        c = BaselineCandidate(d)
        # After update with position 1.0, EMA(alpha=0.3) = 0.0*0.7 + 1.0*0.3 = 0.3
        c.update(_det(cx=1.0, cy=1.0, w=0.5, h=0.5))
        assert abs(c.cx - 0.3) < 1e-6
        assert abs(c.cy - 0.3) < 1e-6
        assert c.hits == 2
        assert c.misses == 0

    def test_class_flip_on_update(self):
        """Updating with a truck should flip a car candidate to truck."""
        c = BaselineCandidate(_det(cls_id=2, name="car"))
        c.update(_det(cls_id=7, name="truck"))
        assert c.cls_id == 7
        assert c.name == "truck"

    def test_as_detection(self):
        c = BaselineCandidate(_det(cls_id=0, name="person", cx=0.5, cy=0.6))
        d = c.as_detection()
        assert d.cls_id == 0
        assert d.conf == 1.0
        assert abs(d.cx - 0.5) < 1e-6


# --- BaselineTracker ---

class TestBaselineTrackerPromotion:
    def test_promote_after_threshold(self):
        """Object must be seen add_threshold times to be promoted."""
        tracker = BaselineTracker(add_threshold=3)
        d = _det(cx=0.5, cy=0.5)

        tracker.update([d])
        assert len(tracker.get_baseline()) == 0

        tracker.update([d])
        assert len(tracker.get_baseline()) == 0

        msgs = tracker.update([d])
        assert len(tracker.get_baseline()) == 1
        assert any("promoted" in m for m in msgs)

    def test_no_promote_before_threshold(self):
        tracker = BaselineTracker(add_threshold=5)
        d = _det()
        for _ in range(4):
            tracker.update([d])
        assert len(tracker.get_baseline()) == 0

    def test_is_warm(self):
        tracker = BaselineTracker(add_threshold=3)
        assert not tracker.is_warm
        tracker.update([])
        assert not tracker.is_warm
        tracker.update([])
        assert not tracker.is_warm
        tracker.update([])
        assert tracker.is_warm


class TestBaselineTrackerMisses:
    def test_unpromoted_candidate_removed_after_3_misses(self):
        tracker = BaselineTracker(add_threshold=5)
        d = _det(cx=0.5, cy=0.5)
        tracker.update([d])  # hit
        tracker.update([])   # miss 1
        tracker.update([])   # miss 2
        tracker.update([])   # miss 3 → removed
        assert len(tracker.candidates) == 0

    def test_promoted_not_removed_on_miss(self):
        """Promoted candidates stay until verify_missed demotes them."""
        tracker = BaselineTracker(add_threshold=2)
        d = _det(cx=0.5, cy=0.5)
        tracker.update([d])
        tracker.update([d])  # promoted
        assert len(tracker.get_baseline()) == 1

        tracker.update([])   # miss — but promoted, not removed
        assert len(tracker.candidates) == 1
        assert tracker.has_missed_promoted


class TestBaselineTrackerVerify:
    def test_verify_found_keeps_candidate(self):
        tracker = BaselineTracker(add_threshold=2)
        d = _det(cx=0.5, cy=0.5)
        tracker.update([d])
        tracker.update([d])  # promoted
        tracker.update([])   # miss

        assert tracker.has_missed_promoted
        # Low-confidence pass finds it
        msgs = tracker.verify_missed([d])
        assert any("verified" in m for m in msgs)
        assert len(tracker.get_baseline()) == 1

    def test_verify_not_found_demotes(self):
        tracker = BaselineTracker(add_threshold=2)
        d = _det(cx=0.5, cy=0.5)
        tracker.update([d])
        tracker.update([d])
        tracker.update([])

        # Low-confidence pass does NOT find it
        msgs = tracker.verify_missed([])
        assert any("demoted" in m for m in msgs)
        assert len(tracker.get_baseline()) == 0


class TestBaselineTrackerObserve:
    def test_observe_creates_candidate(self):
        tracker = BaselineTracker(add_threshold=3)
        d = _det(cx=0.3, cy=0.3)
        tracker.observe([d])
        assert len(tracker.candidates) == 1
        assert tracker.candidates[0].hits == 1

    def test_observe_does_not_increment_cycle(self):
        tracker = BaselineTracker(add_threshold=3)
        for _ in range(5):
            tracker.observe([_det()])
        assert tracker.cycles == 0
        assert not tracker.is_warm

    def test_observe_does_not_count_misses(self):
        """Observe should not penalize absent candidates."""
        tracker = BaselineTracker(add_threshold=3)
        d1 = _det(cx=0.2, cy=0.2)
        d2 = _det(cx=0.8, cy=0.8)
        tracker.observe([d1, d2])
        # Observe with only d1 — d2 should NOT get a miss
        tracker.observe([d1])
        assert tracker.candidates[1].misses == 0

    def test_observe_accumulates_hits_toward_promotion(self):
        tracker = BaselineTracker(add_threshold=3)
        d = _det(cx=0.5, cy=0.5)
        tracker.observe([d])  # hit 1
        tracker.observe([d])  # hit 2
        # One update() cycle should push to 3 hits and promote
        msgs = tracker.update([d])
        assert len(tracker.get_baseline()) == 1


class TestBaselineTrackerVehicleGroup:
    def test_car_then_truck_same_candidate(self):
        tracker = BaselineTracker(add_threshold=2)
        car = _det(cls_id=2, name="car", cx=0.5, cy=0.5)
        truck = _det(cls_id=7, name="truck", cx=0.52, cy=0.52)
        tracker.update([car])
        tracker.update([truck])
        # Should match same candidate and promote
        assert len(tracker.candidates) == 1
        assert len(tracker.get_baseline()) == 1

    def test_person_and_car_separate_candidates(self):
        tracker = BaselineTracker(add_threshold=2)
        person = _det(cls_id=0, name="person", cx=0.5, cy=0.5)
        car = _det(cls_id=2, name="car", cx=0.5, cy=0.5)
        tracker.update([person, car])
        assert len(tracker.candidates) == 2


class TestBaselineTrackerGetAllSeen:
    def test_returns_all_with_hits(self):
        tracker = BaselineTracker(add_threshold=5)
        tracker.update([_det(cx=0.2, cy=0.2), _det(cx=0.8, cy=0.8)])
        assert len(tracker.get_all_seen()) == 2
        assert len(tracker.get_baseline()) == 0  # not yet promoted
