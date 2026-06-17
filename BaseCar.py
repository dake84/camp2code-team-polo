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
        turning_offset = 0
        forward_A = 0
        forward_B = 0
        try:
            with open("config.json", "r") as f:
                data = json.load(f)
                turning_offset = data["turning_offset"]
                forward_A = data["forward_A"]
                forward_B = data["forward_B"]
                print("Daten in config.json:")
                print(" - Turning Offset: ", turning_offset)
                print(" - Forward A: ", forward_A)
                print(" - Forward B: ", forward_B)
        except:
            print("Keine geeignete Datei config.json gefunden!")
        
        self._fw = FrontWheels(turning_offset=turning_offset)
        self._bw = BackWheels(forward_A=forward_A, forward_B=forward_B)

    def test_wheels(self):
        self._fw.test()
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
        if (valid_value >= 0 and valid_value <= 100):
            self._bw.speed = valid_value
            self._mode = self.FORWARD_MODE
            self._bw.forward()
        elif (valid_value < 0 and valid_value >= -100):
            self._bw.speed = -1 * valid_value
            self._mode = self.BACKWARD_MODE
            self._bw.backward()
        else:
            raise ValueError("Geschwindigkeit: Wert zwischen -100 und 100 eingeben")

# Direction
    @property
    def direction(self):
        if self.speed > 0:
            return 1
        elif self.speed < 0:
            return -1
        else:
            return 0

    def drive(self, speed = None, steer = None):
        if speed == None:
            speed = self.speed
        self.speed = speed
    
        if steer == None:
            steer = self.steering_angle
        self.steering_angle = steer

    def stop(self):
        self.drive(0,90)

### MW: Test Fahrmodi ###

def test_fahrmodus_1(car):
    print('Starte Fahrmodus 1')
    car.stop()
    time.sleep(1)

    car.drive(30)
    print(f"3 Sekunden vorwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(3)

    car.stop()
    print(f"1 Sekunde Stopp. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(1)
    
    car.drive(-30)
    print(f"3 Sekunden rückwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(3)

    car.stop()
    print(f"Fahrmodus 1 beendet. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")

def test_fahrmodus_2_rechts(car):
    print('Starte Fahrmodus 2 (Rechts/Uhrzeigersinn)')
    car.stop()
    time.sleep(1)

    car.drive(30)
    print(f"1 Sekunde vorwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(1)

    car.drive(30, 135)
    print(f"8 Sekunden rechts vorwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(8)

    car.stop()
    print(f"Kurzer Zwischenstopp. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(2)

    car.drive(-30, 135)
    print(f"8 Sekunden rechts rückwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(8)

    car.drive(-30, 90)
    print(f"1 Sekunde rückwärts zum Startpunkt. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(1)

    car.stop()
    print(f"Fahrmodus 2 rechts beendet.")

def test_fahrmodus_2_links(car):
    print('Starte Fahrmodus 2 (Links/Gegen Uhrzeigersinn)')
    car.stop()
    time.sleep(1)

    car.drive(30)
    print(f"1 Sekunde vorwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(1)

    car.drive(30, 45)
    print(f"8 Sekunden links vorwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(8)

    car.stop()
    print(f"Kurzer Zwischenstopp. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(2)

    car.drive(-30, 45)
    print(f"8 Sekunden links rückwärts. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(8)

    car.drive(-30, 90)
    print(f"1 Sekunde rückwärts zum Startpunkt. Geschwindigkeit: {car.speed}, Lenkwinkel: {car.steering_angle}")
    time.sleep(1)

    car.stop()
    print(f"Fahrmodus 2 links beendet.")

if __name__ == '__main__':
    print('Testfahrt beginnt')
 
    car1 = BaseCar()
    test_fahrmodus_1(car1)
    #test_fahrmodus_2_rechts(car1)
    #test_fahrmodus_2_links(car1)

