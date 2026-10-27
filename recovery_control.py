"""Zeitlich begrenzte manuelle Bergungssteuerung mit serverseitigem Watchdog.

Architektur (Prinzip 15, keine Hardwarezugriffe aus Dashboard-Callbacks):
    ``command()`` speichert nur den gewuenschten Zustand und eine Frist.
    Fahrbefehle an das Fahrzeug sendet ausschliesslich der eigene
    Watchdog-Thread. Bleiben Befehle aus (Browser zu, Netz weg), stoppt er.
Sicherheitsrelevante STOP-Befehle (``enable``/``disable``/Fehler) werden
weiterhin sofort ausgefuehrt.
"""
import threading
import time


class RecoveryControl:
    """Bewegt nur bei aktivem Deadman und stoppt bei ausbleibenden Befehlen."""

    PERIOD_S = 0.05

    def __init__(self, car, autonomous_running, timeout=0.35, speed=30):
        self._car = car
        self._autonomous_running = autonomous_running
        self._timeout = min(max(float(timeout), 0.15), 0.5)
        self._speed = min(max(int(speed), 1), 40)
        self._lock = threading.RLock()
        self._enabled = False
        self._deadline = 0.0
        self._target = None          # (speed, angle) oder None
        self._applied = None         # zuletzt an das Fahrzeug gesendeter Befehl
        self._wake = threading.Event()
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
            self._target = None
            self._applied = (0, 90)

    def command(self, forward=False, reverse=False, left=False, right=False, deadman=False):
        """Setzt den Sollzustand. Die Ausfuehrung uebernimmt der Watchdog-Thread."""
        with self._lock:
            if not self._enabled or self._autonomous_running():
                self._car.stop()
                self._enabled = False
                self._target = None
                self._applied = (0, 90)
                raise RuntimeError('Bergungsmodus nicht aktiv oder Fahrmodus läuft')
            if not deadman or forward == reverse:
                self._deadline = 0
                self._target = (0, 90)
            else:
                speed = self._speed if forward else -self._speed
                angle = 75 if left and not right else 105 if right and not left else 90
                self._deadline = time.monotonic() + self._timeout
                self._target = (speed, angle)
        self._wake.set()

    def disable(self):
        with self._lock:
            self._enabled = False
            self._deadline = 0
            self._target = None
            self._applied = (0, 90)
            self._car.stop()

    @property
    def enabled(self):
        with self._lock:
            return self._enabled

    @property
    def applied_command(self):
        """Zuletzt an das Fahrzeug gesendeter Befehl (speed, angle) oder None."""
        with self._lock:
            return self._applied

    def _step(self):
        """Ein Watchdog-Zyklus: Frist pruefen und Sollzustand anwenden."""
        with self._lock:
            if not self._enabled:
                return
            if self._deadline and time.monotonic() > self._deadline:
                self._deadline = 0
                self._target = (0, 90)
            target = self._target
            if target is None or target == self._applied:
                return
            if target == (0, 90):
                self._car.stop()
            else:
                self._car.drive(*target)
            self._applied = target

    def _watchdog(self):
        while not self._stop.is_set():
            self._wake.wait(self.PERIOD_S)
            self._wake.clear()
            if self._stop.is_set():
                break
            self._step()

    def shutdown(self):
        self.disable()
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=1)
