#!/usr/bin/env python3
"""Paket H1: Diagramme (feste Hoehe, interaktiv), HUD-Zustand, Simulation (Tempo, Invalidierung),
dauerhafte Einstellungen, neue Hilfetexte.

Setzt Paket G (a9203c6) bzw. den Stand 062f360 voraus. Verankerte Aenderungen; fehlt ein Anker
oder ist er mehrdeutig, wird KEINE Datei geschrieben (alles oder nichts). Dateien mit dem
Marker PAKET-H1 werden uebersprungen (idempotent).

Aufruf: python scripts/paket_h1_patch.py [REPO_ROOT]
"""
import sys
from pathlib import Path

MARKER = 'PAKET-H1'


class PatchError(Exception):
    pass


def _hits(lines, needle, start=0):
    return [i for i in range(start, len(lines)) if needle in lines[i]]


def _one(lines, needle, file, start=0):
    hits = _hits(lines, needle, start)
    if len(hits) != 1:
        raise PatchError(f'{file}: Anker {needle!r} {len(hits)}x gefunden (erwartet 1)')
    return hits[0]


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def sub_once(text, old, new, file):
    count = text.count(old)
    if count != 1:
        raise PatchError(f'{file}: Text {old[:70]!r} {count}x gefunden (erwartet 1)')
    return text.replace(old, new)


def insert(text, needle, block, file, after=True, indent=None, blank_after=None, blank_before=0):
    """Fuegt ``block`` vor/nach der eindeutigen Zeile mit ``needle`` ein.

    Der Block wird mit der Einrueckung der Ankerzeile (oder ``indent``) eingerueckt.
    ``blank_after``: Leerzeilen hinter einem VOR dem Anker eingefuegten mehrzeiligen Block;
    ``blank_before``: Leerzeilen vor dem Block.
    """
    lines = text.split('\n')
    i = _one(lines, needle, file)
    ind = _indent(lines[i]) if indent is None else indent
    body = block.strip('\n').split('\n')
    new = [(ind + line) if line.strip() else '' for line in body]
    if blank_after is None:
        blank_after = 1 if (not after and len(body) > 1) else 0
    new = [''] * blank_before + new + [''] * blank_after
    pos = i + 1 if after else i
    return '\n'.join(lines[:pos] + new + lines[pos:])


def replace_block(text, start_needle, end_needle, block, file, gap=2):
    """Ersetzt die Zeilen von ``start_needle`` (inklusive) bis vor ``end_needle`` (exklusive).

    Zwischen Block und Endanker stehen ``gap`` Leerzeilen.
    """
    lines = text.split('\n')
    s = _one(lines, start_needle, file)
    e = _one(lines, end_needle, file, start=s + 1)
    return '\n'.join(lines[:s] + block.strip('\n').split('\n') + [''] * gap + lines[e:])


def replace_to_end(text, start_needle, block, file, tail_check):
    """Ersetzt ab ``start_needle`` bis Dateiende; ``tail_check`` muss im ersetzten Rest stehen."""
    lines = text.split('\n')
    s = _one(lines, start_needle, file)
    rest = '\n'.join(lines[s:])
    if tail_check not in rest:
        raise PatchError(f'{file}: Dateiende nach {start_needle!r} entspricht nicht der Erwartung ({tail_check!r})')
    return '\n'.join(lines[:s]) + '\n' + block.strip('\n') + '\n'


# ------------------------------------------------------------------ Textbloecke
HUD_BLOCK = r'''
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
           'vehicle_speed': getattr(camera, 'vehicle_speed', None)}
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
'''

HELP_BLOCK = r'''
# Herkunft: gemessen | berechnet | Befehlswert | Einstellung
ORIGINS = ('gemessen', 'berechnet', 'Befehlswert', 'Einstellung')

# PAKET-H1: Jeder Text ist genau EIN Satz (was der Wert bedeutet, Einheit, wichtigste Einflussfaktoren).
HELP = {
    'stell': ('Stellwert (Fahrzeug)', 'Befehlswert',
              'Lenkwinkel in Grad (45–135, 90 = geradeaus), der zuletzt an den Servo gesendet wurde und von '
              'Rohwinkel, steering_gain und der Begrenzung abhängt, aber keine gemessene Radstellung ist.'),
    'speed': ('Speed-Steuerwert', 'Befehlswert',
              'PWM-Wert der Motoren (Betrag 0–100, rückwärts negativ), je nach Einstellung fest 30 oder adaptiv '
              'nach Lenkwinkel, aber keine gemessene Geschwindigkeit.'),
    'soll': ('Sollwinkel (Regler)', 'Befehlswert',
             'Lenkwinkel in Grad, den der Regler anfordert (90 + (Rohwinkel − 90) · steering_gain, begrenzt auf '
             '45–135) und der bei Autofahrt praktisch dem Stellwert entspricht.'),
    'roh': ('Rohwinkel (Linien)', 'berechnet',
            'Richtung in Grad (90 = geradeaus) von der Bildmitte zur Mitte der beiden erkannten Linien an der '
            'ROI-Oberkante, abhängig von Linienerkennung und ROI, wobei der Regler ohne zwei Linien den Vorwert hält.'),
    'diag': ('Controllerdiagnose', 'berechnet',
             'Letzte Entscheidung des CameraMode mit Linienanzahl, Rohwinkel, gehaltenem Vorwert, Soll- und '
             'Stellwert und Stoppursache, abhängig von der Linienerkennung des jeweiligen Frames.'),
    'hud': ('HUD-Darstellung', 'Einstellung',
            'Blendet Frame, Steuer-Speed, Roh-, Stell- und gegebenenfalls Sollwinkel sowie den Fahrzustand '
            '(GESTOPPT, BERGUNG, MANUELL, AUTONOM, STEHT) ins Bild ein, ohne das Rohbild zu verändern.'),
    'rate': ('Regelrate', 'gemessen',
             'Fahrentscheidungen pro Sekunde in Hz, nach oben begrenzt durch Kamerarate und Bildverarbeitung '
             '(grün ab 80 %, gelb ab 50 % des Solls, sonst rot).'),
    'lat': ('Reaktionszeit', 'gemessen',
            'Zeit in Millisekunden von der Bildaufnahme bis zum Fahrbefehl, die mit der Bildverarbeitung, der '
            'Wartezeit bis zur nächsten Regler-Abtastung (100 Hz = bis 10 ms) und der CPU-Last steigt.'),
    'qual': ('Spurqualität', 'berechnet',
             'Anteil der Frames der letzten rund 4 Sekunden mit zwei plausiblen Linien (links vor rechts, '
             'Spurbreite 25–98 % der Bildbreite), abhängig von Beleuchtung, HSV-Grenzen und ROI.'),
    'freq': ('Soll / Ist Frequenz', 'gemessen',
             'Kamerabilder und Fahrentscheidungen pro Sekunde in Hz im Vergleich zum Soll '
             '(cam_sensor.scan_frequency), wobei Ist = 0 gilt, wenn seit 2 Sekunden kein Wert kam.'),
    'freqhist': ('Frequenzverlauf', 'gemessen',
                 'Kamera- und Entscheidungsfrequenz in Hz über die letzten 60 Sekunden, abhängig von '
                 'Bildverarbeitungszeit und CPU-Last und ohne Fahrt bei 0.'),
    'drivehist': ('Lenkwinkel / Speed / Linien', 'gemessen',
                  'Rohwinkel und Stellwert in Grad, Speed-Steuerwert und Linienanzahl je Fahrentscheidung der '
                  'letzten 60 Sekunden, abhängig von Linienerkennung und Regler.'),
    'dataage': ('Datenalter, Latenzen, System', 'gemessen',
                'Alter des letzten Kamerabilds, Dauer der Bildverarbeitung, Reaktionszeit, CPU je Thread und '
                'Renderzeiten des Dashboards in Millisekunden beziehungsweise Prozent, abhängig von der Systemlast.'),
    'crop': ('ROI', 'Einstellung',
             'Bildausschnitt als Anteil der Bildhöhe von unten (0) bis oben (1), der allein ausgewertet wird '
             'und das Fahrzeug im unteren Bildrand ausschließen sollte.'),
    'hue': ('Hue', 'Einstellung',
            'Farbton-Bereich 0–179 (OpenCV) der Linienmaske, wobei blaues Klebeband typisch bei 100–130 liegt.'),
    'sat': ('Saturation', 'Einstellung',
            'Sättigungs-Bereich 0–255 der Linienmaske, bei dem ein zu hohes Minimum Linien im Schatten verliert.'),
    'val': ('Value', 'Einstellung',
            'Helligkeits-Bereich 0–255 der Linienmaske, dessen Minimum sehr dunkle Bereiche ausfiltert.'),
    'blur': ('Blur-Kernel', 'Einstellung',
             'Kantenlänge des Weichzeichners in Pixeln, der Maskenränder glättet und Rauschen unterdrückt, '
             'wobei hohe Werte schmale Linien unter die Binärschwelle von 50 % drücken können.'),
    'morph': ('Morph-Kernel', 'Einstellung',
              'Kantenlänge des Schließens (Dilatation und Erosion) in Pixeln, die kleine Lücken in der Maske '
              'schließt, aber bei hohen Werten Linien verbreitert.'),
    'canny': ('Canny-Schwellen', 'Einstellung',
              'Untere und obere Schwelle (0–255) der Kantenerkennung, die bei aktivem Schritt statt der '
              'Flächen Kanten an die Linienerkennung gibt.'),
    'speedmode': ('Geschwindigkeitsmodus', 'Einstellung',
                  'Fest fährt immer mit demselben Steuerwert, adaptiv mit v_max auf Geraden und linear bis '
                  'v_min bei vollem Einschlag oder gehaltenem Vorwert, nur im Stillstand änderbar und dauerhaft '
                  'in config.json gespeichert.'),
    'speedfixed': ('Fester Speed-Steuerwert', 'Einstellung',
                   'PWM-Steuerwert zwischen 30 und 60 für den Modus fest, wobei das Auto unter 30 nicht '
                   'anfährt.'),
    'speedvmin': ('v_min (Kurve)', 'Einstellung',
                  'Speed-Steuerwert (mindestens 30) im Modus adaptiv bei vollem Lenkeinschlag oder fehlender '
                  'Linie, höchstens so groß wie v_max.'),
    'speedvmax': ('v_max (Gerade)', 'Einstellung',
                  'Speed-Steuerwert (höchstens 60) im Modus adaptiv bei Geradeausfahrt, mindestens so groß wie '
                  'v_min.'),
    'recq': ('JPEG-Qualität der Aufnahme', 'Einstellung',
             'Qualität 50–100 der gespeicherten Rohbilder, bei der höhere Werte schärfere Bilder, größere '
             'Dateien und mehr SD-Schreiblast bedeuten und die ab der nächsten Aufnahme gilt.'),
    'simspeed': ('Wiedergabetempo', 'Einstellung',
                 'Faktor von 0,25 bis 4, mit dem die aufgezeichneten Zeitstempel der Frames abgespielt werden, '
                 'ohne dass neu berechnet werden muss.'),
    'simmode': ('Echtzeit / Jeder Frame', 'Einstellung',
                'Echtzeit hält das Tempo nach den Zeitstempeln und überspringt Anzeigeframes, Jeder Frame '
                'überspringt nichts und läuft langsamer, wenn die Anzeige nicht hinterherkommt.'),
    'simrate': ('Anzeigerate', 'gemessen',
                'Vom Server ausgelieferte Anzeigeframes pro Sekunde, begrenzt durch den Anzeigetakt von 150 ms '
                '(höchstens etwa 6 bis 7 pro Sekunde) sowie Bildkodierung und Übertragung, ohne Messung der '
                'Darstellung im Browser.'),
}


def info(key, dbc, html, suffix=None):
    """ⓘ-Symbol mit Klick-Popover. Liefert eine Liste (in children einsetzen).

    Bei Mehrfachverwendung desselben Schluessels in einem Layout ``suffix`` angeben (eindeutige IDs).
    """
    title, origin, text = HELP[key]
    target = f'info-{key}' + (f'-{suffix}' if suffix else '')
    return [html.Span('ⓘ', id=target, className='c2c-info', title=title, role='button', tabIndex='0'),
            dbc.Popover([dbc.PopoverHeader(f'{title} · {origin}'), dbc.PopoverBody(text)],
                        target=target, trigger='legacy', placement='bottom')]


def label(text, key, dbc, html, className='c2c-kpi-label', suffix=None):
    """Beschriftung mit angehaengtem ⓘ."""
    return html.Div([text, ' ', *info(key, dbc, html, suffix)], className=className)
'''

