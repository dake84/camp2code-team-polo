"""Fahrzeug mit Kamera: zentraler, thread-sicherer Datenspeicher.

``CameraCar`` haelt den zuletzt veroeffentlichten Kameraframe, die letzte
Fahrentscheidung und die begrenzte Historie (``FrameRingBuffer``).
Es fuehrt selbst keine Bildverarbeitung durch.
"""
import logging
from typing import Optional

import numpy as np

from ConfigReader import ConfigReader
from models import CameraData, CameraDataProcessed, DrivingState
from SensorCar import SensorCar
from telemetry import FrameRingBuffer, FrequencyCounter


class CameraCar(SensorCar):
    """Fahrzeug mit Kamera-Datenschnittstelle.

    Args:
        config: Konfiguration des Namensraums ``car`` (optional).
    """

    def __init__(self, config: Optional[ConfigReader] = None):
        super().__init__(config=config)
        self._camera_data = CameraData(process_image=np.empty(0), raw_image=np.empty(0))
        # VERALTET: wird nicht mehr beschrieben, nur fuer Legacy-Importe erhalten.
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
    def save_data(self) -> bool:
        with self._lock:
            return self._cfg.get_bool("save_data", False)

    @property
    def camera_data(self) -> CameraData:
        with self._lock:
            return self._camera_data

    @camera_data.setter
    def camera_data(self, camera_data: CameraData):
        with self._lock:
            self._camera_data = camera_data
            self.frame_history.add_camera(camera_data)
            self.thread_metrics["camera"].tick()

    @property
    def camera_data_processed(self) -> CameraDataProcessed:
        """VERALTET: liefert nur noch den Platzhalter aus der Initialisierung."""
        with self._lock:
            return self._camera_data_processed

    @camera_data_processed.setter
    def camera_data_processed(self, camera_data_processed: CameraDataProcessed):
        with self._lock:
            self._camera_data_processed = camera_data_processed

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
            # Zaehlt Entscheidungen (eine je ausgewertetem Kameraframe).
            self.thread_metrics["controller"].tick()
