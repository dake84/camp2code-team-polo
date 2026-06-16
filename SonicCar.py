from basisklassen import BackWheels, FrontWheels, Ultrasonic
from BaseCar import *
import time

class SonicCar(BaseCar):

    def __init__(self):
        self.us = Ultrasonic()

    def get_distance(self):
        ''' Error types:
            -1: Low signal and timeout reached
            -2: High signal and timeout reached
            -3: Negative distance
            -4: Error in time measurement '''
        return self.us.distance()
    
    def stop_car(self):
        self.get_distance()




if __name__ == '__main__':
    print('Hier mal die main')
 
    car1 = SonicCar()
    print(car1.get_distance())

    print('TEST')