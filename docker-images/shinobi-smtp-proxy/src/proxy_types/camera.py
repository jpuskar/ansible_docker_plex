from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple, TypeAlias

import numpy as np
from numpy.typing import NDArray

CameraId: TypeAlias = str
CameraHosts: TypeAlias = dict[CameraId, str]


class FrameSnapshot(NamedTuple):
    """One buffered JPEG frame and the monotonic time it was captured."""

    timestamp: float
    jpeg_bytes: bytes


GrayFrame: TypeAlias = NDArray[np.uint8]
ReferenceFrames: TypeAlias = dict[CameraId, GrayFrame]
ZonePolygon: TypeAlias = NDArray[np.float32]
CameraZones: TypeAlias = list[ZonePolygon]
ZonesByCamera: TypeAlias = dict[CameraId, CameraZones]

RawZonePoint: TypeAlias = Sequence[float]
RawZonePolygon: TypeAlias = Sequence[RawZonePoint]
RawCameraZones: TypeAlias = Sequence[RawZonePolygon]
RawZonesByCamera: TypeAlias = Mapping[CameraId, RawCameraZones]


def build_zones_by_camera(raw_zones: RawZonesByCamera | None) -> ZonesByCamera:
    """Validate raw config zones and convert them to OpenCV-ready polygons."""
    if not raw_zones:
        return {}

    zones_by_camera: ZonesByCamera = {}
    for camera_id, camera_zones in raw_zones.items():
        zones_by_camera[camera_id] = [
            _build_zone_polygon(camera_id, zone_index, raw_polygon)
            for zone_index, raw_polygon in enumerate(camera_zones)
        ]
    return zones_by_camera


def _build_zone_polygon(
    camera_id: CameraId,
    zone_index: int,
    raw_polygon: RawZonePolygon,
) -> ZonePolygon:
    points = list(raw_polygon)
    if len(points) < 3:
        raise ValueError(
            f"detection_zones[{camera_id!r}][{zone_index}] must contain at least 3 points"
        )

    validated_points: list[tuple[float, float]] = []
    for point_index, raw_point in enumerate(points):
        coords = list(raw_point)
        if len(coords) != 2:
            raise ValueError(
                f"detection_zones[{camera_id!r}][{zone_index}][{point_index}] "
                "must be [x, y]"
            )

        try:
            x = float(coords[0])
            y = float(coords[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"detection_zones[{camera_id!r}][{zone_index}][{point_index}] "
                "must contain numeric x/y coordinates"
            ) from exc

        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError(
                f"detection_zones[{camera_id!r}][{zone_index}][{point_index}] "
                "coordinates must be normalized between 0.0 and 1.0"
            )

        validated_points.append((x, y))

    return np.array(validated_points, dtype=np.float32)
