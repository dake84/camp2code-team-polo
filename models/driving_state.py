"""Fahrentscheidung mit Referenz auf das zugehoerige Kamerabild.

Felder:
    frame_id            Frame-ID des ausgewerteten Kameraframes (Join-Schluessel).
    timestamp_ns        Systemuhr zum Zeitpunkt der Entscheidung.
    steering_angle      Rohwinkel aus den Linien (bzw. gehaltener Vorwert), Grad.
    speed               Speed-Steuerwert, wie im Fahrzeug gespeichert (kein Messwert).
    steering_angle_car  Im Fahrzeug gespeicherter Stellwert nach Begrenzung, Grad.
    requested_angle     An das Fahrzeug gesendeter Sollwinkel (nach steering_gain).
    monotonic_ns        Monotone Uhr zum Zeitpunkt der Entscheidung (Latenzen).
Fehlende Werte bleiben ``None``.
"""
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
    driving_mode: str | None = None
    requested_angle: float | None = None
    monotonic_ns: int | None = None
