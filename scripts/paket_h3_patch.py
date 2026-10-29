#!/usr/bin/env python3
"""Paket H3: getrennte Aufloesungen fuer Aufnahme (gross) und Bildverarbeitung (klein) ueber den
Picamera2-Dual-Stream, Kamera-Metadaten, Linien-Umrechnung, Sessions Revision 4.

Setzt Paket H2/H2b voraus. Verankerte Aenderungen; fehlt ein Anker oder ist er mehrdeutig, wird KEINE Datei
geaendert (alles oder nichts). Dateien mit dem Marker PAKET-H3 werden uebersprungen (idempotent); der Marker wird
vollstaendig geprueft (PAKET-H3 ist Teilstring von PAKET-H3B).

Sonderfall ``basisklassen_cam.py``: Die Datei im Bigpack hat unregelmaessige Leerzeilen (Zeilenenden). Sie wird
deshalb NICHT per Anker geaendert, sondern durch ``files/basisklassen_cam.py`` des Pakets ersetzt - aber nur, wenn
der Inhalt (ohne Leerzeilen und Einrueckungs-/Randleerzeichen) dem erwarteten Stand entspricht (Pruefsumme).

Aufruf: python scripts/paket_h3_patch.py [REPO_ROOT] [PAKET_VERZEICHNIS]
"""
import hashlib
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r'PAKET-H3(?![A-Za-z0-9])')
CAM_FILE = 'basisklassen_cam.py'
# Pruefsumme der Zeilen (ohne Leerzeilen, jede Zeile gestrippt) der erwarteten Vorlage (Bigpack-Stand)
CAM_FINGERPRINT = 'a85a076588526aa7'


class PatchError(Exception):
    pass


def has_marker(text):
    return bool(MARKER_RE.search(text))


def _hits(lines, needle, start=0):
    return [i for i in range(start, len(lines)) if needle in lines[i]]


def _one(lines, needle, file, start=0):
    hits = _hits(lines, needle, start)
    if len(hits) != 1:
        raise PatchError(f'{file}: Anker {needle!r} {len(hits)}x gefunden (erwartet 1)')
    return hits[0]


def sub_once(text, old, new, file):
    count = text.count(old)
    if count != 1:
        raise PatchError(f'{file}: Text {old[:70]!r} {count}x gefunden (erwartet 1)')
    return text.replace(old, new)


def insert_before(text, needle, block, file, gap=1):
    """Fuegt ``block`` (mit eigener Einrueckung) vor der eindeutigen Zeile mit ``needle`` ein (``gap`` Leerzeilen danach)."""
    lines = text.split('\n')
    i = _one(lines, needle, file)
    return '\n'.join(lines[:i] + block.strip('\n').split('\n') + [''] * gap + lines[i:])


def replace_block(text, start_needle, end_needle, block, file, gap=2):
    """Ersetzt die Zeilen ab ``start_needle`` (inklusive) bis vor ``end_needle`` (exklusive)."""
    lines = text.split('\n')
    s = _one(lines, start_needle, file)
    e = _one(lines, end_needle, file, start=s + 1)
    return '\n'.join(lines[:s] + block.strip('\n').split('\n') + [''] * gap + lines[e:])


def replace_to_end(text, start_needle, block, file, tail_check):
    lines = text.split('\n')
    s = _one(lines, start_needle, file)
    if tail_check not in '\n'.join(lines[s:]):
        raise PatchError(f'{file}: Dateiende nach {start_needle!r} entspricht nicht der Erwartung ({tail_check!r})')
    return '\n'.join(lines[:s]) + '\n' + block.strip('\n') + '\n'


# ------------------------------------------------------------------ CameraSensor.py
SENSOR_METHODS = '''
    def _small_image_state(self):
        """PAKET-H3: (Groesse, Herkunft) des kleinen Bildes aus der Kamera; (None, None) ohne kleines Bild."""
        getter = getattr(self, 'get_small_size', None)
        size = getter() if callable(getter) else None
        if size:
            return tuple(size), getattr(self, 'small_source', None) or 'software'
        return None, None

    def _grab(self):
        """PAKET-H3: ``(grosses Bild, kleines Bild oder None, Metadaten oder None)`` aus EINER Aufnahme."""
        getter = getattr(self, 'get_frames', None)
        if getter is None:                       # Kamera-Attrappe ohne Dual-Stream-Methoden
            return self.get_frame(), None, None
        image, small = getter()
        reader = getattr(self, 'get_metadata', None)
        return image, small, (reader() if callable(reader) else None)

    def camera_info(self):
        """PAKET-H3: Groessen und Einstellungen der Kamera (fuer Ringbuffer-Laenge, Recorder-Manifest, Diagnose).

        ``frame_rate_set`` ist die feste Bildrate der KAMERA (FrameDurationLimits, None = Automatik) und nicht
        der Schleifentakt ``scan_frequency``. ``process_size`` ist None, wenn auf dem Aufnahmebild verarbeitet wird.
        """
        size = None
        getter = getattr(self, 'get_size', None)
        if callable(getter):
            try:
                size = tuple(getter())
            except (TypeError, ValueError):
                size = None
            if not size or len(size) != 2 or size[0] <= 0 or size[1] <= 0:
                size = None
        settings = getattr(self, '_camera_settings', None)
        return {'capture_size': size, 'process_size': self._process_size,
                'process_source': self._process_source or 'none',
                'frame_rate_set': (settings.frame_rate or None) if settings is not None else None,
                'downscale': settings.downscale if settings is not None else None,
                'exposure_value': settings.exposure_value if settings is not None else None}

'''

SENSOR_INIT_NEW = '''        # PAKET-H3: Aufnahmegroesse (width/height), Verarbeitungsgroesse (process_width/process_height), Dual-Stream
        # (downscale), Kamera-Bildrate (frame_rate) und Belichtungskorrektur (exposure_value) aus cam_sensor.
        # Ohne neue Eintraege wird die Kamera exakt wie bisher erzeugt. Fehlerhafte Werte ergeben Standardwerte.
        settings = safe_camera_settings(self._cfg, self._log)
        small_size, hint = resolve_process_size((settings.width, settings.height),
                                                settings.process_width, settings.process_height)
        if hint:
            self._log.warning('Verarbeitungsgroesse: %s', hint)
        camera_args = dict(colorspace=colorspace, height=settings.height, width=settings.width)
        if small_size is not None:
            camera_args.update(small_size=small_size, small_mode=settings.downscale)
        if settings.frame_rate:
            camera_args['frame_rate'] = settings.frame_rate
        super().__init__(**camera_args)
        self._camera_settings = settings
        self._process_size, self._process_source = self._small_image_state()
        self._last_sensor_ts = None
        self._log.info('Aufnahme %s, Verarbeitung %s (%s), Kamera-Bildrate %s', self._capture_size_text(),
                       self._process_size or 'wie Aufnahme', self._process_source or 'kein Verkleinern',
                       f'{settings.frame_rate:g} fps' if settings.frame_rate else 'automatisch')
        try:
            self.set_camera_controls({
                "AeEnable": True,
                "ExposureValue": settings.exposure_value,
            })
'''


