"""Clientseitige Wiedergabe der Simulation (Paket I3).

Problem: Die Wiedergabe lief ueber einen Dash-Takt (Server -> Index -> Server -> Bild). Jeder Anzeigeframe kostete zwei
Round-Trips; dauern sie laenger als der Takt, stauen sich Callbacks. Ein Klick auf Pause oder den Slider steht dann hinter
den wartenden Takten und "wirkt nicht" - die Wiedergabe laeuft weiter.

Loesung: Der Browser spielt selbst ab (``assets/sim_player.js``). Der Server liefert nur noch

    GET /_c2c/sim/current.json                      Token des aktuellen Ergebnisses (der Browser fragt ihn selten ab)
    GET /_c2c/sim/<token>/meta.json                 alle Werte je Frame (klein; Tabelle, Beschriftung, Zeitstempel)
    GET /_c2c/sim/<token>/<then|now>/<index>.jpg    ein fertiges Anzeigebild (HUD eingezeichnet), per ?w= in 640/960/1280

Der Browser laedt die naechsten Bilder im Voraus (parallel, begrenzt), spielt nach den aufgezeichneten Zeitstempeln
(Echtzeit mit Frame-Ueberspringen oder "Jeder Frame") und hat Play, Pause, Slider, Tempo lokal - ohne Serveranfrage.
Es gibt keinen Dash-Takt und keine Callback-Kette mehr waehrend der Wiedergabe.

Was bleibt: Berechnung, Runs (Paket I1), HUD-Zeichnen (``SimLab.render_frame`` bzw. Paket I2 ``_render_pair``) und die
alte Server-Wiedergabe (umschaltbar "Server (alt)"). ``sim_lab.py`` wird von diesem Paket NICHT veraendert; das Modul
haengt sich ueber ``install_layout`` (Layout) und ``register`` (Routen) ein. Es gibt keinen zusaetzlichen Dash-Callback: Welches
Ergebnis geladen ist, fragt der Browser (nur bei sichtbarer Ansicht, ca. alle 0,7 s) ueber ``current.json`` ab.

Diese Datei importiert kein Dash und keine Hardware (``ui`` wird uebergeben).
"""
import base64
import logging
import threading
from collections import OrderedDict

import cv2

import hud_style

log = logging.getLogger(__name__)

WIDTHS = (640, 960, 1280)
DEFAULT_WIDTH = 960
JPEG_QUALITY = 70
CACHE_PAIRS = 64          # fertige Bildpaare (damals + jetzt) im Speicher des Servers; je Paar ca. 60 bis 250 KB
MAX_RENDERS = 3           # gleichzeitige Bildberechnungen (der Pi soll nebenbei reagieren)
URL_PREFIX = '/_c2c/sim'


def valid_width(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return value if value in WIDTHS else DEFAULT_WIDTH


class PairCache:
    """LRU-Cache mit Duplikatschutz: dasselbe Bildpaar wird auch bei gleichzeitigen Anfragen nur einmal berechnet."""

    def __init__(self, capacity=CACHE_PAIRS):
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
        return factory()           # der erste Berechner ist gescheitert: selbst rechnen (Fehler erreicht den Aufrufer)

    def drop_other(self, token):
        """Verwirft Bilder anderer Ergebnisse (anderer Run) - Schluessel beginnen mit dem Token."""
        with self._lock:
            for key in [k for k in self._items if k[0] != token]:
                del self._items[key]

    def __len__(self):
        with self._lock:
            return len(self._items)


def _number(value, digits=1):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float('inf'), float('-inf')):
        return None
    return round(value, digits)


