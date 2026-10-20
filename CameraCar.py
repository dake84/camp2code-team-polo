import logging
from typing import Optional

from BaseCar import BaseCar
from ConfigReader import ConfigReader

from dataclasses import dataclass

import numpy as np
import os

import cv2


@dataclass
class CameraData:
    #frame_id: int
    timestamp: float
    process_image: np.ndarray  # OpenCV BGR / RGB Frame
    raw_image: np.ndarray

@dataclass
class CameraDataProcessed:
    timestamp: float
    process_image: np.ndarray  # OpenCV BGR / RGB Frame
    raw_image: np.ndarray
    steering_angle: int
    speed: int


class CameraCar(BaseCar):
    """Lässt das Auto mittels des Ultraschallsensor (Abstandmessung) fahren

    Args:
        BaseCar (object): Basisklasse des Autos - Kann nur Lenkung und Geschwindigkeit einstellen
    """
    def __init__(self, config:Optional[ConfigReader]=None):
        """Initiert das Object 'CameraCar' und über super-init die Methoden aus 'BaseCar'
        """        

        # Super init - Damit ein Object initiiert wird die auch BaseCar kennt (Methoden der BaseCar)
        super().__init__(config=config)
        #self._distance = 300
        self.__log = logging.getLogger(CameraCar.__name__)

    @property
    def save_data(self) -> bool: # Lenkwinkel, Speed für AI imt Zeitstempel
        return self._cfg.get_bool("save_data", False)

    @property
    def camera_data(self) -> CameraData :
        return self._camera_data

    @camera_data.setter
    def camera_data(self, camera_data:CameraData):
        self._camera_data = camera_data
        print(self._camera_data)
