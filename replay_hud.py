"""HUD fuer gespeicherte Rohbilder ohne erneute Fahrspurerkennung.

Korrektur in Version 6.0 (nachgewiesener Fehler):
    Das HUD hat die Linienkoeffizienten bisher als ``x = m*y + b`` im Vollbild
    gezeichnet. Der LaneDetector liefert jedoch ``y = m*x + b`` im
    ROI-Koordinatensystem (siehe tests/test_lane_geometry.py). Das HUD nutzt
    jetzt dieselbe Zeichenfunktion wie das Live-Overlay und verschiebt um
    ``roi_top_px`` aus frames.csv. Fehlt die ROI-Angabe (Sessions mit Schema v1),
    werden keine Linien gezeichnet, statt eine Geometrie zu erfinden.

Das Rohbild wird nie veraendert; gezeichnet wird auf einer Kopie.

PAKET-H1: Zustandsanzeige statt N/A (GESTOPPT > BERGUNG/MANUELL > AUTONOM > STEHT > KEIN
FAHRMODUS), ROH > STELL (SOLL nur bei Abweichung), vorzeichenbehafteter Steuer-Speed,
ASCII-Texte, Kennzeichnung unten im Bild, einklappbare Legende.
"""
import json
import math

import cv2
import mode_registry

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


# --------------------------------------------------------------------- PAKET-H1: Zustand, ASCII, Legende
import unicodedata

VEHICLE_MODES = ('idle', 'auto', 'manual', 'recovery')
STOP_CODES = frozenset(('STOP_REASON', 'CONTROLLER_STOPPED'))

# BGR-Farben der Zustandszeile
STATE_COLORS = {'stopped': (80, 80, 255), 'recovery': (0, 165, 255), 'manual': (0, 205, 255),
                'auto': (120, 230, 120), 'auto_missing': (0, 215, 255), 'idle': (225, 225, 225),
                'none': (160, 160, 160)}

# Felder, Bedeutung, Werte (Quelle fuer die einklappbare HUD-Legende unter Cockpit, Replay und Simulation)
LEGEND = (
    ('FRAME', 'Frame-ID der Aufnahme', 'fortlaufende Nummer'),
    ('STEUER-SPEED', 'Speed-Steuerwert (PWM-Befehl), keine gemessene Geschwindigkeit',
     'vorzeichenbehaftet: vorwärts +, rückwärts −, 0 = Stillstand; fehlt der Wert, wird er ausgeblendet'),
    ('ROH', 'Rohwinkel aus den erkannten Linien (berechnet)', 'Grad, 90 = geradeaus'),
    ('STELL', 'Stellwert, der an die Lenkung gesendet wurde (Befehlswert, keine Rückmeldung)',
     '45–135 Grad; bei Bergung und manueller Fahrt der Fahrzeugzustand im Aufnahmemoment'),
    ('SOLL', 'Sollwinkel des Reglers',
     'nur sichtbar, wenn er um mindestens 1 Grad vom Stellwert abweicht (bei Autofahrt praktisch nie)'),
    ('GESTOPPT: <Ursache>', 'zu diesem Frame gehört ein Stopp-Ereignis',
     'z. B. LANE_TIMEOUT, CAMERA_STALE, NO_CAMERA_DATA, Benutzer-STOP (rot)'),
    ('BERGUNG / MANUELL', 'Fahrzeugzustand im Aufnahmemoment: Bergungsfahrt bzw. Gamepad', 'orange / gelb'),
    ('AUTONOM', 'CameraMode fährt, Fahrentscheidung zum Frame liegt vor', 'grün'),
    ('AUTONOM / ENTSCHEIDUNG FEHLT', 'Fahrmodus aktiv, aber keine Entscheidung zu diesem Frame', 'gelb'),
    ('STEHT', 'Fahrzeug im Leerlauf (kein Fahrmodus)', 'weiß'),
    ('KEIN FAHRMODUS', 'ältere Aufnahme ohne Fahrzeugzustand', 'grau'),
    ('Linien', 'grün/gelb: erkannte Linien im ROI · magenta: Reglerrichtung · gelber Pfeil: Stellwert',
     'ROI-Rahmen cyan'),
    ('EVENT', 'Ereigniscodes dieses Frames (unten im Bild)', 'z. B. LANE_LOST'),
    ('MODUS', 'Name des Fahrmodus im Aufnahmemoment (aus dem Register)', 'rechts in der Winkelzeile; ältere Aufnahmen: nicht angezeigt'),
)


