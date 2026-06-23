
import threading
import time
from typing import Optional

import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar
from basisklassen import Ultrasonic


class UltrasonicSensor(Ultrasonic):

    def __init__(self, car:SonicCar, sensor_config:Optional[ConfigReader.ConfigReader]=None, scan_frequency:int=20) -> None:
        super().__init__()

        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("us_sensors")
        self._car = car 

        self._min_distance = self._cfg.get("min_distance", 0)
        self._max_distance = self._cfg.get("max_distance", 0)

        # Legacy Code
        self._history_length = 1
        self._run = True

        self._scan_frequency = scan_frequency


    @property
    def sensor_values(self) -> int:
        return self.distance()

    # Gibt die Distanz in cm zurück (int-Wert)
    def _normalize(self, sensors:int) -> int:

        min_d = self._cfg.get_int("min_distance", 0)
        max_d = self._cfg.get_int("max_distance", 0)

        my_distance = self.sensor_values
        distance_clamped = my_distance     

        if my_distance == -1:
            distance_clamped = max_d
        elif my_distance == -2:
            distance_clamped = max_d
        elif my_distance == -3:
            distance_clamped = min_d
        elif my_distance == -4:
            distance_clamped = max_d
        elif not (min_d <= my_distance <= max_d):
            distance_clamped = max_d

        return distance_clamped


    def read_loop(self, stop_event:threading.Event):
        while (not stop_event.is_set()):
            v = self.sensor_values
            self._car.distance = self._normalize(v)
            time.sleep(1/self._scan_frequency)