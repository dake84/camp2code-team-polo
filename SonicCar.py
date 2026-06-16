from basisklassen import BackWheels, FrontWheels, Ultrasonic
from BaseCar import *
import time

class SonicCar(BaseCar):

    def __init__(self):
        self.us = Ultrasonic()
        self.car = BaseCar()

    def get_distance(self):
        ''' Error types:
            -1: Low signal and timeout reached
            -2: High signal and timeout reached
            -3: Negative distance
            -4: Error in time measurement '''
        return self.us.distance()
    
    def stop_car(self, max_distance = 5):
        if max_distance < self.get_distance():
            car.stop()






if __name__ == '__main__':
    print('Hier mal die main')
 
    car1 = SonicCar()
    car1.drive(speed = 30, steer = 55)
    time.sleep(1)
    car1.stop_car()#
    time.sleep(1)
    car1.drive(speed = 30, steer = 55)
    time.sleep(1)
    ar1.drive(speed = 0, steer = 90)