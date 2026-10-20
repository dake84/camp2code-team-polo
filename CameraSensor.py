
import logging
import threading
import time
from typing import Optional
import cv2

import numpy as np

import ConfigReader
from CameraCar import CameraCar

from basisklassen_cam import Camera

class CameraSensor(Camera):

    def __init__(self, car:CameraCar, sensor_config:Optional[ConfigReader.ConfigReader]=None, logger:Optional[logging.Logger]=None) -> None:
        
        self._log = logger if logger is not None else logging.getLogger(self.__class__.__name__)
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("cam_sensor", logger=self._log)
        self._lock = threading.Lock()
        self._debug_mode = False
        self._car = car 
        print(self._cfg.get("colormode"))
        super().__init__ (colorspace=str(self._cfg.get_str("colormode","gray")),
                          height=self._cfg.get_int("height",480),
                          width=self._cfg.get_int("width",640))
        


    @property
    def scan_frequency(self) -> int:
        return self._cfg.get_int("scan_frequency", 25)
    
    
    
    @property
    def save_image(self) -> bool: # Mit Zeitstempel. -- In Dateiname: Zeitstempel; Lenkwinkel, Speed
        # Hier einstellen (Auflösung, Pixel etc.)
        return self._cfg.get_bool("save_image", False)
    
    def read_loop(self, stop_event:threading.Event):
        try:
            self._log.debug("Entering Camera-Sensor loop")
            while (not stop_event.is_set()):
                if (self._debug_mode):
                    self._cfg._load_config_file()
                #v = self.sensor_values
                self._log.debug(f"Read from camera-sensor")
                
                image = self.get_frame()
                # cfg -> save_image -> FUNKTION: Checke Ordner fig_raw ja, nein
                if self.save_image:
                    cv2.imwrite("camera_test.jpg", image)
                # Bild zuschneiden (cfg -> horizontal_y_cutline)

                # Bild bearbeitung -> Linienwerte
                # Linienwerte an Auto (CameraCar.py) übergeben

                time.sleep(1/self.scan_frequency)
        except Exception as e:
            raise e
        finally:
            self._log.debug("Exit Camera loop") 

    # def: Bild zuschneiden (Horizont weghauen)

    # def: Bearbeitetes Bild einlsesen, Bild bearbeiten, Linienwerte weitergeben

if __name__ == "__main__":

    print("This is the main.")
