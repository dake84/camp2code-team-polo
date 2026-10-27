"""Begrenzte Historie der zuletzt veroeffentlichten Kameraframes."""
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

    def add_camera(self, data: CameraData) -> None:
        # Nur Referenzen speichern. Bereits publizierte Bilder nicht veraendern!
        with self._lock:
            self._items.append(FrameRecord(camera=data))

    def add_driving(self, state: DrivingState) -> bool:
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