def patch_sensor(t, f):
    t = sub_once(t, 'from basisklassen_cam import Camera\n',
                 'from basisklassen_cam import Camera\n'
                 'from camera_geometry import resolve_process_size  # PAKET-H3\n'
                 'from camera_settings import safe_camera_settings\n', f)
    t = sub_once(t, """        super().__init__(colorspace=colorspace,
                         height=self._cfg.get_int('height', 480),
                         width=self._cfg.get_int('width', 640))
        try:
            self.set_camera_controls({
                "AeEnable": True,
                "ExposureValue": 1.0,
            })
""", SENSOR_INIT_NEW, f)
    t = insert_before(t, '    def set_processor(self, name: str):',
                      SENSOR_METHODS + '''    def _capture_size_text(self):
        getter = getattr(self, 'get_size', None)
        try:
            return tuple(getter()) if callable(getter) else 'unbekannt'
        except TypeError:
            return 'unbekannt'
''', f)
    t = sub_once(t, '                image = self.get_frame()\n',
                 '                image, small, meta = self._grab()  # PAKET-H3: grosses + kleines Bild + Metadaten\n', f)
    t = sub_once(t, '''                # Parameter und Prozessor fuer diesen Frame einmalig lesen.
''', '''                # PAKET-H3: dieselbe Aufnahme (gleicher Sensor-Zeitstempel) nie ein zweites Mal verarbeiten.
                sensor_ts = meta.get('sensor_timestamp_ns') if meta else None
                if sensor_ts is not None and sensor_ts == self._last_sensor_ts:
                    stop_event.wait(0.002)
                    continue
                self._last_sensor_ts = sensor_ts
                # Parameter und Prozessor fuer diesen Frame einmalig lesen.
''', f)
    t = sub_once(t, '                result = processor.process(image, parameters)\n',
                 '                # PAKET-H3: Bildverarbeitung auf dem kleinen Bild; Aufnahme/Anzeige nutzen das grosse.\n'
                 '                process_image = small if small is not None else image\n'
                 '                result = processor.process(process_image, parameters)\n', f)
    t = sub_once(t, '                    vehicle_mode_key=mode_key\n                )\n',
                 '                    vehicle_mode_key=mode_key,\n'
                 '                    process_size=(process_image.shape[1], process_image.shape[0]),\n'
                 "                    process_source=self._process_source or 'none',\n"
                 "                    exposure_us=(meta or {}).get('exposure_us'), gain=(meta or {}).get('gain'),\n"
                 "                    frame_duration_us=(meta or {}).get('frame_duration_us'),\n"
                 "                    lux=(meta or {}).get('lux')\n"
                 '                )\n', f)
    return sub_once(t, '                period = 1.0 / max(1, self.scan_frequency)\n',
                    '                # scan_frequency = Takt dieser Schleife; frame_rate/FrameDurationLimits = Takt der KAMERA.\n'
                    '                # PAKET-H3: die Schleife wird nie schneller getaktet, als die Kamera liefert.\n'
                    '                period = 1.0 / max(1, self.scan_frequency)\n'
                    "                frame_us = (meta or {}).get('frame_duration_us')\n"
                    '                if frame_us:\n'
                    '                    period = max(period, frame_us / 1e6)\n', f)


# ------------------------------------------------------------------ models/camera_data.py
def patch_camera_data(t, f):
    return sub_once(t, '    vehicle_mode_key: str | None = None\n\n    def roi_rows',
                    '''    vehicle_mode_key: str | None = None
    # PAKET-H3: getrennte Aufnahme-/Verarbeitungsgroesse. ``raw_image`` ist das grosse Aufnahmebild;
    # ``process_size`` = (Breite, Hoehe) des Bildes, auf dem der Prozessor die Linien ermittelt hat. ``lanes``
    # (und ``DrivingState.lanes``) gelten in DIESEM Bild; zum Zeichnen auf dem grossen Bild werden sie umgerechnet
    # (camera_geometry.convert_lanes). None = unbekannt (aeltere Daten): dann gelten die Linien im Aufnahmebild.
    process_size: tuple[int, int] | None = None
    process_source: str | None = None       # 'camera' (zweiter Stream) | 'software' | 'none' (nicht verkleinert)
    # Kamera-Metadaten dieser Aufnahme (None = unbekannt)
    exposure_us: float | None = None        # Belichtungszeit in Mikrosekunden
    gain: float | None = None               # analoge Verstaerkung
    frame_duration_us: float | None = None  # Bilddauer in Mikrosekunden
    lux: float | None = None

    def roi_rows''', f)


# ------------------------------------------------------------------ telemetry/ring_buffer.py
RESIZE = '''
    def resize(self, maxlen: int) -> None:
        """PAKET-H3: Laenge aendern (RAM-Budget je nach Bildgroesse); behaelt die neuesten Eintraege."""
        if maxlen < 1:
            raise ValueError("maxlen muss positiv sein")
        with self._lock:
            if maxlen != self._items.maxlen:
                self._items = deque(self._items, maxlen=maxlen)
'''


def patch_ring_buffer(t, f):
    return sub_once(t, '    def add_camera(self, data: CameraData) -> None:', RESIZE.strip('\n') + '\n\n'
                    '    def add_camera(self, data: CameraData) -> None:', f)


# ------------------------------------------------------------------ car_application.py
FIT_RING = '''
    def _fit_ring_buffer(self):
        """PAKET-H3: Ringbuffer-Laenge an die Bildgroesse anpassen (RAM-Budget) und die Kamerainfo an den Recorder
        geben (Manifest). Fehler duerfen den Start nie verhindern; ohne Kamerainfo bleibt alles wie bisher."""
        try:
            getter = getattr(self.sensor, 'camera_info', None)
            info = getter() if callable(getter) else None
            if not isinstance(info, dict):
                return
            try:
                self.recorder.camera_info = getter
            except AttributeError:
                pass
            capture = info.get('capture_size')
            history = self.car.frame_history
            if not capture or not callable(getattr(history, 'resize', None)):
                return
            length = camera_geometry.ring_length(capture, info.get('process_size'))
            if length != getattr(history, 'maxlen', length):
                history.resize(length)
                log.info('Ringbuffer: %d Frames (Aufnahme %s, Verarbeitung %s)', length, capture,
                         info.get('process_size') or 'wie Aufnahme')
        except Exception:
            log.exception('Ringbuffer-Laenge nicht anpassbar - Standardwert bleibt')
'''


