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
    def save_data(self) -> bool:
        return self._cfg.get_int("save_data", 0)

    @property
    def image(self) -> np.ndarray:
        self.__log.debug(f"Image lesen")
        with self._lock:
            im = self._image
            # Bild speichern
            self.__log.debug(f"Image gelesen")
            return im

    @image.setter
    def image(self, image:np.ndarray):
        self.__log.debug(f"Schreibe Image")
        with self._lock:
            self._image = image

            print('For cv2 write')
            if self.save_data == 1:
                # evtl. Checken: Unterordner existiert, danach dynamische FileName (Datum, Uhrzeit?)
                cv2.imwrite("fig/file_namet.jpg", image)
            print('Speichert datei')

            self.__log.debug(f"Image geschrieben.")
        