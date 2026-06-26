
import threading
import time
from typing import Optional

import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar
from basisklassen import Ultrasonic

import logging

class UltrasonicSensor(Ultrasonic):

    def __init__(self, car:SonicCar, sensor_config:Optional[ConfigReader.ConfigReader]=None) -> None:
        super().__init__()

        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("us_sensors")
        self._car = car 

        self._log = logging.getLogger(self.__class__.__name__)
        logging.basicConfig(filename="ultrasensors.log", level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

        self._min_distance = self._cfg.get_int("min_distance", 0)
        self._max_distance = self._cfg.get_int("max_distance", 0)

        self._scan_frequency = self._cfg.get_int("scan_frequency", 10)
        self._log.debug(f"Auslesefrequenz: {self._scan_frequency}")

        self._last_distance = self._max_distance
        self._last_error = 0


    @property
    def sensor_values(self) -> int:
        d = self.distance()
        self._log.debug(f"Sensor-Wert ausgelesen: {d}")
        return d

    @property
    def last_error(self) -> int:
        return self._last_error

    # Gibt die Distanz in cm zurück (int-Wert)
    def _normalize(self, sensors:int) -> int:

        min_d = self._cfg.get_int("min_distance", 0)
        max_d = self._cfg.get_int("max_distance", 300)

        my_distance = sensors
        distance_clamped = my_distance     

        self._log.debug(f"Normalisiere Sensor-Werte. Eingangsdaten: {my_distance}")
        if my_distance == -1:
            distance_clamped = self._last_distance
            self._log.debug(f"Messwert-Fehler {my_distance}")
            self._last_error = -1
        elif my_distance == -2:
            distance_clamped = self._last_distance
            self._log.debug(f"Messwert-Fehler {my_distance}")
            self._last_error = -2
        elif my_distance == -3:
            distance_clamped = self._last_distance
            self._log.debug(f"Messwert-Fehler {my_distance}")
            self._last_error = -3
        elif my_distance == -4:
            distance_clamped = self._last_distance
            self._log.debug(f"Messwert-Fehler {my_distance}")
            self._last_error = -4
        # not(0 <= -4 <= 300) --> true
        #   return max_d
        elif not (min_d <= my_distance <= max_d):
            distance_clamped = max_d
        
        self._last_distance = distance_clamped
        self._log.debug(f"Normalisierter Wert: {distance_clamped}")

        return distance_clamped



    def read_loop(self, stop_event:threading.Event):
        while (not stop_event.is_set()):
            try:
                v = self.sensor_values
                self._car.distance = self._normalize(v)
            except Exception as e:
                self._log.error(e)
            finally:
                time.sleep(1/self._scan_frequency)



if __name__ == '__main__':
    sc = SonicCar()
    us = UltrasonicSensor(sc)
    stop_event = threading.Event()

    while True:
        us.read_loop(stop_event)
        print(sc.distance)