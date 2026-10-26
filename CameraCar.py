import logging
from typing import Optional
 
from BaseCar import BaseCar
from ConfigReader import ConfigReader
 
from models import CameraData, CameraDataProcessed, DrivingState
from telemetry import FrameRingBuffer, FrequencyCounter
 
import numpy as np
import os
 
import cv2
 
 
class CameraCar(BaseCar):
    """Lässt das Auto mittels des Ultraschallsensor (Abstandmessung) fahren
 
    Args:
        BaseCar (object): Basisklasse des Autos - Kann nur Lenkung und Geschwindigkeit einstellen
    """
    def __init__(self, config:Optional[ConfigReader]=None):
        """Initiert das Object 'CameraCar' und über super-init die Methoden aus 'BaseCar'
        """        
 
        # Super init - Damit ein Object initiiert wird die auch BaseCar kennt (Methoden der BaseCar)
        super().__init__(config=config)
        #self._distance = 300
        self._camera_data = CameraData(process_image=np.empty(0), raw_image=np.empty(0))
        self._camera_data_processed = CameraDataProcessed(lanes=None, process_image=np.empty(0), raw_image=np.empty(0), process_image_lines=np.empty(0), line_data=np.empty(0))
        # Der Ringbuffer enthaelt eine begrenzte Historie, keine neue Bildkopie.
        self.frame_history = FrameRingBuffer(maxlen=60)
        self.thread_metrics = {
            "camera": FrequencyCounter(),
            "controller": FrequencyCounter(),
        }
        self._driving_state: DrivingState | None = None
        self.__log = logging.getLogger(CameraCar.__name__)
 
    @property
    def save_data(self) -> bool: # Lenkwinkel, Speed für AI imt Zeitstempel
        with self._lock:
            return self._cfg.get_bool("save_data", False)
 
    @property
    def camera_data(self) -> CameraData :
        with self._lock:
            return self._camera_data
 
    @camera_data.setter
    def camera_data(self, camera_data:CameraData):
        with self._lock:
            self._camera_data = camera_data
            self.frame_history.add_camera(camera_data)
            self.thread_metrics["camera"].tick()
        #print(self._camera_data)
 
    @property
    def camera_data_processed(self) -> CameraDataProcessed :
        with self._lock:
            return self._camera_data_processed
 
    @camera_data_processed.setter
    def camera_data_processed(self, camera_data_processed:CameraDataProcessed):
        with self._lock:
            self._camera_data_processed = camera_data_processed
        #print(self._camera_data_processed)

    @property
    def driving_state(self) -> DrivingState | None:
        """Letzte Fahrentscheidung, unabhaengig vom Dashboard-Format."""
        with self._lock:
            return self._driving_state

    @driving_state.setter
    def driving_state(self, state: DrivingState):
        with self._lock:
            self._driving_state = state
            self.frame_history.add_driving(state)
            self.thread_metrics["controller"].tick()
