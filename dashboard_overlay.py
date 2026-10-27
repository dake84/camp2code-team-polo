"""Gemeinsame Zeichenfunktionen fuer Live-Overlay und Replay-HUD.

Alle Funktionen zeichnen ausschliesslich auf Kopien. Es werden keine Linien
erkannt und keine Fahrentscheidungen berechnet; dargestellt werden nur Werte
aus ``CameraData``/``DrivingState`` bzw. aus einer gespeicherten Session.

Linienmodell: ``(m, b)`` mit ``y = m*x + b`` im ROI-Koordinatensystem.
Im Vollbild wird um ``roi_top_px`` nach unten verschoben.
"""
import math

import cv2
import numpy as np

from LenkwinkelKalkulator import steering_line_from_lanes

LANE_COLORS = ((50, 230, 100), (20, 200, 255))
ROI_COLOR = (255, 200, 0)
STEERING_LINE_COLOR = (255, 80, 255)
TEXT_COLOR = (245, 245, 245)


def as_bgr_copy(image):
    """Liefert eine BGR-Kopie (Graubilder werden konvertiert) oder ``None``."""
    if image is None or getattr(image, 'size', 0) == 0:
        return None
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image.copy()


def roi_rows(camera, height):
    """ROI-Zeilen (oben, unten) im Vollbild, identisch zum LaneProcessor."""
    low, high = getattr(camera, 'crop', (0.1, 0.9))
    top = max(0, min(height, int(height * (1 - high))))
    bottom = max(top, min(height, int(height * (1 - low))))
    return top, bottom


def _finite_lane(lane):
    if lane is None:
        return None
    try:
        m, b = (float(v) for v in lane)
    except (TypeError, ValueError):
        return None
    return (m, b) if math.isfinite(m) and math.isfinite(b) else None


def draw_roi(image, top, bottom):
    h, w = image.shape[:2]
    cv2.rectangle(image, (0, top), (w - 1, max(top, bottom - 1)), ROI_COLOR, 1)


def draw_lanes(image, lanes, top, bottom, thickness=2):
    """Zeichnet Linien ``y = m*x + b`` (ROI-Koordinaten) auf das Bild, auf den ROI begrenzt."""
    h, w = image.shape[:2]
    roi_h = max(1, bottom - top)
    drawn = 0
    for index, lane in enumerate(lanes or ()):
        lane = _finite_lane(lane)
        if lane is None:
            continue
        m, b = lane
        y0 = float(np.clip(b + top, -1e5, 1e5))
        y1 = float(np.clip(m * (w - 1) + b + top, -1e5, 1e5))
        ok, p0, p1 = cv2.clipLine((0, top, w, roi_h), (0, int(y0)), (w - 1, int(y1)))
        if ok:
            cv2.line(image, p0, p1, LANE_COLORS[index % len(LANE_COLORS)], thickness, cv2.LINE_AA)
            drawn += 1
    return drawn


def draw_steering_line(image, lanes, top, bottom):
    """Zeichnet die vom Regler verwendete Richtungsgerade (nur bei genau zwei Linien).

    Die Gerade wird mit derselben Funktion wie im CameraMode aus den Linien
    bestimmt (``steering_line_from_lanes``); sie ist keine neue Messung.
    """
    lanes = tuple(lanes or ())
    if len(lanes) != 2 or any(_finite_lane(lane) is None for lane in lanes):
        return False
    w = image.shape[1]
    line = steering_line_from_lanes([_finite_lane(lane) for lane in lanes], w, max(1, bottom - top))
    if line is None:
        return False
    (x0, y0), (x1, y1) = line
    cv2.line(image, (int(x0), int(y0) + top), (int(x1), int(y1) + top), STEERING_LINE_COLOR, 2, cv2.LINE_AA)
    return True


def put_text(image, text, origin, scale=0.48, color=TEXT_COLOR):
    cv2.putText(image, str(text), origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, str(text), origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def render_overlay(camera, driving, processed=False):
    """Zeigt ROI, erkannte Geraden und Fahrentscheidung auf einer Bildkopie.

    Liefert ``None``, wenn Kameraframe und Fahrentscheidung nicht dieselbe
    ``frame_id`` haben (keine zufaellige Kombination unterschiedlicher Frames).
    """
    image = camera.process_image if processed else camera.raw_image
    if camera.frame_id != driving.frame_id:
        return None
    result = as_bgr_copy(image)
    if result is None:
        return None
    h = result.shape[0]
    if processed:
        # Das vorverarbeitete Bild ist bereits auf den ROI zugeschnitten.
        top, bottom = 0, h
    else:
        top, bottom = roi_rows(camera, h)
        draw_roi(result, top, bottom)
    lanes = driving.lanes if driving.lanes is not None else ()
    draw_lanes(result, lanes, top, bottom)
    draw_steering_line(result, lanes, top, bottom)
    parts = [f'Frame {camera.frame_id}']
    angle = getattr(driving, 'steering_angle_car', None)
    if angle is None:
        angle = getattr(driving, 'steering_angle', None)
    if angle is not None:
        parts.append(f'Stellwert {float(angle):.0f}')
    speed = getattr(driving, 'speed', None)
    if speed is not None:
        parts.append(f'Speed {float(speed):.0f}')
    put_text(result, '  '.join(parts), (8, 22))
    return result


def render_calibration_overlay(camera, processed=False):
    """Zeigt die erkannten Linien ohne Fahrentscheidung (Kalibrierung).

    Frueher wurden hier Platzhalterwerte "Winkel 90 Speed 0" eingeblendet.
    Ohne Fahrentscheidung werden jetzt keine Fahrwerte angezeigt.
    """
    if camera is None:
        return None
    source = camera.process_image if processed else camera.raw_image
    result = as_bgr_copy(source)
    if result is None:
        return None
    h = result.shape[0]
    if processed:
        top, bottom = 0, h
    else:
        top, bottom = roi_rows(camera, h)
        draw_roi(result, top, bottom)
    lanes = tuple(camera.lanes or ())
    draw_lanes(result, lanes, top, bottom)
    draw_steering_line(result, lanes, top, bottom)
    count = sum(_finite_lane(lane) is not None for lane in lanes)
    put_text(result, f'Frame {camera.frame_id}  Linien {count}', (8, 22))
    put_text(result, 'OHNE FAHRENTSCHEIDUNG', (8, max(45, h - 12)), 0.45, (0, 255, 255))
    return result
