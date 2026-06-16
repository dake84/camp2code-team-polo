from basisklassen import Infrared
from BaseCar import BaseCar
import time

class SensorCar(BaseCar):

    def __init__(self, references: list = [40, 300, 300, 300, 300]):
        self._ir = Infrared(references)
        super().__init__()

if __name__ == '__main__':

    sc = SensorCar()
    #sc.drive_around()

    print(f"RAW: {sc._ir._read_raw()}")
    print(f"ANALOG: {sc._ir.read_analog()}")
    print(f"DIGITAL: {sc._ir.read_digital()}")
