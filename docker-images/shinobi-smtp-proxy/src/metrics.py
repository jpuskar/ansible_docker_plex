"""Prometheus metrics for the shinobi-smtp-proxy camera detection system."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ── Inference ────────────────────────────────────────────────────────
inference_duration = Histogram(
    "smtp_proxy_inference_duration_seconds",
    "YOLO inference duration",
    ["priority"],
    buckets=(0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

inference_total = Counter(
    "smtp_proxy_inference_total",
    "Total inference requests processed",
    ["priority", "camera"],
)

inference_queue_depth = Gauge(
    "smtp_proxy_inference_queue_depth",
    "Current inference queue depth",
)

inference_preemptions = Counter(
    "smtp_proxy_inference_preemptions_total",
    "Baseline inferences preempted by motion",
)

# ── Detections ───────────────────────────────────────────────────────
detections_total = Counter(
    "smtp_proxy_detections_total",
    "YOLO detections by class and camera",
    ["camera", "class_name"],
)

# ── Motion ───────────────────────────────────────────────────────────
motion_events_total = Counter(
    "smtp_proxy_motion_events_total",
    "Motion events detected by camera",
    ["camera"],
)

motion_filtered_total = Counter(
    "smtp_proxy_motion_filtered_total",
    "Motion events filtered out (reason label)",
    ["camera", "reason"],
)

# ── Alerts ───────────────────────────────────────────────────────────
alerts_total = Counter(
    "smtp_proxy_alerts_total",
    "Alerts sent by camera and destination",
    ["camera", "destination"],
)

# ── RTSP / Camera ───────────────────────────────────────────────────
camera_frames_total = Counter(
    "smtp_proxy_camera_frames_total",
    "Frames captured by camera and status",
    ["camera", "status"],
)

camera_bytes_total = Counter(
    "smtp_proxy_camera_bytes_total",
    "JPEG bytes received from cameras",
    ["camera"],
)

camera_connected = Gauge(
    "smtp_proxy_camera_connected",
    "Whether camera RTSP stream is connected (1=yes, 0=no)",
    ["camera"],
)

# ── Baseline ─────────────────────────────────────────────────────────
baseline_objects = Gauge(
    "smtp_proxy_baseline_objects",
    "Number of objects in current baseline per camera",
    ["camera"],
)

baseline_skipped_total = Counter(
    "smtp_proxy_baseline_skipped_total",
    "Baseline updates skipped due to active motion",
    ["camera"],
)

# ── Model info ───────────────────────────────────────────────────────
model_info = Info(
    "smtp_proxy_model",
    "YOLO model details",
)
