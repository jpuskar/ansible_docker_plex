"""Composable detection-filter pipeline.

The motion and follow-up loops both whittle a list of YOLO detections down
to "genuinely new" objects worth alerting on. Historically that chain was
hand-inlined in each loop, which made it hard to reorder filters, disable
one, or unit-test a single stage.

This module breaks the chain into small, independently testable ``Filter``
stages assembled into a ``DetectionPipeline``. Each loop builds its own
ordered list of stages (see ``BaselineManager.__init__``), so adjusting which
filters run on which path is a one-line edit.

Stages here are *stateless* noise/confirmation filters. The baseline diff is
deliberately NOT a stage: it mutates the per-camera tracker and has warmup
semantics, so it lives as a method on ``BaselineManager``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

import metrics as m
from object_detector import Detection
from proxy_types.camera import ZonesByCamera
from proxy_types.pipeline import FilterContext
from scene_compare import filter_by_zone, patch_edge_change

log = logging.getLogger("smtp-proxy")

class Filter(ABC):
    """One stage in the pipeline.

    ``reason`` doubles as the Prometheus ``motion_filtered_total`` drop label
    and the debug log tag. Subclasses implement ``apply``.
    """

    reason: ClassVar[str] = "filter"

    @abstractmethod
    def apply(self, dets: list[Detection], ctx: FilterContext) -> list[Detection]:
        raise NotImplementedError


class ZoneFilter(Filter):
    """Drop detections whose center falls outside the camera's configured zones."""

    reason: ClassVar[str] = "outside_zone"

    def __init__(self, zones: ZonesByCamera) -> None:
        self.zones = zones

    def apply(self, dets: list[Detection], ctx: FilterContext) -> list[Detection]:
        return filter_by_zone(camera_id=ctx.camera_id, detections=dets, zones=self.zones)


class NoveltyFilter(Filter):
    """Drop detections overlapping only low-novelty motion.

    Novelty = how much the instantaneous motion exceeds the temporal
    background (heatmap). Trees swaying / shadows = low novelty; a person or
    car arriving = high novelty.
    """

    reason: ClassVar[str] = "low_novelty"

    def __init__(self, min_novelty: float) -> None:
        self.min_novelty = min_novelty

    def apply(self, dets: list[Detection], ctx: FilterContext) -> list[Detection]:
        kept: list[Detection] = []
        for d in dets:
            novelty = d.max_novelty(rects=ctx.motion_rects)
            if novelty >= self.min_novelty:
                kept.append(d)
            else:
                log.debug(
                    "%s %s: %s@(%.2f,%.2f) novelty=%.3f < %.3f (filtered as environmental)",
                    ctx.label, ctx.camera_id, d.name, d.cx, d.cy, novelty, self.min_novelty,
                )
        return kept


class MinAreaFilter(Filter):
    """Drop tiny detections (light flashes, hallucinations) below a min area."""

    reason: ClassVar[str] = "below_min_area"

    def __init__(self, min_area: float) -> None:
        self.min_area = min_area

    def apply(self, dets: list[Detection], ctx: FilterContext) -> list[Detection]:
        if self.min_area <= 0:
            return dets
        return [d for d in dets if d.w * d.h >= self.min_area]


class EdgeChangeFilter(Filter):
    """Drop detections whose edge map matches the calm reference frame.

    Edges are lighting-invariant: a static object has the same contours in
    both frames (~0% new edges); a person introduces many new edges. Passes
    everything through until a reference frame exists for the camera.
    """

    reason: ClassVar[str] = "scene_unchanged"

    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def apply(self, dets: list[Detection], ctx: FilterContext) -> list[Detection]:
        if ctx.reference_frames.get(ctx.camera_id) is None:
            return dets  # no calm reference yet — nothing to compare against
        kept: list[Detection] = []
        for d in dets:
            frac = patch_edge_change(ctx.camera_id, ctx.jpeg_bytes, d, ctx.reference_frames)
            if frac is not None and frac < self.threshold:
                log.info(
                    "%s %s: %s@(%.2f,%.2f) suppressed (edges unchanged, %.2f%% new edges)",
                    ctx.label, ctx.camera_id, d.name, d.cx, d.cy, frac * 100,
                )
            else:
                if frac is not None:
                    log.info(
                        "%s %s: %s@(%.2f,%.2f) scene changed (%.2f%% new edges)",
                        ctx.label, ctx.camera_id, d.name, d.cx, d.cy, frac * 100,
                    )
                kept.append(d)
        return kept


class CooldownFilter(Filter):
    """Drop detections near a position already alerted within the cooldown window."""

    reason: ClassVar[str] = "alert_cooldown"

    def __init__(self, tolerance: float) -> None:
        self.tolerance = tolerance

    def apply(self, dets: list[Detection], ctx: FilterContext) -> list[Detection]:
        kept = [
            d for d in dets
            if not any(
                d.is_near(prev, tolerance=self.tolerance)
                for _, prev in ctx.recent_alerts
            )
        ]
        dropped = len(dets) - len(kept)
        if dropped:
            log.info(
                "%s %s: %d detections suppressed (alert cooldown)",
                ctx.label, ctx.camera_id, dropped,
            )
        return kept


class DetectionPipeline:
    """Runs an ordered list of ``Filter`` stages, short-circuiting when empty.

    Each stage that drops detections increments
    ``motion_filtered_total{reason=<stage.reason>}`` by the number dropped.
    """

    def __init__(self, filters: Sequence[Filter]) -> None:
        self.filters: tuple[Filter, ...] = tuple(filters)

    def run(self, dets: list[Detection], ctx: FilterContext) -> list[Detection]:
        for f in self.filters:
            before = len(dets)
            dets = f.apply(dets, ctx)
            dropped = before - len(dets)
            if dropped:
                m.motion_filtered_total.labels(camera=ctx.camera_id, reason=f.reason).inc(dropped)
                log.debug(
                    "%s %s: %s dropped %d (%d→%d)",
                    ctx.label, ctx.camera_id, f.reason, dropped, before, len(dets),
                )
            if not dets:
                break
        return dets
