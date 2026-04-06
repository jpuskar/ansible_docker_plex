from __future__ import annotations

import asyncio
import io
import logging

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

log = logging.getLogger("smtp-proxy")


class Detection:
    """A single YOLO detection with class, position, and size."""

    __slots__ = ("cls_id", "name", "cx", "cy", "w", "h", "conf")

    def __init__(self, cls_id: int, name: str, cx: float, cy: float,
                 w: float, h: float, conf: float) -> None:
        self.cls_id = cls_id
        self.name = name
        self.cx = cx  # center x (0-1, fraction of image width)
        self.cy = cy  # center y (0-1, fraction of image height)
        self.w = w  # width (0-1)
        self.h = h  # height (0-1)
        self.conf = conf

    def is_near(self, other: Detection, tolerance: float = 0.15) -> bool:
        """Check if another detection of the same class is nearby."""
        if self.cls_id != other.cls_id:
            return False
        return (
            abs(self.cx - other.cx) < tolerance and abs(self.cy - other.cy) < tolerance
        )

    def overlaps_any_rect(self, rects: list[tuple[float, float, float, float]],
                          min_overlap: float = 0.1) -> bool:
        """Check if this detection's bbox overlaps any of the given rects.

        Each rect is (x, y, w, h) in normalized 0-1 coords.
        min_overlap is fraction of this detection's area that must be covered.
        """
        dx1 = self.cx - self.w / 2
        dy1 = self.cy - self.h / 2
        dx2 = self.cx + self.w / 2
        dy2 = self.cy + self.h / 2
        det_area = self.w * self.h
        if det_area <= 0:
            return False
        for rx, ry, rw, rh in rects:
            ox1 = max(dx1, rx)
            oy1 = max(dy1, ry)
            ox2 = min(dx2, rx + rw)
            oy2 = min(dy2, ry + rh)
            if ox1 < ox2 and oy1 < oy2:
                overlap = (ox2 - ox1) * (oy2 - oy1)
                if overlap / det_area >= min_overlap:
                    return True
        return False

    def __repr__(self) -> str:
        return f"{self.name}@({self.cx:.2f},{self.cy:.2f})"


