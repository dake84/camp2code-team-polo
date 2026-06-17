from basisklassen import Infrared
from BaseCar import BaseCar
import numpy as np
import time
import keyboard

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
        print(f"Messwerte: {messwerte}\nGewichte {gewichte}\nMultiplikator {messwerte*gewichte}\nError {error}")

        u = (self.KP * error) + (self.KD * (error - self._previous_error))
        self._previous_error = error
        lenkwinkel_grad = max(45, min(135, u))
        return lenkwinkel_grad





if __name__ == '__main__':

    buffer= [0,0,1,0,0]

    #while True:
        #Sensorwerte auslesen --> bspw [0,0,1,0,0]

    #messwert = np.random.randint(0,2,(10, 5))
    #print(messwert)

        #Sensorwerte interpretieren --> bspw. lenkwinkel 90° (geradeaus)



    #sc = SensorCar([10, 40, 40, 40, 40])
    sc = SensorCar()
    while True:
        print(sc.lenkwinkel_berechnen())
        if (keyboard.is_pressed('p')):
            sc.KP = float(input(f"Neuer Wert für proportionale Korrektur eingeben (alter Wert: Kp = {sc.KP})"))
        elif (keyboard.is_pressed('d')):
            sc.KD = float(input(f"Neuer Wert für differentiale Korrektur eingeben (alter Wert: Kd = {sc.KD})"))
        time.sleep(0.5)
    #sc.drive_around()

    #print(f"RAW: {sc._ir._read_raw()}")
    #print(f"ANALOG: {sc._ir.read_analog()}")
    #print(f"DIGITAL: {sc._ir.read_digital()}")
    #print(f"AVERAGE: {sc._ir.get_average(10)}")

    #while True:
    #    #print(sc._ir.get_average(100))
    #    print(f"DIGITAL: {sc._ir.read_digital()}")
    #    time.sleep(0.1)