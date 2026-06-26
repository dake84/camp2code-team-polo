
import logging
import threading
import time
from typing import Optional

import numpy as np

import ConfigReader
from SensorCar import SensorCar
from basisklassen import Infrared

# Maybe later ;-P
#class InfraredSensor(ConfigReader.Configurable, Infrared):
class InfraredSensor(Infrared):

    def __init__(self, car:SensorCar, sensor_config:Optional[ConfigReader.ConfigReader]=None, logger:Optional[logging.Logger]=None) -> None:
        super().__init__()
        self._log = logger if logger is not None else logging.getLogger(self.__class__.__name__)
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("ir_sensors", logger=self._log)
        self._lock = threading.Lock()

        self._car = car 

        self._sensor_min_values = self._cfg.get_list("sensor_min_values", [1000.,1000.,1000.,1000.,1000.])
        self._sensor_max_values = self._cfg.get_list("sensor_max_values", [0.,0.,0.,0.,0.])

        self._sum_decay_rate = np.zeros(len(self.sensor_values))

        self._run = True

        #update config regularly
        self._debug_mode = True
        if (self._debug_mode):
            self._log.warning("Debug mode activated. This will cost some performance.")


        for s in (("min", self._sensor_min_values), ("max", self._sensor_max_values)):
            for i in range(len(s[1])):
                self._log.info(f"S{i+1}_{s[0]} initially calibrated -> {s[1][i]:.3f}")

    @property
    def sensor_values(self) -> list[float]:
        with self._lock:
            return self.read_analog()

    @property
    def sensor_min_values(self):
        with self._lock:
            return self._sensor_min_values
    
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
        normiert = list(
            map(
                lambda s,mav,miv: 0 if (mav==miv) else max(0.0, min(1.0, (s-miv) / (mav-miv))), 
                sensors, 
                self.sensor_min_values, 
                self.sensor_max_values
            )
        )
        self._log.debug(f"Normiere Sensorwerte. Messwerte: {sensors}, Kalibrierwerte: (min:{self._sensor_min_values}, max:{self._sensor_max_values}) -> Normiert: {normiert}")
        return normiert

    def _dynamic_calibration(self, sensors:list[float]):
        learning_rate = self._cfg.get_float("learning_rate", 0.1)
        calibration_log_threshold_rate = self._cfg.get_float("calibration_log_threshold_rate", 0.05)
        calibration_decay_rate = self._cfg.get_float("calibration_decay_rate", 0.001)
        minimum_contrast = self._cfg.get_int("minimum_contrast", 250)
        self._log.debug(f"Calibrating IR-Sensors with learning_rate {learning_rate} dynamically...")

        for i in range(len(sensors)):
            min = self._sensor_min_values[i]
            max = self._sensor_max_values[i]

            if (sensors[i] < min):
                self.sensor_min_values[i] = min - learning_rate * (min-sensors[i])
                self._sum_decay_rate[i] = 0
                self._log.debug(f"S{i}_min: {min:.3f} -> {self.sensor_min_values[i]:.3f}.")
                if (sensors[i] < (1-calibration_log_threshold_rate)*min):
                    self._log.info(f"S{i}_min calibrated. {min:.3f} -> {self.sensor_min_values[i]:.3f}.")
                    if (self._sum_decay_rate[i] > 0.5*calibration_log_threshold_rate*self._sensor_min_values[i]):
                        self._log.info(f"S{i}_min: Decay {self._sum_decay_rate[i]:.3f} was reset.")
            elif (sensors[i] > max):
                self.sensor_max_values[i] = max + learning_rate * (sensors[i] - max)
                self._sum_decay_rate[i] = 0
                self._log.debug(f"S{i}_max: ({max:.3f} -> {self.sensor_max_values[i]:.3f}.")
                if (sensors[i] > (1+calibration_log_threshold_rate)*max):
                    self._log.info(f"S{i}_max calibrated. {max:.3f} -> {self.sensor_max_values[i]:.3f}")
                    if (self._sum_decay_rate[i] > 0.5*calibration_log_threshold_rate*self.sensor_max_values[i]):
                        self._log.info(f"S{i}_max: Decay {self._sum_decay_rate[i]:.3f} was reset.")
            
            elif ((max-min)>minimum_contrast):
                self.sensor_min_values[i] += calibration_decay_rate
                self.sensor_max_values[i] -= calibration_decay_rate
                self._sum_decay_rate[i] += calibration_decay_rate

                self._log.debug(f"S{i} decaying, min: {min:.3f} -> {self.sensor_min_values[i]:.3f}, total decay from last calibration: {self._sum_decay_rate[i]:.3f}")
                self._log.debug(f"S{i} decaying, max: {max:.3f} -> {self.sensor_max_values[i]:.3f}, total decay from last calibration: {-self._sum_decay_rate[i]:.3f}")
                
                if (self._sum_decay_rate[i] > 0.5*calibration_log_threshold_rate*self._sensor_min_values[i]):
                    self._log.info(f"S{i} calibration decayed by {calibration_log_threshold_rate*100:.2f}% -> max: {max}, min: {min}")
                    self._sum_decay_rate[i] = 0
            else:
                self._log.debug(f"S{i}: no further decay because we reached minimum contrast (max:{max}, min:{min}, minimum_contrast:{minimum_contrast})")
        self._log.debug("Calibration ended")

    def save_calibration(self):
        self._log.info("Storing calibration data in configuration-file")
        osminv = self._cfg.get("sensor_min_values")
        osmaxv = self._cfg.get("sensor_max_values")
        nsminv = self.sensor_min_values
        nsmaxv = self.sensor_max_values
        self._log.debug(f"sensor_min_values: {osminv} --> {nsminv}")
        self._log.debug(f"sensor_max_values: {osmaxv} --> {nsmaxv}")
        #self._cfg.set_config("old_sensor_min_values", osminv)
        #self._cfg.set_config("old_sensor_max_values", osmaxv)
        self._cfg.set_config("sensor_min_values", nsminv)
        self._cfg.set_config("sensor_max_values", nsmaxv)
        try:
            self._cfg._save_config()
        except Exception as e:
            self._log.error(e)
            raise(e)

    def read_loop(self, stop_event:threading.Event):
        try:
            self._log.debug("Entering Infrared-Sensor loop")
            while (not stop_event.is_set()):
                if (self._debug_mode):
                    self._cfg._load_config_file()
                v = self.sensor_values
                self._log.debug(f"Read value {v} from ir_sensors")
                self._dynamic_calibration(v)
                self._car.ir_sensor_values = self._normalize(v)
                self._log.debug(f"Normalized value {v} --> {self._car.ir_sensor_values}")
                time.sleep(1/self.scan_frequency)
        except Exception as e:
            self._log.exception(e)
        finally:
            self._log.debug("Exit Infrared-Sensor loop")
