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

from .approach_obstacle import ApproachObstacle

class RoomExplorer(ApproachObstacle):
    def __init__(self, name="Fahrmodus 4 (RoomExplorer)", car:Optional[SonicCar]=None):
        super().__init__(name=name, car=car)

        self._explore_logged = False

    def _pre_run(self):
        explorer_max_time = self._cfg.get_int("explorer_max_time", 30)

        self._t_end = time.time() + explorer_max_time
        self._log.debug(f"Erkunde den Raum für {explorer_max_time}s bis {datetime.fromtimestamp(self._t_end).strftime("%Y-%m-%d %H:%M:%S")}")

        actual_speed_drive_explore = self._cfg.get_int("start_speed", 60)
        steering_angle_drive_explore = self._cfg.get_int("start_angle", 90)
        self._car.drive(actual_speed_drive_explore, steering_angle_drive_explore)

        self._counter = 0
        self._log.debug(f"Parameter: speed_direction ({actual_speed_drive_explore}, steer_direction:{steering_angle_drive_explore})")

        return True

    def _run_condition(self) -> bool:
        if (self._t_end is None):
            raise RuntimeError("Start- und/oder Endzeit nicht gesetzt (t_start: {self._t_start}, t_end: {self._t_end})")
        return (time.time() < self._t_end)

    def _run(self) -> bool:
            
        if (not self._set_speed_based_on_distance()):
            # Freie Fahrt
            self._log.debug("Freie Fahrt")
            self._explore()
        else:
            # Ausweichmanöver einleiten
            self._explore_logged = False
            suchzeit_sekunden = self._overcome_obstacle()
            self._log.info(f"Fahre rückwärts für {suchzeit_sekunden}")
            start_suchzeit = time.time()
            ende_suchzeit = start_suchzeit + suchzeit_sekunden
            if (self._stop_event is None):
                raise RuntimeError("Stop_Event not available")
            while (not self._stop_event.is_set() and (time.time() < ende_suchzeit)):
                time.sleep(1/self._frequency)
            self._log.info(f"Rückwärtsfahrt beendet")

        return True

    def _post_run(self):
        self._t_end = None
        self._counter = 0

        return True

    def _explore(self):
        if not self._explore_logged:
            self._log.info(f"Fahre auf Erkundungsfahrt bis zum nächsten Hindernis...")
            self._explore_logged = True
        actual_speed = self._soniccar.speed
        steering_angle = self._soniccar.steering_angle
        steer_dir = -1 if steering_angle < 90 else 1
        speed_dir = self._soniccar.direction

        self._log.debug(f"speed: {actual_speed},, angle: {steering_angle}, speed_dir: {speed_dir}, steer_dir: {steer_dir}, counter: {self._counter}")
        self._counter += 1

        # Richtung nur alle x Zyklen ändern
        if self._counter >= 25:
            counter = 0
            speed_dir = random.choice([-1, 1])
            steer_dir = random.choice([-1, 1])

        # kleine Schritte -> smooth
        actual_speed += speed_dir * 1
        steering_angle += int(steer_dir * 1.5)

        # Grenzen
        actual_speed = max(30, min(actual_speed, 100))
        steering_angle = max(45, min(steering_angle, 135))

        self._log.debug(f"Exploriere den Raum..... (Geschwindigkeit: {actual_speed}, Lenkwinkel: {steering_angle})")
        self._soniccar.drive(actual_speed, steering_angle)

    def _overcome_obstacle(self) -> int:
        # Gibt die Rückwärtsfahrzeit in Sekunden aus
                
        self._soniccar.stop()

        # Lenkt links oder rechts ein
        ausweich_lenkung = random.choice(
            (
                (45, "links"), 
                (135, "rechts")
            )
        )

        self._log.info(f"Overcoming obstacle..... driving to the {ausweich_lenkung[1]}")

        self._soniccar.drive(-30, ausweich_lenkung[0])
        return random.randint(1, 4)

    def get_logging_payload(self, log_level:int=logging.INFO) -> dict:
        """_summary_

        Args:
            log_level (int, optional): _description_. Defaults to logging.INFO.

        Returns:
            dict: _description_
        """
        with self._lock:
            payload = self._car.get_logging_payload()
            payload["lenkwinkel"] = "yet to be implemented"
            #if (log_level == logging.DEBUG):
                #payload["lenkwinkel_calculus"] = self._lw_debug_values
            return payload

