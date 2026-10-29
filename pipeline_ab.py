"""A/B-Kalibrierung auf einem eingefrorenen Rohbild, ohne Hardwarezugriff."""
from dataclasses import dataclass
from threading import RLock
import numpy as np
from camera_geometry import downscale_image  # PAKET-H3
from pipeline_processors import CopilotLaneProcessor, LaneProcessor

@dataclass(frozen=True)
class Comparison:
    frame_id: int
    image_a: np.ndarray
    image_b: np.ndarray
    lanes_a: tuple
    lanes_b: tuple

class PipelineAB:
    """Hält höchstens ein Rohbild und eine Referenzkonfiguration im RAM."""
    def __init__(self):
        self._lock = RLock()
        self._raw = None
        self._frame_id = None
        self._reference = None
        self._processor = 'copilot'
        self._process_size = None  # PAKET-H3

    def freeze(self, camera, parameters, processor='copilot'):
        """Kopiert nur das Rohbild; weitere Zwischenbilder werden nicht behalten."""
        if camera is None or camera.raw_image is None or camera.raw_image.size == 0:
            raise ValueError('Kein gültiges Kamerabild verfügbar')
        with self._lock:
            self._raw = camera.raw_image.copy()
            self._frame_id = camera.frame_id
            self._reference = tuple(parameters)
            self._processor = processor
            size = getattr(camera, 'process_size', None)   # PAKET-H3: A/B rechnet auf der damaligen Groesse
            self._process_size = (int(size[0]), int(size[1])) if size else None
            return self._frame_id

    def reference(self):
        """PAKET-D: (Rohbild, Frame-ID, Referenzparameter, Prozessor) der Referenz A, nur lesen."""
        with self._lock:
            return self._raw, self._frame_id, self._reference, self._processor

    def process_size(self):
        """PAKET-H3: Verarbeitungsgroesse des eingefrorenen Frames ((Breite, Hoehe) oder None)."""
        with self._lock:
            return self._process_size

    def downscale(self, raw):
        """PAKET-H3: ``raw`` auf die Verarbeitungsgroesse des eingefrorenen Frames verkleinern (Software, INTER_AREA)."""
        with self._lock:
            return downscale_image(raw, self._process_size)

    def clear(self):
        with self._lock:
            self._raw = None
            self._reference = None
            self._frame_id = None
            self._process_size = None

    def compare(self, candidate, processor=None):
        """Berechnet A und B auf demselben Rohbild, ohne Fahrentscheidung."""
        with self._lock:
            if self._raw is None:
                raise ValueError('Zuerst einen Frame für A/B einfrieren')
            raw = downscale_image(self._raw.copy(), self._process_size)  # PAKET-H3
            reference = self._reference
            frame_id = self._frame_id
            name_a = self._processor
        def run(name, params):
            if name == 'copilot':
                instance = CopilotLaneProcessor()
            elif name == 'legacy':
                instance = LaneProcessor()
            else:  # PAKET-F: weitere Prozessoren aus dem zentralen Verzeichnis
                from processor_registry import create_processor
                instance = create_processor(name)
            return instance.process(raw.copy(), params)
        a = run(name_a, reference)
        b = run(processor or name_a, tuple(candidate))
        return Comparison(frame_id, a.processed_image, b.processed_image,
                          tuple(a.lanes), tuple(b.lanes))
