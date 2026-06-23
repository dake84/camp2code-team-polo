
from abc import ABC, abstractmethod
import logging
import threading
import time
  
class Loggable(ABC):

    @abstractmethod
    def get_logging_payload(self, log_level:int=logging.INFO) -> dict:
        pass
        


class CarLogger():

    def __init__(self, log_object:Loggable, logfile="car_log.json", log_name="CarLogger", log_level=logging.INFO):
        self._lastTime = 0
        self._car = log_object

        # 1. Logger erstellen
        self._logger = logging.getLogger(log_name)
        self._logger.setLevel(log_level)

        # 2. Datei-Handler hinzufügen (Schreibt in die JSON-Datei)
        file_handler = logging.FileHandler(logfile)
        file_handler.setLevel(log_level)
        
        # Einfaches Format für die Datei (Zeit - Nachricht)
        formatter = logging.Formatter('%(asctime)s - %(message)s')
        #formatter = jsonlogger.JsonFormatter(
        #    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        #    rename_fields={"asctime": "timestamp", "levelname": "level"}
        #)
        file_handler.setFormatter(formatter)
        
        # Handler an den Logger hängen
        self._logger.addHandler(file_handler)

        self._log_level = log_level

    @property
    def logger(self):
        return self._logger

    def debug(self, msg):
        return self.logger.debug(msg)
    
    def error(self, msg):
        return self.logger.error(msg)
    
    def run(self, stop_event:threading.Event):
        while not stop_event.is_set():
            self.log_car_state()
            time.sleep(0.1)
       
    def log_car_state(self):
        self._logger.log(self._log_level, self._car.get_logging_payload())