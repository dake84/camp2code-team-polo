"""HUD fuer gespeicherte Rohbilder ohne erneute Fahrspurerkennung.

Korrektur in Version 6.0 (nachgewiesener Fehler):
    Das HUD hat die Linienkoeffizienten bisher als ``x = m*y + b`` im Vollbild
    gezeichnet. Der LaneDetector liefert jedoch ``y = m*x + b`` im
    ROI-Koordinatensystem (siehe tests/test_lane_geometry.py). Das HUD nutzt
    jetzt dieselbe Zeichenfunktion wie das Live-Overlay und verschiebt um
    ``roi_top_px`` aus frames.csv. Fehlt die ROI-Angabe (Sessions mit Schema v1),
    werden keine Linien gezeichnet, statt eine Geometrie zu erfinden.

Das Rohbild wird nie veraendert; gezeichnet wird auf einer Kopie.
"""
import json
import math

import cv2

from dashboard_overlay import draw_lanes, draw_roi, draw_steering_line, put_text


def encode_lanes(lanes):
    """Uebernimmt ausschliesslich numerische Linienkoeffizienten aus dem Datenpaket."""
    result = []
    for lane in lanes or ():
        if lane is None:
            result.append(None)
            continue
        try:
            values = list(lane)
            if len(values) != 2 or not all(math.isfinite(float(x)) for x in values):
                result.append(None)
            else:
                result.append([float(x) for x in values])
        except (TypeError, ValueError, OverflowError):
            result.append(None)
    return json.dumps(result)


def _number(row, key):
    try:
        value = float(row.get(key, ''))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _integer(row, key):
    value = _number(row, key)
    return int(value) if value is not None else None


def decode_lanes(row):
    try:
        lanes = json.loads(row.get('lanes_json') or '[]')
    except (ValueError, TypeError):
        return []
    return [lane if isinstance(lane, list) and len(lane) == 2 else None for lane in lanes]


def draw_hud(image, row):
    """Zeigt gespeicherte Linien, Winkel und Status; keine neuen Messwerte."""
    if image is None or getattr(image, 'size', 0) == 0:
        return image
    result = image.copy()
    height, width = result.shape[:2]
    lanes = decode_lanes(row)
    top, bottom = _integer(row, 'roi_top_px'), _integer(row, 'roi_bottom_px')
    geometry_known = top is not None and bottom is not None and 0 <= top < bottom <= height
    if geometry_known:
        draw_roi(result, top, bottom)
        draw_lanes(result, lanes, top, bottom, thickness=3)
        draw_steering_line(result, lanes, top, bottom)

    angle = _number(row, 'steering_angle_car')
    if angle is None:
        angle = _number(row, 'steering_angle')
    # Gelber Pfeil = gespeicherter Stellwert des Fahrzeugs (keine Servorueckmeldung).
    if angle is not None:
        origin = (width // 2, height - 1)
        theta = math.radians(max(-70, min(70, angle - 90)))
        length = int(height * .35)
        target = (int(origin[0] + length * math.sin(theta)), int(origin[1] - length * math.cos(theta)))
        cv2.arrowedLine(result, origin, target, (40, 210, 255), 3, cv2.LINE_AA, tipLength=.13)

    shade = result.copy()
    cv2.rectangle(shade, (0, 0), (width, min(height, 76)), (12, 20, 32), -1)
    cv2.addWeighted(shade, .68, result, .32, 0, result)
    requested = _number(row, 'requested_angle')
    raw = _number(row, 'steering_angle')
    speed = _number(row, 'speed')
    put_text(result, 'FRAME ' + str(row.get('frame_id', '?')), (12, 23), .6)
    put_text(result, 'STELL ' + (f'{angle:.0f}' if angle is not None else 'N/A')
             + '  SOLL ' + (f'{requested:.0f}' if requested is not None else 'N/A')
             + '  ROH ' + (f'{raw:.0f}' if raw is not None else 'N/A'), (12, 46), .45)
    put_text(result, 'SPEED ' + (f'{speed:.0f}' if speed is not None else 'N/A'),
             (max(12, width - 120), 23), .45)
    status = str(row.get('data_status') or 'unknown').upper()
    if not geometry_known and any(lane is not None for lane in lanes):
        status += '  LINIEN: ROI UNBEKANNT'
    put_text(result, status, (12, 67), .4)
    if row.get('event_codes'):
        cv2.rectangle(result, (0, height - 32), (width, height), (15, 30, 100), -1)
        put_text(result, 'EVENT: ' + str(row['event_codes'])[:55], (10, height - 10), .45)
    return result
