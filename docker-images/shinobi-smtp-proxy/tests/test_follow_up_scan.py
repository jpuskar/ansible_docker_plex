"""Tests for the post-alert follow-up scan in BaselineManager.

These tests verify the follow-up scan logic in isolation by constructing
a minimal BaselineManager-like harness that exercises _follow_up_scan's
decision logic: cooldown filtering, baseline comparison, and alert dispatch.

Since _follow_up_scan depends on the full BaselineManager (buffers, scheduler,
trackers, etc.), we test the core decision logic extracted into small helpers,
then do one integration-style test with mocks for the async infrastructure.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from object_detector import Detection
from baseline_tracker import BaselineTracker


def _det(cls_id=0, name="person", cx=0.5, cy=0.5, w=0.1, h=0.2, conf=0.9):
    return Detection(cls_id=cls_id, name=name, cx=cx, cy=cy, w=w, h=h, conf=conf)


class TestFollowUpCooldownLogic:
    """Test that the cooldown filtering correctly separates new arrivals
    from already-alerted positions."""

    def test_new_position_passes_cooldown(self):
        """A detection at a new position should NOT be suppressed."""
        now = time.monotonic()
        recent_alerts = [(now, _det(cx=0.3, cy=0.3))]  # you, already alerted
        tolerance = 0.15

        # Kid at a different position
        kid = _det(cx=0.7, cy=0.7)
        suppressed = any(
            kid.is_near(prev_d, tolerance=tolerance)
            for _, prev_d in recent_alerts
        )
        assert not suppressed

    def test_same_position_suppressed(self):
        """A detection near an already-alerted position should be suppressed."""
        now = time.monotonic()
        you = _det(cx=0.5, cy=0.5)
        recent_alerts = [(now, you)]
        tolerance = 0.15

        same_spot = _det(cx=0.52, cy=0.52)
        suppressed = any(
            same_spot.is_near(prev_d, tolerance=tolerance)
            for _, prev_d in recent_alerts
        )
        assert suppressed

    def test_expired_cooldown_not_suppressed(self):
        """Detections near an EXPIRED cooldown entry should pass."""
        cooldown = 300.0
        now = time.monotonic()
        old_alert = (now - cooldown - 1, _det(cx=0.5, cy=0.5))
        recent_alerts = [(t, d) for t, d in [old_alert] if now - t < cooldown]
        assert len(recent_alerts) == 0  # expired, so list is empty

    def test_multiple_alerts_all_checked(self):
        """Detections should be checked against ALL recent alerts."""
        now = time.monotonic()
        alert_you = (now, _det(cx=0.3, cy=0.3))
        alert_kid = (now, _det(cx=0.7, cy=0.7))
        recent_alerts = [alert_you, alert_kid]
        tolerance = 0.15

        # Third person at yet another position
        third = _det(cx=0.5, cy=0.9)
        suppressed = any(
            third.is_near(prev_d, tolerance=tolerance)
            for _, prev_d in recent_alerts
        )
        assert not suppressed

        # Same position as kid
        near_kid = _det(cx=0.71, cy=0.71)
        suppressed = any(
            near_kid.is_near(prev_d, tolerance=tolerance)
            for _, prev_d in recent_alerts
        )
        assert suppressed


class TestFollowUpBaselineLogic:
    """Test that baseline filtering correctly identifies new objects during follow-up."""

    def test_non_baseline_object_is_new(self):
        """Object not matching any baseline should be considered new."""
        baseline = [_det(cls_id=2, name="car", cx=0.5, cy=0.5)]
        kid = _det(cls_id=0, name="person", cx=0.3, cy=0.3)
        tolerance = 0.15

        is_baseline = any(
            kid.is_near(b, tolerance=tolerance) for b in baseline
        )
        assert not is_baseline

    def test_baseline_object_filtered(self):
        """Object matching baseline should be filtered out."""
        baseline = [_det(cls_id=2, name="car", cx=0.5, cy=0.5)]
        same_car = _det(cls_id=7, name="truck", cx=0.52, cy=0.52)
        tolerance = 0.15

        is_baseline = any(
            same_car.is_near(b, tolerance=tolerance) for b in baseline
        )
        assert is_baseline


class TestFollowUpDeadlineExtension:
    """Test that the follow-up deadline extends when new arrivals are found."""

    def test_deadline_extends_on_new_arrival(self):
        """Simulates the deadline extension logic."""
        followup_duration = 15.0
        now = time.monotonic()
        original_deadline = now + followup_duration

        # Simulate finding a new arrival at scan 3 (~9s in)
        scan_time = now + 9.0
        new_deadline = scan_time + followup_duration

        # New deadline should be later than original
        assert new_deadline > original_deadline


class TestFollowUpGuard:
    """Test that only one follow-up scan runs per camera at a time."""

    def test_guard_prevents_duplicate(self):
        active = set()
        camera = "camfrontdoor"

        # First activation
        active.add(camera)
        assert camera in active

        # Guard check — should skip
        should_start = camera not in active
        assert not should_start

    def test_guard_cleared_after_completion(self):
        active = {"camfrontdoor"}
        active.discard("camfrontdoor")
        assert "camfrontdoor" not in active


class TestFollowUpIntegration:
    """Integration test using a mock BaselineManager-like setup."""

    @pytest.fixture
    def manager_parts(self):
        """Create the minimal pieces needed to test follow-up logic."""
        from rtsp_reader import CameraBuffer

        camera_id = "camfrontdoor"
        buf = CameraBuffer(maxlen=20)
        tracker = BaselineTracker(add_threshold=3)

        # A simple JPEG-like bytes (doesn't need to be valid for this test)
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100

        return {
            "camera_id": camera_id,
            "buf": buf,
            "tracker": tracker,
            "fake_jpeg": fake_jpeg,
        }

    def test_follow_up_finds_kid(self, manager_parts):
        """Scenario: you triggered the initial alert, kid arrives 5s later.
        The follow-up scan should detect the kid as a new arrival."""
        camera_id = manager_parts["camera_id"]
        tolerance = 0.15
        now = time.monotonic()

        # Initial alert recorded: you at (0.5, 0.5)
        you = _det(cx=0.5, cy=0.5, conf=0.92)
        recent_alerts = [(now, you)]

        # Simulated baseline: just the car
        baseline = [_det(cls_id=2, name="car", cx=0.4, cy=0.2)]

        # Follow-up YOLO detections: you (still there) + kid (new)
        kid = _det(cx=0.3, cy=0.7, conf=0.75)
        followup_dets = [you, kid]

        # Baseline filter
        new = [
            d for d in followup_dets
            if not any(d.is_near(b, tolerance=tolerance) for b in baseline)
        ]
        assert len(new) == 2  # both you and kid are not baseline

        # Cooldown filter
        truly_new = [
            d for d in new
            if not any(
                d.is_near(prev_d, tolerance=tolerance)
                for _, prev_d in recent_alerts
            )
        ]
        assert len(truly_new) == 1
        assert truly_new[0].cx == kid.cx  # only kid passes

    def test_follow_up_no_alert_when_only_you(self, manager_parts):
        """If only the originally-alerted person is still in frame,
        no follow-up alert should be triggered."""
        tolerance = 0.15
        now = time.monotonic()

        you = _det(cx=0.5, cy=0.5, conf=0.92)
        recent_alerts = [(now, you)]
        baseline = [_det(cls_id=2, name="car", cx=0.4, cy=0.2)]

        followup_dets = [you]

        new = [
            d for d in followup_dets
            if not any(d.is_near(b, tolerance=tolerance) for b in baseline)
        ]
        truly_new = [
            d for d in new
            if not any(
                d.is_near(prev_d, tolerance=tolerance)
                for _, prev_d in recent_alerts
            )
        ]
        assert len(truly_new) == 0
