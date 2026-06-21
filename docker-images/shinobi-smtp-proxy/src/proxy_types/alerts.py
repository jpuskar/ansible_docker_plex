from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, TypeAlias

if TYPE_CHECKING:
	from object_detector import Detection


class RecentAlert(NamedTuple):
	"""One previously alerted detection and its monotonic timestamp."""

	timestamp: float
	detection: "Detection"


RecentAlerts: TypeAlias = list[RecentAlert]