def patch_car_application(t, f):
    t = sub_once(t, 'import system_stats  # PAKET-H2\n',
                 'import system_stats  # PAKET-H2\nimport camera_geometry  # PAKET-H3\n', f)
    t = sub_once(t, '        self._attach_vehicle_mode()\n',
                 '        self._attach_vehicle_mode()\n        self._fit_ring_buffer()  # PAKET-H3\n', f)
    return insert_before(t, '    def _attach_vehicle_mode(self):', FIT_RING, f)


# ------------------------------------------------------------------ dashboard_overlay.py
OVERLAY_HELPERS = '''
def _px(image, base):
    """PAKET-H3: Linien-/Schriftstaerke in Pixeln, an die Bildhoehe angepasst (bis 240 Pixel wie bisher)."""
    return max(1, int(round(base * ui_scale(image.shape[0]))))


def _at(image, x, y):
    """PAKET-H3: Textposition, an die Bildhoehe angepasst (bis 240 Pixel wie bisher)."""
    k = ui_scale(image.shape[0])
    return int(round(x * k)), int(round(y * k))


def lanes_for_raw(camera, lanes, shape, top):
    """PAKET-H3: Linien aus dem Verarbeitungsbild in das Bild der Form ``shape`` (grosses Aufnahmebild) umrechnen.

    ``camera.process_size`` ist die Groesse des Bildes, in dem die Linien gelten; ``top`` ist die obere ROI-Zeile
    im Zielbild. Ohne ``process_size`` (aeltere Daten) oder bei gleicher Groesse bleiben die Linien unveraendert.
    Die gespeicherten Linien werden nie geaendert (es entsteht ein neues Tupel).
    """
    size = getattr(camera, 'process_size', None)
    height, width = shape[:2]
    if not size or tuple(size) == (width, height):
        return tuple(lanes or ())
    src_top = roi_top_px(size[1], getattr(camera, 'crop', (0.1, 0.9)))
    return convert_lanes(lanes, (size[0], size[1]), (width, height), src_top, top)
'''

PUT_TEXT = '''
def put_text(image, text, origin, scale=0.48, color=TEXT_COLOR):
    """Text mit schwarzer Kontur. Die Kontur ist derselbe duenne Text in acht Nachbarpositionen,
    damit Kontur und Fuellung denselben Zeichenabstand haben (Dicke 3 verschob die Buchstaben).

    PAKET-H3: Schriftgroesse, Strichstaerke und Konturabstand wachsen mit der Bildhoehe (bis 240 Pixel
    unveraendert); ``origin`` ist bereits in Bildpixeln.
    """
    text = str(text)
    k = ui_scale(image.shape[0])
    x, y = int(origin[0]), int(origin[1])
    step = max(1, int(round(k)))
    thickness = max(1, int(round(k)))
    for dx, dy in _OUTLINE_OFFSETS:
        cv2.putText(image, text, (x + dx * step, y + dy * step), cv2.FONT_HERSHEY_SIMPLEX, scale * k, (0, 0, 0),
                    thickness, cv2.LINE_AA)
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale * k, color, thickness, cv2.LINE_AA)
'''


def patch_overlay(t, f):
    t = sub_once(t, 'from LenkwinkelKalkulator import steering_line_from_lanes\n',
                 'from LenkwinkelKalkulator import steering_line_from_lanes\n'
                 'from camera_geometry import convert_lanes, roi_top_px, ui_scale  # PAKET-H3\n', f)
    t = sub_once(t, 'TEXT_COLOR = (245, 245, 245)\n', 'TEXT_COLOR = (245, 245, 245)\n\n\n' + OVERLAY_HELPERS.strip('\n') + '\n', f)
    t = sub_once(t, 'cv2.rectangle(image, (0, top), (w - 1, max(top, bottom - 1)), ROI_COLOR, 1)',
                 'cv2.rectangle(image, (0, top), (w - 1, max(top, bottom - 1)), ROI_COLOR, _px(image, 1))', f)
    t = sub_once(t, '    roi_h = max(1, bottom - top)\n    drawn = 0\n',
                 '    roi_h = max(1, bottom - top)\n    thickness = _px(image, thickness)  # PAKET-H3\n    drawn = 0\n', f)
    t = sub_once(t, 'STEERING_LINE_COLOR, 2, cv2.LINE_AA)', 'STEERING_LINE_COLOR, _px(image, 2), cv2.LINE_AA)', f)
    t = replace_block(t, 'def put_text(image, text, origin', 'def render_overlay(camera, driving, processed=False):',
                      PUT_TEXT, f)
    t = sub_once(t, '    lanes = driving.lanes if driving.lanes is not None else ()\n',
                 '    lanes = driving.lanes if driving.lanes is not None else ()\n'
                 '    if not processed:   # PAKET-H3: Linien gelten im Verarbeitungsbild\n'
                 '        lanes = lanes_for_raw(camera, lanes, result.shape, top)\n', f)
    t = sub_once(t, "    put_text(result, '  '.join(parts), (8, 22))",
                 "    put_text(result, '  '.join(parts), _at(result, 8, 22))", f)
    t = sub_once(t, '    lanes = tuple(camera.lanes or ())\n',
                 '    lanes = tuple(camera.lanes or ())\n'
                 '    if not processed:   # PAKET-H3: Linien gelten im Verarbeitungsbild\n'
                 '        lanes = lanes_for_raw(camera, lanes, result.shape, top)\n', f)
    t = sub_once(t, "    put_text(result, f'Frame {camera.frame_id}  Linien {count}', (8, 22))",
                 "    put_text(result, f'Frame {camera.frame_id}  Linien {count}', _at(result, 8, 22))", f)
    return sub_once(t, "    put_text(result, 'OHNE FAHRENTSCHEIDUNG', (8, max(45, h - 12)), 0.45, (0, 255, 255))",
                    "    put_text(result, 'OHNE FAHRENTSCHEIDUNG',\n"
                    "             (_at(result, 8, 0)[0], max(_at(result, 0, 45)[1], h - _px(result, 12))), 0.45, (0, 255, 255))", f)


# ------------------------------------------------------------------ dashboard_steering.py
def patch_steering(t, f):
    t = sub_once(t, 'import cv2\n', 'import cv2\n\nfrom camera_geometry import ui_scale  # PAKET-H3\n', f)
    t = sub_once(t, '    radius = max(15, min(h, w) // 13)\n',
                 '    k = ui_scale(h)   # PAKET-H3: Strichstaerke und Schrift wachsen mit der Bildhoehe\n'
                 '    radius = max(15, min(h, w) // 13)\n', f)
    t = sub_once(t, '(160, 180, 200), 2, cv2.LINE_AA)', '(160, 180, 200), max(1, round(2 * k)), cv2.LINE_AA)', f)
    t = sub_once(t, '(0, 220, 255), 3, cv2.LINE_AA)', '(0, 220, 255), max(1, round(3 * k)), cv2.LINE_AA)', f)
    return sub_once(t, '''    cv2.putText(result, label, (max(5, center[0] - radius), center[1] + radius + 20),
                cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1, cv2.LINE_AA)''',
                    '''    cv2.putText(result, label, (max(5, center[0] - radius), center[1] + radius + int(20 * k)),
                cv2.FONT_HERSHEY_SIMPLEX, .45 * k, (255, 255, 255), max(1, round(k)), cv2.LINE_AA)''', f)


