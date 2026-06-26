import logging
import random
import time
from typing import Optional

import numpy as np

from ConfigReader import ConfigReader
from SonicCar import SonicCar

class SensorCar(SonicCar):
    """Erstellung Klasse Sensor Car; Grundfunktionalitäten werden aus BaseCar geerbt und um Infrarotsensorik ergänzt, um Linien zu erkennen und entsprechend zu steuern.

    """
    def __init__(self, config:Optional[ConfigReader]=None):
        """Initialisiert den Infrarotsensor und lädt oder kalibriert Referenzwerte.

        Wenn keine Referenzen übergeben werden, führt das Objekt automatisch eine
        Kalibrierung durch und speichert die ermittelten Referenzwerte intern.

        Args:
            references (list | None, optional): Liste der Referenzmesswerte.
                Wenn None, werden die Referenzen automatisch kalibriert.
        """
        super().__init__(config=config)
        self._ir_sensor_values = [[0.,0.,0.,0.,0.]]
        self._ir_sensor_min_values = [0.,0.,0.,0.,0.]
        self._ir_sensor_max_values = [1.,1.,1.,1.,1.]
        self.__log = logging.getLogger(SensorCar.__name__)
        

    def ir_sensor_value_history(self, length:int=0, clear_history:bool=True):
        with self._lock:
            if not self._ir_sensor_values:
                return []
            
            hist = self._ir_sensor_values[-length:] if (length > 0) else self._ir_sensor_values
            
            if (clear_history):
                self._ir_sensor_values = [hist[-1]]
            
            hist_array = np.array(hist)
            return np.mean(hist_array, axis=0).tolist() if (hist_array.ndim > 1) else hist_array.flatten().tolist()

    @property
    def ir_sensor_values(self) -> list[float]:
        with self._lock:
            self.__log.debug(f"Ausgabe der letzten gespeicherten Sensorwerte ({len(self._ir_sensor_values)} Messungen):")
            for i in range(len(self._ir_sensor_values)):
                self.__log.debug(f"({i}): {self._ir_sensor_values[i]}")
            return self._ir_sensor_values[-1]
    
    @ir_sensor_values.setter
    def ir_sensor_values(self, ir_sensor_values:list[float]):
        with self._lock:
            self._ir_sensor_values.append(ir_sensor_values)
            for i in range(len(ir_sensor_values)):
                ominv = self._ir_sensor_min_values[i]
                omaxv = self._ir_sensor_max_values[i]
                self._ir_sensor_min_values[i] = ominv if ominv > ir_sensor_values[i] else ir_sensor_values[i]
                self._ir_sensor_max_values[i] = omaxv if omaxv < ir_sensor_values[i] else ir_sensor_values[i]

    @property
    def ir_sensor_min_values(self) -> list[float]:
        with self._lock:
            return self._ir_sensor_min_values

    @property
    def ir_sensor_max_values(self) -> list[float]:
        with self._lock:
            return self._ir_sensor_max_values


    def get_logging_payload(self, log_level:int=logging.INFO) -> dict:
        payload = super().get_logging_payload()
        payload["ir_sensor_values"] = self.ir_sensor_value_history()
        return payload

    @property
    def p_wert(self) -> float:
        return random.uniform(5.0, 100.0)

    @property
    def i_wert(self) -> float:
        return random.uniform(5.0, 100.0)

    @property
    def d_wert(self) -> float:
        return random.uniform(5.0, 100.0)
    

class MockSensorCar(SensorCar):

    def __init__(self, config: ConfigReader | None = None, feste_werte:Optional[list[list[float]]]=None):
        super().__init__(config)
        self.__log = logging.getLogger(MockSensorCar.__name__)

        self._sensor_werte = feste_werte if (feste_werte is not None) else [
            [0.,1.,1.,1.,1.],
            [1.,0.,1.,1.,1.],
            [1.,1.,0.,1.,1.],
            [1.,1.,1.,0.,1.],
            [1.,1.,1.,1.,0.]
        ]
        self._sensor_count = 0
    
    @SensorCar.speed.setter
    def speed(self, speed:int): 
        self.__log.debug(f"Geschwindigkeit gesetzt: {speed}")
        self._speed = speed

    @SensorCar.ir_sensor_values.getter
    def ir_sensor_values(self) -> list[float]:
        with self._lock:
            counter = self._sensor_count % len(self._sensor_werte)
            value = self._sensor_werte[counter]
            self._sensor_count +=1
            self.__log.debug(f"Return sensor value #{counter}: {value}")
            time.sleep(5)

            return value
    