SIM_PANEL_BLOCK = r'''
def _panel(dbc, dcc, html):
    from help_texts import info as help_info, label as help_label  # PAKET-H1
    from replay_hud import legend_block

    def card(title, content):
        return dbc.Card([dbc.CardHeader(title), dbc.CardBody(content)], className='h-100')

    def number(id_, value=None, placeholder=''):
        # debounce: erst bei Enter/Verlassen des Feldes (nicht bei jeder Ziffer verwerfen)
        return dcc.Input(id=id_, type='number', step=1, value=value, placeholder=placeholder,
                         debounce=True, className='form-control form-control-sm')

    settings = card('Simulation · Quelle, Prozessor, Parameter', [
        dbc.Row([
            dbc.Col([html.Label('Quelle', className='small'),
                     dcc.Dropdown(id='sim-session', className='c2c-dropdown', clearable=False,
                                  options=[], value=LIVE)], md=4),
            dbc.Col([html.Label('Prozessor', className='small'),
                     dcc.Dropdown(id='sim-processor', className='c2c-dropdown', clearable=False,
                                  options=processor_options(live_only=False), value='copilot')], md=3),
            dbc.Col([html.Label('Parameter', className='small'),
                     dbc.RadioItems(id='sim-params', value='recorded', options=[
                         {'label': 'von damals', 'value': 'recorded'},
                         {'label': 'aktuelle Studio-Slider', 'value': 'studio'}])], md=2),
            dbc.Col([html.Label('Frame von / bis', className='small'),
                     dbc.Row([dbc.Col(number('sim-from', placeholder='Anfang')),
                              dbc.Col(number('sim-to', placeholder='Ende'))], className='g-1')], md=2),
            dbc.Col([html.Label('Vorlauf', className='small'), number('sim-warmup', 30)], md=1),
        ], className='g-2'),
        dbc.Button('Simulation berechnen', id='sim-run', color='primary', size='sm', className='mt-2 me-2'),
        html.Span('Keine Motoransteuerung · START ist im Simulationsmodus gesperrt · '
                  'Änderungen an diesen Einstellungen verwerfen das Ergebnis',
                  className='small text-secondary'),
        html.Div(id='sim-status', className='small mt-2'),
    ])
    controls = dbc.Row([
        dbc.Col([help_label('Tempo', 'simspeed', dbc, html, 'small'),
                 dbc.RadioItems(id='sim-speed', value=1.0, inline=True,
                                options=[{'label': text, 'value': value} for text, value in SPEED_LABELS])],
                md=5),
        dbc.Col([help_label('Wiedergabe', 'simmode', dbc, html, 'small'),
                 dbc.RadioItems(id='sim-timing', value='realtime', inline=True, options=[
                     {'label': 'Echtzeit', 'value': 'realtime'}, {'label': 'Jeder Frame', 'value': 'every'}])],
                md=4),
        dbc.Col([help_label('Anzeigerate', 'simrate', dbc, html, 'small'),
                 html.Div(id='sim-display-rate', className='small')], md=12),
    ], className='g-2 mt-1')
    viewer = card(['Damals · Jetzt ', *help_info('hud', dbc, html, suffix='sim')], [
        html.Div(id='sim-frame-label', className='fw-semibold mb-2'),
        dbc.Row([dbc.Col([html.Div('DAMALS (Aufzeichnung)', className='c2c-kpi-label'),
                          html.Img(id='sim-img-then', style=IMAGE_STYLE)], md=6),
                 dbc.Col([html.Div('JETZT (Simulation)', className='c2c-kpi-label'),
                          html.Img(id='sim-img-now', style=IMAGE_STYLE)], md=6)], className='g-2'),
        legend_block(html),
        dbc.ButtonGroup([dbc.Button('◀', id='sim-prev', size='sm'),
                         dbc.Button('▶ Play', id='sim-play', size='sm'),
                         dbc.Button('▶|', id='sim-next', size='sm')], className='mt-2 mb-2'),
        dcc.Slider(id='sim-index', min=0, max=1, step=1, value=0, marks=None,
                   tooltip={'placement': 'bottom'}),
        controls,
    ])
    compare = card('Vergleich', [html.Div(id='sim-compare'), html.Hr(), html.Div(id='sim-stats')])
    chart = card('Stellwert und Linienanzahl · damals vs. jetzt', [
        chart_kit.chart_block(dcc, html, 'sim-chart', 280, 'Noch keine Simulation berechnet',
                              note='Klick auf einen Punkt = Frame anzeigen · grau = Vorlauf (nicht in den Kennzahlen) · '
                                   'rote Rauten = Regler-Stopp · Mausrad = Zoom · Legende: Klick = Kurve ein/aus')])
    return html.Div([
        dbc.Row([dbc.Col(settings, md=12)], className='g-3 mb-3'),
        dbc.Row([dbc.Col(viewer, lg=8), dbc.Col(compare, lg=4)], className='g-3 mb-3'),
        dbc.Row([dbc.Col(chart, md=12)], className='g-3'),
    ])
'''