# ------------------------------------------------------------------ replay_hud.py
HUD_NEW = '''
def _text_width(text, scale, k=1.0):
    return cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale * k, max(1, int(round(k))))[0][0]


def _lanes_on_image(row, lanes, width, height, top):
    """PAKET-H3: Linien einer Zeile in das gespeicherte (grosse) Bild umrechnen.

    Die Linien in frames.csv gelten im Verarbeitungsbild (``process_width`` x ``process_height``). Fehlen diese
    Spalten (Sessions vor Revision 4) oder stimmen sie mit dem Bild ueberein, bleiben die Linien unveraendert.
    ``top`` ist die obere ROI-Zeile im gespeicherten Bild (``roi_top_px``).
    """
    pw, ph = _integer(row, 'process_width'), _integer(row, 'process_height')
    if not pw or not ph or pw <= 0 or ph <= 0 or (pw, ph) == (width, height):
        return lanes
    high = _number(row, 'roi_high')
    src_top = max(0, min(ph, int(ph * (1 - high)))) if high is not None else int(round(top * ph / height))
    return list(convert_lanes(lanes, (pw, ph), (width, height), src_top, top))


def draw_hud(image, row, events=None, footer=None):
    """Zeigt gespeicherte Linien, Winkel und Zustand; keine neuen Messwerte.

    ``events``: Ereignisse (dicts oder HealthEvent) fuer die Stopp-Ursache. ``footer``: Kennzeichnung unten
    im Bild (z. B. "DAMALS", "JETZT (Simulation)"), damit sie das HUD oben nicht ueberlagert.
    Alle Texte sind ASCII (cv2.putText).

    PAKET-H3: Schrift, Linienstaerke und Abstaende wachsen mit der Bildhoehe (bis 240 Pixel wie bisher); Linien aus
    dem Verarbeitungsbild werden auf das grosse Bild umgerechnet (nur fuer die Anzeige, gespeicherte Werte bleiben).
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return image
    result = image.copy()
    height, width = result.shape[:2]
    k = ui_scale(height)

    def px(value):
        return int(round(value * k))

    lanes = decode_lanes(row)
    top, bottom = _integer(row, 'roi_top_px'), _integer(row, 'roi_bottom_px')
    geometry_known = top is not None and bottom is not None and 0 <= top < bottom <= height
    if geometry_known:
        lanes = _lanes_on_image(row, lanes, width, height, top)
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
        cv2.arrowedLine(result, origin, target, (40, 210, 255), px(3), cv2.LINE_AA, tipLength=.13)
    shade = result.copy()
    cv2.rectangle(shade, (0, 0), (width, min(height, px(76))), (12, 20, 32), -1)
    cv2.addWeighted(shade, .68, result, .32, 0, result)
    frame_text = 'FRAME ' + ascii_text(row.get('frame_id', '?'))
    put_text(result, frame_text, (px(12), px(23)), .6)
    if speed is not None:
        speed_text = 'STEUER-SPEED ' + signed(speed)
        put_text(result, speed_text, (max(px(12) + _text_width(frame_text, .6, k) + px(12),
                                          width - _text_width(speed_text, .45, k) - px(10)), px(23)), .45)
    line = angle_line(stell, soll, roh)
    if line:
        put_text(result, line, (px(12), px(46)), .45)
    label, kind = vehicle_state(row, events)
    label = ascii_text(label)
    key = row.get('vehicle_mode_key') or None
    name = ascii_text(mode_registry.short_label(key)) if key else ''
    if name:
        x = width - _text_width(name, .45, k) - px(10)
        if x > px(12) + _text_width(line, .45, k) + px(10):      # nie die Winkelzeile ueberlagern
            put_text(result, name, (x, px(46)), .45, (255, 220, 160))
    put_text(result, label, (px(12), px(67)), .5, STATE_COLORS[kind])
    if not geometry_known and any(lane is not None for lane in lanes):
        put_text(result, 'LINIEN: ROI UNBEKANNT', (px(12) + _text_width(label, .5, k) + px(12), px(67)), .4)
    base = height
    if footer:
        cv2.rectangle(result, (0, height - px(22)), (width, height), (40, 28, 12), -1)
        put_text(result, ascii_text(footer)[:70], (px(10), height - px(7)), .45, (0, 255, 255))
        base = height - px(22)
    if row.get('event_codes'):
        cv2.rectangle(result, (0, base - px(32)), (width, base), (15, 30, 100), -1)
        put_text(result, 'EVENT: ' + ascii_text(row['event_codes'])[:55], (px(10), base - px(10)), .45)
    return result
'''


def patch_hud(t, f):
    t = sub_once(t, 'from dashboard_overlay import draw_lanes, draw_roi, draw_steering_line, put_text\n',
                 'from camera_geometry import convert_lanes, ui_scale  # PAKET-H3\n'
                 'from dashboard_overlay import draw_lanes, draw_roi, draw_steering_line, put_text\n', f)
    t = sub_once(t, "'vehicle_mode_key': getattr(camera, 'vehicle_mode_key', None)}\n",
                 "'vehicle_mode_key': getattr(camera, 'vehicle_mode_key', None),\n"
                 "           # PAKET-H3: Linien gelten im Verarbeitungsbild (Groesse + ROI-Anteil fuer die Umrechnung)\n"
                 "           'process_width': (getattr(camera, 'process_size', None) or (None, None))[0],\n"
                 "           'process_height': (getattr(camera, 'process_size', None) or (None, None))[1],\n"
                 "           'roi_high': getattr(camera, 'crop', (None, None))[1]}\n", f)
    return replace_to_end(t, 'def _text_width(text, scale):', HUD_NEW, f, 'return result')


# ------------------------------------------------------------------ dashboard/helpers.py
def patch_helpers(t, f):
    t = sub_once(t, '''def uri(image, quality=70):
    """Kodiert ein Bild als JPEG-Data-URI (eine Kodierung je Bild und Tick)."""
    if image is None or getattr(image, 'size', 0) == 0:
        return None
''', '''MAX_UI_WIDTH = 1280      # PAKET-H3: breitere Bilder werden nur fuer die Uebertragung an den Browser verkleinert


def uri(image, quality=70, max_width=None):
    """Kodiert ein Bild als JPEG-Data-URI (eine Kodierung je Bild und Tick).

    PAKET-H3: Bilder breiter als ``max_width`` (Standard ``MAX_UI_WIDTH``) werden NUR fuer die Uebertragung an den
    Browser verkleinert (INTER_AREA); das Original (Ringbuffer, Aufnahme) bleibt unveraendert.
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return None
    limit = MAX_UI_WIDTH if max_width is None else max_width
    if limit and image.shape[1] > limit:
        scale = limit / image.shape[1]
        image = cv2.resize(image, (int(limit), max(1, int(round(image.shape[0] * scale)))),
                           interpolation=cv2.INTER_AREA)
''', f)
    return sub_once(t, '''        top, bottom = camera.roi_rows(raw.shape[0])
        total += 1
        good += lanes_plausible(camera.lanes, raw.shape[1], max(1, bottom - top))
''', '''        size = getattr(camera, 'process_size', None)   # PAKET-H3: Linien gelten im Verarbeitungsbild
        width, height = (size[0], size[1]) if size else (raw.shape[1], raw.shape[0])
        top, bottom = camera.roi_rows(height)
        total += 1
        good += lanes_plausible(camera.lanes, width, max(1, bottom - top))
''', f)


