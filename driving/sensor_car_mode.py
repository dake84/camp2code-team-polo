"""Basisklasse fuer Fahrmodi mit Infrarotsensorik."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import ConfigReader

from .sonic_car_mode import SonicCarMode

if TYPE_CHECKING:
    from SensorCar import SensorCar

class SensorCarMode(SonicCarMode):
    def __init__(self, name:str, car:Optional[SensorCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None):
        if car is None:
            # Lazy-Import: Hardware nur bei Bedarf initialisieren.
            from SensorCar import SensorCar
            car = SensorCar()
        # just a pointer to remove linter-errors
        self._sensorcar = car
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("sensorcar_controller")
        super().__init__(name, car, cfg)

