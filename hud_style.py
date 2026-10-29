"""HUD-Stil: Auswahl und gemeinsamer Zustand (PAKET-H3B).

Stile: ``off`` (kein HUD, nur Overlay), ``classic`` (bisheriges HUD), ``jetpilot``, ``arcade``.

Die Auswahl sitzt im Cockpit (Komponente ``cockpit-hud``, frueher ein Schalter). Der Cockpit-Callback merkt sich den
gewaehlten Stil hier; Replay Lab und Simulation (``draw_hud`` ohne ``style``) folgen ihm. Die Replay-Ansichten zeigen
immer ein HUD: bei ``off`` verwenden sie ``classic``.

Reine Zeichenkettenlogik, keine Abhaengigkeit von Dash oder OpenCV.
"""
import threading

STYLES = ('off', 'classic', 'jetpilot', 'arcade')
LABELS = {'off': 'HUD aus', 'classic': 'HUD klassisch', 'jetpilot': 'HUD Jetpilot', 'arcade': 'HUD Arcade'}
OPTIONS = [{'label': LABELS[name], 'value': name} for name in STYLES]

_lock = threading.Lock()
_current = 'off'


def normalize(value, default='off'):
    """Beliebigen Wert auf einen Stil abbilden.

    Auch der alte Schalter (``dbc.Checklist``) bleibt gueltig: ``['on']`` = klassisch, ``[]`` = aus. Unbekanntes ergibt
    ``default``.
    """
    if isinstance(value, (list, tuple)):
        return 'classic' if 'on' in value else 'off'
    if value is True:
        return 'classic'
    if value is False or value is None:
        return 'off'
    if isinstance(value, str):
        text = value.strip().lower()
        if text in STYLES:
            return text
        if text == 'on':
            return 'classic'
    return default


def set_current(value):
    """Gemeinsamen Stil setzen (Cockpit); gibt den normalisierten Stil zurueck."""
    global _current
    name = normalize(value)
    with _lock:
        _current = name
    return name


def current():
    with _lock:
        return _current


def resolve(value=None):
    """Stil fuer ``draw_hud``: ``None`` = gemeinsamer Stil; ``off`` wird zu ``classic`` (HUD-Ansichten zeigen immer eins)."""
    name = current() if value is None else normalize(value, 'classic')
    return 'classic' if name == 'off' else name
