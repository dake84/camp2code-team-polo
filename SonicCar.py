from basisklassen import BackWheels, FrontWheels, Ultrasonic
from BaseCar import *
import time
import random

import matplotlib.pyplot as plt

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
        if max_distance > self.get_distance():
            self.stop()
            return False
        else:
            return True

    def drive_straigt_ahead(self, speed_max = 80, max_distance = 5):
        # Geschwindigkeit abhängig von Abstand zu Hinderniss
        actual_distance = self.get_distance()

        stop_car_bool = True
        while stop_car_bool == True:
            actual_distance = self.get_distance()
            if actual_distance < 40:
                actual_speed = actual_distance + 15
                stop_car_bool = self.stop_car(max_distance = max_distance)
                if stop_car_bool == True:
                    self.drive(speed = actual_speed, steer = 90)
            else:
                self.drive(speed = speed_max, steer = 90)

    def overcome_obstacle(self, data_time, data_speed, data_steer, data_distance):
        optionen_lenkung = [45, 135]
        ausweich_lenkung = random.choice(optionen_lenkung)
        richtung_text = "links" if ausweich_lenkung == 45 else "rechts"
        zufalls_zeit = random.randint(1, 4)
        
        self.stop()

        data_time, data_speed, data_steer, data_distance = self.get_data(data_time = data_time, 
                                                                                data_speed = data_speed,
                                                                                data_steer = data_steer,
                                                                                data_distance = data_distance)

        print(f"Hindernis erkannt. Geschwindigkeit: {self.speed}, Lenkwinkel: {self.steering_angle}")
        time.sleep(1)

        data_time, data_speed, data_steer, data_distance = self.get_data(data_time = data_time, 
                                                                                data_speed = data_speed,
                                                                                data_steer = data_steer,
                                                                                data_distance = data_distance)
        
        self.drive(speed=-30, steer=ausweich_lenkung)

        data_time, data_speed, data_steer, data_distance = self.get_data(data_time = data_time, 
                                                                                data_speed = data_speed,
                                                                                data_steer = data_steer,
                                                                                data_distance = data_distance)

        print(f"Ausweichfahrt {richtung_text}. Geschwindigkeit: {self.speed}, Lenkwinkel: {self.steering_angle}")
        time.sleep(zufalls_zeit)

        data_time, data_speed, data_steer, data_distance = self.get_data(data_time = data_time, 
                                                                                data_speed = data_speed,
                                                                                data_steer = data_steer,
                                                                                data_distance = data_distance)
        
        self.stop()

        data_time, data_speed, data_steer, data_distance = self.get_data(data_time = data_time, 
                                                                                data_speed = data_speed,
                                                                                data_steer = data_steer,
                                                                                data_distance = data_distance)

        return data_time, data_speed, data_steer, data_distance                                                               

    def drive_explore(self, actual_speed, steering_angle):
        
        actual_speed = int(actual_speed *random.uniform(0.95, 1.05))
        steering_angle = int(steering_angle *random.uniform(0.95, 1.05))

        self.drive(speed = actual_speed, steer = steering_angle)

        return actual_speed, steering_angle


    def get_data(self, data_time = None, 
                        data_speed = None,
                        data_steer = None,
                        data_distance = None):

        if data_time == None:
            data_time = []
        if data_speed == None:
            data_speed = []
        if data_steer ==None:
            data_steer = []
        if data_distance == None:
            data_distance = []

        data_time.append(time.time())
        data_speed.append(self.speed)
        data_steer.append(self.steering_angle)
        data_distance.append(self.get_distance())

        return data_time, data_speed, data_steer, data_distance

    def room_explorer(self, explorer_time = 30, max_distance = 5):
        bool_time = True
        t_start = time.time()

        actual_speed_drive_explore = 80
        steering_angle_drive_explore = 90

        data_time, data_speed, data_steer, data_distance = self.get_data(data_time = None, 
                                                                                data_speed = None,
                                                                                data_steer = None,
                                                                                data_distance = None)

        while bool_time == True:

            if time.time() - t_start > explorer_time:
                bool_time = False

            # Geschwindigkeit abhängig von Abstand zu Hinderniss
            actual_distance = self.get_distance()

            data_time, data_speed, data_steer, data_distance = self.get_data(data_time = data_time, 
                                                                                data_speed = data_speed,
                                                                                data_steer = data_steer,
                                                                                data_distance = data_distance)

            if actual_distance < 40:
                actual_speed = actual_distance + 15
                overcome_obstacle_bool = self.stop_car(max_distance = max_distance)
                if overcome_obstacle_bool == False:
                    data_time, data_speed, data_steer, data_distance = self.overcome_obstacle(data_time, data_speed, data_steer, data_distance)
                else:
                    self.drive(speed = actual_speed, steer = 90)

                    data_time, data_speed, data_steer, data_distance = self.get_data(data_time = data_time, 
                                                                                data_speed = data_speed,
                                                                                data_steer = data_steer,
                                                                                data_distance = data_distance)
            else:
                actual_speed_drive_explore, steering_angle_drive_explore = self.drive_explore(actual_speed_drive_explore, steering_angle_drive_explore)

        self.stop()

        plt.plot(data_time, data_speed)
        plt.show()





            

            



if __name__ == '__main__':
    print('Hier mal die main')
 

    print(time.time())
    car1 = SonicCar()


    car1.room_explorer(explorer_time = 10, max_distance = 5)