SIM_METHODS_BLOCK = r'''
def valid_speed(self, value):
    """Erlaubte Wiedergabetempi (0,25x bis 4x); alles andere ergibt 1x."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 1.0
    return value if value in SPEEDS else 1.0

def discard(self):
    """Verwirft Ergebnis und Wiedergabezustand (Einstellungen wurden geaendert)."""
    self._results = {}
    self._play = None
    self._ts_cache = None
    self._skipped = 0
    self._renders.clear()

def _timestamps(self, token, result):
    """Nicht fallende aufgezeichnete Zeitstempel (SimFrame.captured_ns) je Frame."""
    if self._ts_cache is None or self._ts_cache[0] != token:
        self._ts_cache = (token, list(itertools.accumulate((f.captured_ns for f in result.frames), max)))
    return self._ts_cache[1]

def _start_clock(self, frames, index, now, speed, timing):
    """Verankert die Medienuhr am angezeigten Frame (Start, Sprung, Tempo- oder Modus-Wechsel)."""
    self._play = {'index': index, 'media_ns': frames[index].captured_ns, 'wall': now,
                  'speed': speed, 'timing': timing}

def advance(self, result, token, index, now, speed, timing):
    """Naechster Anzeigeframe nach den aufgezeichneten Zeitstempeln; liefert (index, fertig).

    * ``realtime``: Medienzeit = Anker + Wanduhr * Tempo; es wird der letzte Frame bis zu dieser
      Zeit gezeigt, dazwischenliegende Frames werden uebersprungen.
    * ``every``: hoechstens ein Frame weiter je Aufruf, nie ueberspringen; der Anker wandert mit dem
      angezeigten Frame mit, die Wiedergabe laeuft also langsamer, wenn die Anzeige nicht hinterherkommt.
    """
    frames = result.frames
    last = len(frames) - 1
    play = self._play
    if play is None or play['index'] != index or play['speed'] != speed or play['timing'] != timing:
        self._start_clock(frames, index, now, speed, timing)
        play = self._play
    if index >= last:
        return index, True
    stamps = self._timestamps(token, result)
    media = play['media_ns'] + (now - play['wall']) * 1e9 * speed
    if timing == 'every':
        if media >= stamps[index + 1]:
            index += 1
            play.update(index=index, media_ns=stamps[index], wall=now)
    else:
        target = max(index, min(last, bisect.bisect_right(stamps, media) - 1))
        if target != index:
            self._skipped += target - index - 1
            index = target
            play['index'] = index
    return index, index >= last

def playback(self, trigger, token, speed, timing, index, paused, click=None, now=None):
    """Logik des Wiedergabe-Callbacks: (Index, Takt pausiert, Play-Beschriftung).

    Play ist nur mit gueltigem (berechnetem) Ergebnis moeglich; ein neues oder verworfenes Ergebnis
    pausiert. Am Ende wird automatisch pausiert. Tempo und Modus brauchen keine Neuberechnung.
    """
    keep = self.ui.no_update
    now = self._clock() if now is None else now
    with self._play_lock:
        entry = self.lookup(token)
        if entry is None:
            self._play = None
            return 0, True, PLAY_LABEL
        result = entry[0]
        frames = result.frames
        last = max(0, len(frames) - 1)
        index = max(0, min(int(index or 0), last))
        speed = self.valid_speed(speed)
        timing = 'every' if timing == 'every' else 'realtime'
        playing = not paused
        if trigger == 'sim-result-token':
            self._play = None
            return self.first_measured(result), True, PLAY_LABEL
        if trigger == 'sim-prev':
            return max(0, index - 1), keep, keep
        if trigger == 'sim-next':
            return min(last, index + 1), keep, keep
        if trigger == 'sim-chart':
            points = (click or {}).get('points') or [{}]
            value = points[0].get('customdata')
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return max(0, min(last, int(value))), keep, keep
            return keep, keep, keep
        if trigger == 'sim-play':
            if playing:
                self._play = None
                return keep, True, PLAY_LABEL
            if index >= last:
                index = self.first_measured(result)
            self._start_clock(frames, index, now, speed, timing)
            self._skipped = 0
            self._renders.clear()
            return index, False, PAUSE_LABEL
        if trigger in ('sim-speed', 'sim-timing'):
            if playing:
                self._start_clock(frames, index, now, speed, timing)
            return keep, keep, keep
        if trigger == 'sim-play-tick' and playing:
            new_index, finished = self.advance(result, token, index, now, speed, timing)
            if finished:
                self._play = None
            return (new_index if new_index != index else keep), (True if finished else keep), \
                (PLAY_LABEL if finished else keep)
        return keep, keep, keep

def note_render(self, frame, now=None):
    """Merkt sich einen ausgelieferten Anzeigeframe (Wanduhr, aufgezeichnete Zeit) fuer die Anzeigerate."""
    self._renders.append((self._clock() if now is None else now, frame.captured_ns))

def baseline_text(self, source):
    fps = getattr(source, 'median_fps', None)
    if not fps:
        return '–'
    return f'Aufnahme: Median {fps:.1f} Hz ({getattr(source, "fps_source", "?")})'

def rate_text(self, source):
    """Gemessene Anzeigerate (serverseitig ausgelieferte Frames/s) und effektives Tempo.

    Gemessen wird, wie oft der Server Anzeigeframes ausliefert; die Darstellung im Browser ist nicht gemessen.
    """
    parts = []
    renders = list(self._renders)
    if len(renders) >= 2 and renders[-1][0] > renders[0][0]:
        span = renders[-1][0] - renders[0][0]
        parts.append(f'Anzeige {(len(renders) - 1) / span:.1f} Frames/s (serverseitig gemessen)')
        parts.append(f'effektives Tempo {(renders[-1][1] - renders[0][1]) / 1e9 / span:.2f}x')
    else:
        parts.append('Anzeige wird gemessen')
    if self._skipped:
        parts.append(f'übersprungen {self._skipped}')
    fps = getattr(source, 'median_fps', None)
    if fps:
        parts.append(f'Aufnahme Median {fps:.1f} Hz ({getattr(source, "fps_source", "?")})')
    return ' · '.join(parts)
'''

SIM_RENDER_FRAME_BLOCK = r'''
def render_frame(self, result, source, position):
    """Damals (HUD der Aufzeichnung) und jetzt (HUD der Simulation), Kennzeichnung unten im Bild."""
    from dashboard_overlay import roi_rows
    from replay_hud import draw_hud, encode_lanes
    frame = result.frames[position]
    raw = source.image(frame.index)
    then = source.then_image(frame.index)
    top, bottom = roi_rows(SimpleNamespace(crop=frame.crop), raw.shape[0])
    row = {'frame_id': frame.frame_id, 'roi_top_px': top, 'roi_bottom_px': bottom,
           'lanes_json': encode_lanes(frame.lanes), 'vehicle_mode': 'auto',
           'data_status': 'complete' if frame.car_angle is not None else 'no_driving_state',
           'steering_angle': frame.raw_angle, 'requested_angle': frame.requested_angle,
           'steering_angle_car': frame.car_angle, 'speed': frame.speed}
    if frame.stop_reason:
        row['event_codes'] = 'STOP_REASON'
        row['stop_reason'] = frame.stop_reason
    footer = f'JETZT (Simulation) - {result.processor_key.upper()}' + (' - VORLAUF' if frame.warmup else '')
    return then, draw_hud(raw, row, footer=footer)
'''

