import logging
import time
from typing import Optional

from BaseCar import BaseCar
from ConfigReader import ConfigReader

class SonicCar(BaseCar):
    """Lässt das Auto mittels des Ultraschallsensor (Abstandmessung) fahren

    Args:
        BaseCar (object): Basisklasse des Autos - Kann nur Lenkung und Geschwindigkeit einstellen
    """

    def __init__(self, config:Optional[ConfigReader]=None):
        """Initiert das Object 'SonicCar' und über super-init die Methoden aus 'BaseCar'
        """        

        # Super init - Damit ein Object initiiert wird die auch BaseCar kennt (Methoden der BaseCar)
        super().__init__(config=config)
        self._distance = 300
        self._distance_valid = False
        self._distance_timestamp_ns = None
        self._distance_monotonic_ns = None
        self.__log = logging.getLogger(SonicCar.__name__)


    @property
    def distance(self) -> int:
        self.__log.debug(f"Distanz lesen vor Lock")
        with self._lock:
            d = self._distance
            self.__log.debug(f"Distanz {d} gelesen im Lock")
            return d

    @distance.setter
    def distance(self, distance:int):
        self.__log.debug(f"Schreibe Distanz mit Wert {distance}")
        with self._lock:
            self._distance = distance
            self._distance_valid = distance is not None
            self._distance_timestamp_ns = time.time_ns()
            self._distance_monotonic_ns = time.monotonic_ns()
            self.__log.debug(f"Distanz schreiben erfolgreich mit Wert {distance}")
        

