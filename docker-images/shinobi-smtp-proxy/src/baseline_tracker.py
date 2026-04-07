from __future__ import annotations

from object_detector import Detection

# COCO vehicle class IDs that YOLO frequently confuses with each other.
# Treat as equivalent for baseline matching so a robot mower detected as
# car/bus/truck interchangeably can still accumulate hits.
VEHICLE_CLASSES = frozenset({2, 3, 5, 7})  # car, motorcycle, bus, truck


def _same_class_group(a_cls: int, b_cls: int) -> bool:
    """True if both class IDs are identical or both are vehicles."""
    return a_cls == b_cls or (a_cls in VEHICLE_CLASSES and b_cls in VEHICLE_CLASSES)


class BaselineCandidate:
    """Tracks a single detected object across multiple baseline cycles."""

    __slots__ = ("cls_id", "name", "cx", "cy", "w", "h", "hits", "misses", "promoted")

    def __init__(self, det: Detection) -> None:
        self.cls_id = det.cls_id
        self.name = det.name
        self.cx = det.cx
        self.cy = det.cy
        self.w = det.w
        self.h = det.h
        self.hits = 1
        self.misses = 0
        self.promoted = False

    def update(self, det: Detection) -> None:
        """Exponential moving average of position/size."""
        alpha = 0.3
        self.cx = self.cx * (1 - alpha) + det.cx * alpha
        self.cy = self.cy * (1 - alpha) + det.cy * alpha
        self.w = self.w * (1 - alpha) + det.w * alpha
        self.h = self.h * (1 - alpha) + det.h * alpha
        # Update class to latest detection (handles car/bus/truck flip-flop)
        self.cls_id = det.cls_id
        self.name = det.name
        self.hits += 1
        self.misses = 0

    def as_detection(self) -> Detection:
        return Detection(
            cls_id=self.cls_id, name=self.name,
            cx=self.cx, cy=self.cy, w=self.w, h=self.h,
            conf=1.0,
        )

    def __repr__(self) -> str:
        state = "P" if self.promoted else "c"
        return f"{self.name}@({self.cx:.2f},{self.cy:.2f})[{state} h={self.hits} m={self.misses}]"


class BaselineTracker:
    """Hysteresis-based baseline: objects must appear N cycles to enter.
    To leave, a low-confidence verification pass must also fail to find them."""

    def __init__(self, add_threshold: int = 3,
                 tolerance: float = 0.15) -> None:
        self.add_threshold = add_threshold
        self.tolerance = tolerance
        self.candidates: list[BaselineCandidate] = []
        self.cycles = 0
        # Indices of promoted candidates that missed in the latest update()
        self._missed_promoted: list[int] = []

    @property
    def is_warm(self) -> bool:
        return self.cycles >= self.add_threshold

    def _match(self, det: Detection, cand: BaselineCandidate) -> bool:
        return (_same_class_group(cand.cls_id, det.cls_id)
                and abs(cand.cx - det.cx) < self.tolerance
                and abs(cand.cy - det.cy) < self.tolerance)

    def update(self, detections: list[Detection]) -> list[str]:
        """Feed one cycle of normal-confidence detections.
        Returns log messages. Call verify_missed() after if any promoted missed."""
        self.cycles += 1
        return self._feed(detections, is_cycle=True)

    def observe(self, detections: list[Detection]) -> list[str]:
        """Feed observations from motion events into the tracker.
        Does NOT increment cycles or count misses. Creates new candidates
        for objects not yet tracked (so static objects only visible during
        motion can eventually accumulate hits and get promoted)."""
        return self._feed(detections, is_cycle=False)

    def _feed(self, detections: list[Detection], is_cycle: bool) -> list[str]:
        matched_candidates: set[int] = set()
        messages: list[str] = []
        if is_cycle:
            self._missed_promoted = []

        for det in detections:
            best = None
            for i, cand in enumerate(self.candidates):
                if i in matched_candidates:
                    continue
                if self._match(det, cand):
                    best = i
                    break
            if best is not None:
                matched_candidates.add(best)
                self.candidates[best].update(det)
                if not self.candidates[best].promoted and self.candidates[best].hits >= self.add_threshold:
                    self.candidates[best].promoted = True
                    messages.append(f"promoted {self.candidates[best]}")
            else:
                self.candidates.append(BaselineCandidate(det))

        # Track misses — only on baseline cycles
        if is_cycle:
            to_remove = []
            for i, cand in enumerate(self.candidates):
                if i not in matched_candidates:
                    cand.misses += 1
                    if cand.promoted:
                        self._missed_promoted.append(i)
                    elif cand.misses >= 3:
                        to_remove.append(i)

            for i in reversed(to_remove):
                self.candidates.pop(i)
            # Re-index _missed_promoted after removals
            if to_remove:
                removed_set = set(to_remove)
                shift = [sum(1 for r in to_remove if r < i) for i in self._missed_promoted]
                self._missed_promoted = [
                    i - s for i, s in zip(self._missed_promoted, shift)
                    if i not in removed_set
                ]

        return messages

    @property
    def has_missed_promoted(self) -> bool:
        return len(self._missed_promoted) > 0

    def verify_missed(self, low_conf_detections: list[Detection]) -> list[str]:
        """Check missed promoted candidates against a low-confidence detection pass.
        Found → keep (reset misses). Not found → demote and remove."""
        messages: list[str] = []
        to_remove = []

        for i in self._missed_promoted:
            cand = self.candidates[i]
            found = any(self._match(d, cand) for d in low_conf_detections)
            if found:
                cand.misses = 0
                messages.append(f"verified at low-conf {cand}")
            else:
                messages.append(f"demoted (gone) {cand}")
                cand.promoted = False
                to_remove.append(i)

        for i in reversed(sorted(to_remove)):
            self.candidates.pop(i)

        self._missed_promoted = []
        return messages

    def get_baseline(self) -> list[Detection]:
        """Promoted candidates only — the stable baseline."""
        return [c.as_detection() for c in self.candidates if c.promoted]

    def get_all_seen(self) -> list[Detection]:
        """All candidates seen at least once — used during warmup."""
        return [c.as_detection() for c in self.candidates if c.hits > 0]
