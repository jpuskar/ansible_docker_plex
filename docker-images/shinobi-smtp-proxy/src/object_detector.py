import asyncio
import io
import logging

from PIL import Image
from ultralytics import YOLO

log = logging.getLogger('smtp-proxy')


class ObjectDetector:
    """Runs YOLOv8n to check if an image contains people, vehicles, or animals."""

    def __init__(self, model_path='yolov8n.pt', confidence_threshold=0.25, target_classes=None):
        self.confidence_threshold = confidence_threshold
        self.target_classes = set(target_classes or [])
        log.info("Loading YOLO model %s...", model_path)
        self.model = YOLO(model_path)
        log.info("YOLO model loaded")

    async def detect(self, image_data):
        """Returns True if any target object is found in the image bytes."""
        try:
            img = Image.open(io.BytesIO(image_data))
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.model.predict(img, conf=self.confidence_threshold, imgsz=416, verbose=False),
            )
            for result in results:
                if result.boxes is None or len(result.boxes) == 0:
                    continue
                for cls_id in result.boxes.cls.cpu().numpy().astype(int):
                    if cls_id in self.target_classes:
                        name = result.names[cls_id]
                        log.info("Detected %s in image", name)
                        return True

            log.info("No target objects detected")
            return False
        except Exception:
            log.exception("YOLO detection error, allowing through")
            return True  # fail open
