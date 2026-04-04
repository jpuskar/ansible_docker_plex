import asyncio
import io
import logging

from typing import List, Optional

from PIL import Image
from ultralytics import YOLO


logger = logging.getLogger(__name__)


class ObjectDetector:
    """AI-powered object detector using YOLOv8 for identifying people, vehicles, and animals"""

    def __init__(
        self,
        model_path: str = 'yolov8n.pt',
        confidence_threshold: float = 0.25,
        target_classes: Optional[List[int]] = None
    ) -> None:
        """Initialize the object detector with YOLO model

        Args:
            model_path: Path to the YOLO model file (default: yolov8n.pt)
            confidence_threshold: Minimum confidence score for detections (0.0-1.0)
            target_classes: List of COCO class IDs to detect. If None, uses default set.
        """
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.model: Optional[YOLO] = None

        # COCO class IDs for people, vehicles, and animals
        # person=0, bicycle=1, car=2, motorcycle=3, bus=5, truck=7,
        # bird=14, cat=15, dog=16, horse=17, sheep=18, cow=19, elephant=20, bear=21, zebra=22, giraffe=23
        if target_classes is None:
            self.target_classes = [0, 1, 2, 3, 5, 7, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
        else:
            self.target_classes = target_classes

        self._load_model()

    def _load_model(self) -> None:
        """Load the YOLO model into memory"""
        try:
            logger.info(f"Loading YOLO model from {self.model_path}...")
            self.model = YOLO(self.model_path)
            logger.info(f"YOLO model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            raise

    async def detect_objects_in_image(self, image_data: bytes) -> bool:
        """Detect target objects (people, vehicles, animals) in an image

        Args:
            image_data: Raw image data as bytes

        Returns:
            True if target objects were detected, False otherwise
        """
        try:
            # Load image from bytes
            img = Image.open(io.BytesIO(image_data))

            # Run YOLO detection (runs in thread pool to avoid blocking)
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.model.predict(
                    img,
                    conf=self.confidence_threshold,
                    imgsz=416,
                    verbose=False,
                )
            )

            # Check if any target objects were detected
            for result in results:
                if result.boxes is not None and len(result.boxes) > 0:
                    detected_classes = result.boxes.cls.cpu().numpy().astype(int)
                    for cls_id in detected_classes:
                        if cls_id in self.target_classes:
                            class_name = result.names[cls_id]
                            confidence = result.boxes.conf[detected_classes == cls_id].max()
                            logger.info(
                                f"Detected ({class_name}) in image (confidence: {confidence:.2f})"
                            )
                            return True

            logger.info("No people/vehicles/animals detected in image.")
            return False

        except Exception as e:
            logger.error(f"Error during AI detection: {e}")
            return True  # Allow through on error to avoid false negatives