# ------------------------------------------------------------------ dashboard/tabs/pipeline.py, replay.py
def patch_pipeline_tab(t, f):
    t = sub_once(t, '                result = create_processor(diagnostic).process(raw, params)\n',
                 '                # PAKET-H3: auf der damaligen Verarbeitungsgroesse rechnen (Software-Verkleinern)\n'
                 '                result = create_processor(diagnostic).process(ab.downscale(raw), params)\n', f)
    return sub_once(t, '''            view = SimpleNamespace(raw_image=raw, frame_id=frame_id, crop=crop, lanes=result.lanes,
                                   process_image=result.processed_image)
''', '''            view = SimpleNamespace(raw_image=raw, frame_id=frame_id, crop=crop, lanes=result.lanes,
                                   process_image=result.processed_image, process_size=ab.process_size())
''', f)


RECORD_SIZE = '''
def record_process_size(record):
    """PAKET-H3: Verarbeitungsgroesse eines Replay-Datensatzes (frames.csv-Zeile oder FrameRecord); None = unbekannt."""
    if isinstance(record, dict):
        try:
            width, height = int(float(record.get('process_width'))), int(float(record.get('process_height')))
        except (TypeError, ValueError):
            return None
        return (width, height) if width > 0 and height > 0 else None
    size = getattr(getattr(record, 'camera', None), 'process_size', None)
    return (int(size[0]), int(size[1])) if size else None
'''


def patch_replay_tab(t, f):
    t = sub_once(t, 'ab.freeze(SimpleNamespace(raw_image=image, frame_id=frame_id), params, processor)',
                 'ab.freeze(SimpleNamespace(raw_image=image, frame_id=frame_id,\n'
                 '                                          process_size=record_process_size(record)), params, processor)', f)
    return insert_before(t, 'class ReplayTab(DashboardTab):', RECORD_SIZE, f, gap=2)


# ------------------------------------------------------------------ pipeline_ab.py
AB_METHODS = '''
    def process_size(self):
        """PAKET-H3: Verarbeitungsgroesse des eingefrorenen Frames ((Breite, Hoehe) oder None)."""
        with self._lock:
            return self._process_size

    def downscale(self, raw):
        """PAKET-H3: ``raw`` auf die Verarbeitungsgroesse des eingefrorenen Frames verkleinern (Software, INTER_AREA)."""
        with self._lock:
            return downscale_image(raw, self._process_size)
'''


def patch_pipeline_ab(t, f):
    t = sub_once(t, 'from pipeline_processors import CopilotLaneProcessor, LaneProcessor\n',
                 'from camera_geometry import downscale_image  # PAKET-H3\n'
                 'from pipeline_processors import CopilotLaneProcessor, LaneProcessor\n', f)
    t = sub_once(t, "        self._processor = 'copilot'\n", "        self._processor = 'copilot'\n        self._process_size = None  # PAKET-H3\n", f)
    t = sub_once(t, '            self._processor = processor\n            return self._frame_id\n',
                 '            self._processor = processor\n'
                 "            size = getattr(camera, 'process_size', None)   # PAKET-H3: A/B rechnet auf der damaligen Groesse\n"
                 '            self._process_size = (int(size[0]), int(size[1])) if size else None\n'
                 '            return self._frame_id\n', f)
    t = sub_once(t, '            self._reference = None\n            self._frame_id = None\n',
                 '            self._reference = None\n            self._frame_id = None\n            self._process_size = None\n', f)
    t = sub_once(t, '            raw = self._raw.copy()\n',
                 '            raw = downscale_image(self._raw.copy(), self._process_size)  # PAKET-H3\n', f)
    return sub_once(t, '    def clear(self):\n', '\n' + AB_METHODS.strip('\n') + '\n\n    def clear(self):\n', f)


# ------------------------------------------------------------------ frame_source.py
FS_HELPERS = '''
def _row_size(row):
    """PAKET-H3: Verarbeitungsgroesse einer frames.csv-Zeile; None bei Sessions vor Revision 4 (nie raten)."""
    width, height = _int(row.get('process_width')), _int(row.get('process_height'))
    return (width, height) if width and height and width > 0 and height > 0 else None


def _cam_size(cam):
    size = getattr(cam, 'process_size', None)
    return (int(size[0]), int(size[1])) if size else None
'''


def patch_frame_source(t, f):
    t = sub_once(t, '    then_lanes: tuple = field(default_factory=tuple)\n',
                 '    then_lanes: tuple = field(default_factory=tuple)\n'
                 '    process_size: tuple | None = None   # PAKET-H3: Groesse des Verarbeitungsbilds (None = unbekannt)\n', f)
    t = insert_before(t, 'def fps_info(frames, manifest=None):', FS_HELPERS, f, gap=2)
    t = sub_once(t, "            then_status=row.get('data_status') or None,\n            then_lanes=lanes,\n",
                 "            then_status=row.get('data_status') or None,\n            then_lanes=lanes,\n"
                 "            process_size=_row_size(row),\n", f)
    return sub_once(t, "            then_status='complete' if drive is not None else 'no_driving_state',\n            then_lanes=lanes,\n",
                    "            then_status='complete' if drive is not None else 'no_driving_state',\n            then_lanes=lanes,\n"
                    "            process_size=_cam_size(cam),\n", f)


