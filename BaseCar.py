from basisklassen import BackWheels, FrontWheels
 
class BaseCar():

    FORWARD_MODE = 1
    BACKWARD_MODE = -1


    def __init__(self):
        self._steering_angle = 90
        self._speed = 0
        self._bw = BackWheels()
        self._fw = FrontWheels()
        self._mode = self.FORWARD_MODE

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
    
    @property
    def speed(self):
        return self._mode*self._bw.speed
 
    def speed(self, valid_value):
        if (valid_value >= 0 & valid_value <= 100):
            self._bw.speed = valid_value
            self._mode = self.FORWARD_MODE
            self._bw.forward()
        elif (valid_value < 0 & valid_value >= -100):
            self._bw.backward()
            self._bw.speed = -1*valid_value
            self._mode = self.BACKWARD_MODE
        else:
            raise ValueError("Geschwindigkeit: Wert zwischen -100 und 100 eingeben")

if __name__ == '__main__':
    print('Hier mal die main')
 
    car1 = BaseCar()
    car1.steering_angle = 200
    print(car1.steering_angle)
    car1.speed = 50