SIM_REGISTER_BLOCK = r'''
def register(self, app):
    ui = self.ui
    Input, Output, State, no_update = ui.Input, ui.Output, ui.State, ui.no_update
    html = ui.html

    @app.callback(Output('cockpit-live-wrap', 'is_open'), Output('sim-wrap', 'is_open'),
                  Output('sim-session', 'options'), Output('sim-mode-msg', 'children'),
                  Input('cockpit-source', 'value'))
    def sim_view_mode(source):
        """Live | Simulation. Im Simulationsmodus ist START gesperrt."""
        self.active = source == 'sim'
        offline = getattr(self.application, 'offline', False)
        from session_recorder import list_sessions
        options = [{'label': 'Live-Ringbuffer (Momentaufnahme)', 'value': LIVE}] + [
            {'label': name, 'value': name} for name in list_sessions(self.root)]
        if self.active:
            message = 'SIMULATION · START gesperrt · keine Motoransteuerung'
        else:
            message = 'OFFLINE-Modus: keine Kamera, keine Motoren' if offline else ''
        return not self.active, self.active, options, message

    @app.callback(Output('sim-status', 'children'), Output('sim-stats', 'children'),
                  Output('sim-index', 'max'), Output('sim-chart', 'figure'),
                  Output('sim-result-token', 'data'),
                  Input('sim-run', 'n_clicks'),
                  Input('sim-session', 'value'), Input('sim-processor', 'value'),
                  Input('sim-params', 'value'), Input('sim-from', 'value'), Input('sim-to', 'value'),
                  Input('sim-warmup', 'value'), prevent_initial_call=True)
    def sim_run(_n, name, processor, param_mode, lo, hi, warmup):
        """Berechnen (Button) oder Ergebnis verwerfen (eine Einstellung wurde geaendert).

        Quelle, Prozessor, Parameterquelle, Frame-Bereich und Vorlauf gehoeren zum Ergebnis: Aendert der
        Nutzer eines davon, wird die Wiedergabe pausiert (Token None), das Ergebnis verworfen, die
        Anzeigen werden geleert und es erscheint der Hinweis zum Neuberechnen.
        """
        if ui.ctx.triggered_id != 'sim-run':
            self.discard()
            return (STALE_HINT, '', 1, chart_kit.empty_figure(STALE_HINT), None)
        try:
            token, result = self.simulate(name, processor, param_mode, lo, hi, warmup)
        except (RuntimeError, ValueError, OSError) as exc:
            return f'Simulation nicht möglich: {exc}', no_update, no_update, no_update, no_update
        measured = result.measured
        entry = self.lookup(token)
        source = entry[1] if entry is not None else None
        fps = getattr(source, 'median_fps', None)
        text = (f'{result.source_label} · {label_for(result.processor_key)} · Parameter '
                f"{'Studio' if result.param_mode == 'studio' else 'von damals'} · "
                f'{len(measured)} Frames (+{len(result.frames) - len(measured)} Vorlauf) in '
                f'{result.duration_s:.1f} s' + (' · ABGESCHNITTEN' if result.truncated else '')
                + (f' · {result.errors} Frames übersprungen' if result.errors else '')
                + (f' · Aufnahme Median {fps:.1f} Hz ({source.fps_source})' if fps else ''))
        if not result.frames:
            return text + ' · keine verwertbaren Frames', no_update, no_update, no_update, no_update
        return text, self.stats_table(result), max(0, len(result.frames) - 1), self.chart(result), token

    @app.callback(Output('sim-index', 'value'), Output('sim-play-tick', 'disabled'),
                  Output('sim-play', 'children'),
                  Input('sim-prev', 'n_clicks'), Input('sim-next', 'n_clicks'), Input('sim-play', 'n_clicks'),
                  Input('sim-play-tick', 'n_intervals'), Input('sim-chart', 'clickData'),
                  Input('sim-result-token', 'data'), Input('sim-speed', 'value'), Input('sim-timing', 'value'),
                  State('sim-index', 'value'), State('sim-play-tick', 'disabled'), prevent_initial_call=True)
    def sim_playback(_prev, _next, _play, _tick, click, token, speed, timing, index, paused):
        """Schritte, Sprung ins Diagramm und Wiedergabe nach den aufgezeichneten Zeitstempeln."""
        return self.playback(ui.ctx.triggered_id, token, speed, timing, index, paused, click)

    @app.callback(Output('sim-img-then', 'src'), Output('sim-img-now', 'src'),
                  Output('sim-compare', 'children'), Output('sim-frame-label', 'children'),
                  Output('sim-display-rate', 'children'),
                  Input('sim-index', 'value'), Input('sim-result-token', 'data'),
                  State('sim-play-tick', 'disabled'))
    def sim_render(index, token, paused):
        entry = self.lookup(token)
        if entry is None:
            return None, None, 'Noch keine Simulation berechnet', '', ''
        result, source = entry
        if not result.frames:
            return None, None, 'Keine Frames', '', ''
        position = max(0, min(int(index or 0), len(result.frames) - 1))
        frame = result.frames[position]
        if paused:
            # Nach einer Pause bleibt die zuletzt gemessene Rate stehen; bei neuem Ergebnis nur die Aufnahmerate.
            rate = self.baseline_text(source) if ui.ctx.triggered_id == 'sim-result-token' else no_update
        else:
            self.note_render(frame)
            rate = self.rate_text(source)
        try:
            then, now = self.render_frame(result, source, position)
        except (ValueError, OSError, cv2.error) as exc:
            return None, None, f'Bild nicht verfügbar: {exc}', f'Frame #{frame.frame_id}', rate
        label = f'Frame #{frame.frame_id}' + (' · Vorlauf' if frame.warmup else '')
        if frame.stop_reason:
            label += f' · STOPP {frame.stop_reason}'
        return self.uri(then), self.uri(now), self.frame_table(frame), html.Span(label), rate

    return self.CALLBACKS
'''

DARK_FIGURE = '''
def dark_figure(figure, **layout):
    """Dunkles Layout ohne layout.height (die feste Hoehe liegt am dcc.Graph, siehe chart_block)."""
    figure.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
                         plot_bgcolor='#111a2b', font={'color': '#e9f1ff'},
                         margin=dict(l=45, r=20, t=25, b=40),
                         legend=dict(orientation='h', y=1.12, x=0), **layout)
    return figure
'''

PLAIN_FIGURE = '''
def plain_figure(traces, **layout):
    """Dict-Figur ohne layout.height (feste Hoehe: dcc.Graph.style, siehe chart_block)."""
    base = dict(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#111a2b',
                font={'color': '#e9f1ff'}, margin=dict(l=45, r=20, t=25, b=40),
                legend=dict(orientation='h', y=1.12, x=0), xaxis=axis(), yaxis=axis())
    base.update(layout)
    return {'data': list(traces), 'layout': base}
'''

CHART_HELPERS = '''
# --------------------------------------------------------------------- PAKET-H1 Diagramme
def chart_block(graph_id, height=260, hint='Noch keine Daten', note=None):
    """Diagramm mit fester Hoehe (style.height), Vollbild-Leiste als Geschwister (chart_kit)."""
    return chart_kit.chart_block(dcc, html, graph_id, height, hint, note)


def empty_figure(text='Noch keine Daten'):
    """Dunkle leere Figur mit Hinweistext (statt des weissen Plotly-Standards)."""
    return chart_kit.empty_figure(text)


def saved_note(saved):
    """Hinweis, ob eine Einstellung dauerhaft in config.json gespeichert wurde (None = unbekannt)."""
    if saved is True:
        return ' · dauerhaft gespeichert (config.json)'
    if saved is False:
        return ' · nur bis zum Neustart (config.json nicht beschreibbar)'
    return ''


'''

LIVE_HUD_ROW = '''
def live_hud_row(camera, driving):
    """Zeile im Format von frames.csv fuer draw_hud aus Live-Daten (keine neue Erkennung)."""
    return row_from_camera(camera, driving)
'''

SETTINGS_FILL = '''
# ------------------------------------------------ PAKET-H1: Settings zeigen die aktiven (gespeicherten) Werte
QUALITY_OPTIONS = [{'label': '70 · klein', 'value': 70}, {'label': '85 · mittel', 'value': 85},
                   {'label': '95 · hoch (Standard)', 'value': 95}, {'label': '100 · maximal', 'value': 100}]

@app.callback(Output('set-speed-mode', 'value'), Output('set-speed-fixed', 'value'),
              Output('set-speed-vmin', 'value'), Output('set-speed-vmax', 'value'),
              Output('set-rec-quality', 'value'), Output('set-rec-quality', 'options'),
              Input('main-tabs', 'active_tab'))
def settings_fill(tab):
    """Beim Oeffnen des Reiters die tatsaechlich aktiven Werte zeigen (nach Neustart: gespeicherte)."""
    if tab != 'settings':
        return (no_update,) * 6
    getter = getattr(application, 'speed_policy', None)
    policy = getter() if getter is not None else None
    quality = getattr(getattr(application, 'recorder', None), 'jpeg_quality', None)
    speed = ((policy.mode, policy.fixed_speed, policy.v_min, policy.v_max) if policy is not None
             else (no_update,) * 4)
    if not isinstance(quality, int) or isinstance(quality, bool):
        return (*speed, no_update, no_update)
    options = QUALITY_OPTIONS if quality in (70, 85, 95, 100) else (
        QUALITY_OPTIONS + [{'label': f'{quality} · gespeichert', 'value': quality}])
    return (*speed, quality, options)

'''

REPLAY_CHART_HEAD = '''chart = go.Figure()
# PAKET-H1: keine layout.height; der Legendenzustand haengt an layout.uirevision (je Quelle), die
# Achsenzustaende an xaxis.uirevision (je Quelle und Bereich): eine Bereichsauswahl setzt die Achsen
# neu, ausgeblendete Kurven bleiben ausgeblendet.
dark_figure(chart, xaxis=dict(title={'text': 'Frame-ID'}, uirevision=f'{name}|{bounds}'),
            yaxis=dict(title={'text': 'Winkel'}, fixedrange=True),
            yaxis2=dict(overlaying='y', side='right', range=[0, 3], title={'text': 'Linien'},
                        fixedrange=True),
            uirevision=f'{name}')
if not records:
    return empty_figure('Keine Frames - Kamera starten oder Session wählen')'''

