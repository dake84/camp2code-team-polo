"""Fahrzeug-Attrappe fuer Simulation und Offline-Betrieb (Punkt 4 aus Paket F).

Nimmt Fahrbefehle an wie ``BaseCar`` (gleiche Begrenzungen), sendet aber
nichts an Servo oder Motoren. Es werden keine Hardwaremodule importiert.
"""
import threading
from collections import deque


class ReplayCar:
    """Stellt die von ``CameraMode`` genutzte Fahrzeug-Schnittstelle bereit."""

    is_replay = True

    def __init__(self, v_min=20, v_max=100, history=5000):
        self._lock = threading.RLock()
        self._v_min = int(v_min)
        self._v_max = int(v_max)
        self._steering_angle = 90
        self._speed = 0
        self._direction = 1
        self.camera_data = None
        self._driving_state = None
        self.commands = deque(maxlen=history)

    @property
    def steering_angle(self):
        with self._lock:
            return self._steering_angle

    @property
    def speed(self):
        """Wie ``BaseCar.speed``: Vorzeichen = Fahrtrichtung."""
        with self._lock:
            return self._direction * self._speed

    @property
    def driving_state(self):
        with self._lock:
            return self._driving_state

    @driving_state.setter
    def driving_state(self, state):
        with self._lock:
            self._driving_state = state

    def _clamp_speed(self, speed):
        speed = max(-100, min(100, int(speed)))
        if speed > 0:
            return min(max(self._v_min, speed), self._v_max)
        if speed < 0:
            return max(-self._v_max, min(-self._v_min, speed))
        return 0

    def drive(self, speed=None, steering_angle=None):
        with self._lock:
            if speed is not None:
                value = self._clamp_speed(speed)
                self._direction = -1 if value < 0 else 1
                self._speed = abs(value)
            if steering_angle is not None:
                self._steering_angle = min(135, max(45, int(steering_angle)))
            self.commands.append((self.speed, self._steering_angle))

    def stop(self):
        self.drive(0, 90)

    # Kompatibilitaet zu DrivingMode (update_cfg=True ruft dies auf)
    def _update_config(self):
        return None
