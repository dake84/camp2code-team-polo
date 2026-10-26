"""Erkennt Fahrspurlinien aus bereits aufbereiteten Sensordaten.

Die Erkennung hat keinen Bezug zur Fahrzeugansteuerung oder zum Dashboard.
"""
from LenkwinkelKalkulator import detect_lanes_from_areas

class LaneDetector:
    def detect(self, processed_image):
        """Gibt unveraenderliche (m, b)-Paare im ROI-Koordinatensystem zurueck."""
        lanes, _ = detect_lanes_from_areas(
            processed_image, n_lanes=2, min_area=150, fit_frac=1.0
        )
        return tuple((float(m), float(b)) for m, b in lanes)
