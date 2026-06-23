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

    @property
    def distance(self):
        with self._lock:
            return self._distance

    @distance.setter
    def distance(self, distance:int):
        with self._lock:
            self._distance = distance

        