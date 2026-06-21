from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import ClassVar, NamedTuple, TypeAlias

import cv2
import numpy as np
from numpy.typing import NDArray

CameraId: TypeAlias = str
GrayFrame: TypeAlias = NDArray[np.uint8]


class FrameSnapshot(NamedTuple):
    """One buffered JPEG frame and the monotonic time it was captured."""

    timestamp: float
    jpeg_bytes: bytes


RawZonePoint: TypeAlias = Sequence[float]
RawZonePoints: TypeAlias = Sequence[RawZonePoint]


@dataclass(frozen=True)
class ZonePolygon:
    """One normalized camera zone polygon, ready for OpenCV operations."""

    points: NDArray[np.float32]

    @classmethod
    def from_points(cls, raw_points: RawZonePoints) -> ZonePolygon:
        points = list(raw_points)
        if len(points) < 3:
            raise ValueError("zone must contain at least 3 points")

        validated_points: list[tuple[float, float]] = []
        for point_index, raw_point in enumerate(points):
            coords = list(raw_point)
            if len(coords) != 2:
                raise ValueError(f"point {point_index} must be [x, y]")

            try:
                x = float(coords[0])
                y = float(coords[1])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"point {point_index} must contain numeric x/y coordinates"
                ) from exc

            if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
                raise ValueError(
                    f"point {point_index} coordinates must be normalized between 0.0 and 1.0"
                )

            validated_points.append((x, y))

        return cls(points=np.array(validated_points, dtype=np.float32))

    def contains(self, x: float, y: float) -> bool:
        return cv2.pointPolygonTest(self.points, (x, y), False) >= 0

    def to_pixel_points(self, width: int, height: int) -> NDArray[np.int32]:
        return (self.points * np.array([width, height])).astype(np.int32)


CameraZones: TypeAlias = list[ZonePolygon]


@dataclass(frozen=True)
class CameraTuning:
    """Resolved detection and alert tuning for one camera."""

    position_tolerance: float = 0.15
    motion_threshold: int = 25
    motion_min_area: int = 500
    min_detection_area: float = 0.003
    baseline_add_threshold: int = 3
    baseline_verify_confidence: float = 0.15
    min_motion_novelty: float = 0.05
    scene_change_threshold: float = 0.15
    alert_cooldown_seconds: float = 300.0
    followup_interval_seconds: float = 3.0
    followup_duration_seconds: float = 15.0

    FLOAT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "position_tolerance",
            "min_detection_area",
            "baseline_verify_confidence",
            "min_motion_novelty",
            "scene_change_threshold",
            "alert_cooldown_seconds",
            "followup_interval_seconds",
            "followup_duration_seconds",
        }
    )
    INT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "motion_threshold",
            "motion_min_area",
            "baseline_add_threshold",
        }
    )

    @classmethod
    def from_mapping(
        cls,
        raw_tuning: object,
        base: CameraTuning | None = None,
        path: str = "tuning",
    ) -> CameraTuning:
        tuning = base or cls()
        if raw_tuning is None:
            return tuning
        if not isinstance(raw_tuning, Mapping):
            raise ValueError(f"{path} must be a mapping")

        values: dict[str, float | int] = {}
        for key in cls.FLOAT_FIELDS:
            if key in raw_tuning:
                values[key] = cls._required_float(raw_tuning[key], f"{path}.{key}")
        for key in cls.INT_FIELDS:
            if key in raw_tuning:
                values[key] = cls._required_int(raw_tuning[key], f"{path}.{key}")
        return replace(tuning, **values)

    @staticmethod
    def _required_float(value: object, path: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} must be numeric")
        parsed = float(value)
        if parsed < 0:
            raise ValueError(f"{path} must be non-negative")
        return parsed

    @staticmethod
    def _required_int(value: object, path: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path} must be an integer")
        if value < 0:
            raise ValueError(f"{path} must be non-negative")
        return value


@dataclass(frozen=True)
class CameraConfig:
    """Static configuration for one camera."""

    id: CameraId
    host: str
    zones: CameraZones
    tuning: CameraTuning = field(default_factory=CameraTuning)


CameraConfigs: TypeAlias = list[CameraConfig]


class CameraConfigParser:
    """Parses unvalidated YAML values into camera domain objects."""

    def __init__(self, default_tuning: CameraTuning | None = None) -> None:
        self.default_tuning = default_tuning or CameraTuning()

    def parse_all(self, raw_cameras: object) -> CameraConfigs:
        if raw_cameras is None:
            return []
        if not isinstance(raw_cameras, Sequence) or isinstance(
            raw_cameras, (str, bytes)
        ):
            raise ValueError("cameras must be a list")

        camera_configs: CameraConfigs = []
        seen_ids: set[CameraId] = set()
        for camera_index, raw_camera in enumerate(raw_cameras):
            camera = self.parse_camera(raw_camera, path=f"cameras[{camera_index}]")
            if camera.id in seen_ids:
                raise ValueError(f"cameras[{camera_index}].id duplicates {camera.id!r}")
            seen_ids.add(camera.id)
            camera_configs.append(camera)
        return camera_configs

    def parse_camera(self, raw_camera: object, path: str) -> CameraConfig:
        if not isinstance(raw_camera, Mapping):
            raise ValueError(f"{path} must be a mapping")

        camera_id = self.required_str(raw_camera, "id", path)
        host = self.required_str(raw_camera, "host", path)
        zones = self.parse_zones(raw_camera.get("zones", []), path=f"{path}.zones")
        tuning = CameraTuning.from_mapping(
            raw_camera.get("tuning"),
            base=self.default_tuning,
            path=f"{path}.tuning",
        )
        return CameraConfig(id=camera_id, host=host, zones=zones, tuning=tuning)

    def parse_zones(self, raw_zones: object, path: str) -> CameraZones:
        if not isinstance(raw_zones, Sequence) or isinstance(raw_zones, (str, bytes)):
            raise ValueError(f"{path} must be a list")

        zones: CameraZones = []
        for zone_index, raw_zone in enumerate(raw_zones):
            zones.append(self.parse_zone(raw_zone, path=f"{path}[{zone_index}]"))
        return zones

    def parse_zone(self, raw_zone: object, path: str) -> ZonePolygon:
        if not isinstance(raw_zone, Mapping):
            raise ValueError(f"{path} must be a mapping")
        raw_points = raw_zone.get("points")
        if raw_points is None:
            raise ValueError(f"{path} must define points")
        try:
            return ZonePolygon.from_points(raw_points)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc

    def required_str(self, raw: Mapping[object, object], key: str, path: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{path}.{key} must be a non-empty string")
        return value


def build_camera_configs(
    raw_cameras: object,
    default_tuning: CameraTuning | None = None,
) -> CameraConfigs:
    """Validate app config and return camera configs with owned zone objects."""
    return CameraConfigParser(default_tuning=default_tuning).parse_all(raw_cameras)
