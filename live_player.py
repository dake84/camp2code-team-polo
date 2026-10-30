"""Livebild im Browser (Paket I5).

Bisher kam das Livebild ueber den Dash-Takt des Cockpit-Tabs (``poll_interval_ms = 700``): rund 1,4 Bilder pro Sekunde, je Bild
ein Dash-Callback mit Base64-Bild im JSON. Das wirkt ruckelig. Hier holt der Browser sich die Bilder selbst:

    GET /_c2c/live/frame.jpg?after=<id>&w=<breite>&q=<zeit>   neuestes Bild NACH ``after`` (Long-Poll bis 0,5 s), JPEG mit HUD
    GET /_c2c/live/release?q=<zeit>                           Browser-Wiedergabe beendet (zurueck zur bisherigen Anzeige)

Der Browser fragt immer erst nach dem Eintreffen des vorigen Bildes das naechste (kein Stau, kein Verzoegerungsaufbau, passt
sich Netz und Pi an) und begrenzt die Bildrate (Standard 10 pro Sekunde). Es wird nur gerendert, was angefordert wird.

Schutz der Fahrt: Rendern ist auf EINEN gleichzeitigen Vorgang begrenzt, ein Bild wird je Frame, Breite und Stil nur einmal
berechnet (mehrere Browser teilen es), und das Bild wird VOR dem Zeichnen verkleinert (Linien/ROI/HUD rechnen aus der
Bildgroesse; siehe ``camera_geometry``), statt auf dem grossen Aufnahmebild zu zeichnen. Solange der Browser Bilder holt
(Herzschlag = jede Anfrage, 3 s gueltig), rendert der Cockpit-Callback ``cockpit_view`` kein eigenes Bild mehr
(``browser_active``); nach dem Ende faellt alles automatisch auf die bisherige Anzeige zurueck. Kein neuer Dash-Callback.

Dieses Modul importiert kein Dash.
"""
import dataclasses
import logging
import threading
import time
from collections import OrderedDict

import cv2

import hud_render
import hud_style
from dashboard_overlay import render_calibration_overlay, render_overlay
from dashboard_steering import draw_steering_overlay
from replay_hud import draw_hud, row_from_camera

log = logging.getLogger(__name__)

URL_PREFIX = '/_c2c/live'
WIDTHS = (0, 640, 960, 1280)        # 0 = Originalgroesse des Aufnahmebilds
DEFAULT_WIDTH = 640
STYLE_CHOICES = ('follow', 'off', 'classic', 'jetpilot', 'arcade')   # follow = Auswahl "HUD" im Cockpit
JPEG_QUALITY = 75
WAIT_S = 0.5                         # Long-Poll: so lange wartet eine Anfrage auf ein neueres Bild
POLL_S = 0.01
ACTIVE_FOR_S = 3.0                   # so lange gilt "Browser holt Bilder" ohne neue Anfrage
CACHE_ITEMS = 6
MAX_SIDE = 4096


