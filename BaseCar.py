from basisklassen import BackWheels, FrontWheels
import time
import json

 
class BaseCar():

    FORWARD_MODE = 1
    BACKWARD_MODE = -1

    def __init__(self):
        self._steering_angle = 90
        self._speed = 0
        self._mode = self.FORWARD_MODE
        with open("config.json", "r") as f:
            data = json.load(f)
            turning_offset = data["turning_offset"]
            forward_A = data["forward_A"]
            forward_B = data["forward_B"]
            print("Daten in config.json:")
            print(" - Turning Offset: ", turning_offset)
            print(" - Forward A: ", forward_A)
            print(" - Forward B: ", forward_B)
            print("Keine geeignete Datei config.json gefunden!")
        self._fw = FrontWheels(turning_offset=turning_offset)
        self._fw.test()
        self._bw = BackWheels(forward_A=forward_A, forward_B=forward_B)
        self._bw.test()


    @property
    def steering_angle(self):
        return self._steering_angle
 
    @steering_angle.setter
    def steering_angle(self, valid_value):
        if valid_value < 45:
            self._steering_angle = 45       
        elif valid_value > 135:
            self._steering_angle = 135
        else:
            self._steering_angle = valid_value

        self._fw.turn(self._steering_angle)
    
    @property
    def speed(self):
        return self._mode * self._bw.speed

    @speed.setter
    def speed(self, valid_value):
        if (valid_value >= 0 & valid_value <= 100):
            self._bw.speed = valid_value
            self._mode = self.FORWARD_MODE
            self._bw.forward()
        elif (valid_value < 0 & valid_value >= -100):
            self._bw.speed = -1 * valid_value
            self._mode = self.BACKWARD_MODE
            self._bw.backward()
        else:
            raise ValueError("Geschwindigkeit: Wert zwischen -100 und 100 eingeben")


    def drive(self, speed = None, steer = None):
        if speed == None:
            speed = self.speed
        self.speed = speed
    
        if steer == None:
            steer = self.steering_angle
        self.steering_angle = steer

    def stop(self):
        self.drive(0,90)

if __name__ == '__main__':
    print('Hier mal die main')
 
    car1 = BaseCar()
    car1.drive(50)
    time.sleep(5)
    car1.drive(60, 45)
    time.sleep(1)
    car1.stop()

