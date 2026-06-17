from basisklassen import Infrared
from BaseCar import BaseCar
import numpy as np
import time
import json
import threading

class SensorCar(BaseCar):

    KP = 500.0
    KD = 50.0
    KV = 0.6

    def __init__(self, references: list = []):
        self._ir = Infrared()

        if (references == []):
            self._ir.cali_references()
        else:
            self._ir.set_references(references)
        self._previous_error = 0
        self.v_max = 50
        super().__init__()

    def lenkwinkel_berechnen(self) -> float:
        
        messwerte = self._ir.get_average(10)
        gewichte = np.array([-2,-1,0,1,2])
        
        error = sum((messwerte*gewichte))/sum(messwerte)
        #print(f"Messwerte: {messwerte}\nGewichte {gewichte}\nMultiplikator {messwerte*gewichte}\nError {error}")

        u = (self.KP * error) + (self.KD * (error - self._previous_error))
        self._previous_error = error
        self._lw = max(45, min(135, u))
        return self._lw

    def geschwindigkeit_berechnen(self, lenkwinkel : float):
        v = self.v_max - (self.KV * abs((lenkwinkel - 90)))
        # 115 - 25
        # v_max (70) - v_min (30)
        return int(v)

    @property
    def korrektur_proportional(self) -> float:
        return float(self.get_config()["korrektur_proportional"])

    @property
    def korrektur_differential(self) -> float:
        return float(self.get_config()["korrektur_differential"])


stop_event = threading.Event()

def auto_fahren(sc : SensorCar):
    print("Auto fährt...")
    
    while not stop_event.is_set():
        sc.KP = sc.korrektur_proportional
        sc.KD = sc.korrektur_differential    
        
        lw = sc.lenkwinkel_berechnen()
        v = sc.geschwindigkeit_berechnen(lw)
        sc.drive(v, lw)

    #    print(f"Lenkwinkel: {lw}, Geschwindigkeit: {v}")
    
    sc.stop()


if __name__ == '__main__':

        sc = SensorCar()
        thread = threading.Thread(target=auto_fahren, args=[sc])
        thread.start()

        input("Auto fährt, zum Beenden <ENTER> drücken")
        
        stop_event.set()

        thread.join()
        print("Programm vollständig beendet")
