#!/usr/bin/env python3
"""Paket H3b: HUD-Stile Jetpilot und Arcade.

Setzt Paket H3 voraus. Verankerte Aenderungen; fehlt ein Anker oder ist er mehrdeutig, wird KEINE Datei geaendert
(alles oder nichts). Dateien mit dem Marker PAKET-H3B werden uebersprungen (idempotent); der Marker wird vollstaendig
geprueft (PAKET-H3 ist Teilstring von PAKET-H3B).

Geaendert werden nur:
  * replay_hud.py            ``draw_hud`` wird zur Weiche (Stil), das bisherige HUD heisst ``draw_hud_classic`` und
                             bleibt unveraendert; ``row_from_camera`` liefert zusaetzlich Kamerawerte; Legende.
  * dashboard/tabs/driving.py  der HUD-Schalter (cockpit-hud) wird zur Auswahl (gleiche ID, gleiche Callback-Signatur).
  * CHANGELOG.md             (weich: fehlt der Anker, wird nur ein Hinweis ausgegeben)

Aufruf: python scripts/paket_h3b_patch.py [REPO_ROOT]
"""
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r'PAKET-H3B(?![A-Za-z0-9])')


class PatchError(Exception):
    pass


def has_marker(text):
    return bool(MARKER_RE.search(text))


def sub_n(text, old, new, file, count=1):
    found = text.count(old)
    if found != count:
        raise PatchError(f'{file}: Text {old[:70]!r} {found}x gefunden (erwartet {count})')
    return text.replace(old, new)


# ------------------------------------------------------------------ replay_hud.py
LEGEND_ROWS = """    ('JETPILOT', 'Gruenes Phosphor-HUD: Kompassband oben (Mitte = Stellwert, Raute = Soll nur bei Abweichung, '
                 'Punkt = Roh), Speed-Leiter links, Pipper = Lenkrichtung, ROI als Eckklammern',
     'Auswahl neben dem Livebild; gilt auch im Replay Lab und in der Simulation'),
    ('SPUR LOCK', 'Anzahl erkannter Linien (aus den Daten, keine neue Erkennung)',
     '2/2 gruen - 1/2 gelb - NO LOCK = keine Linie (rot bei Fahrt)'),
    ('ARCADE', 'Spielerischer Stil: Spur-Teppich zwischen den Linien, Chevrons, Tacho = Steuer-Speed (Skala 0-100), '
               'Lenkregler (Ist-Knopf, Soll-Ring nur bei Abweichung), Pillen fuer Modus und Zustand',
     'Teppich rot, wenn bei Fahrt keine Linie erkannt wird'),
    ('SPUR-STREAK', 'Zeit seit dem letzten Frame mit weniger als zwei Linien (abgeleitet, nur live)',
     'Sterne ab 5 / 15 / 30 s (frei gewaehlt); hoechstens so lang wie der gesammelte Verlauf (90 s)'),
    ('LENKSPUR', 'Stellwert und Soll der letzten 5 s (nur live; der Verlauf wird beim Anzeigen gesammelt)',
     'im Replay Lab und in der Simulation nicht vorhanden'),
    ('EXP / GAIN / LUX / CAM', 'Belichtungszeit (ms), analoge Verstaerkung, Lux und Kamera-Bildrate (aus der Bilddauer)',
     'live alle vier; Replay: EXP und GAIN aus frames.csv ab Revision 4, sonst nicht angezeigt'),
"""

