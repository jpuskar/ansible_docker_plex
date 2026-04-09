from __future__ import annotations

import io
import logging

import cv2
import numpy as np
from PIL import Image, ImageDraw

from object_detector import Detection

log = logging.getLogger("smtp-proxy")


def filter_by_zone(camera_id: str, detections: list[Detection],
                   zones: dict[str, list[np.ndarray]]) -> list[Detection]:
    """Return only detections whose center falls inside a configured zone.
    If no zones are configured for this camera, all detections pass through."""
    cam_zones = zones.get(camera_id)
    if not cam_zones:
        return detections
    result = []
    for d in detections:
        pt = (d.cx, d.cy)
        for poly in cam_zones:
            if cv2.pointPolygonTest(poly, pt, False) >= 0:
                result.append(d)
                break
    return result


def annotate_frame(jpeg_bytes: bytes, detections: list[Detection],
                   new_detections: list[Detection] | None = None) -> bytes:
    """Draw bounding boxes and labels on a JPEG frame. Returns annotated JPEG bytes.
    Green boxes for new/alerting detections, gray for baseline-matched ones."""
    try:
        img = Image.open(io.BytesIO(jpeg_bytes))
        draw = ImageDraw.Draw(img)
        w, h = img.size
        new_set = set(id(d) for d in (new_detections or detections))

        for d in detections:
            x1 = int((d.cx - d.w / 2) * w)
            y1 = int((d.cy - d.h / 2) * h)
            x2 = int((d.cx + d.w / 2) * w)
            y2 = int((d.cy + d.h / 2) * h)
            is_new = id(d) in new_set
            color = (0, 255, 0) if is_new else (128, 128, 128)
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
            label = f"{d.name} {d.conf:.0%}"
            # Label background
            bbox = draw.textbbox((x1, y1 - 14), label)
            draw.rectangle(
                [bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1], fill=color
            )
            draw.text((x1, y1 - 14), label, fill=(0, 0, 0))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        log.debug("Annotation failed, sending raw frame", exc_info=True)
        return jpeg_bytes


def patch_edge_change(camera_id: str, jpeg_bytes: bytes, det: Detection,
                      reference_frames: dict[str, np.ndarray],
                      margin: float = 0.03) -> float | None:
    """Compare edge maps (Canny) of a detection's patch between the
    baseline reference frame and the current frame.

    Returns 0.0-1.0: fraction of *current* edge pixels that are new
    (present in current but not in dilated reference).  Edges are
    lighting-invariant — a cloud passing or dusk shift changes brightness
    but not object contours.  A static trashcan has the same edges in
    both frames (≈ 0%).  A person standing where there was none before
    introduces many new contour edges (40-80%)."""
    ref = reference_frames.get(camera_id)
    if ref is None:
        return None

    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    cur = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if cur is None:
        return None

    h, w = cur.shape
    rh, rw = ref.shape
    if (h, w) != (rh, rw):
        return None

    # Crop patch with small margin
    x1 = max(0, int((det.cx - det.w / 2 - margin) * w))
    y1 = max(0, int((det.cy - det.h / 2 - margin) * h))
    x2 = min(w, int((det.cx + det.w / 2 + margin) * w))
    y2 = min(h, int((det.cy + det.h / 2 + margin) * h))

    if x2 - x1 < 8 or y2 - y1 < 8:
        return None

    ref_patch = ref[y1:y2, x1:x2]
    cur_patch = cur[y1:y2, x1:x2]

    # Canny edge detection on both patches
    ref_edges = cv2.Canny(ref_patch, 50, 150)
    cur_edges = cv2.Canny(cur_patch, 50, 150)

    # New edges = present in current but not in reference.
    # Dilate reference edges slightly so minor alignment jitter
    # (compression artifacts, sub-pixel shifts) doesn't count.
    kernel = np.ones((3, 3), dtype=np.uint8)
    ref_dilated = cv2.dilate(ref_edges, kernel, iterations=1)
    new_edges = cv2.bitwise_and(cur_edges, cv2.bitwise_not(ref_dilated))

    ref_edge_count = np.count_nonzero(ref_edges)
    ref_dilated_count = np.count_nonzero(ref_dilated)
    cur_edge_count = np.count_nonzero(cur_edges)
    new_edge_count = np.count_nonzero(new_edges)
    total_pixels = ref_patch.size
    patch_size = (x2 - x1, y2 - y1)

    # Fraction of *current* edges that are genuinely new (not in
    # the dilated reference).  A static object has the same edges
    # → 0%.  A person standing where grass was has mostly new
    # contour edges → 40-80%.
    frac = new_edge_count / cur_edge_count if cur_edge_count > 0 else 0.0

    log.debug(
        "EdgeCmp %s %s@(%.2f,%.2f): patch=%dx%d total_px=%d "
        "ref_edges=%d ref_dilated=%d cur_edges=%d new_edges=%d "
        "frac=%.4f (new/cur_edges)",
        camera_id, det.name, det.cx, det.cy,
        patch_size[0], patch_size[1], total_pixels,
        ref_edge_count, ref_dilated_count, cur_edge_count,
        new_edge_count, frac,
    )

    return frac
