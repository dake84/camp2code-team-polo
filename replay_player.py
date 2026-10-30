"""Wiedergabe im Browser fuer das Replay Lab (Paket I4).

Gleiche Idee wie ``sim_player.py`` (Paket I3), angewandt auf gespeicherte Sessions des Replay Labs: Der Browser spielt selbst
ab (``assets/replay_player.js``); der Server liefert nur noch

    GET /_c2c/replay/state.json              welche Session/welcher Bereich/welcher Index im Replay Lab gerade gewaehlt ist
    GET /_c2c/replay/meta.json?s=&lo=&hi=    Werte je Frame (Zeitachse, Winkel, Status ...), einmal je Session und Bereich
    GET /_c2c/replay/img/<i>.jpg?s=&lo=&hi=&w=&st=   ein fertiges Anzeigebild mit HUD (JPEG, kein Base64)

Warum das schneller und bedienbarer ist: siehe ``REPORT.md``. Kurz: keine Dash-Callback-Kette je Anzeigeframe, Bilder als
Binaerdaten per HTTP, Vorausladen im Browser, Zeitsteuerung lokal (Pause/Slider wirken sofort).

HUD: gezeichnet wird mit ``replay_hud.draw_hud``. Neu gegenueber dem bisherigen Replay Lab: Stilwahl im Spieler (klassisch,
Jetpilot, Arcade, ohne HUD) und ein **Verlauf** (``hud_render.HudTrail``) fuer "Lenkspur" und "Spur-Streak", den es sonst nur
live gibt: er wird aus den vorangehenden Frames der Session aufgebaut (Zeiten aus ``camera_monotonic_ns``).

Die Verbindung zu Dash laeuft ueber genau eine Zeile im Bild-Callback ``replay_render`` (``note_view``): Er meldet dem Backend,
was die Dash-Komponenten gerade enthalten (Session, Bereich, Index), und spart das serverseitige Rendern, solange der Browser
abspielt. Es gibt KEINEN zusaetzlichen Dash-Callback.

Dieses Modul importiert kein Dash.
"""
import bisect
import logging
import threading
import time
from collections import OrderedDict

import cv2

import hud_render
import hud_style
from replay_hud import decode_lanes, draw_hud, hud_values
from replay_selection import record_frame_id
from replay_studio import load_session_image
from session_recorder import load_events, load_session

log = logging.getLogger(__name__)

LIVE = '__live__'                 # wie dashboard.helpers.LIVE (Live-Ringbuffer: nicht abspielbar im Browser)
WIDTHS = (0, 640, 960, 1280)      # 0 = Originalgroesse
DEFAULT_WIDTH = 960
STYLE_CHOICES = ('classic', 'jetpilot', 'arcade', 'none')   # 'none' = Rohbild ohne HUD; sonst folgt der Stil dem Cockpit
JPEG_QUALITY = 72
CACHE_IMAGES = 96                 # fertige Anzeigebilder im Speicher des Servers (JPEG, je 30 bis 150 KB)
CACHE_SESSIONS = 2
MAX_RENDERS = 3                   # gleichzeitige Bildberechnungen (der Pi soll nebenbei reagieren)
ACTIVE_FOR_S = 4.0                # so lange gilt eine Meldung "Browser spielt ab" ohne neue Meldung
TRAIL_MAX = 2000                  # wie hud_render.HudTrail(maxlen)
URL_PREFIX = '/_c2c/replay'


def valid_width(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return value if value in WIDTHS else DEFAULT_WIDTH


def valid_style(value):
    """Gewaehlter Stil oder None (= dem gemeinsamen Stil des Cockpits folgen)."""
    return value if value in STYLE_CHOICES else None


def _float_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float('inf'), float('-inf')) else None


def _int_or_none(value):
    number = _float_or_none(value)
    return None if number is None else int(number)