def ascii_text(value):
    """cv2.putText kennt nur ASCII: Umlaute und Sonderzeichen werden umgeschrieben bzw. entfernt."""
    text = str(value)
    for old, new in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('Ä', 'Ae'), ('Ö', 'Oe'), ('Ü', 'Ue'), ('ß', 'ss'),
                     ('°', ' Grad'), ('·', '-'), ('–', '-'), ('—', '-'), ('→', '>'), ('„', '"'), ('“', '"')):
        text = text.replace(old, new)
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(text.split())


def _ev(event, key):
    return event.get(key) if isinstance(event, dict) else getattr(event, key, None)


def _frame_events(row, events):
    if not events:
        return []
    frame = str(row.get('frame_id', '')).strip()
    return [e for e in events if _ev(e, 'frame_id') is not None and str(_ev(e, 'frame_id')).strip() == frame]


def stop_reason(row, events=None):
    """Ursache eines Stopp-Ereignisses zu diesem Frame oder ``None``.

    Stopp-Ereignisse werden dem Kameraframe zugeordnet, der beim Beobachten durch den Telemetrie-Sampler
    (4 Hz) aktuell war; das ist nicht zwingend der Frame der letzten Fahrentscheidung.
    """
    explicit = row.get('stop_reason')
    codes = {c for c in str(row.get('event_codes') or '').split('|') if c}
    here = _frame_events(row, events)
    codes |= {str(_ev(e, 'code')) for e in here}
    if not (explicit or codes & STOP_CODES):
        return None
    if explicit:
        return str(explicit)
    for wanted in ('STOP_REASON', 'CONTROLLER_STOPPED'):
        for event in here:
            if _ev(event, 'code') == wanted and _ev(event, 'detail'):
                return str(_ev(event, 'detail'))
    return 'UNBEKANNT'


def vehicle_state(row, events=None):
    """Zustandsanzeige (Text, Art). Rangfolge:

    GESTOPPT: <Ursache> > BERGUNG / MANUELL > AUTONOM (complete) > AUTONOM / ENTSCHEIDUNG FEHLT
    (missing_decision oder Modus auto ohne Entscheidung) > STEHT (idle) > KEIN FAHRMODUS.
    """
    reason = stop_reason(row, events)
    if reason is not None:
        return f'GESTOPPT: {reason}', 'stopped'
    mode = row.get('vehicle_mode') or None
    if mode == 'recovery':
        return 'BERGUNG', 'recovery'
    if mode == 'manual':
        return 'MANUELL', 'manual'
    status = row.get('data_status')
    if status == 'complete':
        return 'AUTONOM', 'auto'
    if status == 'missing_decision' or mode == 'auto':
        return 'AUTONOM / ENTSCHEIDUNG FEHLT', 'auto_missing'
    if mode == 'idle':
        return 'STEHT', 'idle'
    return 'KEIN FAHRMODUS', 'none'


def hud_values(row):
    """(Stell, Soll, Roh, Speed); fehlende Werte bleiben None (nie als 0 erfunden)."""
    stell = _number(row, 'steering_angle_car')
    if stell is None:
        stell = _number(row, 'vehicle_angle')
    speed = _number(row, 'speed')
    if speed is None:
        speed = _number(row, 'vehicle_speed')
    return stell, _number(row, 'requested_angle'), _number(row, 'steering_angle'), speed


def signed(value):
    """Vorzeichenbehaftet (rueckwaerts negativ), 0 ohne Vorzeichen."""
    value = int(round(value))
    return '0' if value == 0 else f'{value:+d}'


def angle_line(stell, soll, roh):
    """``ROH 97 > STELL 99``; ``SOLL`` nur bei mindestens 1 Grad Abweichung vom Stellwert."""
    parts = []
    if roh is not None:
        parts.append(f'ROH {roh:.0f}')
    if stell is not None:
        parts.append(f'STELL {stell:.0f}')
    text = ' > '.join(parts)
    if soll is not None and stell is not None and abs(soll - stell) >= 1:
        text += f'  SOLL {soll:.0f}'
    return text


