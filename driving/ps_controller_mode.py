from __future__ import annotations

# Gemeinsame Bibliotheken aus der bisherigen Driving.py.
# Die Fahrlogik bleibt bei dieser reinen Modulaufteilung unveraendert.
import abc
import curses
import random
import threading
import time
from datetime import datetime
from typing import Optional, Tuple
import logging
import cv2
import numpy as np
from BaseCar import BaseCar
from CameraCar import CameraCar, CameraDataProcessed, CameraData
from CarLogger import Loggable
import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar
from LenkwinkelKalkulator import lenkwinkel_from_image
import select
from evdev import InputDevice, ecodes

from .base import DrivingMode

class PSControllerMode(DrivingMode):

    def __init__(self, name='PS-Controller', car:Optional[BaseCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None, device_path:str='/dev/input/event4'):
        car = car if car is not None else CameraCar()
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

