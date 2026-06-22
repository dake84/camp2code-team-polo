
import logging
import threading
import time

import BaseCar


class CarLogger():

    def __init__(self, car:BaseCar.BaseCar, logfile="car_log.json", log_name="CarLogger", log_level=logging.INFO):
        self._lastTime = 0

        self._logger = logging.getLogger(log_name)
        self._logger.setLevel(log_level)

        self._car = car

    @property
    def logger(self):
        return self._logger

    def debug(self, msg:str):
        return self.logger.debug(msg)
    
    def run(self, stop_event:threading.Event):
        while not stop_event.is_set():
            self.log_car_state()
            time.sleep(0.1)
       
    def log_car_state(self):
        self._logger.info(self._car.get_logging_payload())