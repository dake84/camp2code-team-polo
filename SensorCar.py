from basisklassen import Infrared
from BaseCar import BaseCar
import numpy as np
import time
import json
import threading

class SensorCar(BaseCar):

    KP = 500.0
    KD = 50.0

    def __init__(self, references: list = None):
        self._ir = Infrared()

        if (references == None):
            self._ir.cali_references()
        else:
            self._ir.set_references(references)
        self._previous_error = 0
        super().__init__()

    def lenkwinkel_berechnen(self) -> float:
        
        messwerte = self._ir.get_average(10)
        gewichte = np.array([-2,-1,0,1,2])
        
        error = sum((messwerte*gewichte))/sum(messwerte)
        #print(f"Messwerte: {messwerte}\nGewichte {gewichte}\nMultiplikator {messwerte*gewichte}\nError {error}")

        u = (self.KP * error) + (self.KD * (error - self._previous_error))
        self._previous_error = error
        lenkwinkel_grad = max(45, min(135, u))
        return lenkwinkel_grad

stop_event = threading.Event()

def auto_fahren(sc : SensorCar):
    print("Auto fährt...")
    
    while not stop_event.is_set():
        try:
            with open("config.json", "r") as f:
                data = json.load(f)
                sc.KP = data["korrektur_proportional"]
                sc.KD = data["korrektur_differential"]
        except:
            print("Kann config.json nicht öffnen")        
        
        lw = sc.lenkwinkel_berechnen()
        #print(f"Lenkwinkel: {lw}")
        sc.drive(50, lw)
    
    sc.stop()


if __name__ == '__main__':

        sc = SensorCar()
        thread = threading.Thread(target=auto_fahren, args=[sc])
        thread.start()

        input("Auto fährt, zum Beenden <ENTER> drücken")
        
        stop_event.set()

        thread.join()
        print("Programm vollständig beendet")