# ------------------------------------------------------------------ sim_engine.py, sim_lab.py
def patch_sim_engine(t, f):
    t = sub_once(t, 'from replay_car import ReplayCar\n',
                 'from replay_car import ReplayCar\nfrom camera_geometry import downscale_image  # PAKET-H3\n', f)
    t = sub_once(t, '    captured_ns: int = 0  # PAKET-H1: aufgezeichneter Zeitstempel (Grundlage der Wiedergabe)\n',
                 '    captured_ns: int = 0  # PAKET-H1: aufgezeichneter Zeitstempel (Grundlage der Wiedergabe)\n'
                 '    process_size: tuple | None = None  # PAKET-H3: Groesse des Verarbeitungsbilds (Linien gelten dort)\n', f)
    t = sub_once(t, '            t0 = time.perf_counter()\n            try:\n                processed = processor.process(raw, params)\n',
                 '            t0 = time.perf_counter()\n'
                 '            # PAKET-H3: auf der damaligen Verarbeitungsgroesse rechnen. Das Verkleinern erfolgt per Software\n'
                 '            # aus dem gespeicherten JPEG; das damalige Kamerabild kam bei Dual-Stream aus dem zweiten\n'
                 '            # Kamera-Stream - kleine Pixelabweichungen sind moeglich (siehe SIMULATION.md).\n'
                 '            small = downscale_image(raw, frame.process_size)\n'
                 '            try:\n                processed = processor.process(small, params)\n', f)
    t = sub_once(t, 'processing_parameters=params, processor_name=type(processor).__name__)',
                 "processing_parameters=params, processor_name=type(processor).__name__,\n"
                 "                process_size=(small.shape[1], small.shape[0]), process_source='software')", f)
    t = sub_once(t, '            w, h = roi_size(params, raw.shape)\n', '            w, h = roi_size(params, small.shape)  # PAKET-H3\n', f)
    return sub_once(t, 'then_plausible=then_plausible, then_status=frame.then_status))',
                    'then_plausible=then_plausible, then_status=frame.then_status,\n'
                    '                process_size=(small.shape[1], small.shape[0])))', f)


def patch_sim_lab(t, f):
    return sub_once(t, "'steering_angle_car': frame.car_angle, 'speed': frame.speed, 'vehicle_mode_key': 'camera'}",
                    "'steering_angle_car': frame.car_angle, 'speed': frame.speed, 'vehicle_mode_key': 'camera',\n"
                    "                 # PAKET-H3: Linien der Simulation gelten im Verarbeitungsbild\n"
                    "                 'process_width': (frame.process_size or (None, None))[0],\n"
                    "                 'process_height': (frame.process_size or (None, None))[1], 'roi_high': frame.crop[1]}", f)


# ------------------------------------------------------------------ session_recorder.py
CAMERA_MANIFEST = '''
    def _camera_manifest(self):
        """PAKET-H3: Aufloesungen sowie eingestellte und gemessene Bildrate der Kamera fuer das Manifest."""
        info = {}
        provider = self.camera_info
        if callable(provider):
            try:
                info = dict(provider() or {})
            except Exception:
                info = {}
        seen = self._camera_seen or {}

        def pair(width, height):
            try:
                return [int(width), int(height)] if width not in (None, '') and height not in (None, '') else None
            except (TypeError, ValueError):
                return None

        rate = info.get('frame_rate_set')
        return {
            'capture_size': pair(seen.get('image_width'), seen.get('image_height'))
            or (list(info['capture_size']) if info.get('capture_size') else None),
            'process_size': pair(seen.get('process_width'), seen.get('process_height')),
            'process_source': seen.get('process_source') or None,
            'frame_rate_set_hz': rate,
            'frame_rate_mode': ('fest' if rate else 'automatisch') if 'frame_rate_set' in info else 'unbekannt',
            'frame_rate_measured_median_hz': self._capture_stats()['median_fps'],
            'exposure_value': info.get('exposure_value'),
            'downscale': info.get('downscale'),
            'note': ('Linien in frames.csv gelten im Verarbeitungsbild (process_width x process_height), nicht im '
                     'gespeicherten Bild. process_source: camera = zweiter Kamera-Stream (YUV420), software = '
                     'verkleinert, none = nicht verkleinert. Aus dem gespeicherten Bild per Software verkleinerte '
                     'Bilder (Simulation) weichen von einem Kamera-Stream leicht ab. Gemessene Rate: Median aus '
                     'den Zeitstempeln der gespeicherten Frames.'),
        }
'''


def patch_recorder(t, f):
    t = sub_once(t, 'SCHEMA_REVISION = 3\n', 'SCHEMA_REVISION = 4\n', f)
    t = sub_once(t, "                 'Aeltere Sessions ohne diese Spalten bleiben lesbar; ohne vehicle_mode_key zeigt das HUD keinen Modusnamen.')",
                 "                 'Revision 4 (Paket H3): Spalten process_width, process_height, process_source, exposure_us, gain; '\n"
                 "                 'Linien (lanes_json, lane0_m ...) gelten im Verarbeitungsbild; Manifest camera (Aufloesungen, '\n"
                 "                 'Bildrate). '\n"
                 "                 'Aeltere Sessions ohne diese Spalten bleiben lesbar (fehlende Werte = unbekannt); ohne '\n"
                 "                 'vehicle_mode_key zeigt das HUD keinen Modusnamen.')", f)
    t = sub_once(t, "    'vehicle_mode', 'vehicle_angle', 'vehicle_speed', 'vehicle_mode_key'\n)",
                 "    'vehicle_mode', 'vehicle_angle', 'vehicle_speed', 'vehicle_mode_key',\n"
                 "    'process_width', 'process_height', 'process_source', 'exposure_us', 'gain'   # PAKET-H3 (Revision 4)\n)", f)
    t = sub_once(t, "        self._frame_ids = set()  # PAKET-H2: IDs der geschriebenen Frames (has_frame)\n",
                 "        self._frame_ids = set()  # PAKET-H2: IDs der geschriebenen Frames (has_frame)\n"
                 "        self.camera_info = None   # PAKET-H3: optionale Funktion -> dict (CameraSensor.camera_info)\n"
                 "        self._camera_seen = None  # PAKET-H3: Groessen des zuletzt geschriebenen Frames\n", f)
    t = sub_once(t, "            vehicle_mode_key=getattr(cam, 'vehicle_mode_key', None),\n",
                 "            vehicle_mode_key=getattr(cam, 'vehicle_mode_key', None),\n"
                 "            process_width=(getattr(cam, 'process_size', None) or (None, None))[0],   # PAKET-H3\n"
                 "            process_height=(getattr(cam, 'process_size', None) or (None, None))[1],\n"
                 "            process_source=getattr(cam, 'process_source', None),\n"
                 "            exposure_us=getattr(cam, 'exposure_us', None), gain=getattr(cam, 'gain', None),\n", f)
    t = sub_once(t, '        self.count += len(rows)\n',
                 '        self.count += len(rows)\n'
                 '        if rows:   # PAKET-H3: Groessen fuer das Manifest\n'
                 '            self._camera_seen = {key: rows[-1].get(key) for key in (\n'
                 "                'image_width', 'image_height', 'process_width', 'process_height', 'process_source')}\n", f)
    t = sub_once(t, "            'capture': self._capture_stats(),\n",
                 "            'capture': self._capture_stats(),\n            'camera': self._camera_manifest(),  # PAKET-H3\n", f)
    t = sub_once(t, "            'lane_model': 'y = m*x + b; x,y in Pixeln im ROI; Vollbild: y_voll = y + roi_top_px',",
                 "            'lane_model': 'y = m*x + b; x,y in Pixeln im ROI; Vollbild: y_voll = y + roi_top_px; '\n"
                 "                          'ab Revision 4 im Verarbeitungsbild (process_width x process_height), ROI-Versatz dort '\n"
                 "                          'int(process_height * (1 - roi_high))',", f)
    return insert_before(t, '    def _write_manifest(self, status):', CAMERA_MANIFEST, f)


