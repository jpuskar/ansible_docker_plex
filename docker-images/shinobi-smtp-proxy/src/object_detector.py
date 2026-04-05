import asyncio
import io
import logging

from PIL import Image
from ultralytics import YOLO

log = logging.getLogger('smtp-proxy')


class Detection:
    """A single YOLO detection with class, position, and size."""
    __slots__ = ('cls_id', 'name', 'cx', 'cy', 'w', 'h', 'conf')

    def __init__(self, cls_id, name, cx, cy, w, h, conf):
        self.cls_id = cls_id
        self.name = name
        self.cx = cx    # center x (0-1, fraction of image width)
        self.cy = cy    # center y (0-1, fraction of image height)
        self.w = w      # width (0-1)
        self.h = h      # height (0-1)
        self.conf = conf

    def is_near(self, other, tolerance=0.15):
        """Check if another detection of the same class is nearby."""
        if self.cls_id != other.cls_id:
            return False
        return (abs(self.cx - other.cx) < tolerance
                and abs(self.cy - other.cy) < tolerance)

    def __repr__(self):
        return f"{self.name}@({self.cx:.2f},{self.cy:.2f})"


class ObjectDetector:
    """Runs YOLOv8n to detect people, vehicles, or animals."""

    def __init__(self, model_path='yolov8n.pt', confidence_threshold=0.25,
                 target_classes=None, ir_confidence_threshold=0.45):
        self.confidence_threshold = confidence_threshold
        self.ir_confidence_threshold = ir_confidence_threshold
        self.target_classes = set(target_classes or [])
        log.info("Loading YOLO model %s...", model_path)
        self.model = YOLO(model_path)
        log.info("YOLO model loaded")

    async def detect(self, image_data):
        """Returns True if any target object is found in the image bytes."""
        detections = await self.get_detections(image_data)
        return len(detections) > 0

    @staticmethod
    def _is_grayscale(img):
        """Fast check: sample patches across the image to detect IR/night mode.
        Returns True if image has negligible color saturation."""
        if img.mode != 'RGB':
            return img.mode in ('L', 'LA')
        import numpy as np
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

    async def get_detections(self, image_data):
        """Returns list of Detection objects for target classes found in image."""
        try:
            img = Image.open(io.BytesIO(image_data))
            img_w, img_h = img.size

            # Detect IR/night mode by checking color saturation
            # IR images are grayscale — R,G,B channels nearly identical
            is_ir = self._is_grayscale(img)
            conf_thresh = self.ir_confidence_threshold if is_ir else self.confidence_threshold

            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.model.predict(img, conf=conf_thresh, imgsz=416, verbose=False),
            )
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
                    log.debug("Detected %s (%.0f%%) at (%.2f, %.2f)",
                              d.name, conf * 100, d.cx, d.cy)

            return detections
        except Exception:
            log.exception("YOLO detection error")
            return []
