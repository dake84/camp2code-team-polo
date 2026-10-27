"""Fahrentscheidung mit Referenz auf das zugehoerige Kamerabild."""
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class DrivingState:
    frame_id: int
    timestamp_ns: int
    steering_angle: float
    speed: float
    # Originale Linienrepraesentation vorerst beibehalten; kein Algorithmus-Umbau.
    lanes: Any = None
    steering_angle_car: float | None = None


