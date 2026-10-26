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

from .mode_one import ModeOne

class ModeTwo(ModeOne):

    def __init__(self, car: Optional[BaseCar], name: str = "Fahrmodus 2", logger: Optional[logging.Logger] = None, cfg: Optional[ConfigReader.ConfigReader] = None, update_cfg: bool = False, frequency: int = 500) -> None:
        super().__init__(car=car, name=name, logger=logger, cfg=cfg, update_cfg=update_cfg, frequency=frequency)

    def _run_condition(self) -> bool:
        return True
    
    def _run(self) -> bool:
        self._circle((135, "rechts/Uhrzeigersinn"))
        self._circle((45, "links/gegen Uhrzeigersinn"))
        return False
    
    def _circle(self, direction: Tuple[int, str]) -> bool:
        self._log.info(f'Starte Fahrmodus 2 ({direction[1]})')
        
        self._sleep_with_stop(1, stop=True)

        self._car.drive(30)
        self._log.debug(f"1 Sekunde vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(1, stop=False)

        self._car.drive(30, direction[0])
        self._log.debug(f"8 Sekunden {direction[1]} vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(8, stop=False)

        self._log.debug(f"Kurzer Zwischenstopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(2, stop=True)

        self._car.drive(-30, direction[0])
        self._log.debug(f"8 Sekunden {direction[1]} rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(8, stop=False)

        self._car.drive(-30, 90)
        self._log.debug(f"1 Sekunde rückwärts zum Startpunkt. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(1, stop=False)

        self._car.stop()
        self._log.info(f"Fahrmodus 2 {direction[1]} beendet. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}") 

        return False

