from basisklassen import Infrared
from BaseCar import BaseCar
import numpy as np
import time

class SensorCar(BaseCar):

    def __init__(self, references: list = None):
        self._ir = Infrared()

        if (references == None):
            self._ir.cali_references()
        else:
            self._ir.set_references(references)
        
        super().__init__()

    def simulate_steering(self, ir_buffer):

        return



if __name__ == '__main__':

    buffer= [0,0,1,0,0]

    #while True:
        #Sensorwerte auslesen --> bspw [0,0,1,0,0]

    messwert = np.random.randint(0,2,(10, 5))
    print(messwert)

        #Sensorwerte interpretieren --> bspw. lenkwinkel 90° (geradeaus)



    #sc = SensorCar([10, 40, 40, 40, 40])
    sc = SensorCar()
    #sc.drive_around()

    print(f"RAW: {sc._ir._read_raw()}")
    print(f"ANALOG: {sc._ir.read_analog()}")
    print(f"DIGITAL: {sc._ir.read_digital()}")
    print(f"AVERAGE: {sc._ir.get_average(10)}")

    while True:
        #print(sc._ir.get_average(100))
        print(f"DIGITAL: {sc._ir.read_digital()}")
        time.sleep(0.1)