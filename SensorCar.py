import logging
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
        self._p_wert = 0
        self._i_wert = 0
        self._d_wert = 0

        self._logged_lost_line = False
        

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
        with self._lock:
            return self._p_wert

    @p_wert.setter
    def p_wert(self, p:float):
        self.__log.info(f"P-Wert: {p}")
        with self._lock:
            self._p_wert = p

    @property
    def i_wert(self) -> float:
        with self._lock:
            return self._i_wert

    @i_wert.setter
    def i_wert(self, i:float):
        self.__log.info(f"I-Wert: {i}")
        with self._lock:
            self._p_wert = i

    @property
    def d_wert(self) -> float:
        with self._lock:
            return self._d_wert

    @d_wert.setter
    def d_wert(self, d:float):
        self.__log.info(f"D-Wert: {d}")
        with self._lock:
            self._p_wert = d

    # Methode macht hier mehr Sinn (direkte Interpretation von Messdaten. Im Mode nur Reaktion auf Messdaten, keine Interpretation)
    def is_on_line(self) -> bool:
        minimum_line_contrast = self._cfg.get_float("minimum_line_contrast", 0.5)
        messwerte = self.ir_sensor_values
        
        min_sensor = min(messwerte)
        max_sensor = max(messwerte)

        if (max_sensor == 0):
            return True

        self.__log.debug(f"Prüfung is_on_line mit min_sensor: {min_sensor:.2f}, max_sensor: {max_sensor:.2f}, line_threshold: {minimum_line_contrast}, min/max: {min_sensor/max_sensor:.2f}")
        if (not (max_sensor-min_sensor > minimum_line_contrast)):
            if (not self._logged_lost_line):
                # Erzeugt eine Ausgabe wie: [0.12, 1.00,  0.05] (feste Abstände)
                formatiert = ", ".join(f"{v:4.2f}" for v in messwerte)                
                self.__log.info(f"Lost line with parameters: [{formatiert}] (max: {max_sensor:.3f}, min: {min_sensor:.3f}, delta: {max_sensor-min_sensor:.3f})")                
                self._logged_lost_line = True
            return False
        
        self._logged_lost_line = False
        return True
            

class MockSensorCar(SensorCar):

    def __init__(self, config: ConfigReader | None = None, feste_werte:Optional[list[list[float]]]=None, mockSensor=False, mockSpeed=False):
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
        self._mockSensor = mockSensor
        self._mockSpeed = mockSpeed
    
    @SensorCar.speed.setter
    def speed(self, speed:int): 
        if (self._mockSpeed):
            speed = max(-100, min(100, speed))
            self.__log.debug(f"Geschwindigkeit gesetzt: {speed}")
            if (self._speed != speed):
                self.__log.info(f"Changed speed: {self._speed} -> {speed}")
            self._speed = speed
        else:
            super(MockSensorCar, type(self)).speed.__set__(self, speed) # pyright: ignore[reportAttributeAccessIssue]

    @SensorCar.ir_sensor_values.getter
    def ir_sensor_values(self) -> list[float]:
        if (self._mockSensor):
            with self._lock:
                counter = self._sensor_count % len(self._sensor_werte)
                value = self._sensor_werte[counter]
                self._sensor_count +=1
                self.__log.debug(f"Return sensor value #{counter}: {value}")
                time.sleep(5)

                return value
        return super(MockSensorCar, type(self)).ir_sensor_values.__get__(self) # pyright: ignore[reportAttributeAccessIssue]
    
