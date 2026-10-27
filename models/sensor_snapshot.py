"""Zeitlich zuordenbare Messwerte ohne Hardware-Abhängigkeiten."""
from dataclasses import dataclass

@dataclass(frozen=True)
class SensorSnapshot:
    infrared: tuple[float | None, ...] = (None, None, None, None, None)
    ultrasonic_cm: float | None = None
    timestamp_ns: int | None = None
    monotonic_ns: int | None = None
    infrared_timestamp_ns: int | None = None
    infrared_monotonic_ns: int | None = None
    ultrasonic_timestamp_ns: int | None = None
    ultrasonic_monotonic_ns: int | None = None

    def infrared_age_ns(self, now_ns: int) -> int | None:
        """Alter der letzten Infrarotmessung auf der monotonen Uhr."""
        return None if self.infrared_monotonic_ns is None else max(0, now_ns - self.infrared_monotonic_ns)

    def ultrasonic_age_ns(self, now_ns: int) -> int | None:
        """Alter der letzten Ultraschallmessung auf der monotonen Uhr."""
        return None if self.ultrasonic_monotonic_ns is None else max(0, now_ns - self.ultrasonic_monotonic_ns)
