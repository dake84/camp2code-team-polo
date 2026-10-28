"""Austauschbare Bildverarbeitung, ohne Eingriff in die Fahrregelung."""

from dataclasses import dataclass, field

import cv2
import numpy as np

from lane_detector import LaneDetector


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