class ObjectDetector:
    """Runs YOLOv8l with OpenVINO on Intel GPU (falls back to CPU)."""

    # OpenVINO model directory exported during Docker build
    _OPENVINO_MODEL = "yolov8l_openvino_model"
    _PYTORCH_MODEL = "yolov8l.pt"

    def __init__(
        self,
        model_path: str | None = None,
        confidence_threshold: float = 0.25,
        target_classes: set[int] | None = None,
        ir_confidence_threshold: float = 0.45,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.ir_confidence_threshold = ir_confidence_threshold
        self.target_classes = set(target_classes or [])

        # Try OpenVINO model first (exported during Docker build), fall back to PyTorch
        import pathlib
        ov_path = pathlib.Path(self._OPENVINO_MODEL)
        if model_path:
            resolved = model_path
        elif ov_path.is_dir() and (ov_path / "yolov8l.xml").exists():
            resolved = str(ov_path)
        else:
            resolved = self._PYTORCH_MODEL

        log.info("Loading YOLO model %s...", resolved)
        self.model = YOLO(resolved, task="detect")
        self._using_openvino = resolved.endswith("_openvino_model") or resolved.endswith(".xml")

        # Select OpenVINO device: prefer GPU, fall back to CPU
        if self._using_openvino:
            try:
                import openvino as ov
                devices = ov.Core().available_devices
                log.info("OpenVINO available devices: %s", devices)
                self._ov_device = "GPU" if "GPU" in devices else "CPU"
            except Exception:
                self._ov_device = "CPU"
        else:
            self._ov_device = None

        log.info("YOLO model loaded (OpenVINO=%s, device=%s)", self._using_openvino, self._ov_device)

    async def detect(self, image_data: bytes) -> bool:
        """Returns True if any target object is found in the image bytes."""
        detections = await self.get_detections(image_data)
        return len(detections) > 0

    @staticmethod
    def _is_grayscale(img: Image.Image) -> bool:
        """Fast check: sample patches across the image to detect IR/night mode.
        Returns True if image has negligible color saturation."""
        if img.mode != "RGB":
            return img.mode in ("L", "LA")
        w, h = img.size
        ps = 20  # patch half-size
        # Sample 5 spread-out patches: top-left, top-right, center, bottom-left, bottom-right
        points = [
            (w // 4, h // 4),
            (3 * w // 4, h // 4),
            (w // 2, h // 2),
            (w // 4, 3 * h // 4),
            (3 * w // 4, 3 * h // 4),
        ]
        total_spread = 0.0
        for cx, cy in points:
            x1, y1 = max(0, cx - ps), max(0, cy - ps)
            x2, y2 = min(w, cx + ps), min(h, cy + ps)
            patch = img.crop((x1, y1, x2, y2))
            px = np.array(patch, dtype=np.int16)
            total_spread += np.abs(px[:, :, 0] - px[:, :, 1]).mean()
            total_spread += np.abs(px[:, :, 1] - px[:, :, 2]).mean()
        avg_spread = total_spread / len(points)
        return avg_spread < 10.0

    @staticmethod
    def _apply_clahe(img: Image.Image) -> Image.Image:
        """Apply CLAHE contrast enhancement to an IR/grayscale PIL image.
        Converts to LAB, applies CLAHE to the L channel, converts back to RGB."""
        arr = np.array(img)
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        return Image.fromarray(enhanced)

    async def get_detections(self, image_data: bytes,
                             confidence_override: float | None = None) -> list[Detection]:
        """Returns list of Detection objects for target classes found in image.
        If confidence_override is set, it is used instead of the dynamic IR/day threshold.
        """
        try:
            img = Image.open(io.BytesIO(image_data))
            img_w, img_h = img.size

            is_ir = self._is_grayscale(img)

            if confidence_override is not None:
                conf_thresh = confidence_override
            else:
                conf_thresh = (
                    self.ir_confidence_threshold if is_ir else self.confidence_threshold
                )

            # CLAHE contrast enhancement for IR/night frames
            # Improves YOLO detection of dim objects (e.g. parked car under IR LEDs)
            if is_ir:
                img = self._apply_clahe(img)

            import time
            t0 = time.monotonic()
            loop = asyncio.get_running_loop()
            predict_kwargs: dict = dict(conf=conf_thresh, imgsz=640, verbose=False)
            if self._ov_device:
                predict_kwargs["device"] = self._ov_device
            results = await loop.run_in_executor(
                None,
                lambda: self.model.predict(img, **predict_kwargs),
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            log.info("YOLO inference: %.0fms (OpenVINO=%s, device=%s)", elapsed_ms, self._using_openvino, self._ov_device)

            detections = []
            for result in results:
                if result.boxes is None or len(result.boxes) == 0:
                    continue
                boxes = result.boxes
                for i, cls_id in enumerate(boxes.cls.cpu().numpy().astype(int)):
                    if cls_id not in self.target_classes:
                        continue
                    x1, y1, x2, y2 = boxes.xyxy[i].cpu().numpy()
                    conf = float(boxes.conf[i])
                    d = Detection(
                        cls_id=cls_id,
                        name=result.names[cls_id],
                        cx=(x1 + x2) / 2 / img_w,
                        cy=(y1 + y2) / 2 / img_h,
                        w=(x2 - x1) / img_w,
                        h=(y2 - y1) / img_h,
                        conf=conf,
                    )
                    detections.append(d)
                    log.debug(
                        "Detected %s (%.0f%%) at (%.2f, %.2f)",
                        d.name,
                        conf * 100,
                        d.cx,
                        d.cy,
                    )

            return detections
        except Exception:
            log.exception("YOLO detection error")
            return []
