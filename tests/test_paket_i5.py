"""Paket I5: Livebild im Browser (live_player.py, assets/live_player.js).

Hardwarefrei und kurz. Geprueft werden: Quellenauswahl wie im Cockpit-Callback, Long-Poll, Cache und Begrenzung auf ein Bild
gleichzeitig, Verkleinern vor dem Zeichnen (Geometrie wie auf dem Original), Rueckfall ohne ``process_size``, Herzschlag
(Reihenfolge, Ablauf), Layout-Einbau ohne verlorene IDs, ``cockpit_view`` rendert nicht, wenn der Browser abspielt, keine
zusaetzlichen Dash-Callbacks, HTTP-Routen mit echtem Dash und - mit Headless-Chromium (``C2C_CHROMIUM=<Pfad>``, Playwright und
Dash noetig, auf dem Pi nicht vorgesehen) - das Verhalten im Browser: Bilder ohne Dash-Bildtransport, Bildrate und Begrenzung,
Groesse, Umschalter, Verbergen, Wiederaufnahme.
Nicht getestet: Optik (die Sandbox hat kein Bootstrap-CSS), Laufzeit und Kamera auf dem Raspberry Pi.
"""
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import hud_style  # noqa: E402
import live_player  # noqa: E402
from models import CameraData, DrivingState  # noqa: E402

HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
ROOT = Path(__file__).resolve().parent.parent
LANES = ((-1.2, 166.0), (1.6, -317.0))


