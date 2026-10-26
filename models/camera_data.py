"""Gemeinsame Kameradaten ohne Abhaengigkeit von der Fahrzeugklasse."""
from dataclasses import dataclass
import numpy as np

@dataclass
class CameraData:
    # Vorhandene Felder bleiben fuer die alten Verbraucher erhalten.
    process_image: np.ndarray
    raw_image: np.ndarray
    timestamp: int = 0
    frame_id: int = -1
    # Der Zeitstempel fuer Laufzeitmessungen ist unabhaengig von der Systemuhr.
    monotonic_ns: int = 0

@dataclass
class CameraDataProcessed:
    # Uebergangsmodell: Dashboard und Recorder lesen diese Felder noch.
    process_image: np.ndarray
    process_image_lines: np.ndarray
    raw_image: np.ndarray
    line_data: np.ndarray
    lanes: list
    timestamp: int = 0
    steering_angle: int = 90
    steering_angle_car: int | None = None
    speed: int = 0
    frame_id: int = -1
