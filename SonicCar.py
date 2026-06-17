from basisklassen import BackWheels, FrontWheels, Ultrasonic
from BaseCar import *
import time

class SonicCar(BaseCar):

    def __init__(self):
        self.us = Ultrasonic()
        super().__init__()

    def get_distance(self):
        ''' Error types:
            -1: Low signal and timeout reached
            -2: High signal and timeout reached
            -3: Negative distance
            -4: Error in time measurement '''

        my_distance = self.us.distance()
        set_max_distance = 40

        if my_distance == -1:
            my_distance = set_max_distance
        elif my_distance == -2:
            my_distance = set_max_distance
        elif my_distance == -3:
            my_distance = 0
        elif my_distance == -4:
            my_distance = set_max_distance
        else:
            my_distance = my_distance
        
        return my_distance
    
    def stop_car(self, max_distance = 5):
        print(self.get_distance())
        if max_distance > self.get_distance():
            self.stop()
            return False
        else:
            return True

    def drive_straigt_ahead(self, speed = 30, max_distance = 5):
        self.drive(speed = speed, steer = 90)

        bool_distance = True
        while bool_distance == True:
            bool_distance = self.stop_car(max_distance = max_distance)

    
    #def overcome_obstacle(self):

        
        

    
    
    
    def room_explorer(self, explorer_time = 30):
        bool_time = True
        t_start = time.time()

        while bool_time == True:
            if time.time() - t_start > explorer_time:
                bool_time = False

            actual_distance = self.get_distance()
            if actual_distance < 40:
                actual_speed = actual_distance + 15
            else:
                actual_speed = 100

            #print(f'Distance = {actual_distance}')
            #print(f'Speed = {actual_speed}')





            

            



if __name__ == '__main__':
    print('Hier mal die main')
 
    car1 = SonicCar()
    car1.drive_straigt_ahead(speed = 80, max_distance = 5)
    #car1.room_explorer(explorer_time = 10)