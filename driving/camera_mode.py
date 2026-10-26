from __future__ import annotations
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
from CameraCar import CameraCar
from models import CameraDataProcessed, CameraData, DrivingState
from CarLogger import Loggable
import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar
from LenkwinkelKalkulator import lenkwinkel_from_image
import select
from evdev import InputDevice, ecodes
from .base import DrivingMode
class CameraMode(DrivingMode):

    def __init__(self, name='CameraModus', car:Optional[CameraCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None):
        car = car if car is not None else CameraCar()
        self._cameracar = car
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("cameracar_controller")
        self._last_steering_angle = 90
        self._lost_since = None
        self._lost_timeout = cfg.get_float('lost_line_timeout', 1)
        logger = logging.getLogger(self.__class__.__name__)

        super().__init__(name, car, logger, cfg)

    # Werte aus dem vorherigen run
    def _pre_run(self) -> bool:
        self._last_steering_angle = 90
        self._lost_since = None
        self._car.drive(0, 90)
        return True

    def _run(self) -> bool:
        print('1: _run START', flush=True)

        camera_data_transfer = self._cameracar.camera_data
        print('2: camera_data gelesen', flush=True)

        try:
            now = time.monotonic_ns()

            print('3: vor lenkwinkel_from_image', flush=True)

            steering_angle_raw, processed_img_areas, lanes = lenkwinkel_from_image(camera_data_transfer.process_image)

            print('4: nach lenkwinkel_from_image', flush=True)

            if steering_angle_raw is None:
                if self._lost_since is None:
                    self._lost_since = now

                if now - self._lost_since > self._lost_timeout * 1e9:
                    self._log.info(f'Seit {self._lost_timeout}s keine zwei Linien – {self._name} wird beendet')
                    return False

                steering_angle_raw = self._last_steering_angle

            else:
                self._lost_since = None
                self._last_steering_angle = steering_angle_raw

            driving_parameter = self.driving_parameter(steering_angle_raw)

            steering_gain = self._cfg.get_float('steering_gain', 2.0)
            steering_angle = int(max(45, min(135, 90 + (driving_parameter[0] - 90) * steering_gain)))
            speed = driving_parameter[1]

            print('5: vor drive', flush=True)

            self._cameracar.drive(speed, steering_angle)

            print('6: nach drive', flush=True)

            timestamp = time.time_ns()

            latenz = timestamp - camera_data_transfer.timestamp
            print(f'7: Latenz = {latenz}', flush=True)

            camera_data_processed = CameraDataProcessed(
                frame_id=camera_data_transfer.frame_id,
                raw_image=camera_data_transfer.raw_image,
                timestamp=timestamp,
                process_image=camera_data_transfer.process_image,
                process_image_lines=processed_img_areas,
                steering_angle=steering_angle_raw,
                steering_angle_car=self._car.steering_angle,
                line_data=np.empty(0),
                speed=self._car.speed,
                lanes=lanes
            )

            print('8: CameraDataProcessed erzeugt', flush=True)

            # Die Fahrentscheidung referenziert genau das verarbeitete Bild.
            self._cameracar.driving_state = DrivingState(
                frame_id=camera_data_transfer.frame_id,
                timestamp_ns=timestamp,
                steering_angle=camera_data_processed.steering_angle,
                speed=camera_data_processed.speed,
                lanes=camera_data_processed.lanes,
                steering_angle_car=camera_data_processed.steering_angle_car,
            )
            # Alter Datenvertrag bleibt fuer Dashboard/Recorder vorerst aktiv.
            self._cameracar.camera_data_processed = camera_data_processed

            print('9: camera_data_processed gesetzt', flush=True)

        except Exception:
            self._log.exception('Fehler in CameraMode._run()')
            return False

        print('10: _run ENDE', flush=True)

        return True


    def _run_condition(self) -> bool:
        return True    
    # def: Linienwerte bekommen, Aus den Linien lenkwinkel bearbeiten


    def driving_parameter (self, steering_angle_raw: int ) -> tuple[int, int]:
        steering_angle = steering_angle_raw  # Replace with actual calculation
        speed = 30           # Replace with actual calculation

        return steering_angle, speed
    



















