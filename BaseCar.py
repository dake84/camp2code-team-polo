
from threading import Lock, RLock
from typing import Any, Optional

from CarLogger import Loggable
from ConfigReader import ConfigReader
from basisklassen import BackWheels, FrontWheels
import time

class BaseCar(Loggable):

    FORWARD_MODE = 1
    BACKWARD_MODE = -1

    _json_config = {}

    def __init__(self, config:Optional[ConfigReader]=None):
        self._steering_angle = 90
        self._speed = 0
        self._mode = self.FORWARD_MODE
        self._config = config if config is not None else ConfigReader("car")
        self._fw = FrontWheels(turning_offset=self.turning_offset)
        self._bw = BackWheels(forward_A=self.forward_a, forward_B=self.forward_b)


        self._lock = RLock()

    def get_logging_payload(self) -> dict:
        with self._lock:
            return {
                "timestamp": time.time(),
                "steering_angle": self.steering_angle,
                "speed": self.speed,
                "direction": self.direction
            }


    def get_car_properties(self) -> dict[str, Any]:
        return {
            "steering_angle": self._steering_angle,
            "speed": self._speed
        }


    # self._config
    def _save_config(self) -> bool:
        return self._config._save_config()

    # Legacy, use self._config.set_config() instead
    def set_config(self, attribut: str, values = any, save_config=False) -> bool:
        return self._config.set_config(attribut, values, save_config)

    # Legacy, use self._config.get_config() instead
    def get_config(self, file: str = "config.json", force_update = False) -> dict:
        return self._config.get_config(force_update)
    
    # Legacy, use self._config._update_config() instead
    def _update_config(self):
        self._config._load_config_file()

    # Value from cfg_file
    @property
    def turning_offset(self) -> int:
        return self.get_config().get("turning_offset", 0)

    # Value from cfg_file
    @property
    def forward_a(self) -> int:
        return self.get_config().get("forward_A", 0)
    
    # Value from cfg_file
    @property
    def forward_b(self) -> int:
        return self.get_config().get("forward_B", 0)

    def test_wheels(self):
        self._fw.test()
        self._bw.test()

    # Live value (with lock)
    @property
    def steering_angle(self):
        with self._lock:
            return self._steering_angle
 
    # Live value (with lock)
    @steering_angle.setter
    def steering_angle(self, angle:int):
        angle = min(135, max(45, angle))
        self._fw.turn(angle)
        with self._lock:
            self._steering_angle
    
    # Live value (with lock)
    @property
    def speed(self):
        with self._lock:
            return self._mode * self._speed

    # Live value (with lock)
    @speed.setter
    def speed(self, speed):
        speed = max(-100, min(100, speed))
        self._mode = self.FORWARD_MODE if speed >= 0 else self.BACKWARD_MODE
        if (speed >= 0):
            self._bw.speed = speed
            self._mode = self.FORWARD_MODE
            self._bw.forward()
        else:
            self._bw.speed = -1 * speed
            self._mode = self.BACKWARD_MODE
            self._bw.backward()
        with self._lock:
            self._speed = speed

    # Direction (equals mode)
    # Live value (with lock)
    @property
    def direction(self):
        with self._lock:
            return self._mode

    def drive(self, speed:Optional[int]=None, steering_angle:Optional[int]=None):
        self.speed = speed if speed is not None else self._speed
        self.steering_angle = steering_angle if steering_angle is not None else self._steering_angle

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

