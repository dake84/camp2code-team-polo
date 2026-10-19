import logging
from typing import Optional

from BaseCar import BaseCar
from ConfigReader import ConfigReader

import numpy as np
import os

import cv2

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
        return self._cfg.get_int("save_data", 0)