HUD_DISPATCH = '''def _hud_info(image, row, events, footer):
    """PAKET-H3B: Werte fuer die Stile Jetpilot und Arcade (gleiche Quellen wie ``draw_hud_classic``).

    Linien werden wie im klassischen HUD auf das gespeicherte (grosse) Bild umgerechnet; fehlende Werte bleiben None.
    """
    height, width = image.shape[:2]
    lanes = decode_lanes(row)
    top, bottom = _integer(row, 'roi_top_px'), _integer(row, 'roi_bottom_px')
    geometry = top is not None and bottom is not None and 0 <= top < bottom <= height
    if geometry:
        lanes = _lanes_on_image(row, lanes, width, height, top)
    stell, soll, roh, speed = hud_values(row)
    label, kind = vehicle_state(row, events)
    key = row.get('vehicle_mode_key') or None
    name = ''
    if key:
        try:
            name = ascii_text(mode_registry.short_label(key))
        except Exception:
            name = ''
    return {'frame_id': ascii_text(row.get('frame_id', '?')), 'lanes': lanes, 'geometry': geometry,
            'top': top if geometry else None, 'bottom': bottom if geometry else None,
            'stell': stell, 'soll': soll, 'roh': roh, 'speed': speed,
            'label': ascii_text(label), 'kind': kind, 'color': STATE_COLORS[kind], 'mode_name': name,
            'events': ascii_text(row['event_codes'])[:55] if row.get('event_codes') else '',
            'footer': ascii_text(footer)[:70] if footer else '',
            'exposure_us': _number(row, 'exposure_us'), 'gain': _number(row, 'gain'), 'lux': _number(row, 'lux'),
            'frame_duration_us': _number(row, 'frame_duration_us')}


_STYLE_FAILED = set()


def draw_hud(image, row, events=None, footer=None, style=None, trail=None):
    """HUD in einem der Stile ``classic``, ``jetpilot`` oder ``arcade`` (PAKET-H3B).

    ``style=None`` folgt dem gemeinsamen Stil (``hud_style``, im Cockpit gewaehlt); ``off`` und unbekannte Werte
    ergeben ``classic``, denn diese Ansichten zeigen immer ein HUD. ``trail`` ist der Verlauf fuer Lenkspur und
    Spur-Streak (nur live, ``hud_render.TRAIL``); ohne Verlauf entfallen diese Elemente. Schlaegt ein neuer Stil fehl,
    wird das klassische HUD gezeichnet (Hinweis einmalig im Log), das Bild bleibt nie leer.
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return image
    chosen = hud_style.resolve(style)
    if chosen in hud_render.STYLES:
        try:
            info = _hud_info(image, row, events, footer)
            if trail is not None:
                trail.add(row.get('frame_id'), info['stell'], info['soll'],
                          sum(lane is not None for lane in info['lanes']))
            return hud_render.draw(chosen, image, info, trail)
        except Exception:
            if chosen not in _STYLE_FAILED:
                _STYLE_FAILED.add(chosen)
                logging.getLogger(__name__).exception('HUD-Stil %s fehlgeschlagen - klassisches HUD (einmalige Meldung)',
                                                      chosen)
    return draw_hud_classic(image, row, events=events, footer=footer)


def draw_hud_classic(image, row, events=None, footer=None):
'''


def patch_hud(t, f):
    t = sub_n(t, 'from dashboard_overlay import draw_lanes, draw_roi, draw_steering_line, put_text\n',
              'from dashboard_overlay import draw_lanes, draw_roi, draw_steering_line, put_text\n'
              'import hud_render  # PAKET-H3B: Stile Jetpilot und Arcade\n'
              'import hud_style\n', f)
    t = sub_n(t, "'roi_high': getattr(camera, 'crop', (None, None))[1]}\n",
              "'roi_high': getattr(camera, 'crop', (None, None))[1],\n"
              "           # PAKET-H3B: Kamerawerte fuer Jetpilot/Arcade (live; in frames.csv nur exposure_us und gain)\n"
              "           'exposure_us': getattr(camera, 'exposure_us', None), 'gain': getattr(camera, 'gain', None),\n"
              "           'lux': getattr(camera, 'lux', None),\n"
              "           'frame_duration_us': getattr(camera, 'frame_duration_us', None)}\n", f)
    old_modus = ("    ('MODUS', 'Name des Fahrmodus im Aufnahmemoment (aus dem Register)', "
                 "'rechts in der Winkelzeile; ältere Aufnahmen: nicht angezeigt'),\n")
    t = sub_n(t, old_modus, old_modus + LEGEND_ROWS, f)
    t = sub_n(t, 'import json\nimport math\n', 'import json\nimport logging  # PAKET-H3B\nimport math\n', f)
    return sub_n(t, 'def draw_hud(image, row, events=None, footer=None):\n', HUD_DISPATCH, f)


# ------------------------------------------------------------------ dashboard/tabs/driving.py
OLD_SWITCH = """                          dbc.Checklist(id='cockpit-hud', options=[{'label': 'HUD', 'value': 'on'}],
                                        value=[], switch=True, inline=True,
                                        className='d-inline-block ms-3 small'),
"""
NEW_SWITCH = """                          dbc.Select(id='cockpit-hud', options=hud_style.OPTIONS, value='off', size='sm',
                                     className='d-inline-block ms-3',
                                     style={'width': 'auto', 'minWidth': '9.5rem'}),
"""
OLD_DRAW = """                        image = uri(draw_hud(shown.raw_image, row_from_camera(
                            shown, record.driving if record is not None else None)))
"""
NEW_DRAW = """                        image = uri(draw_hud(shown.raw_image, row_from_camera(
                            shown, record.driving if record is not None else None),
                            style=hud_style_name, trail=hud_render.TRAIL))
"""


