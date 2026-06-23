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
        super().__init__(config)
        self._distance = 300

    @property
    def distance(self) -> int:
        self._log.debug(f"Distanz lesen vor Lock")
        with self._lock:
            d = self._distance
            self._log.debug(f"Distanz {d} gelesen im Lock")
            return d

    @distance.setter
    def distance(self, distance:int):
        self._log.debug(f"Schreibe Distanz mit Wert {distance}")
        with self._lock:
            self._distance = distance
            self._log.debug(f"Distanz schreiben erfolgreich mit Wert {distance}")
        