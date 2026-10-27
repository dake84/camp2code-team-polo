"""Fahrmodus 1: vorwaerts und rueckwaerts."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

import ConfigReader

from .base import DrivingMode

if TYPE_CHECKING:
    from BaseCar import BaseCar

class ModeOne(DrivingMode):

    def __init__(self, car: Optional[BaseCar], name:str="Fahrmodus 1", logger: Optional[logging.Logger]=None, cfg: Optional[ConfigReader.ConfigReader]=None, update_cfg: bool = False, frequency: int = 500) -> None:
        if car is None:
            # Lazy-Import: Hardware nur bei Bedarf initialisieren.
            from SonicCar import SonicCar
            car = SonicCar()
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("soniccar_controller")
        super().__init__(name, car, logger, cfg)

    def _run_condition(self) -> bool:
        return True
    
    def _run(self) -> bool:
        self._sleep_with_stop(1, stop=True)  
                     
        self._car.drive(30)
        self._log.debug(f"3 Sekunden vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(3, stop=False)

        self._log.debug(f"1 Sekunde Stopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(1, stop=True)
            
        self._car.drive(-30)
        self._log.debug(f"3 Sekunden rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(3, stop = False)

        self._car.stop()
        return False


    def _sleep_with_stop(self, duration: float, stop:bool) -> bool:
        if (self._stop_event is None):
            raise RuntimeError("Stop_event not availabel")
        end_time = time.time() + duration
        step_time = 1/self._frequency 
        if (stop):
            self._car.stop()

        while time.time() < end_time and not self._stop_event.is_set():
            time.sleep(step_time)

        return False

