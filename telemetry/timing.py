"""Leichte Laufzeitmessung fuer Telemetrie und Dashboard (ohne Abhaengigkeiten)."""
from collections import deque
from contextlib import contextmanager
from threading import Lock
from time import perf_counter_ns


class DurationStats:
    """Speichert die letzten Messdauern je Name in begrenzten Puffern."""

    def __init__(self, window: int = 50):
        self._window = window
        self._data: dict[str, deque] = {}
        self._lock = Lock()

    def add(self, name: str, duration_ns: int) -> None:
        with self._lock:
            self._data.setdefault(name, deque(maxlen=self._window)).append(int(duration_ns))

    @contextmanager
    def measure(self, name: str):
        start = perf_counter_ns()
        try:
            yield
        finally:
            self.add(name, perf_counter_ns() - start)

    def summary(self) -> dict[str, dict[str, float]]:
        """Mittelwert, Maximum und Anzahl in Millisekunden je Name."""
        with self._lock:
            items = {k: tuple(v) for k, v in self._data.items()}
        result = {}
        for name, values in items.items():
            if values:
                result[name] = {'mean_ms': sum(values) / len(values) / 1e6,
                                'max_ms': max(values) / 1e6, 'count': len(values)}
        return result
