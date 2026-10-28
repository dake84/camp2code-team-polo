"""Austauschbare Bildverarbeitung, ohne Eingriff in die Fahrregelung."""

from dataclasses import dataclass, field

import cv2
import numpy as np

from lane_detector import LaneDetector

# Schluessel, die nicht der Prozessor, sondern das Dashboard liefert:
#   raw     = unveraendertes Kamerabild
#   overlay = Rohbild mit eingezeichneten Linien (dashboard_overlay)
STAGE_RAW = 'raw'
STAGE_OVERLAY = 'overlay'
DASHBOARD_STAGES = (STAGE_RAW, STAGE_OVERLAY)


@dataclass(frozen=True)
class PipelineControl:
    """Ein Regler im Pipeline Studio.

    ``key`` ist zugleich der Schluessel in der Studio-Konfiguration
    (``pipeline_studio.json``: crop, hue, sat, val, blur, morph, canny).
    ``pair=True`` ergibt einen Bereichsregler (zwei Werte), sonst einen einfachen Regler.
    ``help_key`` verweist auf einen Hilfetext (help_texts.HELP); ohne Angabe wird ``key`` versucht.
    """

    key: str
    label: str
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    pair: bool = False
    help_key: str | None = None


@dataclass(frozen=True)
class PipelineStage:
    """Eine sichtbare Stufe der Pipeline (eine Karte im Pipeline Studio).

    ``key`` ist der Schluessel im ``PipelineResult.stages`` (oder ein reservierter
    Dashboard-Schluessel, siehe ``DASHBOARD_STAGES``). ``switch`` ist der Schluessel
    in ``enabled`` (Schalter "Schritt aktiv"); ohne Angabe ist die Stufe nicht abschaltbar.
    """

    key: str
    title: str
    switch: str | None = None
    controls: tuple = ()


def validate_stages(stages):
    """Prueft eine Stufenbeschreibung (eindeutige Stufen-, Regler- und Schalterschluessel)."""
    stages = tuple(stages)
    for label, values in (('Stufe', [s.key for s in stages]),
                          ('Regler', [c.key for s in stages for c in s.controls]),
                          ('Schalter', [s.switch for s in stages if s.switch])):
        duplicates = sorted({v for v in values if values.count(v) > 1})
        if duplicates:
            raise ValueError(f'{label} mehrfach beschrieben: {duplicates}')
    return stages


@dataclass(frozen=True)
class PipelineResult:
    """Ergebnis einer Bildverarbeitungspipeline.

    processed_image:
        Bild, das an den LaneDetector übergeben wurde.

    lanes:
        Ergebnis des LaneDetectors.
        Linien liegen als (m, b) mit y = m*x + b
        im ROI-Koordinatensystem vor.

    stages:
        Diagnosebilder in der Reihenfolge, in der die Pipeline
        sie erzeugt hat. Python-Dicts behalten ihre Einfügereihenfolge.
    """

    processed_image: np.ndarray
    lanes: tuple
    stages: dict[str, np.ndarray] = field(default_factory=dict)


