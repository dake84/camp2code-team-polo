"""Basisklasse fuer Fahrmodi mit Ultraschallsensorik."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import ConfigReader

from .base import DrivingMode

if TYPE_CHECKING:
    from SonicCar import SonicCar

class SonicCarMode(DrivingMode):

    def __init__(self, name:str, car:Optional[SonicCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None):
        if car is None:
            # Lazy-Import: Hardware nur bei Bedarf initialisieren.
            from SonicCar import SonicCar
            car = SonicCar()
        # just a pointer to remove linter-errors
        self._soniccar = car
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("soniccar_controller")
        super().__init__(name, car, logger, cfg)