class LruCache:
    """LRU-Cache mit Duplikatschutz: derselbe Schluessel wird auch bei gleichzeitigen Anfragen nur einmal berechnet."""

    def __init__(self, capacity):
        self.capacity = max(2, int(capacity))
        self._lock = threading.Lock()
        self._items = OrderedDict()
        self._inflight = {}
        self.hits = self.misses = 0

    def get(self, key, factory):
        for _attempt in range(2):
            with self._lock:
                if key in self._items:
                    self._items.move_to_end(key)
                    self.hits += 1
                    return self._items[key]
                event = self._inflight.get(key)
                owner = event is None
                if owner:
                    event = self._inflight[key] = threading.Event()
            if owner:
                try:
                    value = factory()
                except BaseException:
                    with self._lock:
                        self._inflight.pop(key, None)
                    event.set()
                    raise
                with self._lock:
                    self.misses += 1
                    self._items[key] = value
                    self._items.move_to_end(key)
                    while len(self._items) > self.capacity:
                        self._items.popitem(last=False)
                    self._inflight.pop(key, None)
                event.set()
                return value
            event.wait(60)
        return factory()      # der erste Berechner ist gescheitert: selbst rechnen (Fehler erreicht den Aufrufer)

    def __len__(self):
        with self._lock:
            return len(self._items)


