
import logging
import threading
import time
from typing import Optional

import ConfigReader
from SensorCar import SensorCar
from basisklassen import Infrared

# Maybe later ;-P
#class InfraredSensor(ConfigReader.Configurable, Infrared):
class InfraredSensor(Infrared):

    def __init__(self, car:SensorCar, sensor_config:Optional[ConfigReader.ConfigReader]=None) -> None:
        super().__init__()
        self._log = logging.getLogger(self.__class__.__name__)
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("ir_sensors", logger=self._log)
        self._lock = threading.Lock()

        self._car = car 

        self._sensor_min_values = self._cfg.get_list("sensor_min_values", [1000.,1000.,1000.,1000.,1000.])
        self._sensor_max_values = self._cfg.get_list("sensor_max_values", [0.,0.,0.,0.,0.])

        self._raw_sensor_min_values = self.sensor_values
        self._raw_sensor_max_values = self.sensor_values

        self._run = True

    @property
    def sensor_values(self) -> list[float]:
        with self._lock:
            return self.read_analog()

    @property
    def sensor_min_values(self):
        with self._lock:
            return self._sensor_min_values
    
    @property
    def raw_sensor_max_values(self) -> list[float]:
        """
        Liefert die nicht-normierten Sensor_Max_Values
        """
        with self._lock:
            return self._raw_sensor_max_values

    @property
    def raw_sensor_min_values(self) -> list[float]:
        """
        Liefert die nicht-normierten Sensor_Min_Values
        """
        with self._lock:
            return self._raw_sensor_min_values


    @property
    def sensor_max_values(self):
        """
        Liefert die normierten Sensor_Max_Values
        """
        with self._lock:
            return self._sensor_max_values
    
    @sensor_min_values.setter
    def sensor_min_values(self, values:list[float]):
        with self._lock:
            self._log.debug(f"Set sensor_min_values: {values}")
            self._sensor_min_values = values

    @sensor_max_values.setter
    def sensor_max_values(self, values:list[float]):
        with self._lock:
            self._log.debug(f"Set sensor_max_values: {values}")
            self._sensor_max_values = values
    
    @property
    def scan_frequency(self) -> int:
        return self._cfg.get_int("scan_frequency", 50)

    # Gibt Werte zwischen 0.0 und 1.0 zurück
    def _normalize(self, sensors) -> list[float]:
        return list(
            map(
                lambda s,mav,miv: 0 if (mav==miv) else max(0.0, min(1.0, (s-miv) / (mav-miv))), 
                sensors, 
                self.sensor_min_values, 
                self.sensor_max_values
            )
        )

    def _dynamic_calibration(self, sensors:list[float]):
        learning_rate = self._cfg.get_float("learning_rate", 0.1)
        self._log.debug(f"Calibrating IR-Sensors with learning_rate {learning_rate} dynamically...")

        for i in range(len(sensors)):
            min = self._sensor_min_values[i]
            max = self._sensor_max_values[i]
            if (sensors[i] < min):
                self.sensor_min_values[i] = min - learning_rate * (min-sensors[i])
                self._raw_sensor_min_values[i] = sensors[i]
                self._log.info(f"Sensor {i} calibrated. Old min_value {min}, new min value: {sensors[i]}. Newly calibrated min_value: {self.sensor_min_values[i]}")
            
            if (sensors[i] > max):
                self.sensor_max_values[i] = max + learning_rate * (sensors[i] - max)
                self._raw_sensor_max_values[i] = sensors[i]
                self._log.info(f"Sensor {i} calibrated. Old max_value {max}, new max value: {sensors[i]}. Newly calibrated max_value: {self.sensor_max_values[i]}")
        
        self._log.debug("Calibration ended")

    def save_calibration(self):
        self._log.info("Storing calibration data in configuration-file")
        osminv = self._cfg.get("sensor_min_values")
        osmaxv = self._cfg.get("sensor_max_values")
        nsminv = self.sensor_min_values
        nsmaxv = self.sensor_max_values
        self._log.debug(f"sensor_min_values: {osminv} --> {nsminv}")
        self._log.debug(f"sensor_max_values: {osmaxv} --> {nsmaxv}")
        self._cfg.set_config("old_sensor_min_values", osminv)
        self._cfg.set_config("old_sensor_max_values", osmaxv)
        self._cfg.set_config("sensor_min_values", nsminv)
        self._cfg.set_config("sensor_max_values", nsmaxv)
        try:
            self._cfg._save_config()
        except Exception as e:
            self._log.error(e)
            raise(e)

    def read_loop(self, stop_event:threading.Event):
        self._log.debug("Entering Infrared-Sensor loop")
        while (not stop_event.is_set()):
            v = self.sensor_values
            self._log.debug(f"Read value {v} from ir_sensors")
            self._dynamic_calibration(v)
            self._car.ir_sensor_values = self._normalize(v)
            self._log.debug(f"Normalized value {v} --> {self._car.ir_sensor_values}")
            time.sleep(1/self.scan_frequency)
        self._log.debug("Exit Infrared-Sensor loop")