def patch_driving(t, f):
    t = sub_n(t, 'from replay_hud import draw_hud, row_from_camera\n',
              'from replay_hud import draw_hud, row_from_camera\n'
              'import hud_render  # PAKET-H3B: HUD-Stile\n'
              'import hud_style\n', f)
    t = sub_n(t, OLD_SWITCH, NEW_SWITCH, f)
    t = sub_n(t, "                hud_on = bool(hud and 'on' in hud)\n",
              "                # PAKET-H3B: Auswahl statt Schalter; die alte Liste ['on'] bedeutet klassisch\n"
              "                hud_style_name = hud_style.set_current(hud)   # Replay Lab und Simulation folgen diesem Stil\n"
              "                hud_on = hud_style_name != 'off'\n", f)
    t = sub_n(t, "(':hud' if hud_on else '')", "(f':hud-{hud_style_name}' if hud_on else '')", f, count=2)
    return sub_n(t, OLD_DRAW, NEW_DRAW, f)


# ------------------------------------------------------------------ CHANGELOG.md (weich)
CHANGELOG = '''## 6.11 – Paket H3b (PAKET-H3B)
* **HUD-Stile:** Die Auswahl neben dem Livebild ersetzt den HUD-Schalter (gleiche ID `cockpit-hud`, gleiche Callback-Signatur,
  34 Callbacks): *HUD aus*, *klassisch* (unverändert), *Jetpilot* (grünes Kompassband mit Stellwert, Soll-Raute und Roh-Punkt,
  Speed-Leiter, Pipper, ROI-Eckklammern, SPUR LOCK) und *Arcade* (Spur-Teppich mit Chevrons, Tacho, Lenkregler, Zustands-Pillen,
  Spur-Streak, Lenkspur). Replay Lab und Simulation folgen dem gewählten Stil (ohne Auswahl: klassisch).
* `hud_render.py` zeichnet die neuen Stile aus denselben Werten wie das klassische HUD; `hud_style.py` hält Stil und Auswahl;
  `replay_hud.draw_hud` ist die Weiche, `draw_hud_classic` das bisherige HUD. Schlägt ein neuer Stil fehl, erscheint das
  klassische HUD (einmalige Meldung im Log).
* Kamerawerte (Belichtung, Verstärkung, Lux, Bilddauer) kommen live aus `CameraData`; in `frames.csv` stehen nur Belichtung
  und Verstärkung (ab Revision 4). Lenkspur und Spur-Streak brauchen einen Verlauf, der beim Anzeigen im Cockpit gesammelt
  wird (90 s) und im Replay Lab fehlt.

'''


def patch_changelog(t, f):
    return sub_n(t, '## 6.10 – Paket H3 (PAKET-H3)\n', CHANGELOG + '## 6.10 – Paket H3 (PAKET-H3)\n', f)


PATCHES = {'replay_hud.py': patch_hud, 'dashboard/tabs/driving.py': patch_driving}
SOFT = {'CHANGELOG.md': patch_changelog}


def read_text(path):
    raw = path.read_bytes().decode('utf-8')
    return raw.replace('\r\n', '\n'), '\r\n' in raw


def write_text(path, text, crlf):
    path.write_bytes((text.replace('\n', '\r\n') if crlf else text).encode('utf-8'))


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    results = {}
    try:
        for name, func in {**PATCHES, **SOFT}.items():
            path = root / name
            text, crlf = read_text(path)
            if has_marker(text):
                print(f'  = {name} (bereits gepatcht)')
                continue
            try:
                new = func(text, name)
                if not has_marker(new):
                    raise PatchError(f'{name}: Marker PAKET-H3B fehlt nach dem Patch')
                if name.endswith('.py'):
                    compile(new, name, 'exec')
            except PatchError as exc:
                if name in SOFT:
                    print(f'  ! {name} uebersprungen (Dokumentation): {exc}')
                    continue
                raise
            results[path] = (new, crlf)
    except (PatchError, SyntaxError, OSError) as exc:
        print(f'PATCH ABGEBROCHEN, keine Datei geaendert: {exc}', file=sys.stderr)
        return 1
    for path, (new, crlf) in results.items():
        write_text(path, new, crlf)
        print(f'  + {path.relative_to(root)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
