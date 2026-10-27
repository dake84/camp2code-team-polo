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
from LenkwinkelKalkulator import (
    put_steering_lines_into_image, cals_drive_parameter_from_lanes
)
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
        self.last_stop_reason = None
        self.last_diagnostic = None
        self._lost_timeout = cfg.get_float('lost_line_timeout', 1)
        logger = logging.getLogger(self.__class__.__name__)

        super().__init__(name, car, logger, cfg)

    # Werte aus dem vorherigen run
    def _pre_run(self) -> bool:
        self._last_steering_angle = 90
        self._lost_since = None
        self.last_stop_reason = None
        self.last_diagnostic = None
        self._car.drive(0, 90)
        return True

    def _run(self) -> bool:

        camera_data_transfer = self._cameracar.camera_data
        if camera_data_transfer is None:
            self.last_stop_reason = 'NO_CAMERA_DATA'
            self._log.error('CameraMode beendet: kein Kameradatenpaket')
            return False

        try:
            now = time.monotonic_ns()


            # Der LaneDetector hat die Linien bereits fuer diesen Frame ermittelt.
            # Hier wird nur noch der Lenkwinkel aus den Linien berechnet.
            lanes = list(camera_data_transfer.lanes)
            img_with_lines = put_steering_lines_into_image(
                camera_data_transfer.process_image, lanes
            )
            steering_angle_raw, processed_img_areas = cals_drive_parameter_from_lanes(
                img_with_lines, lanes
            )


            # Diagnose wird unabhängig von der Fahrentscheidung veröffentlicht.
            # Die beiden Linienwerte stammen ausschließlich aus CameraData.
            self.last_diagnostic = {
                'frame_id': camera_data_transfer.frame_id,
                'lanes': tuple(lanes),
                'raw_angle': steering_angle_raw,
                'held_previous_angle': steering_angle_raw is None,
                'previous_angle': self._last_steering_angle,
                'stop_reason': None,
            }
            if steering_angle_raw is None:
                if self._lost_since is None:
                    self._lost_since = now

                if now - self._lost_since > self._lost_timeout * 1e9:
                    self.last_stop_reason = 'LANE_TIMEOUT'
                    self.last_diagnostic['stop_reason'] = 'LANE_TIMEOUT'
                    self._log.warning('CameraMode stoppt: LANE_TIMEOUT, Frame %s, letzte Lenkung %s', camera_data_transfer.frame_id, self._last_steering_angle)
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


            self._cameracar.drive(speed, steering_angle)
            self.last_diagnostic.update(
                requested_angle=steering_angle,
                actual_angle=self._car.steering_angle,
                speed_control=self._car.speed,
            )


            timestamp = time.time_ns()

            latenz = timestamp - camera_data_transfer.timestamp

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


        except Exception:
            self.last_stop_reason = 'CONTROLLER_EXCEPTION'
            self._log.exception('Fehler in CameraMode._run()')
            return False


        return True


    def _run_condition(self) -> bool:
        return True    
    # def: Linienwerte bekommen, Aus den Linien lenkwinkel bearbeiten


    def driving_parameter (self, steering_angle_raw: int ) -> tuple[int, int]:
        steering_angle = steering_angle_raw  # Replace with actual calculation
        speed = 30           # Replace with actual calculation

        return steering_angle, speed
    





















