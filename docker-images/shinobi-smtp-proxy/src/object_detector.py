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

    def __init__(self, model_path='yolov8n.pt', confidence_threshold=0.25, target_classes=None):
        self.confidence_threshold = confidence_threshold
        self.target_classes = set(target_classes or [])
        log.info("Loading YOLO model %s...", model_path)
        self.model = YOLO(model_path)
        log.info("YOLO model loaded")

    async def detect(self, image_data):
        """Returns True if any target object is found in the image bytes."""
        detections = await self.get_detections(image_data)
        return len(detections) > 0

    async def get_detections(self, image_data):
        """Returns list of Detection objects for target classes found in image."""
        try:
            img = Image.open(io.BytesIO(image_data))
            img_w, img_h = img.size
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.model.predict(img, conf=self.confidence_threshold, imgsz=416, verbose=False),
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
                    log.info("Detected %s (%.0f%%) at (%.2f, %.2f)",
                             d.name, conf * 100, d.cx, d.cy)

            if not detections:
                log.info("No target objects detected")
            return detections
        except Exception:
            log.exception("YOLO detection error")
            return []
