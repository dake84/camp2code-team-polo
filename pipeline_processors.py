"""Austauschbare Bildverarbeitung, ohne Eingriff in die Fahrregelung."""
from dataclasses import dataclass, field
import cv2
import numpy as np
from lane_detector import LaneDetector

@dataclass(frozen=True)
class PipelineResult:
    processed_image: np.ndarray
    lanes: tuple
    stages: dict[str, np.ndarray] = field(default_factory=dict)

class LaneProcessor:
    """Legacy: bisherige Operationen und Reihenfolge, ohne Debug-Zwischenbilder."""
    def __init__(self):
        self.detector = LaneDetector()

    def process(self, raw: np.ndarray, params: tuple) -> PipelineResult:
        result = self._process(raw, params, capture=False)
        return result

    def _process(self, raw, params, capture):
        if raw is None or raw.size == 0:
            raise ValueError('Kamerabild ist leer')
        hue, saturation, value, crop, blur, morph = params[:6]
        canny = params[6] if len(params) > 6 else (50, 100)
        # Optionale Stufen sind reine Diagnose-/Experiment-Einstellungen.
        # Ohne Schalter bleibt die bisherige Verarbeitung unveraendert.
        enabled = params[7] if len(params) > 7 else {}
        def on(name):
            return bool(enabled.get(name, True))

        h = raw.shape[0]
        top = int(h * (1 - crop[1])) if on('roi') else 0
        bottom = int(h * (1 - crop[0])) if on('roi') else h
        if not (0 <= top < bottom <= h):
            raise ValueError('ROI ist ungueltig')
        roi = raw[top:bottom, :]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        # Ohne HSV-Filter verwenden wir ein Graustufenbild als Eingabe.
        mask = (cv2.inRange(hsv, np.array([hue[0], saturation[0], value[0]]),
                            np.array([hue[1], saturation[1], value[1]]))
                if on('mask') else cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY))
        blurred = (cv2.blur(mask, (max(1, int(blur)), max(1, int(blur))))
                   if on('blur') else mask)
        # Beim Weichzeichnen entstehen schwache Grauwerte am Maskenrand.
        # > 0 würde jeden Randpixel wieder vollweiß machen und die Linie
        # künstlich verbreitern. Die Hälfte der Maskenintensität trennt
        # Vordergrund und Hintergrund bei einer binären Eingangsmaske.
        # Ohne Blur bleibt die bisherige Binärisierung unverändert.
        threshold = 127 if on('blur') and on('mask') else 0
        binary = (blurred > threshold).astype(np.uint8) * 255
        if on('morph'):
            kernel = np.ones((max(1, int(morph)), max(1, int(morph))), dtype=np.uint8)
            closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        else:
            closed = binary
        edges = (cv2.Canny(closed, int(canny[0]), int(canny[1]))
                 if on('canny') else closed)
        lanes = self.detector.detect(edges) if on('lanes') else ()
        stages = {}
        if capture:
            # Kopien sind nur fuer die Diagnose vorgesehen, nicht fuer den Fahrregler.
            stages = {'roi': roi.copy(), 'hsv': cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR),
                      'mask': mask.copy(), 'blur': blurred.copy(),
                      'morph': closed.copy(), 'edges': edges.copy()}
        return PipelineResult(edges, lanes, stages)

class CopilotLaneProcessor(LaneProcessor):
    """Drop-in: gleiche Erkennung, zusaetzlich sichtbare Pipeline-Stufen."""
    def process(self, raw: np.ndarray, params: tuple) -> PipelineResult:
        return self._process(raw, params, capture=True)
