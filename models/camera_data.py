"""Gemeinsame Kameradaten ohne Abhaengigkeit von der Fahrzeugklasse.

Zeitstempel (verbindlich ab Version 6.0):
    ``timestamp``      Systemuhr (``time.time_ns``) direkt nach der Bildaufnahme.
    ``monotonic_ns``   Monotone Uhr (``time.monotonic_ns``) direkt nach der Bildaufnahme.
                       Grundlage fuer Datenalter, Latenzen und Timeouts.
    ``processing_ns``  Dauer der Bildverarbeitung im CameraSensor.
    Veroeffentlichungszeitpunkt = ``monotonic_ns + processing_ns`` (naeherungsweise).

Linien (``lanes``):
    Tupel von ``(m, b)`` mit ``y = m * x + b`` im ROI-Koordinatensystem
    (Pixel, Ursprung oben links des ROI). Vollbild: ``y_voll = y + roi_top_px``.

Veraenderbarkeit:
    Veroeffentlichte NumPy-Arrays (Rohbild, Zwischenbilder) gelten als
    unveraenderlich. Verbraucher zeichnen ausschliesslich auf Kopien.
"""
from dataclasses import dataclass, field

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
    crop: tuple[float, float] = (0.1, 0.9)
    # Bereits erkannte Linien, einmal pro Frame ermittelt.
    lanes: tuple[tuple[float, float], ...] = ()
    # Optionale Diagnosebilder, nur im CopilotLaneProcessor vorhanden.
    stages: dict[str, np.ndarray] = field(default_factory=dict)
    processing_parameters: tuple | None = None
    processor_name: str | None = None
    # Dauer der Bildverarbeitung in Nanosekunden (0 = unbekannt).
    processing_ns: int = 0
    # PAKET-H1: Fahrzeugzustand im Aufnahmemoment (None = unbekannt, z. B. alte Aufnahme).
    # vehicle_mode: idle | auto | manual | recovery; Winkel in Grad; Speed = vorzeichenbehafteter Steuerwert.
    vehicle_mode: str | None = None
    vehicle_angle: float | None = None
    vehicle_speed: float | None = None

    def roi_rows(self, height: int | None = None) -> tuple[int, int]:
        """Liefert (oben, unten) des ROI in Vollbild-Pixeln wie im LaneProcessor."""
        if height is None:
            image = self.raw_image
            height = image.shape[0] if image is not None and getattr(image, 'size', 0) else 0
        low, high = self.crop
        top = int(height * (1 - high))
        bottom = int(height * (1 - low))
        top = max(0, min(height, top))
        return top, max(top, min(height, bottom))


@dataclass
class CameraDataProcessed:
    """VERALTET (DEPRECATED) seit Version 6.0.

    Wird von keinem Modul mehr erzeugt oder gelesen. Die Klasse bleibt nur,
    weil ``legacy/dashboards/CameraCar_DashLive.py`` sie noch importiert.
    Entfernen, sobald die Legacy-Dashboards geloescht sind (siehe CLEANUP_REPORT.md).
    """
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
