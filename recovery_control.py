"""Zeitlich begrenzte manuelle Bergungssteuerung mit serverseitigem Watchdog."""
import threading
import time

class RecoveryControl:
    """Bewegt nur bei aktivem Deadman und stoppt bei ausbleibenden Befehlen."""
    def __init__(self, car, autonomous_running, timeout=0.35, speed=30):
        self._car = car
        self._autonomous_running = autonomous_running
        self._timeout = min(max(float(timeout), 0.15), 0.5)
        self._speed = min(max(int(speed), 1), 40)
        self._lock = threading.RLock()
        self._enabled = False
        self._deadline = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watchdog, daemon=True, name='recovery-watchdog')
        self._thread.start()

    def enable(self):
        with self._lock:
            if self._autonomous_running():
                raise RuntimeError('Autonomen Fahrmodus zuerst stoppen')
            self._car.stop()
            self._enabled = True
            self._deadline = 0

    def command(self, forward=False, reverse=False, left=False, right=False, deadman=False):
        with self._lock:
            if not self._enabled or self._autonomous_running():
                self._car.stop()
                self._enabled = False
                raise RuntimeError('Bergungsmodus nicht aktiv oder Fahrmodus läuft')
            if not deadman or forward == reverse:
                self._deadline = 0
                self._car.stop()
                return
            speed = self._speed if forward else -self._speed
            angle = 75 if left and not right else 105 if right and not left else 90
            self._deadline = time.monotonic() + self._timeout
            self._car.drive(speed, angle)

    def disable(self):
        with self._lock:
            self._enabled = False
            self._deadline = 0
            self._car.stop()

    @property
    def enabled(self):
        with self._lock:
            return self._enabled

    def _watchdog(self):
        while not self._stop.wait(0.05):
            with self._lock:
                if self._enabled and self._deadline and time.monotonic() > self._deadline:
                    self._deadline = 0
                    self._car.stop()

    def shutdown(self):
        self.disable()
        self._stop.set()
        self._thread.join(timeout=1)