class LaneProcessor:
    """Legacy-Bildverarbeitung auf Basis einer HSV-Farbmaske.

    Diese Pipeline bleibt absichtlich weitgehend unverändert, damit sie
    weiterhin als Referenz für A/B-Vergleiche verwendet werden kann.

    Pipeline:
        ROI
        -> HSV
        -> Farbmaske
        -> Blur
        -> Binarisierung
        -> Morph Close
        -> Canny
        -> LaneDetector
    """

    name = "Legacy"

    # Regler wie bisher im Studio; die Beschreibung gehoert zur Klasse, damit das
    # Dashboard keine Stufen mehr fest kennt.
    CROP = PipelineControl('crop', 'ROI (unten / oben, Anteil)', 0, 1, 0.01, True)
    BLUR = PipelineControl('blur', 'Blur-Kernel', 1, 15)
    MORPH = PipelineControl('morph', 'Morph-Kernel', 1, 15)
    CANNY = PipelineControl('canny', 'Canny-Schwellen', 0, 255, 1, True)

    @classmethod
    def dashboard_stages(cls):
        """Stufen in Pipeline-Reihenfolge, so wie sie im Pipeline Studio erscheinen."""
        return validate_stages((
            PipelineStage(STAGE_RAW, 'Rohbild'),
            PipelineStage('roi', 'ROI', 'roi', (cls.CROP,)),
            PipelineStage('hsv', 'HSV (Falschfarben)', None, (
                PipelineControl('hue', 'Hue', 0, 179, 1, True),
                PipelineControl('sat', 'Saturation', 0, 255, 1, True),
                PipelineControl('val', 'Value', 0, 255, 1, True))),
            PipelineStage('mask', 'Farbmaske', 'mask'),
            PipelineStage('blur', 'Blur', 'blur', (cls.BLUR,)),
            PipelineStage('binary', 'Binarisierung'),
            PipelineStage('morph', 'Morphologie', 'morph', (cls.MORPH,)),
            PipelineStage('edges', 'Canny', 'canny', (cls.CANNY,)),
            PipelineStage(STAGE_OVERLAY, 'Linien', 'lanes'),
        ))

    def __init__(self):
        self.detector = LaneDetector()

    def process(self, raw: np.ndarray, params: tuple) -> PipelineResult:
        return self._process(raw, params, capture=False)

    def _process(
        self,
        raw: np.ndarray,
        params: tuple,
        capture: bool,
    ) -> PipelineResult:

        if raw is None or raw.size == 0:
            raise ValueError("Kamerabild ist leer")

        hue, saturation, value, crop, blur, morph = params[:6]

        canny = params[6] if len(params) > 6 else (50, 100)
        enabled = params[7] if len(params) > 7 else {}

        def on(name: str) -> bool:
            return bool(enabled.get(name, True))

        # ------------------------------------------------------------
        # ROI
        # ------------------------------------------------------------

        h = raw.shape[0]

        top = int(h * (1 - crop[1])) if on("roi") else 0
        bottom = int(h * (1 - crop[0])) if on("roi") else h

        if not (0 <= top < bottom <= h):
            raise ValueError("ROI ist ungueltig")

        roi = raw[top:bottom, :]

        # ------------------------------------------------------------
        # HSV
        # ------------------------------------------------------------

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # ------------------------------------------------------------
        # Farbmaske
        # ------------------------------------------------------------

        if on("mask"):
            mask = cv2.inRange(
                hsv,
                np.array(
                    [hue[0], saturation[0], value[0]],
                    dtype=np.uint8,
                ),
                np.array(
                    [hue[1], saturation[1], value[1]],
                    dtype=np.uint8,
                ),
            )
        else:
            mask = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # ------------------------------------------------------------
        # Blur
        # ------------------------------------------------------------

        if on("blur"):
            blur_size = max(1, int(blur))

            blurred = cv2.blur(
                mask,
                (blur_size, blur_size),
            )
        else:
            blurred = mask

        # ------------------------------------------------------------
        # Binärisierung
        # ------------------------------------------------------------

        # Bei einer binären HSV-Maske entstehen durch Blur Grauwerte
        # am Maskenrand. 127 trennt Vordergrund und Hintergrund wieder.
        #
        # Dieses Verhalten bleibt aus Kompatibilitätsgründen erhalten.

        threshold = 127 if on("blur") and on("mask") else 0

        binary = (blurred > threshold).astype(np.uint8) * 255

        # ------------------------------------------------------------
        # Morphologie
        # ------------------------------------------------------------

        if on("morph"):
            morph_size = max(1, int(morph))

            kernel = np.ones(
                (morph_size, morph_size),
                dtype=np.uint8,
            )

            closed = cv2.morphologyEx(
                binary,
                cv2.MORPH_CLOSE,
                kernel,
            )
        else:
            closed = binary

        # ------------------------------------------------------------
        # Canny
        # ------------------------------------------------------------

        if on("canny"):
            edges = cv2.Canny(
                closed,
                int(canny[0]),
                int(canny[1]),
            )
        else:
            edges = closed

        # ------------------------------------------------------------
        # Lane Detection
        # ------------------------------------------------------------

        lanes = self.detector.detect(edges) if on("lanes") else ()

        # ------------------------------------------------------------
        # Diagnosebilder
        # ------------------------------------------------------------

        stages = {}

        if capture:
            stages = {
                "roi": roi,
                "hsv": hsv,
                "mask": mask,
                "blur": blurred,
                "binary": binary,
                "morph": closed,
                "edges": edges,
            }

        return PipelineResult(
            processed_image=edges,
            lanes=lanes,
            stages=stages,
        )


class CopilotLaneProcessor(LaneProcessor):
    """Legacy-Pipeline mit sichtbaren Diagnose-Zwischenbildern."""

    name = "Legacy / Diagnose"

    def process(
        self,
        raw: np.ndarray,
        params: tuple,
    ) -> PipelineResult:

        return self._process(
            raw,
            params,
            capture=True,
        )


