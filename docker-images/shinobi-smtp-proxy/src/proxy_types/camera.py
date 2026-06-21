from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, TypeAlias

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
RawZoneConfig: TypeAlias = Mapping[str, RawZonePoints]
RawCameraConfig: TypeAlias = Mapping[str, Any]


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

    @classmethod
    def from_config(cls, raw_zone: RawZoneConfig) -> ZonePolygon:
        try:
            raw_points = raw_zone["points"]
        except KeyError as exc:
            raise ValueError("zone must define points") from exc
        return cls.from_points(raw_points)

    def contains(self, x: float, y: float) -> bool:
        return cv2.pointPolygonTest(self.points, (x, y), False) >= 0

    def to_pixel_points(self, width: int, height: int) -> NDArray[np.int32]:
        return (self.points * np.array([width, height])).astype(np.int32)


CameraZones: TypeAlias = list[ZonePolygon]


@dataclass(frozen=True)
class CameraConfig:
    """Static configuration for one camera."""

    id: CameraId
    host: str
    zones: CameraZones


CameraConfigs: TypeAlias = list[CameraConfig]


def build_camera_configs(raw_cameras: Sequence[RawCameraConfig] | None) -> CameraConfigs:
    """Validate app config and return camera configs with owned zone objects."""
    if not raw_cameras:
        return []

    camera_configs: CameraConfigs = []
    seen_ids: set[CameraId] = set()
    for camera_index, raw_camera in enumerate(raw_cameras):
        camera_path = f"cameras[{camera_index}]"
        camera_id = _required_str(raw_camera, "id", camera_path)
        if camera_id in seen_ids:
            raise ValueError(f"{camera_path}.id duplicates {camera_id!r}")
        seen_ids.add(camera_id)

        host = _required_str(raw_camera, "host", camera_path)
        raw_zones = raw_camera.get("zones", [])
        if not isinstance(raw_zones, Sequence):
            raise ValueError(f"{camera_path}.zones must be a list")

        zones: CameraZones = []
        for zone_index, raw_zone in enumerate(raw_zones):
            zone_path = f"{camera_path}.zones[{zone_index}]"
            if not isinstance(raw_zone, Mapping):
                raise ValueError(f"{zone_path} must be a mapping")
            try:
                zones.append(ZonePolygon.from_config(raw_zone))
            except ValueError as exc:
                raise ValueError(f"{zone_path}: {exc}") from exc

        camera_configs.append(CameraConfig(id=camera_id, host=host, zones=zones))
    return camera_configs


def _required_str(raw: RawCameraConfig, key: str, path: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}.{key} must be a non-empty string")
    return value