# ------------------------------------------------------------------ Konfiguration, Doku, Tests
def patch_config_example(t, f):
    return sub_once(t, '        "hough_threshold": 30\n',
                    '        "hough_threshold": 30,\n'
                    '        "frame_rate": 0,\n'
                    '        "exposure_value": 1.0,\n'
                    '        "downscale": "auto"\n', f)


CHANGELOG = '''
## 6.10 – Paket H3 (PAKET-H3)
* **Dual-Stream** (`basisklassen_cam.py`, optional): grosses Bild („main“, RGB888) für Aufnahme und Anzeige, kleines Bild
  („lores“, YUV420, Zeilen-Stride beachtet) für die Bildverarbeitung, beide aus DERSELBEN Aufnahme (`capture_arrays`);
  neue Methoden `get_frames()`, `get_metadata()`, `get_small_size()`, `get_sizes()`, `get_stream_info()`,
  `sensor_modes()` (aus Picamera2, nichts festgeschrieben), `set_frame_rate()`, `set_exposure_value()`. Lehnt die Kamera
  die Konfiguration ab oder ist das Backend OpenCV/USB, verkleinert die Software (INTER_AREA) mit Hinweis im Log. Der
  Konstruktor ohne neue Argumente und alle bisherigen Methoden verhalten sich unverändert.
* **`cam_sensor` in `config.json`** (nur dort einstellbar; Settings-Oberfläche folgt in H3b): `width`/`height` = Aufnahme;
  neu `process_width`/`process_height` (Verarbeitung, nicht größer als die Aufnahme, gleiches Seitenverhältnis),
  `downscale` = `auto|camera|software` (Standard `auto`: Dual-Stream, sonst Software), `frame_rate` (feste Kamera-fps über
  FrameDurationLimits, 0/fehlend = Automatik), `exposure_value` (Standard 1.0). `scan_frequency` (Takt der Kameraschleife)
  und `frame_rate` (Takt der Kamera) sind verschieden; die Schleife wird nie schneller getaktet, als die Kamera liefert,
  und verarbeitet dieselbe Aufnahme (gleicher Sensor-Zeitstempel) nie zweimal. Ohne neue Einträge bleibt das Verhalten
  wie bisher; fehlerhafte Werte ergeben Standardwerte (`camera_settings.py`).
* **Datenmodell:** `CameraData.process_size`, `.process_source`, `.exposure_us`, `.gain`, `.frame_duration_us`, `.lux`
  (Standardwerte, alte Aufrufer bleiben gültig). Linien gelten im VERARBEITUNGSBILD; zum Zeichnen auf dem großen Bild
  rechnet `camera_geometry.convert_lanes` um (Overlay, HUD, Replay, Simulation, Pipeline Studio, Spurqualität). Gespeicherte
  Werte werden nie geändert. Schrift und Linienstärke wachsen mit der Bildhöhe (bis 240 Pixel unverändert); Bilder breiter
  als 1280 Pixel werden nur für die Übertragung an den Browser verkleinert. Ringbuffer-Länge nach RAM-Budget
  (`camera_geometry.ring_length`, 60 Frames bei 320x240 wie bisher, bei größeren Bildern weniger, mindestens 10).
* **Sessions Revision 4:** neue Spalten `process_width`, `process_height`, `process_source`, `exposure_us`, `gain`; Manifest
  `camera` (Aufloesungen, eingestellte und gemessene Bildrate). Revision 2 und 3 bleiben lesbar (fehlende Spalten = unbekannt).
* **Simulation und A/B-Labor** rechnen auf der damaligen Verarbeitungsgröße (Software-Verkleinern aus dem gespeicherten
  Bild; kleine Abweichung zum damaligen Kamerabild, siehe SIMULATION.md).
* `scripts/cam_check.py` prüft auf dem Pi Sensor-Modi und Dual-Stream ohne Fahren. Nicht enthalten (H3b): Settings-Karte,
  „Übernehmen + Neustart“, Telemetrie-Kachel.
'''

ARCH_SECTION = '''
## 4b. Aufnahme- und Verarbeitungsbild (PAKET-H3)
`CameraData.raw_image` ist das große Aufnahmebild (Anzeige, Aufnahme), die Bildverarbeitung läuft auf dem kleinen Bild
(`CameraData.process_size`, Herkunft `process_source`: `camera` = zweiter Kamera-Stream, `software`, `none`). Die Linien
(`lanes`, `DrivingState.lanes`, `frames.csv`) gelten im **Verarbeitungsbild**, ROI-Versatz dort `int(process_height ·
(1 − crop_oben))`. Umrechnung auf das große Bild (`y = m·x + b`, `sx = Breite_groß/Breite_klein`, `sy = Höhe_groß/Höhe_klein`):
`m' = m·sy/sx`, `b' = sy·b + sy·top_klein − top_groß` (`camera_geometry.convert_lanes`). Die Fahrregelung (`CameraMode`) rechnet
unverändert im Verarbeitungsbild. Konfiguration: `cam_sensor.*` (siehe CHANGELOG 6.10); `scan_frequency` = Schleifentakt,
`frame_rate` = Kamera-Bildrate. Ringbuffer-Länge nach RAM-Budget (60 Frames bei 320x240).

'''

SIM_SECTION = '''
## Verarbeitungsgröße (PAKET-H3)
Aufnahmen ab Revision 4 speichern das große Bild (`image_width` x `image_height`) und die Verarbeitungsgröße
(`process_width` x `process_height`). Die Simulation und das A/B-Labor verkleinern das gespeicherte Bild per Software
(INTER_AREA) auf diese Größe und rechnen dort; Linien gelten im Verarbeitungsbild und werden nur für die Anzeige auf das große
Bild umgerechnet. **Abweichung zur Aufnahme:** Kam das damalige kleine Bild aus dem zweiten Kamera-Stream (`process_source =
camera`, ISP-Skalierung und YUV420), unterscheidet es sich leicht von dem aus dem JPEG verkleinerten Bild (Pixelwerte, Kanten,
JPEG-Artefakte); einzelne Linien und Winkel können minimal abweichen. Sessions ohne diese Spalten (Revision 2 und 3) werden
wie bisher auf dem gespeicherten Bild gerechnet.
'''


def patch_changelog(t, f):
    return sub_once(t, '## 6.9 – Paket H2 (PAKET-H2)\n', CHANGELOG.strip('\n') + '\n\n## 6.9 – Paket H2 (PAKET-H2)\n', f)


