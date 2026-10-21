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
    process_image: np.ndarray
    raw_image: np.ndarray # Auf Dashboard zeigen
    #frame_id: int
    timestamp: float = 0

@dataclass
class CameraDataProcessed:
    process_image: np.ndarray # Auf Dashboard zeigen
    raw_image: np.ndarray
    line_data: np.ndarray # Auf Dashboard
    timestamp: float = 0
    steering_angle: int = 90 # AufDashboard
    speed: int = 0 # Auf Dashboard


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
        self._camera_data = CameraData(process_image=np.empty(0), raw_image=np.empty(0))

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
        #print(self._camera_data)

    @property
    def camera_data_processed(self) -> CameraDataProcessed :
        return self._camera_data_processed

    @camera_data_processed.setter
    def camera_data_processed(self, camera_data_processed:CameraDataProcessed):
        self._camera_data_processed = camera_data_processed
        #print(self._camera_data_processed)

    