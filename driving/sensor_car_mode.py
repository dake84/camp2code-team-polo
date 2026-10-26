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

from .sonic_car_mode import SonicCarMode

class SensorCarMode(SonicCarMode):
    def __init__(self, name:str, car:Optional[SensorCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None):
        car = car if car is not None else SensorCar()
        # just a pointer to remove linter-errors
        self._sensorcar = car
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("sensorcar_controller")
        super().__init__(name, car, cfg)