def patch_architecture(t, f):
    t = sub_once(t, '## 5. Threads und Lebenszyklus\n', ARCH_SECTION.strip('\n') + '\n\n## 5. Threads und Lebenszyklus\n', f)
    return sub_once(t, 'RB[FrameRingBuffer\\n60 FrameRecords]',
                    'RB[FrameRingBuffer\\n60 FrameRecords (PAKET-H3: nach Bildgroesse)]', f)


def patch_simulation_doc(t, f):
    return t.rstrip('\n') + '\n\n' + SIM_SECTION.strip('\n') + '\n'


STUB_ADDITIONS = '''
    # PAKET-H3: Dual-Stream-Methoden der Kamera-Attrappe (nur aktiv, wenn small_size uebergeben wird)
    Camera.dual_ok = True                    # False = "Konfiguration abgelehnt" (Software-Verkleinern)
    Camera.metadata = {'exposure_us': None, 'gain': None, 'digital_gain': None, 'frame_duration_us': None,
                       'lux': None, 'sensor_timestamp_ns': None}

    def _small_size(self):
        size = self.init_kwargs.get('small_size')
        return tuple(size) if size else None

    def _get_frames(self):
        import cv2
        main = self.get_frame()
        size = _small_size(self)
        small = cv2.resize(main, size, interpolation=cv2.INTER_AREA) if size else None
        return main, small

    def _get_size(self):
        return (self.init_kwargs.get('width') or 640, self.init_kwargs.get('height') or 480)

    Camera.get_frames = _get_frames
    Camera.get_metadata = lambda self: dict(self.metadata)
    Camera.get_small_size = _small_size
    Camera.get_size = _get_size
    Camera.small_source = property(lambda self: (('camera' if self.dual_ok else 'software') if _small_size(self) else None))
'''


def patch_stubs(t, f):
    t = sub_once(t, '            self.released = 0\n',
                 '            self.released = 0\n            self.init_kwargs = dict(kwargs)   # PAKET-H3\n', f)
    return sub_once(t, '    cam.Camera = Camera\n', STUB_ADDITIONS.strip('\n') + '\n    cam.Camera = Camera\n', f)


def patch_test_h1(t, f):
    t = sub_once(t, "self.assertEqual(FIELDS[-4:], ('vehicle_mode', 'vehicle_angle', 'vehicle_speed', 'vehicle_mode_key'))",
                 "self.assertEqual(FIELDS[-9:-5], ('vehicle_mode', 'vehicle_angle', 'vehicle_speed', 'vehicle_mode_key'))  # PAKET-H3", f)
    return sub_once(t, "self.assertEqual(manifest['schema_revision'], 3)", "self.assertEqual(manifest['schema_revision'], 4)  # PAKET-H3", f)


def patch_test_h2b(t, f):
    t = sub_once(t, "self.assertEqual(FIELDS[-1], 'vehicle_mode_key')", "self.assertIn('vehicle_mode_key', FIELDS)  # PAKET-H3", f)
    return sub_once(t, "self.assertEqual(manifest['schema_revision'], 3)", "self.assertEqual(manifest['schema_revision'], 4)  # PAKET-H3", f)


PATCHES = {
    'CameraSensor.py': patch_sensor,
    'models/camera_data.py': patch_camera_data,
    'telemetry/ring_buffer.py': patch_ring_buffer,
    'car_application.py': patch_car_application,
    'dashboard_overlay.py': patch_overlay,
    'dashboard_steering.py': patch_steering,
    'replay_hud.py': patch_hud,
    'dashboard/helpers.py': patch_helpers,
    'dashboard/tabs/pipeline.py': patch_pipeline_tab,
    'dashboard/tabs/replay.py': patch_replay_tab,
    'pipeline_ab.py': patch_pipeline_ab,
    'frame_source.py': patch_frame_source,
    'sim_engine.py': patch_sim_engine,
    'sim_lab.py': patch_sim_lab,
    'session_recorder.py': patch_recorder,
    'config.json.example': patch_config_example,
    'CHANGELOG.md': patch_changelog,
    'ARCHITECTURE.md': patch_architecture,
    'SIMULATION.md': patch_simulation_doc,
    'tests/hardware_stubs.py': patch_stubs,
    'tests/test_paket_h1.py': patch_test_h1,
    'tests/test_paket_h2b.py': patch_test_h2b,
}


# JSON kennt keine Kommentare: Erkennung ueber einen der neuen Schluessel statt ueber den Marker
NO_MARKER = {'config.json.example': '"downscale"'}


def read_text(path):
    """Text mit '\\n' als Zeilenende; merkt sich, ob die Datei CRLF-Zeilenenden hatte."""
    raw = path.read_bytes().decode('utf-8')
    crlf = '\r\n' in raw
    return raw.replace('\r\n', '\n'), crlf


def write_text(path, text, crlf):
    path.write_bytes((text.replace('\n', '\r\n') if crlf else text).encode('utf-8'))


def cam_fingerprint(text):
    lines = [line.strip() for line in text.replace('\r\n', '\n').split('\n') if line.strip()]
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()[:16]


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    package = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    results = {}
    try:
        for name, func in PATCHES.items():
            path = root / name
            text, crlf = read_text(path)
            if (name in NO_MARKER and NO_MARKER[name] in text) or (name not in NO_MARKER and has_marker(text)):
                print(f'  = {name} (bereits gepatcht)')
                continue
            new = func(text, name)
            if name not in NO_MARKER and not has_marker(new):
                raise PatchError(f'{name}: Marker PAKET-H3 fehlt nach dem Patch')
            if name.endswith('.py'):
                compile(new, name, 'exec')
            results[path] = (new, crlf)
        cam_path = root / CAM_FILE
        cam_text, cam_crlf = read_text(cam_path)
        if has_marker(cam_text):
            print(f'  = {CAM_FILE} (bereits gepatcht)')
        else:
            if cam_fingerprint(cam_text) != CAM_FINGERPRINT:
                raise PatchError(f'{CAM_FILE}: Inhalt weicht vom erwarteten Stand ab (Pruefsumme '
                                 f'{cam_fingerprint(cam_text)} statt {CAM_FINGERPRINT}) - bitte melden, nichts geaendert')
            if package is None or not (package / 'files' / CAM_FILE).is_file():
                raise PatchError(f'{CAM_FILE}: Paketverzeichnis mit files/{CAM_FILE} fehlt (zweites Argument)')
            new, _ = read_text(package / 'files' / CAM_FILE)
            if not has_marker(new):
                raise PatchError(f'{CAM_FILE}: Marker PAKET-H3 fehlt in der Ersatzdatei')
            compile(new, CAM_FILE, 'exec')
            results[cam_path] = (new, False)
    except (PatchError, SyntaxError, OSError) as exc:
        print(f'PATCH ABGEBROCHEN, keine Datei geaendert: {exc}', file=sys.stderr)
        return 1
    for path, (new, crlf) in results.items():
        write_text(path, new, crlf)
        print(f'  + {path.relative_to(root)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