def make_camera(frame_id, size=(1296, 972), process_size=(320, 240), monotonic_ns=None):
    """Kameradaten wie im Betrieb: grosses Aufnahmebild, Linien im Verarbeitungsbild (``process_size``)."""
    width, height = size
    image = np.full((height, width, 3), (180, 160, 200), np.uint8)
    cv2.line(image, (width // 5, height - 1), (width // 2 - 60, 0), (200, 40, 20), 18)
    cv2.line(image, (width * 4 // 5, height - 1), (width // 2 + 60, 0), (200, 40, 20), 18)
    return CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=image, frame_id=frame_id,
                      monotonic_ns=time.monotonic_ns() if monotonic_ns is None else monotonic_ns, crop=(0.15, 0.55),
                      lanes=LANES, process_size=process_size)


def publish(fake, frame_id, decision=True, **kwargs):
    camera = make_camera(frame_id, **kwargs)
    fake.car.camera_data = camera
    fake.car.frame_history.add_camera(camera)
    if decision:
        fake.car.frame_history.add_driving(DrivingState(frame_id, time.time_ns(), 95, 30, LANES, 99, 'camera', 99,
                                                        time.monotonic_ns()))
    return camera


def fake_application(running=True):
    from test_dashboard_callbacks import FakeApplication
    fake = FakeApplication()
    fake._running = running
    return fake


def decode(data):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


class Base(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.addCleanup(hud_style.set_current, hud_style.current())
        self.addCleanup(setattr, live_player, '_ACTIVE', None)


# ------------------------------------------------------------------ Server: Quelle, Long-Poll, Bilder
class BackendTests(Base):
    def setUp(self):
        super().setUp()
        self.fake = fake_application(running=True)
        self.backend = live_player.LiveBackend(self.fake, self.fake.car)
        patcher = mock.patch.object(live_player, 'WAIT_S', 0.15)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_quelle_wie_im_cockpit_callback(self):
        self.assertEqual(self.backend.source(), (None, None))                         # noch kein Kamerabild
        camera = make_camera(-1)                                                       # Platzhalter beim Start
        self.fake.car.camera_data = camera
        self.assertEqual(self.backend.source(), (None, None))
        publish(self.fake, 3, decision=False)                                          # laeuft, aber noch ohne Entscheidung
        shown, record = self.backend.source()
        self.assertEqual((shown.frame_id, record), (3, None))
        publish(self.fake, 4)
        shown, record = self.backend.source()
        self.assertEqual((shown.frame_id, record.driving.frame_id), (4, 4))
        self.fake._running = False                                                     # Kalibrierung: Kamerabild ohne Entscheidung
        shown, record = self.backend.source()
        self.assertEqual((shown.frame_id, record), (4, None))

    def test_bild_ist_jpeg_in_gewaehlter_breite_mit_infos(self):
        publish(self.fake, 7)
        for width, expected in ((640, 640), (960, 960), (1280, 1280), (0, 1296)):
            data, info = self.backend.frame(-1, width, 'classic')
            self.assertEqual(decode(data).shape[1], expected, width)
        self.assertEqual((info['frame_id'], info['state'], info['running']), (7, 'ok', True))
        self.assertLess(info['age_ms'], 2000)
        self.assertEqual(decode(self.backend.frame(-1, 'x', 'classic')[0]).shape[1], live_player.DEFAULT_WIDTH)   # ungueltig -> Standard

    def test_langes_warten_nichts_neues_und_neues_bild_waehrend_des_wartens(self):
        publish(self.fake, 10)
        started = time.monotonic()
        data, info = self.backend.frame(10, 640, 'classic')                            # kein neueres Bild
        self.assertIsNone(data)
        self.assertEqual(info['state'], 'no-new-frame')
        self.assertGreaterEqual(time.monotonic() - started, 0.14)
        timer = threading.Timer(0.05, publish, args=(self.fake, 11))
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        data, info = self.backend.frame(10, 640, 'classic')                            # Bild trifft waehrend des Wartens ein
        self.assertEqual(info['frame_id'], 11)
        self.assertLess(time.monotonic() - started, 0.14)                              # sofort, nicht erst nach dem Timeout
        self.assertEqual(self.backend.frame(-1, 640, 'classic')[1]['frame_id'], 11)    # after=-1: das neueste, ohne Warten

    def test_ohne_kamerabild(self):
        data, info = self.backend.frame(-1, 640, 'classic')
        self.assertEqual((data, info['state']), (None, 'no-camera'))

    def test_wartet_nur_auf_dem_leichten_weg(self):
        """Das Warten fragt den Ringbuffer (Lock der Regelschleife) nicht ab, solange kein neueres Kamerabild da ist."""
        publish(self.fake, 5)
        calls = []
        real = self.fake.car.frame_history.latest_complete
        self.fake.car.frame_history.latest_complete = lambda: calls.append(1) or real()
        self.backend.frame(5, 640, 'classic')                                          # wartet 0,15 s
        self.assertEqual(calls, [])

    def test_cache_ein_bild_je_frame_breite_und_stil(self):
        publish(self.fake, 20)
        spy = mock.Mock(wraps=self.backend._draw)
        with mock.patch.object(self.backend, '_draw', spy):
            first, _ = self.backend.frame(-1, 640, 'classic')
            again, info = self.backend.frame(-1, 640, 'classic')
            self.assertEqual((first, info['render_ms']), (again, 0.0))
            self.assertEqual(spy.call_count, 1)
            self.backend.frame(-1, 960, 'classic')
            self.backend.frame(-1, 640, 'arcade')
            self.assertEqual(spy.call_count, 3)
            publish(self.fake, 21)
            self.backend.frame(20, 640, 'classic')
            self.assertEqual(spy.call_count, 4)
        self.assertLessEqual(len(self.backend._cache), live_player.CACHE_ITEMS)
        for item in range(30, 45):
            publish(self.fake, item)
            self.backend.frame(item - 1, 640, 'classic')
        self.assertLessEqual(len(self.backend._cache), live_player.CACHE_ITEMS)       # begrenzt

    def test_hoechstens_ein_bild_gleichzeitig_und_gleiche_anfragen_rechnen_einmal(self):
        publish(self.fake, 40)
        concurrent, peak, calls = [0], [0], []
        real = self.backend._draw

        def slow(shown, record, width, style):
            concurrent[0] += 1
            peak[0] = max(peak[0], concurrent[0])
            calls.append((width, style))
            time.sleep(0.1)
            try:
                return real(shown, record, width, style)
            finally:
                concurrent[0] -= 1
        results = []
        with mock.patch.object(self.backend, '_draw', slow):
            same = [threading.Thread(target=lambda: results.append(self.backend.frame(-1, 640, 'classic')[0])) for _ in range(4)]
            other = [threading.Thread(target=lambda w=w: results.append(self.backend.frame(-1, w, 'classic')[0])) for w in (960, 1280)]
            [t.start() for t in same + other]
            [t.join(15) for t in same + other]
        self.assertEqual(peak[0], 1)                                                    # nie zwei Zeichenvorgaenge parallel
        self.assertEqual(sorted(calls), [(640, 'classic'), (960, 'classic'), (1280, 'classic')])   # gleiche Anfragen: einmal
        self.assertEqual(len(results), 6)

    def test_verkleinern_vor_dem_zeichnen_entspricht_dem_zeichnen_auf_dem_original(self):
        """Linien, ROI und HUD liegen auf dem verkleinerten Bild an denselben Stellen wie auf dem verkleinerten Original."""
        from dashboard_overlay import render_overlay
        from dashboard_steering import draw_steering_overlay
        import hud_render
        from replay_hud import draw_hud, row_from_camera
        publish(self.fake, 50)
        shown, record = self.backend.source()
        for style in ('off', 'classic', 'jetpilot', 'arcade'):
            fast = decode(self.backend._draw(shown, record, 640, style))
            if style == 'off':
                full = draw_steering_overlay(render_overlay(shown, record.driving), self.fake.car.steering_angle)
            else:
                full = draw_hud(shown.raw_image, row_from_camera(shown, record.driving), style=style, trail=hud_render.HudTrail())
            slow = cv2.resize(full, (640, 480), interpolation=cv2.INTER_AREA)
            self.assertEqual(fast.shape, slow.shape, style)
            blur = lambda image: cv2.GaussianBlur(image, (0, 0), 3).astype(np.int16)          # Strichstaerke/Kanten ausgleichen
            self.assertLess(float(np.abs(blur(fast) - blur(slow)).mean()), 4.0, style)
        self.assertEqual(shown.raw_image.shape[1], 1296)                                # das veroeffentlichte Rohbild bleibt unveraendert
        for style, target in (('classic', 'draw_hud'), ('off', 'render_overlay')):
            spy = mock.Mock(wraps=getattr(live_player, target))
            with mock.patch.object(live_player, target, spy):
                self.backend._draw(shown, record, 640, style)
            first = spy.call_args[0][0]
            width = first.raw_image.shape[1] if hasattr(first, 'raw_image') else first.shape[1]
            self.assertEqual(width, 640, f'{style}: gezeichnet wird auf dem verkleinerten Bild (schneller), nicht auf dem Original')

    def test_rueckfall_ohne_process_size_zeichnet_auf_dem_original(self):
        publish(self.fake, 60, process_size=None)
        spy = mock.Mock(wraps=live_player.draw_hud)
        with mock.patch.object(live_player, 'draw_hud', spy):
            data, _ = self.backend.frame(-1, 640, 'classic')
        self.assertEqual(decode(data).shape[1], 640)
        self.assertEqual(spy.call_args[0][0].shape[1], 1296)                            # Linien gelten im Aufnahmebild

    def test_stil_folgt_dem_cockpit_oder_ist_gewaehlt(self):
        publish(self.fake, 70)
        hud_style.set_current('arcade')
        self.assertEqual(self.backend.frame(-1, 640, 'follow')[0], self.backend.frame(-1, 640, 'arcade')[0])
        hud_style.set_current('off')                                                    # off = Linien-Overlay (bisheriger Standard)
        self.assertEqual(self.backend.frame(-1, 640, 'follow')[0], self.backend.frame(-1, 640, 'off')[0])
        self.assertNotEqual(self.backend.frame(-1, 640, 'off')[0], self.backend.frame(-1, 640, 'classic')[0])
        self.assertEqual(self.backend.frame(-1, 640, 'gibt_es_nicht')[0], self.backend.frame(-1, 640, 'off')[0])   # -> follow

    def test_kalibrierung_ohne_laufende_fahrt(self):
        self.fake._running = False
        publish(self.fake, 80, decision=False)
        data, info = self.backend.frame(-1, 640, 'off')
        self.assertEqual((info['running'], decode(data).shape[1]), (False, 640))
        self.assertTrue(self.backend.frame(-1, 640, 'jetpilot')[0])

    def test_herzschlag_reihenfolge_und_ablauf(self):
        self.assertFalse(self.backend.active())
        self.assertTrue(self.backend.beat(1000.0))
        self.assertTrue(self.backend.active())
        self.backend.release(2000.0)
        self.assertFalse(self.backend.active())
        self.assertFalse(self.backend.beat(1500.0))                                     # aelter als release: zaehlt nicht
        self.assertFalse(self.backend.active())
        self.assertTrue(self.backend.beat(2500.0))
        self.assertTrue(self.backend.active())
        with mock.patch.object(live_player.time, 'monotonic', return_value=time.monotonic() + 60):
            self.assertFalse(self.backend.active())                                     # Herzschlag abgelaufen: Rueckfall auf Dash
        self.assertTrue(self.backend.beat())                                            # ohne Zeitstempel: immer

    def test_modulfunktion_browser_active_ist_harmlos(self):
        live_player._ACTIVE = None
        self.assertFalse(live_player.browser_active())
        live_player._ACTIVE = mock.Mock(active=mock.Mock(side_effect=RuntimeError('x')))
        self.assertFalse(live_player.browser_active())                                  # Fehler im Backend: kein Fehler im Callback
        live_player._ACTIVE = self.backend
        self.backend.beat()
        self.assertTrue(live_player.browser_active())

    def test_eingaben_werden_geprueft(self):
        self.assertEqual([live_player.valid_width(v) for v in ('640', 0, 960, 1280, 777, None, 'x')], [640, 0, 960, 1280, 640, 640, 640])
        self.assertEqual([live_player.valid_style(v) for v in ('off', 'classic', 'x', None)], ['off', 'classic', 'follow', 'follow'])
        self.assertEqual([live_player._int(v) for v in ('5', '5.9', 'x', None, 'inf')], [5, 5, -1, -1, -1])


# ------------------------------------------------------------------ Layout und Dash (Attrappe)
class DashStubTests(Base):
    def setUp(self):
        super().setUp()
        from test_dashboard_callbacks import load_dashboard
        self.module, self.context, self.ctx = load_dashboard()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.fake = fake_application(running=False)
        self.app = self.module.create_app(self.fake)
        self.tab = next(t for t in self.app.c2c_tabs if t.tab_id == 'drive')
        self.cb = self.app.callbacks

    @staticmethod
    def ids(node):
        found = []

        def walk(item):
            if getattr(item, 'id', None) is not None:
                found.append(item.id)
            children = getattr(item, 'children', None)
            for child in (children if isinstance(children, (list, tuple)) else [children]):
                if child is not None and not isinstance(child, str):
                    walk(child)
        walk(node)
        return found

    def test_bedienleiste_steht_vor_dem_bild_und_keine_id_geht_verloren(self):
        ids = self.ids(self.tab.layout())
        for wanted in ('drive-overlay', 'drive-frame-badge', 'drive-age', 'cockpit-hud', 'drive-angle', 'drive-speed',
                       'drive-requested', 'drive-raw', 'drive-mode-status', 'drive-diagnostic', 'drive-rendered',
                       'drive-to-studio', 'drive-events', 'sim-wrap', 'cockpit-live-wrap'):
            self.assertIn(wanted, ids, wanted)
        self.assertEqual(ids.count('live-player-bar'), 1)
        self.assertEqual(len(ids), len(set(ids)), 'doppelte IDs')
        self.assertEqual(ids.index('live-player-bar') + 1, ids.index('drive-overlay'))             # unmittelbar vor dem Bild
        self.assertIsNotNone(self.tab.live_backend)

    def test_keine_zusaetzlichen_dash_callbacks(self):
        self.assertFalse([name for name in self.cb if 'live' in name and 'player' in name])
        self.assertIn('cockpit_view', self.cb)
        self.assertEqual(len(self.app.clientside), 2)                                              # nur die beiden Frame-Balken

    def test_cockpit_view_rendert_nicht_wenn_der_browser_die_bilder_holt(self):
        view = self.cb['cockpit_view']
        publish(self.fake, 5, decision=False, size=(640, 480))
        with mock.patch('dashboard.tabs.driving.draw_hud', wraps=__import__('replay_hud').draw_hud) as hud_spy:
            image, key, *_ = view(1, 'drive', None, 'classic')                                     # bisheriges Verhalten
            self.assertTrue(image.startswith('data:image'))
            self.assertEqual(key, 'cal:5:hud-classic')
            self.assertEqual(hud_spy.call_count, 1)
            self.tab.live_backend.beat()                                                           # Browser holt Bilder
            publish(self.fake, 6, decision=False, size=(640, 480))
            image, key, *rest = view(2, 'drive', 'cal:5:hud-classic', 'classic')
            self.assertIs(image, self.module.no_update)
            self.assertEqual(key, 'cal:5:hud-classic')                                             # Schluessel bleibt: spaeterer Rueckfall rendert
            self.assertEqual(hud_spy.call_count, 1)
            self.assertEqual(rest[-1] is not None, True)                                           # Kennzahlen laufen weiter
            self.assertEqual(hud_style.current(), 'classic')                                       # HUD-Auswahl wird weiter uebernommen
            self.tab.live_backend.release()
            image, key, *_ = view(3, 'drive', 'cal:5:hud-classic', 'classic')                      # zurueck: Dash rendert wieder
            self.assertTrue(image.startswith('data:image'))
            self.assertEqual(key, 'cal:6:hud-classic')

    def test_cockpit_view_bei_laufender_fahrt_mit_entscheidung(self):
        self.fake._running = True
        publish(self.fake, 9, size=(640, 480))
        image, key, angle, *_ = self.cb['cockpit_view'](1, 'drive', None, 'off')
        self.assertEqual(key, 'drive:9')
        self.tab.live_backend.beat()
        self.assertIs(self.cb['cockpit_view'](2, 'drive', key, 'off')[0], self.module.no_update)


# ------------------------------------------------------------------ Echtes Dash/Flask ohne Browser
@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashTests(Base):
    def setUp(self):
        super().setUp()
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        self.addCleanup(self.stubs.__exit__, None, None, None)
        self.pin = hardware_stubs.pin_pipeline_config()
        self.addCleanup(self.pin.stop)
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        self.fake = fake_application(running=True)
        self.app = module.create_app(self.fake)
        self.tab = next(t for t in self.app.c2c_tabs if t.tab_id == 'drive')
        self.client = self.app.server.test_client()
        patcher = mock.patch.object(live_player, 'WAIT_S', 0.15)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_routen_kopfzeilen_und_assets(self):
        client = self.client
        response = client.get('/_c2c/live/frame.jpg?after=-1&w=640&st=classic&q=1000')
        self.assertEqual((response.status_code, response.headers['X-C2C-State']), (204, 'no-camera'))      # noch kein Bild
        publish(self.fake, 3, size=(640, 480))
        response = client.get('/_c2c/live/frame.jpg?after=-1&w=640&st=classic&q=2000')
        self.assertEqual((response.status_code, response.mimetype), (200, 'image/jpeg'))
        self.assertEqual((response.headers['X-C2C-Frame'], response.headers['X-C2C-Running'], response.headers['Cache-Control']),
                         ('3', '1', 'no-store'))
        self.assertGreaterEqual(float(response.headers['X-C2C-Render-Ms']), 0.0)
        self.assertIn('X-C2C-Age-Ms', response.headers)
        self.assertEqual(decode(response.data).shape[1], 640)
        self.assertTrue(self.tab.live_backend.active())                                           # Anfrage = Herzschlag
        again = client.get('/_c2c/live/frame.jpg?after=3&w=640&st=classic&q=2001')
        self.assertEqual((again.status_code, again.headers['X-C2C-State'], again.headers['X-C2C-Frame']), (204, 'no-new-frame', '-1'))
        self.assertEqual(client.get('/_c2c/live/release?q=3000').status_code, 204)
        self.assertFalse(self.tab.live_backend.active())
        client.get('/_c2c/live/frame.jpg?after=2&w=640&q=2500')                                    # aelter als release: kein Herzschlag
        self.assertFalse(self.tab.live_backend.active())
        for path in ('/_c2c/live/frame.jpg?after=x&w=abc&st=gibtsnicht&q=y', '/_c2c/live/frame.jpg', '/_c2c/live/release'):
            self.assertIn(client.get(path).status_code, (200, 204), path)                         # ungueltige Eingaben: kein Fehler
        for asset in ('live_player.js', 'live_player.css'):
            response = client.get(f'/assets/{asset}')
            self.assertEqual(response.status_code, 200, asset)
            response.close()
        self.assertIn('live_player.js', client.get('/').get_data(as_text=True))

    def test_renderfehler_ist_ein_500_und_das_dashboard_laeuft_weiter(self):
        publish(self.fake, 4, size=(640, 480))
        with mock.patch.object(self.tab.live_backend, '_draw', side_effect=RuntimeError('kaputt')):
            response = self.client.get('/_c2c/live/frame.jpg?after=-1&w=640')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_data(as_text=True), 'Fehler: RuntimeError')                  # eigener Handler (Flasks Standardseite wuerde Text enthalten)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get('/_c2c/live/frame.jpg?after=-1&w=640').status_code, 200)

    def test_abhaengigkeiten_und_layout(self):
        deps = self.client.get('/_dash-dependencies').get_json()
        outputs = [d['output'] for d in deps]
        self.assertEqual(len(outputs), len(set(outputs)))
        self.assertFalse([d for d in deps if 'live-player' in d['output']])
        layout = json.dumps(self.client.get('/_dash-layout').get_json())
        for wanted in ('live-player-bar', 'drive-overlay', 'cockpit-hud'):
            self.assertIn(wanted, layout)


# ------------------------------------------------------------------ Browser (optional)
CHROMIUM = os.environ.get('C2C_CHROMIUM', '')
BROWSER = bool(CHROMIUM and os.path.exists(CHROMIUM) and HAS_DASH and importlib.util.find_spec('playwright'))


@unittest.skipUnless(BROWSER, 'Browsertest: C2C_CHROMIUM=<Pfad zu chrome-headless-shell> setzen (Playwright und Dash noetig)')
class BrowserTests(unittest.TestCase):
    """Echter Browser (Headless-Chromium) mit einer Kamera-Attrappe, die mit 25 Bildern pro Sekunde veroeffentlicht."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        cls.pin = hardware_stubs.pin_pipeline_config()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = fake_application(running=True)
        publish(cls.fake, 1, size=(640, 480))
        cls.app = module.create_app(cls.fake)
        cls.tab = next(t for t in cls.app.c2c_tabs if t.tab_id == 'drive')
        cls.stop_camera = threading.Event()
        cls.counter = [1]

        def camera_thread():
            while not cls.stop_camera.is_set():
                cls.counter[0] += 1
                publish(cls.fake, cls.counter[0], size=(640, 480))
                time.sleep(0.04)
        cls.camera = threading.Thread(target=camera_thread, daemon=True)
        cls.camera.start()
        from werkzeug.serving import make_server
        cls.server = make_server('127.0.0.1', 0, cls.app.server, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=CHROMIUM, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.stop_camera.set()
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)
        live_player._ACTIVE = None

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 1300})
        self.page.set_default_timeout(30000)
        self.errors, self.live_requests, self.dash_payloads = [], [], []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda r: self.live_requests.append(r.url) if '/_c2c/live/frame.jpg' in r.url else None)

        def on_response(response):
            if '_dash-update-component' in response.url:
                try:
                    self.dash_payloads.append(response.text())
                except Exception:  # Antwort schon verworfen (Seite geschlossen)
                    pass
        self.page.on('response', on_response)
        self.page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page.goto(self.url, wait_until='domcontentloaded')
        self.page.wait_for_selector('#main-tabs')
        self.page.evaluate("() => window.localStorage.clear()")
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_selector('#live-player-bar')
        # Die Sandbox laedt das Bootstrap-CSS nicht (CDN gesperrt): die zwei Regeln, die eingeklappte Bereiche und inaktive Tabs
        # verbergen, werden nachgebildet (im echten Browser kommen sie aus Bootstrap).
        self.page.add_style_tag(content='.collapse:not(.show){display:none}.tab-content>.tab-pane{display:none}'
                                        '.tab-content>.tab-pane.active{display:block}')

    def pause_polling(self):
        """Dash-Takte anhalten (wie in den Browsertests zu I3/I4): auf einem Kern wuerden sie die Messung dominieren."""
        self.page.evaluate("() => window.dash_clientside.set_props('refresh', {disabled: true})")
        self.page.wait_for_timeout(1200)

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def stats(self):
        return self.page.evaluate('() => window.c2cLivePlayer.stats()')

    def wait_running(self, shown=5):
        self.page.wait_for_function(f"() => {{ const s = window.c2cLivePlayer.stats(); return s.running && s.shown >= {shown}; }}")

    def count_changes(self, seconds):
        """Anzahl der Bildwechsel des Livebilds in ``seconds`` (MutationObserver auf src)."""
        self.page.evaluate("() => { window.__n = 0; if (window.__obs) window.__obs.disconnect(); window.__obs = new MutationObserver(() => window.__n++);"
                           " window.__obs.observe(document.getElementById('drive-overlay'), {attributes: true, attributeFilter: ['src']}); }")
        self.page.wait_for_timeout(int(seconds * 1000))
        return self.page.evaluate('() => window.__n')

    def test_bilder_kommen_aus_dem_browser_nicht_aus_dash(self):
        page = self.page
        self.wait_running()
        self.assertTrue(page.evaluate("() => document.getElementById('drive-overlay').getAttribute('src').startsWith('blob:')"))
        self.assertTrue(page.evaluate("() => document.getElementById('drive-overlay').naturalWidth > 0"))
        page.wait_for_timeout(500)
        self.dash_payloads.clear()
        page.wait_for_timeout(3000)                                                         # mehrere Takte des Cockpit-Callbacks
        self.assertGreaterEqual(len(self.dash_payloads), 2)
        self.assertFalse([p for p in self.dash_payloads if 'drive-overlay' in p],
                         'Dash darf kein Bild mehr liefern, solange der Browser die Bilder holt')
        self.assertTrue(self.tab.live_backend.active())
        self.assertIn('Bilder/s', page.inner_text('#live-player-bar'))

    def test_bildrate_ist_begrenzt_und_einstellbar(self):
        page = self.page
        self.wait_running()
        self.pause_polling()
        ten = self.count_changes(3)                                                          # Standard 10/s, Kamera 25/s
        self.assertGreaterEqual(ten, 18)
        self.assertLessEqual(ten, 36)
        page.select_option('#live-player-bar [data-r=fps]', '5')
        page.wait_for_timeout(600)
        five = self.count_changes(3)
        self.assertLessEqual(five, 19)
        self.assertGreater(ten, five)
        page.select_option('#live-player-bar [data-r=fps]', '25')
        page.wait_for_timeout(600)
        self.assertGreater(self.count_changes(3), ten)                                       # hoeher als 10/s (Kamera liefert 25/s)

    def test_neuestes_bild_geringes_datenalter_und_kein_stau(self):
        page = self.page
        self.wait_running(5)
        self.pause_polling()
        page.wait_for_timeout(1500)
        s = page.evaluate("() => window.c2cLivePlayer.state.stats")
        ages = s['age']
        self.assertTrue(ages)
        self.assertLess(sum(ages) / len(ages), 250, ages)                                    # Bild ist beim Senden hoechstens ~ein Takt alt
        shown_id = page.evaluate("() => window.c2cLivePlayer.state.last")
        self.assertLessEqual(self.counter[0] - shown_id, 12)                                 # angezeigt wird (fast) das neueste Bild

    def test_groesse_wird_gewaehlt_und_gemerkt(self):
        page = self.page
        self.wait_running()
        page.select_option('#live-player-bar [data-r=width]', '960')
        page.wait_for_function("() => window.c2cLivePlayer.stats().width === 960")
        page.wait_for_function("() => window.c2cLivePlayer.state.last >= 0")
        page.wait_for_timeout(800)
        self.assertTrue([u for u in self.live_requests if 'w=960' in u])
        self.assertEqual(page.evaluate("() => window.localStorage.getItem('c2c.lp.width')"), '960')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#live-player-bar [data-r=width]')
        self.assertEqual(page.input_value('#live-player-bar [data-r=width]'), '960')

    def test_hud_auswahl_des_cockpits_wirkt_auf_das_browserbild(self):
        page = self.page
        self.wait_running()
        page.select_option('#cockpit-hud', 'arcade')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and hud_style.current() != 'arcade':           # Dash uebernimmt die Auswahl im naechsten Takt
            page.wait_for_timeout(200)
        self.assertEqual(hud_style.current(), 'arcade')
        key = lambda: [k for k in self.tab.live_backend._cache if k[2] == 'arcade']
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not key():
            page.wait_for_timeout(200)
        self.assertTrue(key(), 'Livebild wurde nicht im gewaehlten Stil gerendert')

    def test_umschalter_alt_und_zurueck(self):
        page = self.page
        self.wait_running()
        page.locator('#live-player-bar').get_by_label('Über den Server (alt)').check()
        page.wait_for_function("() => !window.c2cLivePlayer.stats().running")
        deadline = time.monotonic() + 2.0                 # kuerzer als der Ablauf des Herzschlags (3 s): "release" gibt sofort frei
        while time.monotonic() < deadline and self.tab.live_backend.active():
            page.wait_for_timeout(100)
        self.assertFalse(self.tab.live_backend.active(), 'ohne release bliebe das Bild bis zu 3 s stehen (Dash rendert noch nicht)')
        page.wait_for_function("() => (document.getElementById('drive-overlay').getAttribute('src') || '').startsWith('data:image')")
        self.live_requests.clear()
        page.wait_for_timeout(1500)
        self.assertEqual(self.live_requests, [], 'im Modus "alt" darf der Browser keine Bilder holen')
        page.locator('#live-player-bar').get_by_label('Im Browser (schnell)').check()
        page.wait_for_function("() => window.c2cLivePlayer.stats().running")
        page.wait_for_function("() => (document.getElementById('drive-overlay').getAttribute('src') || '').startsWith('blob:')")

    def test_unsichtbar_stoppt_sichtbar_setzt_fort(self):
        page = self.page
        self.wait_running()
        page.get_by_text('Simulation (Session-Replay)', exact=True).click()                    # Livebild eingeklappt
        page.wait_for_function("() => !window.c2cLivePlayer.stats().running")
        page.get_by_text('Live', exact=True).first.click()
        page.wait_for_function("() => window.c2cLivePlayer.stats().running")
        page.get_by_text('Replay Lab', exact=True).click()                                     # anderer Tab
        page.wait_for_function("() => !window.c2cLivePlayer.stats().running")
        deadline = time.monotonic() + 10                                                       # Herzschlag laeuft ab
        while time.monotonic() < deadline and self.tab.live_backend.active():
            page.wait_for_timeout(200)
        self.assertFalse(self.tab.live_backend.active())
        page.get_by_text('Driving Cockpit', exact=True).click()
        page.wait_for_function("() => window.c2cLivePlayer.stats().running")

    def test_kamera_haelt_an_und_laeuft_weiter(self):
        page = self.page
        self.wait_running()
        self.stop_camera.set()
        time.sleep(0.3)
        try:
            page.wait_for_function("() => window.c2cLivePlayer.stats().empty >= 2", timeout=20000)    # 204: nichts Neues
            self.assertTrue(self.stats()['running'])
            self.assertEqual(self.stats()['errors'], 0)
        finally:
            self.stop_camera.clear()
            self.camera = threading.Thread(target=self._restart_camera, daemon=True)
            self.camera.start()
        page.wait_for_function("() => window.c2cLivePlayer.stats().running")

    def _restart_camera(self):
        while not self.stop_camera.is_set():
            self.counter[0] += 1
            publish(self.fake, self.counter[0], size=(640, 480))
            time.sleep(0.04)

    def test_vollbild_knopf_bleibt_am_bild(self):
        page = self.page
        self.wait_running()
        self.assertEqual(page.get_attribute('.c2c-fs[data-target=drive-overlay]', 'data-target'), 'drive-overlay')
        page.click('.c2c-fs[data-target=drive-overlay]')
        page.wait_for_function("() => document.fullscreenElement && document.fullscreenElement.id === 'drive-overlay'")
        page.wait_for_timeout(600)
        self.assertGreaterEqual(self.stats()['shown'], 3)                                         # Bilder kommen im Vollbild weiter
        page.keyboard.press('Escape')


if __name__ == '__main__':
    unittest.main()
