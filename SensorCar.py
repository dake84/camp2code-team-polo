import logging
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
        self._ir_sensor_values = []
        self._ir_sensor_min_values = [0.,0.,0.,0.,0.]
        self._ir_sensor_max_values = [1.,1.,1.,1.,1.]
        

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
            self._log.debug(f"Rufe Sensorwerte ab, Liste: {len(self._ir_sensor_values)}")
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