# ------------------------------------------------------------------ Session-Daten
class SessionData:
    """Eine gespeicherte Session mit allem, was Meta-Daten und HUD brauchen. Unveraenderlich nach dem Aufbau."""

    def __init__(self, name, path, rows, events, stamp):
        self.name, self.path, self.rows, self.events, self.stamp = name, path, tuple(rows), events, stamp
        self.ids = [record_frame_id(row) for row in self.rows]
        self.t_s = self._times(self.rows)
        values = [hud_values(row) for row in self.rows]
        self.stell = [v[0] for v in values]
        self.soll = [v[1] for v in values]
        self.lane_counts = [sum(lane is not None for lane in decode_lanes(row)) for row in self.rows]
        self.version = f'{stamp[0]:x}-{stamp[1]:x}'
        self._positions = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _times(rows):
        """Aufnahmezeit je Zeile in Sekunden (camera_monotonic_ns), nicht fallend; Luecken mit dem Median der Abstaende."""
        raw = [_int_or_none(row.get('camera_monotonic_ns')) for row in rows]
        gaps = sorted(b - a for a, b in zip(raw, raw[1:]) if a and b and b > a)
        step = (gaps[len(gaps) // 2] if gaps else 40_000_000) / 1e9
        times, last = [], None
        for value in raw:
            if value and (last is None or value / 1e9 >= last):
                last = value / 1e9
            else:
                last = (last + step) if last is not None else 0.0
            times.append(last)
        return times

    def positions(self, lo, hi):
        """Indizes (in ``rows``) der Frames im Frame-ID-Bereich - genau die Auswahl, die das Replay Lab per Slider zeigt."""
        key = (lo, hi)
        with self._lock:
            found = self._positions.get(key)
            if found is not None:
                self._positions.move_to_end(key)
                return found
        if lo is None or hi is None:
            found = tuple(range(len(self.rows)))
        else:
            found = tuple(i for i, fid in enumerate(self.ids) if fid is not None and lo <= fid <= hi)
        with self._lock:
            self._positions[key] = found
            while len(self._positions) > 8:
                self._positions.popitem(last=False)
        return found


def _fmt(row, key):
    value = row.get(key)
    return '' if value is None else str(value)


class ReplayBackend:
    """Liefert dem Browser-Spieler Zustand, Meta-Daten und Anzeigebilder; haelt Cache und Zustandsmeldung."""

    def __init__(self, root='Data_AI/sessions'):
        self.root = root
        self.images = LruCache(CACHE_IMAGES)
        self._sessions = OrderedDict()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_RENDERS)
        self._view = None
        self._rev = 0
        self._active_until = 0.0
        self._beat_seq = 0

    # ---- Sessions
    def session(self, name):
        """``SessionData`` (gecacht bis zur naechsten Dateiaenderung); ``ValueError`` bei ungueltigem Namen/fehlender Session."""
        if not isinstance(name, str) or not name or name == LIVE:
            raise ValueError('Keine gespeicherte Session')
        path, rows = load_session(name, self.root)          # prueft den Namen (kein Pfad ausserhalb der Sessions)
        events_file = path / 'events.csv'
        frames_stat = (path / 'frames.csv').stat()
        events_stat = events_file.stat() if events_file.is_file() else None
        stamp = (frames_stat.st_mtime_ns, frames_stat.st_size, events_stat.st_mtime_ns if events_stat else 0)
        with self._lock:
            cached = self._sessions.get(name)
            if cached is not None and cached.stamp == stamp:
                self._sessions.move_to_end(name)
                return cached
        data = SessionData(name, path, rows, load_events(name, self.root), stamp)
        with self._lock:
            self._sessions[name] = data
            self._sessions.move_to_end(name)
            while len(self._sessions) > CACHE_SESSIONS:
                self._sessions.popitem(last=False)
        return data

    # ---- Zustand des Replay Labs (kommt aus dem Callback replay_render)
    def note_view(self, name, bounds, index):
        """Merkt, was die Dash-Komponenten enthalten. Rueckgabe: True, wenn der Browser gerade abspielt und der Server
        das Bild deshalb nicht rendern muss (nur fuer gespeicherte Sessions)."""
        try:
            lo, hi = (float(bounds[0]), float(bounds[1])) if bounds else (None, None)
        except (TypeError, ValueError, IndexError):
            lo = hi = None
        view = (name, lo, hi, _int_or_none(index) or 0)
        with self._lock:
            if view != self._view:
                self._view = view
                self._rev += 1
            active = time.monotonic() < self._active_until
        return bool(active and name not in (None, LIVE))

    def heartbeat(self, active, seq=None):
        """Meldung "Browser spielt ab" (``active``) bzw. "nicht mehr". ``seq`` (Zeitstempel des Browsers) ordnet die Meldungen:
        eine aeltere Meldung, die erst nach einer neueren eintrifft (parallele Anfragen), wird ignoriert."""
        with self._lock:
            if seq is not None:
                if seq < self._beat_seq:
                    return
                self._beat_seq = seq
            self._active_until = time.monotonic() + ACTIVE_FOR_S if active else 0.0

    def state(self):
        with self._lock:
            view, rev = self._view, self._rev
        if view is None:
            return {'session': None, 'lo': None, 'hi': None, 'index': 0, 'rev': rev, 'live': True}
        name, lo, hi, index = view
        return {'session': name, 'lo': lo, 'hi': hi, 'index': index, 'rev': rev, 'live': name in (None, LIVE)}

    # ---- Meta
    def meta(self, name, lo, hi):
        data = self.session(name)
        positions = data.positions(lo, hi)
        rows = [data.rows[i] for i in positions]
        if not rows:
            raise LookupError('Bereich leer')
        base = data.t_s[positions[0]]
        return {
            'session': name, 'lo': lo, 'hi': hi, 'n': len(rows), 'v': data.version,
            'ids': [data.ids[i] for i in positions],
            't': [round((data.t_s[i] - base) * 1000.0, 1) for i in positions],
            'status': [_fmt(r, 'data_status') for r in rows], 'sc': [_fmt(r, 'steering_angle_car') for r in rows],
            'rq': [_fmt(r, 'requested_angle') for r in rows], 'rw': [_fmt(r, 'steering_angle') for r in rows],
            'sp': [_fmt(r, 'speed') for r in rows], 'ln': [_fmt(r, 'lane_count') for r in rows],
            'ev': [_fmt(r, 'event_codes') for r in rows],
            'styles': [{'value': 'follow', 'label': 'HUD wie im Cockpit'}, {'value': 'classic', 'label': 'HUD klassisch'},
                       {'value': 'jetpilot', 'label': 'HUD Jetpilot'}, {'value': 'arcade', 'label': 'HUD Arcade'},
                       {'value': 'none', 'label': 'ohne HUD (Rohbild)'}],
            'cockpit_style': hud_style.resolve(None), 'widths': list(WIDTHS), 'default_width': DEFAULT_WIDTH,
        }

    # ---- Bilder
    @staticmethod
    def _trail(data, index):
        """``HudTrail`` fuer Lenkspur/Spur-Streak aus den vorangehenden Frames (bis 90 s bzw. 2000 Frames) + dem aktuellen.

        Die Zeiten kommen aus der Aufnahme; das aktuelle Bild wird hier (mit der richtigen Zeit) eingetragen, damit
        ``draw_hud`` es nicht mit der Uhr der Anzeige nochmals anhaengt (es erkennt denselben Frame und ueberspringt ihn).
        """
        trail = hud_render.HudTrail()
        now = data.t_s[index]
        start = max(bisect.bisect_left(data.t_s, now - hud_render.TRAIL_WINDOW_S, 0, index + 1), index + 1 - TRAIL_MAX)
        for j in range(start, index + 1):
            trail.add(data.ids[j], data.stell[j], data.soll[j], data.lane_counts[j], now=data.t_s[j])
        return trail

    def _render(self, data, index, width, style):
        """JPEG-Bytes des Anzeigebilds ``index`` (Index in ``data.rows``).

        Das HUD wird wie im bisherigen Replay Lab auf dem Originalbild gezeichnet (Linien, ROI und Schriftgroessen stimmen dann
        auch fuer Sessions ohne Geometriespalten) und erst danach auf die gewaehlte Breite verkleinert.
        """
        row = data.rows[index]
        image = load_session_image(data.path, row)              # ValueError: Bild fehlt/nicht lesbar
        if style != 'none':
            trail = self._trail(data, index) if hud_style.resolve(style) in ('jetpilot', 'arcade') else None
            image = draw_hud(image, row, events=data.events, style=style, trail=trail)
        if width and image.shape[1] > width:
            factor = width / image.shape[1]
            image = cv2.resize(image, (width, max(1, int(round(image.shape[0] * factor)))), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise ValueError('JPEG-Kodierung fehlgeschlagen')
        return encoded.tobytes()

    def image(self, name, lo, hi, index, width=None, style=None):
        """JPEG-Bytes; ``LookupError`` ausserhalb des Bereichs, ``ValueError`` bei fehlendem Bild/ungueltiger Session."""
        data = self.session(name)
        positions = data.positions(lo, hi)
        if not 0 <= index < len(positions):
            raise LookupError('index')
        width = valid_width(width)
        effective = valid_style(style) or hud_style.resolve(None)       # classic | jetpilot | arcade | none
        key = (name, data.stamp, positions[index], width, effective)

        def factory():
            with self._slots:
                return self._render(data, positions[index], width, effective)
        return self.images.get(key, factory)


# ------------------------------------------------------------------ Verbindung zu Dash
_ACTIVE = None            # das registrierte Backend (fuer ``note_view`` im Callback replay_render)


def note_view(name, bounds, index):
    """Aufruf im Callback ``replay_render``: True = der Browser spielt ab, kein serverseitiges Bild noetig. Nie eine Ausnahme."""
    backend = _ACTIVE
    if backend is None:
        return False
    try:
        return backend.note_view(name, bounds, index)
    except Exception:
        log.exception('note_view fehlgeschlagen')
        return False


def _children(node):
    children = getattr(node, 'children', None)
    if children is None:
        return []
    return list(children) if isinstance(children, (list, tuple)) else [children]


def _contains_id(node, wanted):
    if getattr(node, 'id', None) == wanted:
        return True
    return any(_contains_id(child, wanted) for child in _children(node))


def _find_parent_of(node, predicate):
    """(Elternknoten, Position) des ersten Kindes, fuer das ``predicate(kind)`` gilt (Tiefensuche)."""
    children = _children(node)
    for position, child in enumerate(children):
        if predicate(child):
            return node, position
        found = _find_parent_of(child, predicate)
        if found is not None:
            return found
    return None


def _set_child(parent, position, new_child):
    if isinstance(parent.children, (list, tuple)):
        items = list(parent.children)
        items[position] = new_child
        parent.children = items
    else:
        parent.children = new_child


def player_block(ctx):
    """Umschalter + bisherige Anzeige (Server, alt) + Browser-Spieler (die Oberflaeche baut ``replay_player.js``)."""
    html = ctx.html
    try:
        legend = [ctx.legend()]
    except Exception:  # die Legende ist Beiwerk
        legend = []
    return html.Div([
        html.Div(id='replay-player-switch', className='small mb-2'),
        html.Div(id='replay-player-wrap', style={'display': 'none'}, children=[html.Div(id='replay-player-root'), *legend]),
    ], id='replay-viewer-host')


def install_layout(root, ctx):
    """Baut den Browser-Spieler in das Layout des Replay Labs ein, ohne eine bestehende Komponente zu entfernen.

    * Der Inhalt der Karte "Frame Inspector" (Bild, Beschriftung, Legende, Details) wandert unveraendert in
      ``replay-legacy-view`` und wird vom Spieler bei Bedarf ausgeblendet; daneben steht ``replay-player-wrap``.
    * ``◀ ▶ Play ▶|`` und der Slider (``replay-prev/-play/-next/-index``) kommen in ``replay-legacy-controls``.

    Findet sich die erwartete Struktur nicht, bleibt der Baum unveraendert (die bisherige Anzeige wirkt wie vorher).
    """
    if _contains_id(root, 'replay-viewer-host'):
        return root
    body = _find_parent_of(root, lambda n: type(n).__name__ == 'CardBody' and _contains_id(n, 'replay-image'))
    slider_group = _find_parent_of(root, lambda n: type(n).__name__ == 'ButtonGroup' and _contains_id(n, 'replay-prev'))
    if body is None or slider_group is None:
        log.warning('Replay Lab: erwartete Struktur nicht gefunden - Browser-Wiedergabe nicht eingebaut')
        return root
    html = ctx.html
    card_body = _children(body[0])[body[1]]
    legacy = html.Div(_children(card_body), id='replay-legacy-view')
    card_body.children = [player_block(ctx), legacy]
    parent, position = slider_group
    items = _children(parent)
    group = items[position]
    follower = items[position + 1] if position + 1 < len(items) and _contains_id(items[position + 1], 'replay-index') else None
    controls = html.Div([group] + ([follower] if follower is not None else []), id='replay-legacy-controls')
    parent.children = items[:position] + [controls] + items[position + (2 if follower is not None else 1):]
    return root


def layout_supported(tab):
    """Wahr, wenn ``install_layout`` auf dem Layout des Tabs greift (Probe auf einem frisch gebauten Baum)."""
    try:
        return _contains_id(tab.layout(), 'replay-player-root')
    except Exception:  # eine Probe darf die Anwendung nie am Start hindern
        log.exception('Layout-Probe der Replay-Browser-Wiedergabe fehlgeschlagen')
        return False


def register(app, tab, root='Data_AI/sessions'):
    """Registriert Backend und HTTP-Routen. Kein Dash-Callback. Mit der Dash-Attrappe der Tests (kein Flask) nur das Backend."""
    global _ACTIVE
    backend = ReplayBackend(root)
    tab.player_backend = backend
    if not layout_supported(tab):
        log.warning('Replay Lab: Aufbau unbekannt - Browser-Wiedergabe nicht aktiviert (bisherige Anzeige bleibt)')
        return backend
    _ACTIVE = backend
    server = getattr(app, 'server', None)
    if server is None or getattr(server, '_c2c_replay_player', False):
        return backend
    from flask import Response, jsonify, request
    server._c2c_replay_player = True

    def bounds_args():
        return _float_or_none(request.args.get('lo')), _float_or_none(request.args.get('hi'))

    def plain(status, text):
        return Response(text, status=status, mimetype='text/plain')

    @server.route(URL_PREFIX + '/state.json')
    def c2c_replay_state():
        if request.args.get('a') is not None:
            backend.heartbeat(request.args.get('a') == '1', _float_or_none(request.args.get('q')))
        response = jsonify(backend.state())
        response.headers['Cache-Control'] = 'no-store'
        return response

    @server.route(URL_PREFIX + '/meta.json')
    def c2c_replay_meta():
        lo, hi = bounds_args()
        try:
            meta = backend.meta(request.args.get('s'), lo, hi)
        except LookupError:
            return plain(404, 'Bereich leer')
        except (ValueError, OSError) as exc:
            return plain(404, f'Session nicht lesbar: {exc}')
        response = jsonify(meta)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @server.route(URL_PREFIX + '/img/<int:index>.jpg')
    def c2c_replay_image(index):
        lo, hi = bounds_args()
        try:
            data = backend.image(request.args.get('s'), lo, hi, index, request.args.get('w'), request.args.get('st'))
        except LookupError:
            return plain(404, 'Nicht gefunden')
        except (ValueError, OSError, cv2.error) as exc:
            return plain(404, f'Bild nicht verfuegbar: {exc}')
        response = Response(data, mimetype='image/jpeg')
        response.headers['Cache-Control'] = 'private, max-age=3600'
        return response

    return backend