class PlayerBackend:
    """Liefert dem Browser-Spieler Meta-Daten und Anzeigebilder aus dem aktuellen SimLab-Ergebnis."""

    def __init__(self, lab):
        self.lab = lab
        self.cache = PairCache()
        self._slots = threading.BoundedSemaphore(MAX_RENDERS)
        self._pair_failed = False     # Paket-I2-Weg (``_render_pair``) hat einmal versagt: dauerhaft auf render_frame

    # ---- Ergebnis finden
    def entry(self, token):
        """(result, source) zum Token oder None. Tokens sind Ganzzahlen (monotonic_ns); Text wird in int gewandelt."""
        entry = self.lab.lookup(token)
        if entry is None:
            try:
                entry = self.lab.lookup(int(token))
            except (TypeError, ValueError):
                entry = None
        if entry is None:
            # JSON kennt nur Gleitkommazahlen: ab 2^53 (monotonic_ns nach ca. 104 Tagen Laufzeit) kommt das Token im Browser
            # gerundet an. Dann den Schluessel suchen, der als Gleitkommazahl gleich ist.
            try:
                wanted = float(token)
            except (TypeError, ValueError):
                return None
            for key in list(getattr(self.lab, '_results', {})):
                try:
                    if float(key) == wanted:
                        return self.lab.lookup(key)
                except (TypeError, ValueError):
                    continue
        return entry

    # ---- Meta
    def current_token(self):
        """Token des geladenen Ergebnisses als Text (JSON-Zahlen sind ab 2^53 ungenau) oder None."""
        for key in list(getattr(self.lab, '_results', {})):
            return str(key)
        return None

    def meta(self, token):
        """Spaltenweise Werte aller Frames (kompakt) plus Zeitachse fuer die Wiedergabe; None ohne Ergebnis."""
        entry = self.entry(token)
        if entry is None:
            return None
        result, source = entry
        frames = result.frames
        if not frames:
            return None
        stamp, media = 0, []
        base = frames[0].captured_ns
        for frame in frames:                       # nicht fallend, wie SimLab._timestamps
            stamp = max(stamp, frame.captured_ns) if media else frame.captured_ns
            media.append(round((stamp - base) / 1e6, 1))
        self.cache.drop_other(token)

        def bool_or_none(value):
            return None if value is None else (1 if value else 0)

        return {
            'token': token, 'n': len(frames), 'first': self.lab.first_measured(result),
            'style': hud_style.resolve(None), 'widths': list(WIDTHS), 'default_width': DEFAULT_WIDTH,
            'processor': getattr(result, 'processor_key', ''),
            'fps': _number(getattr(source, 'median_fps', None)), 'fps_source': getattr(source, 'fps_source', None),
            'ids': [f.frame_id for f in frames], 't': media,
            'warm': [1 if f.warmup else 0 for f in frames],
            'stop': [f.stop_reason or '' for f in frames],
            'held': [1 if f.held else 0 for f in frames],
            'tl': [f.then_lane_count for f in frames], 'nl': [f.lane_count for f in frames],
            'tp': [bool_or_none(f.then_plausible) for f in frames], 'np': [1 if f.plausible else 0 for f in frames],
            'tr': [_number(f.then_raw_angle) for f in frames], 'nr': [_number(f.raw_angle) for f in frames],
            'tc': [_number(f.then_car_angle) for f in frames], 'nc': [_number(f.car_angle) for f in frames],
            'ts': [_number(f.then_speed, 0) for f in frames], 'ns': [_number(f.speed, 0) for f in frames],
            'tst': [f.then_status or '' for f in frames],
        }

    # ---- Bilder
    def _encode(self, image, width):
        if image is None or getattr(image, 'size', 0) == 0:
            raise ValueError('Bild nicht verfuegbar')
        if image.shape[1] > width:
            scale = width / image.shape[1]
            image = cv2.resize(image, (int(width), max(1, int(round(image.shape[0] * scale)))),
                               interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise ValueError('JPEG-Kodierung fehlgeschlagen')
        return encoded.tobytes()

    @staticmethod
    def _decode_uri(uri):
        if not isinstance(uri, str) or ',' not in uri:
            raise ValueError('Bild nicht verfuegbar')
        return base64.b64decode(uri.split(',', 1)[1])

    def _render(self, result, source, position, width):
        """(damals, jetzt) als JPEG-Bytes. Bevorzugt den schnellen Weg aus Paket I2 (``SimLab._render_pair``: einmal
        dekodieren, HUD auf dem verkleinerten Bild); fehlt er oder versagt er, ``render_frame`` wie die alte Wiedergabe."""
        pair = getattr(self.lab, '_render_pair', None)
        if callable(pair) and not self._pair_failed:
            try:
                then_uri, now_uri = pair(result, source, position, width, hud_style.resolve(None))
                try:
                    return self._decode_uri(then_uri), self._decode_uri(now_uri)
                except ValueError:
                    # PAKET-I3B: uri() lieferte kein dekodierbares Bild (None/Attrappe): wie ohne I2 ueber render_frame
                    pass
            except (TypeError, AttributeError, KeyError) as exc:     # andere Signatur als erwartet: nicht erneut versuchen
                self._pair_failed = True
                log.warning('Schneller Bildweg (_render_pair) nicht nutzbar (%s) - verwende render_frame', exc)
        then, now = self.lab.render_frame(result, source, position)
        return self._encode(then, width), self._encode(now, width)

    def image(self, token, kind, index, width=None):
        """JPEG-Bytes eines Anzeigebilds; ``LookupError`` ohne Ergebnis/Frame, ``ValueError`` wenn das Bild fehlt."""
        if kind not in ('then', 'now'):
            raise LookupError('kind')
        entry = self.entry(token)
        if entry is None:
            raise LookupError('token')
        result, source = entry
        if not 0 <= index < len(result.frames):
            raise LookupError('index')
        width = valid_width(width)
        key = (token, index, width, hud_style.resolve(None))

        def factory():
            with self._slots:
                return self._render(result, source, index, width)

        pair = self.cache.get(key, factory)
        return pair[0] if kind == 'then' else pair[1]


# ------------------------------------------------------------------ Layout
def _children(node):
    children = getattr(node, 'children', None)
    if children is None:
        return []
    return list(children) if isinstance(children, (list, tuple)) else [children]


def _contains_id(node, wanted):
    if getattr(node, 'id', None) == wanted:
        return True
    return any(_contains_id(child, wanted) for child in _children(node))


def viewer_block(viewer, ui):
    """Umschalter + bisherige Wiedergabe (Server, alt) + Browser-Wiedergabe.

    Die alte Anzeige bleibt vollstaendig erhalten (gleiche IDs, gleiche Callbacks) und ist sichtbar, solange das
    JavaScript nicht geladen ist. ``assets/sim_player.js`` schaltet nach dem Laden auf den Browser-Spieler um.
    """
    dbc, html = ui.dbc, ui.html
    try:
        from replay_hud import legend_block
        legend = [legend_block(html)]
    except Exception:  # die Legende ist Beiwerk; ohne sie bleibt der Spieler nutzbar
        legend = []
    player = dbc.Card([dbc.CardHeader('Damals · Jetzt · Wiedergabe im Browser'),
                       dbc.CardBody([html.Div(id='sim-player-root'), *legend])], className='h-100')
    return html.Div([
        html.Div(id='sim-player-switch', className='small mb-2'),
        html.Div(viewer, id='sim-legacy-view'),
        html.Div(player, id='sim-player-wrap', style={'display': 'none'}),
    ], id='sim-viewer-host')


def install_layout(root, ui):
    """Ersetzt im Simulationsbereich die Spalte mit dem Viewer-Card (``sim-img-then``) durch ``viewer_block``.

    Arbeitet am fertigen Komponentenbaum von ``SimLab.wrap_cockpit``; ``sim_lab.py`` bleibt unveraendert. Findet sich
    der Viewer nicht (anderer Aufbau), bleibt der Baum unveraendert und die alte Wiedergabe wirkt wie bisher.
    """
    def find_slot(node):
        """(Elternknoten, Index/None) des kleinsten Knotens, der ``sim-img-then`` enthaelt und ein Card ist."""
        children = _children(node)
        for position, child in enumerate(children):
            if _contains_id(child, 'sim-img-then'):
                deeper = find_slot(child)
                if deeper is not None:
                    return deeper
                if type(child).__name__ == 'Card':
                    return node, position
        return None

    if _contains_id(root, 'sim-viewer-host'):
        return root                               # bereits eingebaut (idempotent)
    slot = find_slot(root)
    if slot is None:
        log.warning('Simulation: Viewer nicht gefunden - Browser-Wiedergabe nicht eingebaut')
        return root
    parent, position = slot
    block = viewer_block(_children(parent)[position], ui)
    if isinstance(parent.children, (list, tuple)):
        items = list(parent.children)
        items[position] = block
        parent.children = items
    else:
        parent.children = block
    return root


def layout_supported(lab):
    """Wahr, wenn ``install_layout`` den Viewer im Layout von ``SimLab.wrap_cockpit`` findet (Probe auf einem Wegwerf-Baum).

    Nur dann werden die Routen registriert; sonst gaebe es Routen ohne Oberflaeche.
    """
    ui = lab.ui
    try:
        probe = type(lab).wrap_cockpit(ui.html.Div(), ui.dbc, ui.dcc, ui.html)
        install_layout(probe, ui)
        return _contains_id(probe, 'sim-viewer-host')
    except Exception:  # eine Probe darf die Anwendung nie am Start hindern
        log.exception('Layout-Probe der Browser-Wiedergabe fehlgeschlagen')
        return False


# ------------------------------------------------------------------ Routen und Callback
def register(app, lab):
    """Registriert die Routen (nur mit Flask-Server; mit der Dash-Attrappe der Tests nur das Backend)."""
    backend = PlayerBackend(lab)
    lab.player_backend = backend
    if not layout_supported(lab):
        log.warning('Simulation: Aufbau des Viewers unbekannt - Browser-Wiedergabe nicht aktiviert (alte Wiedergabe bleibt)')
        return backend
    server = getattr(app, 'server', None)
    if server is not None and not getattr(server, '_c2c_sim_player', False):
        from flask import Response, jsonify, request
        server._c2c_sim_player = True

        @server.route(URL_PREFIX + '/current.json')
        def c2c_sim_current():
            response = jsonify({'token': backend.current_token()})
            response.headers['Cache-Control'] = 'no-store'
            return response

        @server.route(URL_PREFIX + '/<token>/meta.json')
        def c2c_sim_meta(token):
            meta = backend.meta(token)
            if meta is None:
                return Response('Kein Ergebnis', status=404, mimetype='text/plain')
            response = jsonify(meta)
            response.headers['Cache-Control'] = 'no-store'
            return response

        @server.route(URL_PREFIX + '/<token>/<kind>/<int:index>.jpg')
        def c2c_sim_image(token, kind, index):
            try:
                data = backend.image(token, kind, index, request.args.get('w'))
            except LookupError:
                return Response('Nicht gefunden', status=404, mimetype='text/plain')
            except (ValueError, OSError, cv2.error) as exc:
                return Response(f'Bild nicht verfuegbar: {exc}', status=404, mimetype='text/plain')
            response = Response(data, mimetype='image/jpeg')
            response.headers['Cache-Control'] = 'private, max-age=3600'
            return response

    return backend
