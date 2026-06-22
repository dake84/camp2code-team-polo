from typing import Optional
from BaseCar import BaseCar

from ConfigReader import ConfigReader

class SensorCar(BaseCar):
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
        

    def ir_sensor_value_history(self, length:int=0, clear_history:bool=True):
        with self._lock:
            hist = self._ir_sensor_values[-length:] if (length > 0) else self._ir_sensor_values
            if (clear_history):
                self._ir_sensor_values = hist[-1:]
            return hist

    @property
    def ir_sensor_values(self) -> list[float]:
        with self._lock:
            return self._ir_sensor_values[-1:]
    
    @ir_sensor_values.setter
    def ir_sensor_values(self, ir_sensor_values:list[float]):
        with self._lock:
            self._ir_sensor_values.append(ir_sensor_values)