CSS = """
/* PAKET-H1: Diagramme mit fester Hoehe (style.height am dcc.Graph), kein Padding am Plotly-Element */
.c2c-chart-box{background:transparent}
.c2c-chart-bar{min-height:30px}
.c2c-graph{width:100%;overflow:hidden}
.c2c-chart-box:fullscreen{background:var(--c2c-bg)}
.c2c-chart-box:fullscreen .c2c-graph{height:calc(100vh - 64px)!important}
.c2c-hud-legend summary{cursor:pointer}
"""


# ------------------------------------------------------------------ models/camera_data.py
def patch_camera_data(t, f):
    return sub_once(t, 'processing_ns: int = 0\n', '''processing_ns: int = 0
    # PAKET-H1: Fahrzeugzustand im Aufnahmemoment (None = unbekannt, z. B. alte Aufnahme).
    # vehicle_mode: idle | auto | manual | recovery; Winkel in Grad; Speed = vorzeichenbehafteter Steuerwert.
    vehicle_mode: str | None = None
    vehicle_angle: float | None = None
    vehicle_speed: float | None = None
''', f)


# ------------------------------------------------------------------ CameraSensor.py
SENSOR_METHODS = '''
@property
def mode_provider(self):
    """PAKET-H1: Funktion, die (Modus, Winkel, Speed) des Fahrzeugs liefert (CarApplication.vehicle_mode)."""
    with self._lock:
        return self._mode_provider

@mode_provider.setter
def mode_provider(self, provider):
    with self._lock:
        self._mode_provider = provider
    self._mode_provider_failed = False

def _vehicle_snapshot(self):
    """PAKET-H1: (Modus, Winkel, Speed) im Aufnahmemoment oder (None, None, None).

    Fehler duerfen die Kameraschleife nie stoeren; sie werden einmalig protokolliert.
    """
    provider = self._mode_provider
    if provider is None:
        return None, None, None
    try:
        mode, angle, speed = provider()
        if mode not in ('idle', 'auto', 'manual', 'recovery'):
            mode = None
        return mode, angle, speed
    except Exception:
        if not self._mode_provider_failed:
            self._mode_provider_failed = True
            self._log.warning('Fahrzeugzustand nicht lesbar (einmalige Meldung)', exc_info=True)
        return None, None, None

'''


def patch_camera_sensor(t, f):
    t = sub_once(t, 'logger: Optional[logging.Logger] = None) -> None:',
                 'logger: Optional[logging.Logger] = None, mode_provider=None) -> None:', f)
    t = insert(t, 'self._frame_id = 0', '''
# PAKET-H1: Fahrzeugzustand je Frame (Aufnahmemoment)
self._mode_provider = mode_provider
self._mode_provider_failed = False''', f)
    t = insert(t, 'def set_processor(self, name: str):', SENSOR_METHODS, f, after=False)
    t = insert(t, 'captured_mono_ns = time.monotonic_ns()',
               'vehicle_mode, vehicle_angle, vehicle_speed = self._vehicle_snapshot()  # PAKET-H1', f)
    return sub_once(t, 'processing_ns=time.monotonic_ns() - captured_mono_ns,\n',
                    'processing_ns=time.monotonic_ns() - captured_mono_ns,\n'
                    '                    vehicle_mode=vehicle_mode, vehicle_angle=vehicle_angle,\n'
                    '                    vehicle_speed=vehicle_speed,\n', f)


# ------------------------------------------------------------------ car_application.py
APP_METHODS = '''
# ------------------------------------------------------------ Fahrzeugzustand, dauerhafte Einstellungen (PAKET-H1)
def vehicle_mode(self):
    """PAKET-H1: (Modus, Winkel, Speed) des Fahrzeugs fuer den Kamerathread (je Frame).

    Nimmt bewusst NICHT das Anwendungs-Lock: ein STOP haelt es bis zu 2 s und wuerde die
    Kameraschleife blockieren. Modus: recovery | manual (Gamepad) | auto (CameraMode) | idle.
    Winkel/Speed sind die zuletzt gesendeten Stellwerte (Speed vorzeichenbehaftet).
    """
    car = self.car
    with car._lock:
        angle = car.steering_angle
        speed = car.speed
    if self.recovery.enabled:
        mode = 'recovery'
    elif self.running():
        mode = 'auto' if self.active_mode == 'camera' else 'manual'
    else:
        mode = 'idle'
    return mode, angle, speed

def _attach_vehicle_mode(self):
    """PAKET-H1: der CameraSensor liest den Fahrzeugzustand je Frame ueber diesen Provider."""
    try:
        self.sensor.mode_provider = self.vehicle_mode
    except AttributeError:
        log.warning('Sensor ohne mode_provider - Fahrzeugzustand wird nicht aufgezeichnet')

def _apply_stored_settings(self):
    """PAKET-H1: dauerhafte Einstellungen laden; Fehler duerfen den Start nie verhindern."""
    try:
        policy = config_store.load_speed_policy()
        mode = self.modes.get('camera')
        if mode is not None and hasattr(mode, 'speed_policy'):
            mode.speed_policy = policy
        self.recorder.jpeg_quality = config_store.load_jpeg_quality()
    except Exception:
        log.exception('Gespeicherte Einstellungen nicht anwendbar - Standardwerte bleiben')

def _persist(self, action, what):
    """PAKET-H1: dauerhaft speichern. False (mit Protokoll), wenn nicht moeglich - der Wert gilt dann bis zum Neustart."""
    try:
        action()
        return True
    except (config_store.ConfigStoreError, OSError, ValueError) as exc:
        log.warning('%s nicht dauerhaft gespeichert: %s', what, exc)
        self.health.record('CONFIG_NOT_SAVED', f'{what}: {exc}')
        return False

'''


def patch_car_application(t, f):
    t = sub_once(t, "self.health.record('SPEED_POLICY', policy.describe())\n",
                 "self.health.record('SPEED_POLICY', policy.describe())\n"
                 "        return self._persist(lambda: config_store.save_speed_policy(policy), 'Geschwindigkeit')\n", f)
    t = sub_once(t, 'self.recorder.jpeg_quality = quality\n',
                 'self.recorder.jpeg_quality = quality\n'
                 "        return self._persist(lambda: config_store.save_jpeg_quality(quality), 'JPEG-Qualität')\n\n", f)
    t = insert(t, 'from driving_health import DrivingHealthMonitor', 'import config_store  # PAKET-H1', f,
               after=False)
    t = insert(t, 'self._freq_history = deque(maxlen=240)', '''
# PAKET-H1: Fahrzeugzustand fuer die Kamera (Aufnahmemoment) und gespeicherte Einstellungen
self._attach_vehicle_mode()
self._apply_stored_settings()''', f)
    return insert(t, '# ------------------------------------------------------------ Einstellungen (PAKET-G)',
                  APP_METHODS, f, after=False)


# ------------------------------------------------------------------ offline_application.py
def patch_offline(t, f):
    t = insert(t, 'from driving_health import DrivingHealthMonitor', 'import config_store  # PAKET-H1', f,
               after=False)
    t = sub_once(t, 'self._speed_policy = SpeedPolicy()',
                 'self._speed_policy = config_store.load_speed_policy()  # PAKET-H1: gespeichert oder fest 30', f)
    return sub_once(t, '        self._speed_policy = policy\n', '''        self._speed_policy = policy
        try:  # PAKET-H1: dauerhaft speichern, falls config.json vorhanden ist (auf dem Laptop oft nicht)
            config_store.save_speed_policy(policy)
            return True
        except (config_store.ConfigStoreError, OSError, ValueError):
            return False
''', f)


# ------------------------------------------------------------------ session_recorder.py
CAPTURE_STATS = '''
def _capture_stats(self):
    """PAKET-H1: Aufnahmerate aus den Kamerazeitstempeln der geschriebenen Frames (Median)."""
    times = [t for t in self._frame_times if t]
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    stats = {'frames_measured': len(times), 'median_fps': None,
             'fps_definition': 'Kehrwert des Median der Abstaende von camera_monotonic_ns '
                               'aufeinanderfolgender gespeicherter Frames'}
    if gaps:
        mid = len(gaps) // 2
        median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
        stats['median_fps'] = round(1e9 / median, 2)
    return stats

'''


