"""Manuelle Steuerung ueber einen Gamepad-Controller (evdev)."""
from __future__ import annotations

import logging
import select
from typing import TYPE_CHECKING, Optional

import ConfigReader

from .base import DrivingMode

try:  # evdev ist nur auf Linux verfuegbar; Import des Pakets bleibt dadurch moeglich.
    from evdev import InputDevice, ecodes
except ImportError:  # pragma: no cover - abhaengig von der Plattform
    InputDevice = None
    ecodes = None

if TYPE_CHECKING:
    from BaseCar import BaseCar

class PSControllerMode(DrivingMode):

    def __init__(self, name='PS-Controller', car:Optional[BaseCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None, device_path:str='/dev/input/event4'):
        if car is None:
            # Lazy-Import: Hardware nur bei Bedarf initialisieren.
            from CameraCar import CameraCar
            car = CameraCar()
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader('cameracar_controller')
        super().__init__(name, car, logger, cfg)
        self._device_path = device_path
        self._controller = None
        self._steer = 0.0
        self._speed = 0.0
        self._steer_min = 0.1
        self._speed_min = 15
        self._last_drive = None

    def _run_condition(self) -> bool:
        return True

    def _pre_run(self) -> bool:
        if InputDevice is None:
            self._log.error('evdev ist nicht installiert; Controller-Modus nicht verfuegbar')
            return False
        try:
            self._controller = InputDevice(self._device_path)
            self._controller.grab()
        except OSError as e:
            self._log.error(f'Controller {self._device_path} nicht verfügbar: {e}')
            self._controller = None
            return False
        self._log.info(f'Using device: {self._controller.path} - {self._controller.name}')
        self._steer, self._speed, self._last_drive = 0.0, 0.0, None
        return super()._pre_run()

    def _run(self) -> bool:
        if self._controller is None: return False

        try:
            ready, _, _ = select.select([self._controller.fd], [], [], 0.005)
            if ready:
                for event in self._controller.read():
                    if event.type != ecodes.EV_ABS: continue
                    if event.code == ecodes.ABS_RY: self._speed = (event.value - 127) / 128
                    elif event.code == ecodes.ABS_X: self._steer = (event.value - 127) / 128
        except BlockingIOError:
            pass
        except OSError as e:
            self._log.error(f'Controller getrennt: {e}')
            return False

        # Lenkung
        steer_angle = 90 if abs(self._steer) < self._steer_min else int(max(45, min(135, 90 + 45 * self._steer)))

        # Geschwindigkeit: Stick nach oben -> speed < 0 -> vorwärts (wie bw.forward() im Originalskript)
        speed_0_to_100 = min(100, int(abs(self._speed) * 100))
        v = 0 if speed_0_to_100 < self._speed_min else (speed_0_to_100 if self._speed < 0 else -speed_0_to_100)

        # Nur bei Änderung an die Hardware senden
        if (v, steer_angle) != self._last_drive:
            self._car.drive(v, steer_angle)
            self._last_drive = (v, steer_angle)

        return True

    def _post_run(self) -> bool:
        if self._controller is not None:
            try: self._controller.ungrab()
            except OSError: pass
            try: self._controller.close()
            except OSError: pass
            self._controller = None
        return super()._post_run()

