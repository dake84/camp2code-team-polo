"""Begrenzte Historie der zuletzt veroeffentlichten Kameraframes.

Speicherbedarf: Es werden nur Referenzen gehalten. Pro Eintrag bleiben
Rohbild, Prozessbild und (beim CopilotLaneProcessor) die Zwischenbilder
am Leben. Bei 320x240 BGR sind das ca. 0,4-0,5 MB je Frame (siehe
PERFORMANCE_REPORT.md), bei ``maxlen=60`` also ca. 25-30 MB.
"""
from collections import deque
from dataclasses import dataclass
from threading import RLock

from models import CameraData, DrivingState


@dataclass(frozen=True)
class FrameRecord:
    camera: CameraData
    driving: DrivingState | None = None


class FrameRingBuffer:
    def __init__(self, maxlen: int = 60):
        if maxlen < 1:
            raise ValueError("maxlen muss positiv sein")
        self._items = deque(maxlen=maxlen)
        self._lock = RLock()

    @property
    def maxlen(self) -> int:
        return self._items.maxlen

    def resize(self, maxlen: int) -> None:
        """PAKET-H3: Laenge aendern (RAM-Budget je nach Bildgroesse); behaelt die neuesten Eintraege."""
        if maxlen < 1:
            raise ValueError("maxlen muss positiv sein")
        with self._lock:
            if maxlen != self._items.maxlen:
                self._items = deque(self._items, maxlen=maxlen)

    def add_camera(self, data: CameraData) -> None:
        # Nur Referenzen speichern. Bereits publizierte Bilder nicht veraendern!
        with self._lock:
            self._items.append(FrameRecord(camera=data))

    def add_driving(self, state: DrivingState) -> bool:
        """Ordnet eine Fahrentscheidung ausschliesslich ueber die frame_id zu."""
        with self._lock:
            for index in range(len(self._items) - 1, -1, -1):
                record = self._items[index]
                if record.camera.frame_id == state.frame_id:
                    self._items[index] = FrameRecord(record.camera, state)
                    return True
        return False

    def snapshot(self) -> tuple[FrameRecord, ...]:
        # Kopie der Referenzliste, damit Leser den Lock nicht lange halten.
        with self._lock:
            return tuple(self._items)

    def find(self, frame_id: int) -> FrameRecord | None:
        """Sucht einen Frame ueber seine ID (neueste zuerst)."""
        with self._lock:
            for record in reversed(self._items):
                if record.camera.frame_id == frame_id:
                    return record
        return None

    def latest_complete(self) -> FrameRecord | None:
        """Neuester Frame mit zugeordneter Fahrentscheidung oder ``None``."""
        with self._lock:
            for record in reversed(self._items):
                if record.driving is not None:
                    return record
        return None