def patch_session_recorder(t, f):
    t = sub_once(t, 'SCHEMA_VERSION = 2\n', '''SCHEMA_VERSION = 2
SCHEMA_REVISION = 2  # PAKET-H1: Revision 2 = + vehicle_mode, vehicle_angle, vehicle_speed (Schema bleibt 2)
REVISION_NOTE = ('Revision 1: Paket B bis G. Revision 2 (Paket H1): Spalten vehicle_mode, vehicle_angle, '
                 'vehicle_speed; Manifest capture.median_fps. Aeltere Sessions ohne diese Spalten bleiben lesbar.')
''', f)
    t = sub_once(t, "    'ultrasonic_cm', 'ultrasonic_age_ns', 'event_codes',\n)",
                 "    'ultrasonic_cm', 'ultrasonic_age_ns', 'event_codes',\n"
                 "    'vehicle_mode', 'vehicle_angle', 'vehicle_speed',\n)", f)
    t = sub_once(t, "event_codes='|'.join(events_for_frame.get(cam.frame_id, ())),\n",
                 "event_codes='|'.join(events_for_frame.get(cam.frame_id, ())),\n"
                 "            vehicle_mode=getattr(cam, 'vehicle_mode', None),\n"
                 "            vehicle_angle=getattr(cam, 'vehicle_angle', None),\n"
                 "            vehicle_speed=getattr(cam, 'vehicle_speed', None),\n", f)
    t = sub_once(t, "'schema_version': SCHEMA_VERSION,\n",
                 "'schema_version': SCHEMA_VERSION,\n"
                 "            'schema_revision': SCHEMA_REVISION,\n"
                 "            'revision_note': REVISION_NOTE,\n"
                 "            'capture': self._capture_stats(),\n", f)
    t = insert(t, 'self.count = 0', 'self._frame_times = []  # PAKET-H1: camera_monotonic_ns der geschriebenen Frames', f)
    t = insert(t, 'self.count = self.gap_frames = self.missing_decisions = self.skipped_empty = 0',
               'self._frame_times = []', f)
    t = sub_once(t, '        self.count += len(rows)\n', '''        self.count += len(rows)
        self._frame_times.extend(int(r['camera_monotonic_ns']) for r in rows
                                 if r.get('camera_monotonic_ns') not in ('', None))  # PAKET-H1
''', f)
    return insert(t, 'def _write_manifest(self, status):', CAPTURE_STATS, f, after=False)


# ------------------------------------------------------------------ sim_engine.py
def patch_sim_engine(t, f):
    t = sub_once(t, 'then_status: str | None\n', 'then_status: str | None\n'
                 '    captured_ns: int = 0  # PAKET-H1: aufgezeichneter Zeitstempel (Grundlage der Wiedergabe)\n', f)
    return sub_once(t, 'index=index, frame_id=frame.frame_id, warmup=index < first, crop=crop_from_params(params),',
                    'index=index, frame_id=frame.frame_id, captured_ns=captured, warmup=index < first,\n'
                    '                crop=crop_from_params(params),', f)


# ------------------------------------------------------------------ frame_source.py
FPS_INFO = '''
def fps_info(frames, manifest=None):
    """PAKET-H1: Median-fps der Aufnahme: aus dem Manifest (capture.median_fps) oder aus den Zeitstempeln."""
    value = ((manifest or {}).get('capture') or {}).get('median_fps')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value), 'Manifest'
    times = [f.monotonic_ns for f in frames if f.monotonic_ns]
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not gaps:
        return None, None
    mid = len(gaps) // 2
    median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
    return 1e9 / median, 'berechnet aus Zeitstempeln'

'''


def patch_frame_source(t, f):
    t = sub_once(t, 'from session_recorder import load_events, load_session\n',
                 'from session_recorder import load_events, load_manifest, load_session\n', f)
    t = insert(t, "if (fid := _int(row.get('frame_id'))) is not None and fid >= 0]", '''
self.hud_events = [{'frame_id': fid, 'code': code, 'detail': detail} for fid, code, detail in self.events]
self.median_fps, self.fps_source = fps_info(self.frames, load_manifest(name, root))  # PAKET-H1''', f,
               indent='        ')
    t = sub_once(t, 'return draw_hud(self.image(index), self._rows[index])',
                 "return draw_hud(self.image(index), self._rows[index], events=self.hud_events,\n"
                 "                        footer='DAMALS (Aufzeichnung)')", f)
    t = insert(t, 'self.events = [(e.frame_id, e.code, e.detail) for e in events if e.frame_id in ids]', '''
self.hud_events = [{'frame_id': fid, 'code': code, 'detail': detail} for fid, code, detail in self.events]
self.median_fps, self.fps_source = fps_info(self.frames, None)  # PAKET-H1''', f)
    t = replace_to_end(t, 'from dashboard_overlay import render_calibration_overlay, render_overlay', '''
        from replay_hud import draw_hud, row_from_camera  # PAKET-H1: HUD wie im Replay
        record = self._records[index]
        row = row_from_camera(record.camera, record.driving, events=self.hud_events)
        return draw_hud(record.camera.raw_image, row, events=self.hud_events, footer='DAMALS (Ringbuffer)')''', f,
                       'return render_calibration_overlay(record.camera)')
    return insert(t, '@dataclass(frozen=True)', FPS_INFO, f, after=False, indent='', blank_after=2)


# ------------------------------------------------------------------ tests/test_static_checks.py
WALK = '''
def _py_files():
    """PAKET-H1: Dateisuche mit Verzeichnis-Pruning (rglob durchlief frueher auch .venv; auf dem Pi
    brach der Test im Paket-C-Installer nach 121 s mit rc=124 ab)."""
    skip = {'.venv', 'venv', 'legacy', 'camp2code', '__pycache__', '.git', 'node_modules', 'Data_AI'}
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if name.endswith('.py'):
                yield Path(folder) / name

'''


def patch_test_static(t, f):
    t = sub_once(t, 'import ast\nimport unittest\n', 'import ast\nimport os\nimport unittest\n', f)
    t = sub_once(t, '''    for path in sorted(p for p in ROOT.rglob('*.py')
                       if not {'.venv', 'venv', 'legacy', 'camp2code', '__pycache__', '.git'}
                       & set(p.relative_to(ROOT).parts)):
''', '''    for path in sorted(_py_files()):  # PAKET-H1
''', f)
    return insert(t, 'def project_files(include_deleted=False):', WALK, f, after=False, indent='')



def indented(block, prefix):
    return '\n'.join((prefix + line) if line.strip() else '' for line in block.strip('\n').split('\n'))


# ------------------------------------------------------------------ replay_hud.py
def patch_replay_hud(t, f):
    t = sub_once(t, 'Das Rohbild wird nie veraendert; gezeichnet wird auf einer Kopie.\n',
                 'Das Rohbild wird nie veraendert; gezeichnet wird auf einer Kopie.\n\n'
                 'PAKET-H1: Zustandsanzeige statt N/A (GESTOPPT > BERGUNG/MANUELL > AUTONOM > STEHT > KEIN\n'
                 'FAHRMODUS), ROH > STELL (SOLL nur bei Abweichung), vorzeichenbehafteter Steuer-Speed,\n'
                 'ASCII-Texte, Kennzeichnung unten im Bild, einklappbare Legende.\n', f)
    return replace_to_end(t, 'def draw_hud(image, row):', HUD_BLOCK, f, 'return result')


# ------------------------------------------------------------------ help_texts.py
def patch_help_texts(t, f):
    t = sub_once(t, 'Ein Text je Schluessel: Titel, Beschreibung, Herkunft (gemessen/berechnet/Steuerwert/Einstellung).\n',
                 'Ein Text je Schluessel: Titel, Herkunft (gemessen/berechnet/Befehlswert/Einstellung) und genau EIN\n'
                 'Satz (PAKET-H1): was der Wert bedeutet, Einheit, wichtigste Einflussfaktoren.\n', f)
    return replace_to_end(t, 'HELP = {', HELP_BLOCK, f, 'def label(text, key, dbc, html')


# ------------------------------------------------------------------ assets/studio.css
def patch_css(t, f):
    return t.rstrip('\n') + '\n' + CSS


