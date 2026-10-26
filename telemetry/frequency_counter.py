"""Misst die tatsaechliche Zyklusfrequenz in einem gleitenden Fenster."""
from collections import deque
from threading import Lock
from time import monotonic

class FrequencyCounter:
    def __init__(self, window: int = 30):
        if window < 2:
            raise ValueError("window muss mindestens 2 sein")
        self._times = deque(maxlen=window)
        self._lock = Lock()

    def tick(self) -> None:
        with self._lock:
            self._times.append(monotonic())

    @property
    def hz(self) -> float:
        with self._lock:
            if len(self._times) < 2:
                return 0.0
            delta = self._times[-1] - self._times[0]
            return (len(self._times) - 1) / delta if delta > 0 else 0.0

    @property
    def age_seconds(self) -> float | None:
        with self._lock:
            return monotonic() - self._times[-1] if self._times else None
