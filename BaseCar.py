
from threading import RLock
from typing import Any, Optional
import logging

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
        self.__log = logging.getLogger(BaseCar.__name__)

        self._lock = RLock()

    def get_logging_payload(self, log_level:int=logging.INFO) -> dict:
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

    @property
    def v_min(self) -> int:
        return self._config.get_int("v_min", 20)

    @property
    def v_max(self) -> int:
        return self._config.get_int("v_max", 100)


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
            self.__log.debug(f"Set steering angle: {angle}")
            self._steering_angle
    
    # Live value (with lock)
    @property
    def speed(self):
        with self._lock:
            return self._mode * self._speed

    # Live value (with lock)
    @speed.setter
    def speed(self, speed:int): 
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
            self.__log.debug(f"Set steering angle: {speed}")           
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