def valid_width(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return value if value in WIDTHS else DEFAULT_WIDTH


def valid_style(value):
    return value if value in STYLE_CHOICES else 'follow'


def _int(value, default=-1):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _float_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float('inf') else None


class LiveBackend:
    """Waehlt das anzuzeigende Livebild (wie ``cockpit_view``), rendert es und haelt den Herzschlag des Browsers."""

    def __init__(self, application, car, measure=None):
        self.application = application
        self.car = car
        self._measure = measure
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()          # hoechstens ein Bild gleichzeitig
        self._cache = OrderedDict()
        self._active_until = 0.0
        self._released_q = 0.0
        self.renders = 0
        self.hits = 0

    # ---- Herzschlag
    def beat(self, q=None):
        """Eine Bildanfrage = Browser holt Bilder. Eine Anfrage, die vor dem letzten ``release`` gestartet wurde, zaehlt nicht
        (parallele Anfragen koennen vertauscht eintreffen)."""
        with self._lock:
            if q is not None and q <= self._released_q:
                return False
            self._active_until = time.monotonic() + ACTIVE_FOR_S
            return True

    def release(self, q=None):
        with self._lock:
            if q is not None:
                self._released_q = max(self._released_q, q)
            self._active_until = 0.0

    def active(self):
        with self._lock:
            return time.monotonic() < self._active_until

    # ---- Quelle: dieselbe Auswahl wie der Cockpit-Callback
    def source(self):
        """``(angezeigte Kameradaten, Datensatz mit Fahrentscheidung oder None)``; ``(None, None)`` ohne Bild."""
        car = self.car
        camera = car.camera_data
        running = self.application.running()
        record = car.frame_history.latest_complete() if running else None
        shown = record.camera if record is not None else camera
        if shown is None or getattr(shown, 'frame_id', -1) < 0 or getattr(shown, 'raw_image', None) is None:
            return None, None
        return shown, record

    def _wait_for_newer(self, after, timeout=WAIT_S):
        """Wartet (leicht, ohne den Ringbuffer-Lock) bis ein Bild mit ``frame_id > after`` vorliegt."""
        deadline = time.monotonic() + timeout
        while True:
            camera = self.car.camera_data
            newest = getattr(camera, 'frame_id', -1) if camera is not None else -1
            if newest > after:
                shown, record = self.source()
                if shown is not None and shown.frame_id > after:
                    return shown, record
            if time.monotonic() >= deadline:
                return None, None
            time.sleep(POLL_S)

    # ---- Rendern
    @staticmethod
    def _scaled(camera, width):
        """Kopie der Kameradaten mit verkleinertem Rohbild; ``None`` = nicht moeglich (dann auf dem Original zeichnen).

        Linien, ROI und HUD-Schrift leiten sich aus der Bildgroesse ab (``dashboard_overlay.roi_rows``, ``lanes_for_raw`` mit
        ``process_size``). Ohne ``process_size`` (aeltere Daten) gelten die Linien im Aufnahmebild: dann nicht verkleinern.
        """
        image = camera.raw_image
        if not width or image.shape[1] <= width or not getattr(camera, 'process_size', None):
            return None
        scale = width / image.shape[1]
        small = cv2.resize(image, (int(width), max(1, int(round(image.shape[0] * scale)))), interpolation=cv2.INTER_AREA)
        try:
            return dataclasses.replace(camera, raw_image=small)
        except TypeError:
            return None

    def _draw(self, shown, record, width, style):
        camera = self._scaled(shown, width) or shown
        driving = record.driving if record is not None else None
        if style == 'off':
            overlay = render_overlay(camera, driving) if record is not None else render_calibration_overlay(camera)
            image = draw_steering_overlay(overlay, self.car.steering_angle)
        else:
            image = draw_hud(camera.raw_image, row_from_camera(camera, driving), style=style, trail=hud_render.TRAIL)
        if image is None or getattr(image, 'size', 0) == 0:
            return None
        if width and image.shape[1] > width:                  # Rueckfall: auf dem Original gezeichnet, jetzt verkleinern
            scale = width / image.shape[1]
            image = cv2.resize(image, (int(width), max(1, int(round(image.shape[0] * scale)))), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return encoded.tobytes() if ok else None

    def frame(self, after=-1, width=None, style=None):
        """Neuestes Bild nach ``after`` als ``(jpeg_bytes, info)`` oder ``(None, info)`` (nichts Neues / kein Kamerabild).

        ``info``: frame_id, render_ms (0 = aus dem Cache), age_ms (Alter der Aufnahme beim Senden), running, state.
        """
        width = valid_width(width)
        style = valid_style(style)
        shown, record = self._wait_for_newer(after)
        if shown is None:
            camera = self.car.camera_data
            state = 'no-camera' if camera is None or getattr(camera, 'frame_id', -1) < 0 else 'no-new-frame'
            return None, {'state': state, 'frame_id': -1}
        effective = hud_style.current() if style == 'follow' else style     # off | classic | jetpilot | arcade
        if effective not in ('off', 'classic', 'jetpilot', 'arcade'):
            effective = 'classic'
        key = (shown.frame_id, width, effective, record is not None)
        info = {'frame_id': shown.frame_id, 'running': record is not None, 'state': 'ok', 'render_ms': 0.0}
        with self._render_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self.hits += 1
                data = cached
            else:
                started = time.perf_counter()
                with (self._measure('dashboard.live_player') if self._measure else _null()):
                    data = self._draw(shown, record, width, effective)
                info['render_ms'] = round((time.perf_counter() - started) * 1000, 1)
                if data is None:
                    info['state'] = 'no-image'
                    return None, info
                self.renders += 1
                self._cache[key] = data
                while len(self._cache) > CACHE_ITEMS:
                    self._cache.popitem(last=False)
        monotonic = getattr(shown, 'monotonic_ns', 0) or 0
        info['age_ms'] = round((time.monotonic_ns() - monotonic) / 1e6, 1) if monotonic else None
        return data, info


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ------------------------------------------------------------------ Verbindung zu Dash
_ACTIVE = None            # das registrierte Backend (fuer ``browser_active`` im Callback ``cockpit_view``)


def browser_active():
    """Aufruf im Callback ``cockpit_view``: True = der Browser holt gerade selbst Bilder, kein serverseitiges Bild noetig.
    Nie eine Ausnahme."""
    backend = _ACTIVE
    if backend is None:
        return False
    try:
        return backend.active()
    except Exception:
        log.exception('browser_active fehlgeschlagen')
        return False


def bar(html):
    """Leerer Mount-Punkt fuer die Bedienleiste (baut ``assets/live_player.js``); eine Zeile im Layout des Cockpits."""
    return html.Div(id='live-player-bar', className='c2c-lp-bar small mb-2')


def register(app, tab):
    """Registriert Backend und HTTP-Routen. Kein Dash-Callback. Mit der Dash-Attrappe der Tests (kein Flask) nur das Backend."""
    global _ACTIVE
    ctx = tab.ctx
    backend = LiveBackend(ctx.application, ctx.car, getattr(ctx, 'measure', None))
    tab.live_backend = backend
    _ACTIVE = backend
    server = getattr(app, 'server', None)
    if server is None or getattr(server, '_c2c_live_player', False):
        return backend
    from flask import Response, request
    server._c2c_live_player = True

    @server.route(URL_PREFIX + '/frame.jpg')
    def c2c_live_frame():
        q = _float_or_none(request.args.get('q'))
        backend.beat(q)
        try:
            data, info = backend.frame(_int(request.args.get('after'), -1), request.args.get('w'), request.args.get('st'))
        except Exception as exc:  # ein Renderfehler darf das Dashboard nicht beeintraechtigen
            log.exception('Livebild nicht renderbar')
            response = Response(f'Fehler: {type(exc).__name__}', status=500, mimetype='text/plain')
            response.headers['Cache-Control'] = 'no-store'
            return response
        headers = {'Cache-Control': 'no-store', 'X-C2C-State': info.get('state', ''), 'X-C2C-Frame': str(info.get('frame_id', -1))}
        if data is None:
            return Response(status=204, headers=headers)
        headers.update({'X-C2C-Render-Ms': str(info.get('render_ms', 0.0)), 'X-C2C-Running': '1' if info.get('running') else '0'})
        if info.get('age_ms') is not None:
            headers['X-C2C-Age-Ms'] = str(info['age_ms'])
        return Response(data, mimetype='image/jpeg', headers=headers)

    @server.route(URL_PREFIX + '/release')
    def c2c_live_release():
        backend.release(_float_or_none(request.args.get('q')))
        return Response(status=204, headers={'Cache-Control': 'no-store'})

    return backend