def row_from_camera(camera, driving=None, events=None):
    """Zeile im Format von frames.csv fuer ``draw_hud`` aus Live-Daten (keine neue Erkennung)."""
    lanes = driving.lanes if driving is not None and driving.lanes is not None else camera.lanes
    top, bottom = camera.roi_rows()
    row = {'frame_id': camera.frame_id, 'roi_top_px': top, 'roi_bottom_px': bottom,
           'lanes_json': encode_lanes(lanes), 'event_codes': '',
           'data_status': 'complete' if driving is not None else 'no_driving_state',
           'vehicle_mode': getattr(camera, 'vehicle_mode', None),
           'vehicle_angle': getattr(camera, 'vehicle_angle', None),
           'vehicle_speed': getattr(camera, 'vehicle_speed', None),
           'vehicle_mode_key': getattr(camera, 'vehicle_mode_key', None)}
    if events:
        codes = []
        for event in _frame_events(row, events):
            code = str(_ev(event, 'code'))
            if code not in codes:
                codes.append(code)
        row['event_codes'] = '|'.join(codes)
    if driving is not None:
        row.update(steering_angle_car=driving.steering_angle_car, requested_angle=driving.requested_angle,
                   steering_angle=driving.steering_angle, speed=driving.speed)
    return row


def legend_block(html, className='c2c-hud-legend mt-2 small'):
    """Einklappbare HUD-Legende (kein Callback, keine IDs; html wird uebergeben)."""
    rows = [html.Tr([html.Td(field, className='fw-semibold pe-3 text-nowrap'), html.Td(meaning),
                     html.Td(values, className='text-secondary')]) for field, meaning, values in LEGEND]
    return html.Details([html.Summary('HUD-Legende (Felder, Bedeutung, Werte)', className='text-info'),
                         html.Table(html.Tbody(rows), className='table table-sm table-dark mb-0 mt-2')],
                        className=className)


def _text_width(text, scale):
    return cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]


def draw_hud(image, row, events=None, footer=None):
    """Zeigt gespeicherte Linien, Winkel und Zustand; keine neuen Messwerte.

    ``events``: Ereignisse (dicts oder HealthEvent) fuer die Stopp-Ursache. ``footer``: Kennzeichnung unten
    im Bild (z. B. "DAMALS", "JETZT (Simulation)"), damit sie das HUD oben nicht ueberlagert.
    Alle Texte sind ASCII (cv2.putText).
    """
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
    stell, soll, roh, speed = hud_values(row)
    # Gelber Pfeil = Stellwert des Fahrzeugs (keine Servorueckmeldung); bei Bergung der Zustand beim Aufnehmen.
    if stell is not None:
        origin = (width // 2, height - 1)
        theta = math.radians(max(-70, min(70, stell - 90)))
        length = int(height * .35)
        target = (int(origin[0] + length * math.sin(theta)), int(origin[1] - length * math.cos(theta)))
        cv2.arrowedLine(result, origin, target, (40, 210, 255), 3, cv2.LINE_AA, tipLength=.13)
    shade = result.copy()
    cv2.rectangle(shade, (0, 0), (width, min(height, 76)), (12, 20, 32), -1)
    cv2.addWeighted(shade, .68, result, .32, 0, result)
    frame_text = 'FRAME ' + ascii_text(row.get('frame_id', '?'))
    put_text(result, frame_text, (12, 23), .6)
    if speed is not None:
        speed_text = 'STEUER-SPEED ' + signed(speed)
        put_text(result, speed_text, (max(12 + _text_width(frame_text, .6) + 12,
                                          width - _text_width(speed_text, .45) - 10), 23), .45)
    line = angle_line(stell, soll, roh)
    if line:
        put_text(result, line, (12, 46), .45)
    label, kind = vehicle_state(row, events)
    label = ascii_text(label)
    key = row.get('vehicle_mode_key') or None
    name = ascii_text(mode_registry.short_label(key)) if key else ''
    if name:
        x = width - _text_width(name, .45) - 10
        if x > 12 + _text_width(line, .45) + 10:      # nie die Winkelzeile überlagern
            put_text(result, name, (x, 46), .45, (255, 220, 160))    
    put_text(result, label, (12, 67), .5, STATE_COLORS[kind])
    if not geometry_known and any(lane is not None for lane in lanes):
        put_text(result, 'LINIEN: ROI UNBEKANNT', (12 + _text_width(label, .5) + 12, 67), .4)
    base = height
    if footer:
        cv2.rectangle(result, (0, height - 22), (width, height), (40, 28, 12), -1)
        put_text(result, ascii_text(footer)[:70], (10, height - 7), .45, (0, 255, 255))
        base = height - 22
    if row.get('event_codes'):
        cv2.rectangle(result, (0, base - 32), (width, base), (15, 30, 100), -1)
        put_text(result, 'EVENT: ' + ascii_text(row['event_codes'])[:55], (10, base - 10), .45)
    return result
