
import threading
import time
from typing import Optional

import numpy as np

import ConfigReader
from SensorCar import SensorCar
from basisklassen import Infrared


class InfraredSensor(Infrared):

    def __init__(self, car:SensorCar, sensor_config:Optional[ConfigReader.ConfigReader]=None) -> None:
        super().__init__()
        # Legacy Code
        self._history_length = 1
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("ir_sensors")
        self._car = car 
        self._sensor_min_values = self._cfg.get_list("sensor_min_values", [200,200,200,200,200])
        self._sensor_max_values = self._cfg.get_list("sensor_max_values", [800,800,800,800,800])
        self._run = True

    @property
    def sensor_values(self) -> list[float]:
        return self.read_analog()

    # Gibt Werte zwischen 0.0 und 1.0 zurück
    def _normalize(self, sensors) -> list[float]:
        return list(
            map(
                lambda s,mav,miv: 0 if (mav==miv) else max(0.0, min(1.0, (s-miv) / (mav-miv))), 
                sensors, 
                self._sensor_min_values, 
                self._sensor_max_values
            )
        )

    def _dynamic_calibration(self, sensors:list[float]):
        learning_rate = self._cfg.get("learning_rate", 0.1)

        for i in range(len(sensors)):
            min = self._sensor_min_values[i]
            max = self._sensor_max_values[i]
            if (sensors[i] < min):
                self._sensor_min_values[i] = min - learning_rate * (min-sensors[i])
            
            if (sensors[i] > max):
                self._sensor_max_values[i] = max + learning_rate * (sensors[i] - max)

    def read_loop(self, stop_event:threading.Event):
        while (not stop_event.is_set()):
            v = self.sensor_values
            self._dynamic_calibration(v)
            self._car.ir_sensor_values = self._normalize(v)
            time.sleep(0.01)