# ------------------------------------------------------------------ sim_lab.py
def patch_sim_lab(t, f):
    t = sub_once(t, 'import logging\nimport threading\nimport time\n',
                 'import bisect\nimport itertools\nimport logging\nimport threading\nimport time\n'
                 'from collections import deque\nfrom types import SimpleNamespace\n', f)
    t = sub_once(t, 'from processor_registry import label_for, processor_options\n',
                 'import chart_kit  # PAKET-H1\nfrom processor_registry import label_for, processor_options\n', f)
    t = insert(t, "LIVE = '__live__'", '''
# PAKET-H1: Wiedergabe nach aufgezeichneten Zeitstempeln
PLAY_TICK_MS = 150   # Anzeigetakt; bei 150 ms sind hoechstens etwa 6 bis 7 Anzeigeframes/s moeglich
SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
SPEED_LABELS = (('0,25x', 0.25), ('0,5x', 0.5), ('1x', 1.0), ('2x', 2.0), ('4x', 4.0))
PLAY_LABEL = '▶ Play'
PAUSE_LABEL = '⏸ Pause'
STALE_HINT = 'Einstellungen geändert - bitte neu berechnen'
''', f)
    t = sub_once(t, "CALLBACKS = ('sim_view_mode', 'sim_run', 'sim_seek', 'sim_play_toggle', 'sim_render')",
                 "CALLBACKS = ('sim_view_mode', 'sim_run', 'sim_playback', 'sim_render')  # PAKET-H1", f)
    t = insert(t, 'self._engine = None', '''
self._play = None            # PAKET-H1: Medienuhr der Wiedergabe (Anker, Tempo, Modus)
self._play_lock = threading.Lock()
self._ts_cache = None
self._skipped = 0
self._renders = deque(maxlen=20)
self._clock = time.monotonic''', f)
    t = sub_once(t, "dcc.Interval(id='sim-play-tick', interval=250, n_intervals=0, disabled=True),",
                 "dcc.Interval(id='sim-play-tick', interval=PLAY_TICK_MS, n_intervals=0, disabled=True),", f)
    t = replace_block(t, 'def _panel(dbc, dcc, html):',
                      '------ Logik',
                      indented(SIM_PANEL_BLOCK, '    '), f, gap=1)
    t = insert(t, 'def chart(self, result):', SIM_METHODS_BLOCK, f, after=False)
    t = sub_once(t, "plot_bgcolor='#111a2b', height=280,", "plot_bgcolor='#111a2b',", f)
    t = sub_once(t, "uirevision=f'sim-{result.source_label}')", "uirevision=f'sim-{id(result)}')", f)
    t = replace_block(t, 'def render_frame(self, result, source, position):',
                      '------ Callbacks',
                      indented(SIM_RENDER_FRAME_BLOCK, '    '), f, gap=1)
    return replace_to_end(t, 'def register(self, app):', indented(SIM_REGISTER_BLOCK, '    '), f,
                          'return self.CALLBACKS')


# ------------------------------------------------------------------ Camp2CodeDashboard.py
def patch_dashboard(t, f):
    t = sub_once(t, 'from replay_hud import draw_hud\n',
                 'from replay_hud import draw_hud, legend_block, row_from_camera  # PAKET-H1\n'
                 'import chart_kit  # PAKET-H1\n', f)
    t = replace_block(t, 'def dark_figure(figure, height=260, **layout):', "def fmt(value, digits=0, unit=''):",
                      DARK_FIGURE, f)
    t = replace_block(t, 'def plain_figure(traces, height=260, **layout):', 'def thin(rows, limit=240):',
                      PLAIN_FIGURE, f)
    t = insert(t, 'PAKET-G Hilfen', CHART_HELPERS, f, after=False, indent='', blank_before=2, blank_after=2)
    t = replace_block(t, 'def live_hud_row(camera, driving):', 'def lane_quality(records):', LIVE_HUD_ROW, f)
    # Layout: Diagramme mit fester Hoehe und Vollbild-Leiste, leere dunkle Figuren
    t = sub_once(t, "dcc.Graph(id='freq-bars', config={'displayModeBar': False})",
                 "chart_block('freq-bars', 240, 'Noch keine Messwerte')", f)
    t = sub_once(t, "dcc.Graph(id='freq-history', config={'displayModeBar': False})",
                 "chart_block('freq-history', 240, 'Noch keine Frequenzdaten')", f)
    t = sub_once(t, "dcc.Graph(id='drive-history', config={'displayModeBar': False})",
                 "chart_block('drive-history', 300, 'Noch keine Fahrdaten - START im Cockpit')", f)
    t = sub_once(t, """            dcc.Graph(id='replay-chart', config={'displayModeBar': False}),
            html.Div('Klick auf Messpunkt = Frame wählen · Ziehen = Bereich wählen · Doppelklick = alles',
                     className='small text-secondary')]), md=12)], className='mt-3'),""",
                 """            chart_block('replay-chart', 260, 'Noch keine Daten',
                        note='Klick auf Messpunkt = Frame wählen · Ziehen oder Mausrad = Bereich wählen · '
                             'Doppelklick = alles · Legende: Klick = Kurve ein/aus')]), md=12)], className='mt-3'),""", f)
    # HUD-Legende (einklappbar) unter Cockpit-Bild und Replay-Bild
    t = sub_once(t, "html.Img(id='drive-overlay', style={**IMAGE_STYLE, 'maxHeight': '68vh'}),\n",
                 "html.Img(id='drive-overlay', style={**IMAGE_STYLE, 'maxHeight': '68vh'}),\n"
                 "            legend_block(html),  # PAKET-H1\n", f)
    t = sub_once(t, "html.Img(id='replay-image', style={**IMAGE_STYLE, 'maxHeight': '60vh'}),\n",
                 "html.Img(id='replay-image', style={**IMAGE_STYLE, 'maxHeight': '60vh'}),\n"
                 "                legend_block(html),  # PAKET-H1\n", f)
    t = sub_once(t, "fs_button('replay-image')], className='mb-2'),",
                 "fs_button('replay-image'),\n                          *info('hud', dbc, html, suffix='replay')], className='mb-2'),", f)
    t = sub_once(t, "'Nur im Stillstand · gilt bis zum Neustart · Werte sind Steuerwerte (PWM).'",
                 "'Nur im Stillstand · wird dauerhaft in config.json gespeichert · Werte sind Steuerwerte (PWM).'", f)
    # Settings: Rueckmeldung zur Speicherung
    t = sub_once(t, """            setter(policy)
            messages.append(html.Div(f'✔ Geschwindigkeit: {policy.describe()}', className='text-success'))""",
                 """            saved = setter(policy)
            messages.append(html.Div(f'✔ Geschwindigkeit: {policy.describe()}' + saved_note(saved),
                                     className='text-success'))""", f)
    t = sub_once(t, """            setter(quality)
            messages.append(html.Div(f'✔ JPEG-Qualität {quality} ab der nächsten Aufnahme', className='text-success'))""",
                 """            saved = setter(quality)
            messages.append(html.Div(f'✔ JPEG-Qualität {quality} ab der nächsten Aufnahme' + saved_note(saved),
                                     className='text-success'))""", f)
    t = insert(t, 'Aktionen (ein Callback)',
               SETTINGS_FILL, f, after=False)
    # Telemetrie: keine Hoehe in der Figur, leere Figuren mit Hinweis
    t = sub_once(t, "240, barmode='group', yaxis=axis('Hz'), uirevision='bars')",
                 "barmode='group', yaxis=axis('Hz'), uirevision='bars')", f)
    t = sub_once(t, "fplot = plain_figure(ftraces, 240, xaxis=axis('Sekunden'), yaxis=axis('Hz'), uirevision='freq')",
                 "fplot = (plain_figure(ftraces, xaxis=axis('Sekunden'), yaxis=axis('Hz'), uirevision='freq')\n"
                 "                         if ftraces else empty_figure('Noch keine Frequenzdaten'))", f)
    t = sub_once(t, """                dplot = plain_figure(dtraces, 300, xaxis=axis('Sekunden', range=[-CHART_WINDOW_S, 0]),
                                     yaxis=axis('Grad / Steuerwert'),
                                     yaxis2=axis('Linien', overlaying='y', side='right', range=[0, 3]),
                                     uirevision='drive')""",
                 """                dplot = (plain_figure(dtraces, xaxis=axis('Sekunden', range=[-CHART_WINDOW_S, 0]),
                                      yaxis=axis('Grad / Steuerwert'),
                                      yaxis2=axis('Linien', overlaying='y', side='right', range=[0, 3]),
                                      uirevision='drive')
                         if dtraces else empty_figure('Noch keine Fahrdaten - START im Cockpit'))""", f)
    # Replay: HUD mit Ereignissen (Stopp-Ursache), auch fuer den Live-Ringbuffer
    t = sub_once(t, """_, _, path, record = replay_record_at(name, bounds, index)
            if record is None:
                return None, 'Keine Frames'""",
                 """_, events, path, record = replay_record_at(name, bounds, index)
            if record is None:
                return None, 'Keine Frames'""", f)
    t = sub_once(t, 'return uri(draw_hud(image, record), 80), f"Frame #{record.get(\'frame_id\')}", details',
                 'return uri(draw_hud(image, record, events=events), 80), f"Frame #{record.get(\'frame_id\')}", details', f)
    t = sub_once(t, """            image = (render_overlay(record.camera, record.driving) if record.driving
                     else render_calibration_overlay(record.camera))
""", """            # PAKET-H1: HUD auch fuer den Live-Ringbuffer (Zustand, Stopp-Ursache aus den Ereignissen)
            image = draw_hud(record.camera.raw_image,
                             row_from_camera(record.camera, record.driving, events=events), events=events)
""", f)
    return sub_once(t, """chart = go.Figure()
            dark_figure(chart, 240, xaxis_title='Frame-ID', yaxis_title='Winkel',
                        yaxis2=dict(overlaying='y', side='right', range=[0, 3], title='Linien'),
                        uirevision=f'{name}|{bounds}')
            if not records:
                return chart""", indented(REPLAY_CHART_HEAD, '            ').lstrip(), f)