class OpusLaneProcessor(LaneProcessor):
    """Linienerkennung für dunkle Fahrbahnlinien.

    Im Gegensatz zur Legacy-Pipeline wird nicht primär nach einer
    bestimmten Farbe gesucht.

    Stattdessen werden dunkle lokale Strukturen gegenüber ihrem
    Hintergrund hervorgehoben.

    Pipeline:
        ROI
        -> Gray
        -> CLAHE
        -> Gaussian Blur
        -> Blackhat
        -> Otsu Threshold
        -> Morph Close
        -> Canny
        -> LaneDetector

    Die bisherigen Parameter werden zunächst weiterverwendet, damit
    der Processor ohne Änderung der restlichen Anwendung eingesetzt
    werden kann.

    Relevant:
        crop    -> ROI
        blur    -> Blur
        morph   -> Morphologie
        canny   -> Canny

    HSV-Parameter werden von Opus absichtlich ignoriert.
    """

    name = "Modus Opus"

    # Eigener Hilfetext: hier ist der Blur ein Gauss-Filter vor dem Blackhat, kein Maskenrand-Glaetter.
    BLUR = PipelineControl('blur', 'Blur-Kernel (Gauß)', 1, 15, help_key='blurgauss')

    @classmethod
    def dashboard_stages(cls):
        return validate_stages((
            PipelineStage(STAGE_RAW, 'Rohbild'),
            PipelineStage('roi', 'ROI', 'roi', (cls.CROP,)),
            PipelineStage('gray', 'Graustufen'),
            PipelineStage('clahe', 'CLAHE (lokaler Kontrast)', 'clahe'),
            PipelineStage('blur', 'Gaussian Blur', 'blur', (cls.BLUR,)),
            PipelineStage('blackhat', 'Blackhat (dunkle Linie)', 'blackhat'),
            PipelineStage('threshold', 'Otsu-Schwelle', 'threshold'),
            PipelineStage('morph', 'Morphologie', 'morph', (cls.MORPH,)),
            PipelineStage('edges', 'Canny', 'canny', (cls.CANNY,)),
            PipelineStage(STAGE_OVERLAY, 'Linien', 'lanes'),
        ))

    def process(
        self,
        raw: np.ndarray,
        params: tuple,
    ) -> PipelineResult:

        if raw is None or raw.size == 0:
            raise ValueError("Kamerabild ist leer")

        _, _, _, crop, blur, morph = params[:6]

        canny = params[6] if len(params) > 6 else (50, 100)
        enabled = params[7] if len(params) > 7 else {}

        def on(name: str) -> bool:
            return bool(enabled.get(name, True))

        # ============================================================
        # 01 - ROI
        # ============================================================

        h = raw.shape[0]

        top = int(h * (1 - crop[1])) if on("roi") else 0
        bottom = int(h * (1 - crop[0])) if on("roi") else h

        if not (0 <= top < bottom <= h):
            raise ValueError("ROI ist ungueltig")

        roi = raw[top:bottom, :]

        # ============================================================
        # 02 - Graustufen
        # ============================================================

        gray = cv2.cvtColor(
            roi,
            cv2.COLOR_BGR2GRAY,
        )

        # ============================================================
        # 03 - Lokaler Kontrast (CLAHE)
        # ============================================================

        if on("clahe"):
            clahe = cv2.createCLAHE(
                clipLimit=2.0,
                tileGridSize=(8, 8),
            )

            enhanced = clahe.apply(gray)
        else:
            enhanced = gray

        # ============================================================
        # 04 - Gaussian Blur
        # ============================================================

        if on("blur"):
            blur_size = max(1, int(blur))

            # GaussianBlur benötigt ungerade Kernelgrößen.
            if blur_size % 2 == 0:
                blur_size += 1

            blurred = cv2.GaussianBlur(
                enhanced,
                (blur_size, blur_size),
                0,
            )
        else:
            blurred = enhanced

        # ============================================================
        # 05 - Blackhat
        #
        # Blackhat hebt dunkle Strukturen hervor, die kleiner als der
        # verwendete Kernel sind.
        #
        # Wichtig:
        # Der Kernel sollte deutlich breiter als die erwartete Linie
        # im Bild sein.
        # ============================================================

        if on("blackhat"):
            blackhat_kernel_size = 15

            blackhat_kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (
                    blackhat_kernel_size,
                    blackhat_kernel_size,
                ),
            )

            blackhat = cv2.morphologyEx(
                blurred,
                cv2.MORPH_BLACKHAT,
                blackhat_kernel,
            )
        else:
            # Fallback:
            # dunkle Pixel werden durch Invertierung hell.
            blackhat = cv2.bitwise_not(blurred)

        # ============================================================
        # 06 - Automatischer Threshold
        #
        # Otsu bestimmt den Grenzwert aus dem aktuellen ROI.
        # ============================================================

        if on("threshold"):
            _, binary = cv2.threshold(
                blackhat,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )
        else:
            binary = blackhat

        # ============================================================
        # 07 - Morphologie
        #
        # CLOSE verbindet kleine Unterbrechungen innerhalb der Linie.
        # ============================================================

        if on("morph"):
            morph_size = max(1, int(morph))

            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (
                    morph_size,
                    morph_size,
                ),
            )

            closed = cv2.morphologyEx(
                binary,
                cv2.MORPH_CLOSE,
                kernel,
            )
        else:
            closed = binary

        # ============================================================
        # 08 - Canny
        # ============================================================

        if on("canny"):
            edges = cv2.Canny(
                closed,
                int(canny[0]),
                int(canny[1]),
            )
        else:
            edges = closed

        # ============================================================
        # 09 - Lane Detection
        # ============================================================

        lanes = self.detector.detect(edges) if on("lanes") else ()

        # ============================================================
        # Diagnosebilder
        #
        # Reihenfolge absichtlich == Reihenfolge der Pipeline.
        # Das Dashboard kann später genau daraus dynamisch seine
        # Pipeline-Karten erzeugen.
        # ============================================================

        stages = {
            "roi": roi,
            "gray": gray,
            "clahe": enhanced,
            "blur": blurred,
            "blackhat": blackhat,
            "threshold": binary,
            "morph": closed,
            "edges": edges,
        }

        return PipelineResult(
            processed_image=edges,
            lanes=lanes,
            stages=stages,
        )

