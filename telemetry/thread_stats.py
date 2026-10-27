"""CPU-Anteil je Thread (Linux, psutil). 100 % entspricht einem voll ausgelasteten Kern.

Messung statt Vermutung: Grundlage fuer die Entscheidung, ob Multiprocessing
oder CPU-Affinitaet ueberhaupt etwas bringt.
"""
import threading
import time

try:  # optional
    import psutil
except ImportError:  # pragma: no cover - abhaengig von der Installation
    psutil = None


class ThreadCpuSampler:
    """Vergleicht CPU-Zeiten der Threads zwischen zwei Abfragen (mindestens 1 s Abstand)."""

    def __init__(self, min_interval=1.0):
        self._min_interval = float(min_interval)
        self._lock = threading.Lock()
        self._last = {}
        self._last_time = None
        self._cache = []

    def sample(self):
        """Liste (Threadname, CPU-Prozent), absteigend sortiert; leer bei der ersten Messung."""
        if psutil is None:
            return []
        now = time.monotonic()
        with self._lock:
            if self._last_time is not None and now - self._last_time < self._min_interval:
                return list(self._cache)
            names = {t.native_id: t.name for t in threading.enumerate() if t.native_id}
            current = {t.id: t.user_time + t.system_time for t in psutil.Process().threads()}
            result = []
            if self._last_time is not None:
                elapsed = max(1e-6, now - self._last_time)
                for tid, total in current.items():
                    previous = self._last.get(tid)
                    if previous is not None:
                        # Threads ohne Python-Namen (OpenCV, libcamera, ...) mit Kernel-ID.
                        result.append((names.get(tid, f'TID {tid}'), max(0.0, (total - previous) / elapsed * 100)))
                result.sort(key=lambda item: -item[1])
            self._last, self._last_time, self._cache = current, now, result
            return list(result)

    def lines(self, top=6):
        if psutil is None:
            return ['Thread-CPU: psutil nicht installiert']
        data = self.sample()
        if not data:
            return ['Thread-CPU: Messung läuft (Wert ab dem nächsten Takt)']
        total = sum(p for _, p in data)
        return [f'Thread-CPU gesamt {total:.0f} % (100 % = 1 Kern) · '
                + ' · '.join(f'{name} {percent:.0f} %' for name, percent in data[:top])]