# ------------------------------------------------------------------ Tests und Doku
def patch_test_callbacks(t, f):
    t = sub_once(t, "'sim_seek', 'sim_play_toggle', 'sim_render',", "'sim_playback', 'sim_render',", f)
    return sub_once(t, "'settings_apply'}", "'settings_apply', 'settings_fill'}  # PAKET-H1", f)


def patch_test_paket_f(t, f):
    t = sub_once(t, "status, stats, maximum, chart, token = self.cb['sim_run'](1, self.name, 'mock', 'recorded', 40, 50, 5)",
                 "self.ctx.set('sim-run')  # PAKET-H1\n"
                 "        status, stats, maximum, chart, token = self.cb['sim_run'](1, self.name, 'mock', 'recorded', 40, 50, 5)", f)
    t = sub_once(t, "index = self.cb['sim_seek'](None, None, None, None, token, 0)",
                 "index, _paused, _label = self.cb['sim_playback'](None, None, None, None, None, token, 1.0,\n"
                 "                                                         'realtime', 0, True)", f)
    t = sub_once(t, "then, now, table, label = self.cb['sim_render'](index, token)",
                 "then, now, table, label, _rate = self.cb['sim_render'](index, token, True)", f)
    return sub_once(t, "status = self.cb['sim_run'](1, self.name, 'copilot', 'recorded', None, None, 0)[0]",
                    "self.ctx.set('sim-run')\n"
                    "        status = self.cb['sim_run'](1, self.name, 'copilot', 'recorded', None, None, 0)[0]", f)


CHANGELOG = '''
## 6.6 – Paket H1 (PAKET-H1)
* **Diagramme:** feste Höhe nur als `style.height` am `dcc.Graph` (kein `layout.height`, kein Padding am
  Plotly-Element), Vollbild-Schalter als Geschwister in einer Leiste über dem Diagramm, jedes Diagramm startet
  mit einer dunklen leeren Figur samt Hinweis. Interaktiv: Legende klicken = Kurve ein/aus, Doppelklick =
  nur diese Kurve, Mausrad-Zoom, Werkzeugleiste (Zoom, Verschieben, Reset, PNG), Doppelklick = alles.
  Zoom und Legendenzustand überleben Live-Updates (`uirevision`). Replay: Zoom/Ziehen bleibt die
  Bereichsauswahl (nur X-Achse). Ursache des „wachsenden leeren Diagramms“ nicht am Code verifiziert
  (der frühere Chart-Zusatz ist nicht im Stand 062f360), siehe `chart_kit.py`.
* **HUD** (`replay_hud.py`, Replay, Cockpit, Simulation damals und jetzt): Zustandsanzeige statt N/A
  (GESTOPPT: Ursache > BERGUNG/MANUELL > AUTONOM > AUTONOM/ENTSCHEIDUNG FEHLT > STEHT > KEIN FAHRMODUS),
  Speed standardmäßig sichtbar und vorzeichenbehaftet (Steuerwert), ROH > STELL, SOLL nur bei ≥ 1° Abweichung,
  fehlende Werte ausgeblendet, ASCII-Texte, Kennzeichnung unten im Bild, einklappbare Legende.
  Neue Felder `CameraData.vehicle_mode/vehicle_angle/vehicle_speed` (Aufnahmemoment, über
  `CameraSensor.mode_provider` = `CarApplication.vehicle_mode()`), neue Spalten in `frames.csv`
  (Schema 2, Revision 2; alte Sessions bleiben lesbar → KEIN FAHRMODUS).
* **Simulation:** Änderung von Quelle, Prozessor, Parameterquelle, Frame-Bereich oder Vorlauf pausiert,
  verwirft das Ergebnis und leert die Anzeigen („Einstellungen geändert - bitte neu berechnen“). Tempo
  0,25x/0,5x/1x/2x/4x nach den aufgezeichneten Zeitstempeln (`SimFrame.captured_ns`), automatische Pause am Ende,
  Schalter Echtzeit (überspringt) / Jeder Frame (überspringt nichts), gemessene Anzeigerate.
  Median-fps der Aufnahme im `manifest.json` (`capture.median_fps`).
* **Einstellungen dauerhaft:** `config_store.py` speichert Geschwindigkeit (`cameracar_controller.speed_*`) und
  JPEG-Qualität (`session_recorder.jpeg_quality`) atomar in `config.json` (Sicherung in
  `Data_AI/config_backups`, letzte 5), lädt beim Start und fällt bei Fehlern auf fest 30 / 95 zurück.
* **Hilfetexte** (`help_texts.py`): jeder Text genau ein Satz mit Herkunft gemessen/berechnet/Befehlswert/Einstellung.
* **Tests:** `test_static_checks` durchsucht Verzeichnisse mit Pruning (rglob durchlief `.venv`).
'''

SIM_DOC = '''

## Wiedergabe (PAKET-H1)

* **Tempo** 0,25x / 0,5x / 1x / 2x / 4x bezieht sich auf die aufgezeichneten Zeitstempel der Frames
  (`SimFrame.captured_ns`), nicht auf feste fps; Aufnahmen haben ca. 13–16 Hz, schwankend. Tempowechsel
  braucht keine Neuberechnung. Am Ende wird automatisch pausiert.
* **Echtzeit:** zeitgenau, Anzeigeframes dürfen übersprungen werden. **Jeder Frame:** nichts wird übersprungen,
  die Wiedergabe läuft langsamer, wenn die Anzeige nicht hinterherkommt.
* **Anzeigerate** (Frames/s) wird serverseitig gemessen (ausgelieferte Frames). Der Anzeigetakt beträgt
  150 ms (`PLAY_TICK_MS`), daher sind höchstens etwa 6–7 Anzeigeframes/s möglich. Die Darstellung im Browser
  ist nicht gemessen.
* Änderungen an Quelle, Prozessor, Parameterquelle, Frame-Bereich oder Vorlauf verwerfen das Ergebnis; Play ist
  erst nach erfolgreicher Berechnung möglich.
'''

ARCH_NOTE = ('Sessions v1 (V5.4/5.5) werden gelesen; ohne ROI-Angabe zeichnet das HUD keine Linien.\n'
             '**Revision 2 (PAKET-H1):** zusaetzliche Spalten `vehicle_mode` (idle/auto/manual/recovery), `vehicle_angle`,\n'
             '`vehicle_speed` (Fahrzeugzustand im Aufnahmemoment) und `manifest.capture.median_fps`; Schema bleibt 2,\n'
             'ältere Sessions ohne diese Spalten bleiben lesbar (HUD: KEIN FAHRMODUS).')


def patch_architecture(t, f):
    return sub_once(t, 'Sessions v1 (V5.4/5.5) werden gelesen; ohne ROI-Angabe zeichnet das HUD keine Linien.', ARCH_NOTE, f)


PATCHES = {
    'models/camera_data.py': patch_camera_data,
    'CameraSensor.py': patch_camera_sensor,
    'car_application.py': patch_car_application,
    'offline_application.py': patch_offline,
    'session_recorder.py': patch_session_recorder,
    'sim_engine.py': patch_sim_engine,
    'frame_source.py': patch_frame_source,
    'replay_hud.py': patch_replay_hud,
    'help_texts.py': patch_help_texts,
    'sim_lab.py': patch_sim_lab,
    'Camp2CodeDashboard.py': patch_dashboard,
    'assets/studio.css': patch_css,
    'tests/test_static_checks.py': patch_test_static,
    'tests/test_dashboard_callbacks.py': patch_test_callbacks,
    'tests/test_paket_f.py': patch_test_paket_f,
    'CHANGELOG.md': lambda t, f: insert(t, '# Changelog', CHANGELOG, f, blank_after=1),
    'SIMULATION.md': lambda t, f: t.rstrip('\n') + '\n' + SIM_DOC,
    'ARCHITECTURE.md': patch_architecture,
}


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    results = {}
    try:
        for name, func in PATCHES.items():
            path = root / name
            text = path.read_text(encoding='utf-8')
            if MARKER in text:
                print(f'  = {name} (bereits gepatcht)')
                continue
            new = func(text, name)
            if MARKER not in new:
                raise PatchError(f'{name}: Marker fehlt nach dem Patch')
            if name.endswith('.py'):
                compile(new, name, 'exec')
            results[path] = new
    except (PatchError, SyntaxError, OSError) as exc:
        print(f'PATCH ABGEBROCHEN, keine Datei geaendert: {exc}', file=sys.stderr)
        return 1
    for path, new in results.items():
        path.write_text(new, encoding='utf-8', newline='\n')
        print(f'  + {path.relative_to(root)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
