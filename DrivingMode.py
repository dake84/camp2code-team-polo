from BaseCar import BaseCar
from SensorCar import SensorCar

class DrivingMode:
    
    def drive(self, car: BaseCar): raise NotImplementedError

class ModeSix(DrivingMode):

    def drive(self, car: SensorCar):
        car = car
        