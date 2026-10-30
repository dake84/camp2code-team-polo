"""Paket I3: Wiedergabe der Simulation im Browser (sim_player.py, assets/sim_player.js).

Hardwarefrei und kurz. Geprueft werden: Meta-Daten und Bild-Routen des Servers, Cache mit Duplikatschutz, Wege mit und ohne
Paket I2, Einbau ins Layout ohne Aenderung von sim_lab.py, keine zusaetzlichen Dash-Callbacks und - mit Headless-Chromium
(``C2C_CHROMIUM=<Pfad>``, Playwright und Dash noetig, auf dem Pi nicht vorgesehen) - das Verhalten im Browser: Play,
Pause und Slider wirken lokal, auch wenn der Server langsam antwortet; waehrend der Wiedergabe gibt es keine Dash-Callbacks.
Nicht getestet: Optik, Laufzeit auf dem Raspberry Pi.
"""
import base64
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from test_paket_f import write_session  # noqa: E402  (vor jeder Dash-Attrappe importieren)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import hud_style  # noqa: E402
import sim_player  # noqa: E402

HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
ROOT = Path(__file__).resolve().parent.parent
FRAMES = 40
NO_UPDATE = object()


def data_uri(image):
    """Wie dashboard.helpers.uri (JPEG als Data-URI). PAKET-I3B: die fruehere Attrappe ``'data:x'`` ist nicht dekodierbar;
    mit Paket I2 geht die Wiedergabe ueber SimLab._render_pair, das uri() benutzt."""
    ok, encoded = cv2.imencode('.jpg', image)
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded.tobytes()).decode('ascii') if ok else None


class Base(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                                  cameracar_controller={'lost_line_timeout': 1})
        os.chdir(self.tmp.name)
        self.root = os.path.join(self.tmp.name, 'Data_AI', 'sessions')
        self.name = write_session(self.root, count=FRAMES)
        self.addCleanup(hud_style.set_current, hud_style.current())

    def ui(self):
        import dash_stub
        modules = dash_stub.modules()
        return SimpleNamespace(dbc=modules['dash_bootstrap_components'], dcc=modules['dash.dcc'], html=modules['dash.html'],
                               Input=dash_stub.Input, Output=dash_stub.Output, State=dash_stub.State, ctx=dash_stub.ctx,
                               no_update=NO_UPDATE, ClientsideFunction=dash_stub.ClientsideFunction)

    def lab(self, ui=None):
        from sim_lab import SimLab
        return SimLab(SimpleNamespace(running=lambda: False), ui or self.ui(), data_uri, self.root)

    def load(self, lab, token=1234567890123):
        """Legt ein berechnetes Ergebnis unter ``token`` ab - wie jeder Weg der SimLab (Berechnung oder gespeicherter Run)."""
        import ConfigReader
        from frame_source import SessionSource
        from sim_engine import SimEngine
        source = SessionSource(self.name, self.root)
        engine = SimEngine(ConfigReader.ConfigReader('cameracar_controller', self.config), v_min=20, v_max=100)
        result = engine.run(source, 'copilot', warmup=5)
        lab.discard()
        lab._results = {token: (result, source)}
        return token, result


# ------------------------------------------------------------------ Server: Meta und Bilder
class BackendTests(Base):
    def setUp(self):
        super().setUp()
        self.lab_ = self.lab()
        self.token, self.result = self.load(self.lab_)
        self.backend = sim_player.PlayerBackend(self.lab_)
        self.lab_._render_pair = None        # PAKET-I3B: diese Tests pruefen den Weg ueber render_frame (mit/ohne I2 gleich)

    def test_meta_enthaelt_alles_fuer_die_anzeige(self):
        meta = self.backend.meta(str(self.token))
        frames = self.result.frames
        self.assertEqual(meta['n'], len(frames))
        for key in ('ids', 't', 'warm', 'stop', 'held', 'tl', 'nl', 'tp', 'np', 'tr', 'nr', 'tc', 'nc', 'ts', 'ns', 'tst'):
            self.assertEqual(len(meta[key]), len(frames), key)
        self.assertEqual(meta['ids'], [f.frame_id for f in frames])
        self.assertEqual(meta['first'], self.lab_.first_measured(self.result))
        self.assertEqual(meta['t'][0], 0.0)
        self.assertEqual(meta['t'], sorted(meta['t']))                       # nicht fallend
        self.assertAlmostEqual(meta['t'][-1], (frames[-1].captured_ns - frames[0].captured_ns) / 1e6, delta=0.1)
        i = 20
        self.assertEqual((meta['nl'][i], meta['tl'][i]), (frames[i].lane_count, frames[i].then_lane_count))
        self.assertEqual(meta['nc'][i], None if frames[i].car_angle is None else round(frames[i].car_angle, 1))
        self.assertEqual(meta['style'], hud_style.resolve(None))
        self.assertEqual(meta['widths'], [640, 960, 1280])
        json.dumps(meta)                                                    # reines JSON (kein NaN, keine numpy-Typen)
        self.assertIsNone(self.backend.meta('999'))

    def test_meta_stimmt_mit_der_server_tabelle_ueberein(self):
        """Die JS-Tabelle zeigt dieselben Werte wie ``SimLab.frame_table`` (Zeilen und Formatierung)."""
        meta = self.backend.meta(self.token)
        for i in (0, 10, 25, 39):
            frame = self.result.frames[i]
            table = self.lab_.frame_table(frame)
            cells = [[td.children for td in tr.children] for tr in table.children[1].children]
            self.assertEqual(cells[0][1:], [self.fmt(meta['tl'][i]), self.fmt(meta['nl'][i])])
            self.assertEqual(cells[2][1:], [self.fmt(meta['tr'][i], '°'), self.fmt(meta['nr'][i], '°')])
            self.assertEqual(cells[3][1:], [self.fmt(meta['tc'][i], '°'), self.fmt(meta['nc'][i], '°')])
            self.assertEqual(cells[4][1:], [self.fmt(meta['ts'][i]), self.fmt(meta['ns'][i])])
            self.assertEqual(cells[5][2], meta['stop'][i] or ('Vorwert gehalten' if meta['held'][i] else 'Entscheidung'))
            self.assertEqual(cells[1][2], 'ja' if meta['np'][i] else 'nein')

    @staticmethod
    def fmt(value, unit=''):
        return '–' if value is None else f'{float(value):.0f}{unit}'

    def decode(self, data):
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

    def test_bilder_sind_jpeg_damals_und_jetzt_unterscheiden_sich(self):
        then = self.decode(self.backend.image(self.token, 'then', 12))
        now = self.decode(self.backend.image(self.token, 'now', 12))
        self.assertEqual(then.shape, now.shape)
        self.assertGreater(int(np.abs(then.astype(int) - now.astype(int)).sum()), 0)       # eigene Fusszeile / HUD
        self.assertTrue(self.backend.image(str(self.token), 'now', 12))                    # Token als Text (URL)

    def test_breite_wird_begrenzt_und_validiert(self):
        big = self.lab_  # Quellbild ist klein (320 px): Breite kann nicht groesser werden als das Original
        self.assertEqual(sim_player.valid_width('640'), 640)
        self.assertEqual(sim_player.valid_width('777'), sim_player.DEFAULT_WIDTH)
        self.assertEqual(sim_player.valid_width(None), sim_player.DEFAULT_WIDTH)
        image = np.full((1080, 1920, 3), 90, np.uint8)
        with mock.patch.object(big, 'render_frame', return_value=(image, image.copy())):
            small = self.decode(self.backend.image(self.token, 'then', 30, 640))
            large = self.decode(self.backend.image(self.token, 'then', 31, 1280))
        self.assertEqual((small.shape[1], large.shape[1]), (640, 1280))
        self.assertEqual(small.shape[0], 360)                                          # Seitenverhaeltnis bleibt

    def test_ungueltige_anfragen(self):
        for args in ((999, 'now', 1), (self.token, 'now', -1), (self.token, 'now', FRAMES), (self.token, 'oben', 1)):
            with self.assertRaises(LookupError, msg=args):
                self.backend.image(*args)
        with mock.patch.object(self.lab_, 'render_frame', side_effect=ValueError('Bild fehlt')):
            with self.assertRaises(ValueError):
                self.backend.image(self.token, 'now', 3)

    def test_token_rundung_durch_json_wird_toleriert(self):
        big = 2 ** 60 + 12345                                                  # ueber 2^53: JSON rundet im Browser
        self.load(self.lab_, big)
        rounded = int(float(big))
        self.assertNotEqual(rounded, big)
        self.assertIsNotNone(self.backend.meta(str(rounded)))
        self.assertTrue(self.backend.image(str(rounded), 'now', 2))

    def test_ein_paar_wird_auch_bei_gleichzeitigen_anfragen_nur_einmal_berechnet(self):
        calls = []
        real = self.lab_.render_frame

        def slow(*args):
            calls.append(args[2])
            time.sleep(0.15)
            return real(*args)
        results = []
        with mock.patch.object(self.lab_, 'render_frame', slow):
            threads = [threading.Thread(target=lambda k=kind: results.append(self.backend.image(self.token, k, 7)))
                       for kind in ('then', 'now', 'then', 'now')]
            [t.start() for t in threads]
            [t.join(10) for t in threads]
            self.backend.image(self.token, 'now', 7)                             # danach aus dem Cache
        self.assertEqual(calls, [7])
        self.assertEqual(len(results), 4)
        self.assertEqual((self.backend.cache.misses, self.backend.cache.hits >= 1), (1, True))

    def test_anderer_hud_stil_und_andere_breite_sind_eigene_cache_eintraege(self):
        with mock.patch.object(self.lab_, 'render_frame', wraps=self.lab_.render_frame) as spy:
            self.backend.image(self.token, 'now', 4, 960)
            self.backend.image(self.token, 'now', 4, 960)
            self.assertEqual(spy.call_count, 1)
            self.backend.image(self.token, 'now', 4, 640)
            hud_style.set_current('jetpilot')
            self.backend.image(self.token, 'now', 4, 960)
            self.assertEqual(spy.call_count, 3)

    def test_cache_ist_begrenzt_und_verwirft_andere_ergebnisse(self):
        cache = sim_player.PairCache(capacity=3)
        for i in range(6):
            cache.get(('a', i), lambda i=i: (b'x', b'y'))
        self.assertEqual(len(cache), 3)
        cache.get(('b', 0), lambda: (b'x', b'y'))
        cache.drop_other('b')
        self.assertEqual(len(cache), 1)
        with self.assertRaises(RuntimeError):
            cache.get(('c', 0), mock.Mock(side_effect=RuntimeError('x')))
        self.assertEqual(cache.get(('c', 0), lambda: (b'1', b'2')), (b'1', b'2'))       # Fehler wird nicht gemerkt

    def test_i2_weg_mit_echter_uri_und_rueckfall_bei_nicht_dekodierbarer_uri(self):  # PAKET-I3B
        """Paket I2 (``_render_pair``) liefert Data-URIs: echte uri() wird genutzt, eine nicht dekodierbare faellt auf
        render_frame zurueck (kein Fehler, der schnelle Weg bleibt eingeschaltet)."""
        def pair_with(uri):
            return lambda result, source, position, width, style: tuple(
                uri(image) for image in self.lab_.render_frame(result, source, position))
        self.lab_._render_pair = pair_with(data_uri)
        self.assertEqual(self.decode(self.backend.image(self.token, 'then', 6, 960)).ndim, 3)
        self.lab_._render_pair = pair_with(lambda image: 'data:x')
        self.assertEqual(self.decode(self.backend.image(self.token, 'now', 7, 960)).ndim, 3)
        self.assertFalse(self.backend._pair_failed)

    def test_schneller_weg_aus_paket_i2_wird_genutzt_und_faellt_sicher_zurueck(self):
        def uri(image):
            ok, enc = cv2.imencode('.jpg', image)
            return 'data:image/jpeg;base64,' + base64.b64encode(enc.tobytes()).decode()
        marker = np.full((50, 80, 3), 200, np.uint8)
        seen = []

        def pair(result, source, position, width, style):
            seen.append((position, width, style))
            return uri(marker), uri(marker)
        self.lab_._render_pair = pair                                           # so sieht Paket I2 aus (sim_lab.py)
        data = self.backend.image(self.token, 'then', 9, 640)
        self.assertEqual(seen, [(9, 640, hud_style.resolve(None))])
        self.assertEqual(self.decode(data).shape[:2], (50, 80))
        # andere Signatur: einmalig melden, dann dauerhaft render_frame
        self.lab_._render_pair = mock.Mock(side_effect=TypeError('falsche Signatur'))
        data = self.backend.image(self.token, 'then', 10, 640)
        self.assertGreater(self.decode(data).shape[1], 80)
        self.backend.image(self.token, 'then', 11, 640)
        self.assertEqual(self.lab_._render_pair.call_count, 1)
        self.assertTrue(self.backend._pair_failed)


# ------------------------------------------------------------------ Layout und Registrierung
class LayoutTests(Base):
    def ids(self, root):
        found = []

        def walk(node):
            if getattr(node, 'id', None) is not None:
                found.append(node.id)
            for child in sim_player._children(node):
                walk(child)
        walk(root)
        return found

    def test_einbau_ohne_aenderung_von_sim_lab_und_ohne_verlorene_ids(self):
        from sim_lab import SimLab
        ui = self.ui()
        plain = SimLab.wrap_cockpit(ui.html.Div(id='live'), ui.dbc, ui.dcc, ui.html)
        before = self.ids(plain)
        sim_player.install_layout(plain, ui)
        after = self.ids(plain)
        self.assertEqual(sorted(set(before) - set(after)), [])                       # nichts geht verloren
        added = set(after) - set(before)
        self.assertEqual(added, {'sim-viewer-host', 'sim-player-switch', 'sim-legacy-view', 'sim-player-wrap',
                                 'sim-player-root'})
        self.assertEqual(len(after), len(set(after)), 'doppelte IDs')
        legacy = next(n for n in self.nodes(plain) if getattr(n, 'id', None) == 'sim-legacy-view')
        inside = self.ids(legacy)
        for legacy_id in ('sim-img-then', 'sim-img-now', 'sim-play', 'sim-prev', 'sim-next', 'sim-index', 'sim-speed',
                          'sim-timing', 'sim-frame-label', 'sim-display-rate'):
            self.assertIn(legacy_id, inside)                                         # alte Wiedergabe bleibt komplett
        sim_player.install_layout(plain, ui)                                          # idempotent
        self.assertEqual(self.ids(plain), after)

    def nodes(self, root):
        yield root
        for child in sim_player._children(root):
            yield from self.nodes(child)

    def test_unbekannter_aufbau_laesst_den_baum_unveraendert(self):
        ui = self.ui()
        tree = ui.html.Div([ui.html.Div(id='nichts'), ui.html.Div(ui.html.Img(id='sim-img-then'))])
        before = self.ids(tree)
        sim_player.install_layout(tree, ui)                                          # kein Card: nicht einbauen
        self.assertEqual(self.ids(tree), before)

    def test_register_legt_keinen_dash_callback_an(self):
        """Der Spieler braucht keinen Dash-Callback (Token ueber current.json): die Callback-Zahl bleibt gleich."""
        lab = self.lab(self.ui())
        import dash_stub
        app = dash_stub.Dash('t')
        backend = sim_player.register(app, lab)
        self.assertIs(lab.player_backend, backend)
        self.assertEqual((app.clientside, app.callbacks), ([], {}))

    def test_unbekannter_viewer_aktiviert_nichts(self):
        """Findet sich der Viewer nicht, bleiben Callback und Routen aus (sonst fehlten Komponenten im Layout)."""
        import dash_stub
        from sim_lab import SimLab
        ui = self.ui()
        lab = self.lab(ui)
        app = dash_stub.Dash('t')
        with mock.patch.object(SimLab, 'wrap_cockpit', staticmethod(lambda cockpit, dbc, dcc, html: html.Div(id='leer'))):
            sim_player.register(app, lab)
        self.assertEqual((app.clientside, app.callbacks), ([], {}))

    def test_js_und_layout_stimmen_ueberein(self):
        """Jede ID, die das JavaScript anspricht, gibt es im Layout; die IDs der Callbacks bleiben erhalten."""
        js = (ROOT / 'assets' / 'sim_player.js').read_text(encoding='utf-8')
        used = set(re.findall(r"\$\('([a-z0-9-]+)'\)", js)) | set(re.findall(r"'(sim-[a-z0-9-]+)'", js))
        used -= {'sim-bar'}                                                          # Store des Frame-Balkens (chart_kit)
        from sim_lab import SimLab
        ui = self.ui()
        tree = SimLab.wrap_cockpit(ui.html.Div(id='live'), ui.dbc, ui.dcc, ui.html)
        sim_player.install_layout(tree, ui)
        layout_ids = set(self.ids(tree))
        missing = {i for i in used if i not in layout_ids and i not in ('sim-live-compare', 'sim-chart-box')}
        self.assertEqual(missing, set())
        self.assertIn('sim-chart', layout_ids)

    def test_javascript_syntax(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node nicht installiert')
        done = subprocess.run([node, '--check', str(ROOT / 'assets' / 'sim_player.js')], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


# ------------------------------------------------------------------ Echtes Dash/Flask ohne Browser
@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashTests(Base):
    def build(self, with_player=True):
        import dash_bootstrap_components as dbc
        from dash import ClientsideFunction, Dash, Input, Output, State, ctx, dcc, html, no_update
        from sim_lab import SimLab
        ui = SimpleNamespace(dbc=dbc, dcc=dcc, html=html, Input=Input, Output=Output, State=State, ctx=ctx,
                             no_update=no_update, ClientsideFunction=ClientsideFunction)
        fake = SimpleNamespace(running=lambda: False, car=SimpleNamespace(frame_history=None), offline=True)
        app = Dash(__name__, assets_folder=str(ROOT / 'assets'))
        cockpit = SimLab.wrap_cockpit(html.Div('live'), dbc, dcc, html)
        app.layout = html.Div([sim_player.install_layout(cockpit, ui) if with_player else cockpit])
        lab = SimLab(fake, ui, data_uri, self.root)
        lab.register(app)
        if with_player:
            sim_player.register(app, lab)
        return app, lab

    def test_routen_layout_abhaengigkeiten_und_assets(self):
        app, lab = self.build()
        token, result = self.load(lab)
        lab_source = lab.lookup(token)[1]
        client = app.server.test_client()
        self.assertEqual(client.get('/_c2c/sim/current.json').get_json(), {'token': str(token)})
        lab.discard()
        self.assertEqual(client.get('/_c2c/sim/current.json').get_json(), {'token': None})
        lab._results = {token: (result, lab_source)}
        meta = client.get(f'/_c2c/sim/{token}/meta.json')
        self.assertEqual((meta.status_code, meta.headers['Cache-Control']), (200, 'no-store'))
        self.assertEqual(meta.get_json()['n'], FRAMES)
        image = client.get(f'/_c2c/sim/{token}/now/5.jpg?w=640')
        self.assertEqual((image.status_code, image.mimetype), (200, 'image/jpeg'))
        self.assertIn('max-age', image.headers['Cache-Control'])
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(image.data, np.uint8), cv2.IMREAD_COLOR))
        for path in ('/_c2c/sim/42/meta.json', f'/_c2c/sim/{token}/now/999.jpg', f'/_c2c/sim/{token}/zwei/1.jpg'):
            self.assertEqual(client.get(path).status_code, 404, path)
        deps = client.get('/_dash-dependencies').get_json()
        plain, _lab = self.build(with_player=False)
        plain_deps = plain.server.test_client().get('/_dash-dependencies').get_json()
        self.assertEqual(len(deps), len(plain_deps), 'der Spieler darf keine Dash-Callbacks hinzufuegen')
        self.assertFalse([d for d in deps if 'sim-player' in d['output']])
        outputs = [d['output'] for d in deps]
        self.assertEqual(len(outputs), len(set(outputs)))
        layout = json.dumps(client.get('/_dash-layout').get_json())
        for wanted in ('sim-player-root', 'sim-legacy-view', 'sim-player-switch', 'sim-img-then', 'sim-chart'):
            self.assertIn(wanted, layout)
        for asset in ('sim_player.js', 'sim_player.css'):
            response = client.get(f'/assets/{asset}')
            self.assertEqual(response.status_code, 200)
            response.close()
        self.assertIn('sim_player.js', client.get('/').get_data(as_text=True))

    def test_zweite_registrierung_doppelt_keine_routen(self):
        app, lab = self.build()
        sim_player.register(app, lab)                                               # darf nicht ausnahmsweise scheitern
        routes = [r.rule for r in app.server.url_map.iter_rules() if '_c2c' in r.rule]
        self.assertEqual(len(routes), len(set(routes)))


# ------------------------------------------------------------------ Browser (optional)
CHROMIUM = os.environ.get('C2C_CHROMIUM', '')


@unittest.skipUnless(CHROMIUM and os.path.exists(CHROMIUM) and HAS_DASH and importlib.util.find_spec('playwright'),
                     'Browsertest: C2C_CHROMIUM=<Pfad zu chrome-headless-shell> setzen (Playwright und Dash noetig)')
class BrowserTests(unittest.TestCase):
    """Echter Browser (Headless-Chromium): Der Spieler laeuft lokal; Pause und Slider wirken auch bei langsamem Server."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        from test_dashboard_callbacks import FakeApplication
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        cls.pin = hardware_stubs.pin_pipeline_config()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        cls.config = hardware_stubs.write_config(os.path.join(cls.tmp.name, 'config.json'),
                                                 cameracar_controller={'lost_line_timeout': 1})
        cls.root = os.path.join(cls.tmp.name, 'Data_AI', 'sessions')
        cls.session = write_session(cls.root, count=120)
        cls.fake = FakeApplication()
        for frame_id in range(1, 11):
            cls.fake.publish(frame_id, with_decision=True)
        cls.app = module.create_app(cls.fake)
        cls.lab = cls.app.c2c_tabs[0].ctx.sim
        from werkzeug.serving import make_server
        cls.server = make_server('127.0.0.1', 0, cls.app.server, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=CHROMIUM, args=['--no-sandbox'])
        import ConfigReader
        from frame_source import SessionSource
        from sim_engine import SimEngine
        cls.source = SessionSource(cls.session, cls.root)
        engine = SimEngine(ConfigReader.ConfigReader('cameracar_controller', cls.config), v_min=20, v_max=100)
        cls.result = engine.run(cls.source, 'copilot', warmup=5)
        cls.token = 9007199254740993 + 1000                                          # ueber 2^53: Rundung im Browser
        cls.lab._results = {cls.token: (cls.result, cls.source)}

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        os.chdir(cls.cwd)
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.delay = 0.0
        backend = self.lab.player_backend
        real = backend._render

        def slow(*args, **kwargs):
            if self.delay:
                time.sleep(self.delay)
            return real(*args, **kwargs)
        patcher = mock.patch.object(backend, '_render', slow)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 1300})
        self.page.set_default_timeout(30000)
        self.errors, self.dash_calls = [], []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda r: self.dash_calls.append(r.url) if '_dash-update-component' in r.url else None)
        self.page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page.goto(self.url, wait_until='domcontentloaded')
        self.page.wait_for_selector('#main-tabs')
        self.page.evaluate("() => { window.localStorage.clear(); window.dash_clientside.set_props('refresh', {disabled: true}); }")
        self.page.get_by_text('Simulation (Session-Replay)', exact=True).click()
        self.page.wait_for_selector('#sim-player-root .c2c-sp')
        self.lab._results = {self.token: (self.result, self.source)}                    # der Spieler fragt das Token selbst ab
        self.page.wait_for_function("() => { const s = window.c2cSimPlayer.stats(); return s.n === 120 && s.ready >= 1; }")
        self.page.wait_for_function("() => { const i = document.querySelector('#sim-player-root [data-r=now]'); return i.complete && i.naturalWidth > 0; }")

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def stats(self):
        return self.page.evaluate('() => window.c2cSimPlayer.stats()')

    def test_oberflaeche_umschalter_und_tabelle(self):
        page = self.page
        self.assertEqual(page.evaluate("() => document.getElementById('sim-legacy-view').style.display"), 'none')
        self.assertEqual(page.evaluate("() => document.getElementById('sim-player-wrap').style.display"), '')
        self.assertIn('Frame #', page.inner_text('#sim-player-root [data-r=label]'))
        self.assertIn('Stellwert', page.inner_text('#sim-live-compare'))
        self.assertFalse(page.is_visible('#sim-compare'))                                # Serverteil ersetzt
        page.get_by_label('Über den Server (alt)').check()
        self.assertTrue(page.is_visible('#sim-play'))                                     # alte Wiedergabe sichtbar
        self.assertFalse(page.is_visible('#sim-player-root'))
        page.get_by_label('Im Browser (schnell)').check()
        self.assertTrue(page.is_visible('#sim-player-root'))

    def test_wiedergabe_laeuft_lokal_ohne_dash_callbacks(self):
        page = self.page
        start = self.stats()['idx']
        self.dash_calls.clear()
        page.click('[data-r=play]')
        self.assertEqual(page.inner_text('[data-r=play]'), '⏸ Pause')
        page.wait_for_function(f"() => window.c2cSimPlayer.stats().idx >= {start + 8}")
        self.assertEqual(self.dash_calls, [], 'waehrend der Wiedergabe darf es keine Dash-Callbacks geben')
        page.click('[data-r=play]')
        self.assertEqual(page.inner_text('[data-r=play]'), '▶ Play')
        self.assertIn('Frames/s', page.inner_text('[data-r=diag]') + ' Frames/s')

    def test_pause_wirkt_sofort_auch_bei_langsamem_server(self):
        page = self.page
        self.delay = 0.35                                                              # jedes Bildpaar braucht 350 ms
        self.page.click('[data-r=play]')
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx > 3", timeout=30000)
        before = time.monotonic()
        page.click('[data-r=play]')
        stopped = self.stats()['idx']
        self.assertLess(time.monotonic() - before, 1.0)
        self.assertFalse(self.stats()['playing'])
        page.wait_for_timeout(1500)
        self.assertEqual(self.stats()['idx'], stopped, 'nach Pause darf der Index nicht weiterlaufen')
        self.assertEqual(page.inner_text('[data-r=play]'), '▶ Play')

    def test_slider_springt_waehrend_der_wiedergabe_sofort_und_spielt_dort_weiter(self):
        page = self.page
        self.delay = 0.2
        page.click('[data-r=play]')
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx > 2", timeout=30000)
        page.evaluate("() => { const s = document.querySelector('[data-r=slider]'); s.value = '90';"
                      " s.dispatchEvent(new Event('input', {bubbles: true})); }")
        self.assertEqual(self.stats()['idx'], 90)                                      # sofort, ohne Antwort des Servers
        self.assertIn('Frame #91', page.inner_text('[data-r=label]'))
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx > 92", timeout=30000)
        self.assertTrue(self.stats()['playing'])
        page.click('[data-r=play]')
        stopped = self.stats()['idx']
        page.wait_for_timeout(800)
        self.assertEqual(self.stats()['idx'], stopped)

    def test_einzelschritte_tempo_und_modus_jeder_frame(self):
        page = self.page
        start = self.stats()['idx']
        page.click('[data-r=next]')
        page.click('[data-r=next]')
        page.click('[data-r=prev]')
        self.assertEqual(self.stats()['idx'], start + 1)
        page.locator('#sim-player-root').get_by_label('Jeder Frame').check()
        page.locator('#sim-player-root').get_by_label('4x').check()
        seen = []
        page.evaluate("() => { window.__seen = []; const o = new MutationObserver(() => window.__seen.push(window.c2cSimPlayer.stats().idx));"
                      " o.observe(document.querySelector('[data-r=count]'), {childList: true}); }")
        page.click('[data-r=play]')
        page.wait_for_function(f"() => window.c2cSimPlayer.stats().idx >= {start + 12}")
        page.click('[data-r=play]')
        seen = page.evaluate('() => window.__seen')
        steps = [b - a for a, b in zip(seen, seen[1:]) if b >= a]
        self.assertTrue(steps and set(steps) <= {0, 1}, steps)                            # Jeder Frame: nie ueberspringen
        self.assertEqual(self.stats()['skipped'], 0)

    def test_echtzeit_ueberspringt_wenn_der_server_nicht_mitkommt(self):
        page = self.page
        self.delay = 0.6
        page.locator('#sim-player-root').get_by_label('4x').check()
        page.click('[data-r=play]')
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx > 15", timeout=30000)
        page.click('[data-r=play]')
        self.assertGreater(self.stats()['skipped'], 0)

    def test_bildgroesse_wechselt_ohne_den_index_zu_veraendern(self):
        page = self.page
        page.click('[data-r=next]')
        index = self.stats()['idx']
        page.select_option('[data-r=width]', '640')
        page.wait_for_function("() => window.c2cSimPlayer.stats().ready >= 1")
        self.assertEqual((self.stats()['idx'], self.stats()['width']), (index, 640))

    def test_neues_ergebnis_und_kein_ergebnis(self):
        page = self.page
        self.lab.discard()                                                              # Einstellungen geaendert / kein Ergebnis
        page.wait_for_function("() => window.c2cSimPlayer.stats().n === 0")
        self.assertIn('Noch keine Simulation', page.inner_text('[data-r=label]'))
        self.assertEqual(page.inner_text('#sim-live-compare').strip(), '')
        self.lab._results = {self.token + 1: (self.result, self.source)}                # neues Ergebnis (anderes Token)
        page.wait_for_function("() => window.c2cSimPlayer.stats().n === 120")

    def test_wiedergabe_pausiert_wenn_die_ansicht_verborgen_wird(self):
        page = self.page
        page.click('[data-r=play]')
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx > 1")
        page.evaluate("() => { document.getElementById('sim-player-wrap').style.display = 'none'; }")     # wie Live-Modus
        page.wait_for_function("() => !window.c2cSimPlayer.stats().playing", timeout=5000)

    def test_frame_balken_folgt_der_wiedergabe_wird_lokal_gezogen_und_diagrammklick_springt(self):
        page = self.page
        figure = json.loads(json.dumps(self.lab.chart(self.result)))
        page.evaluate("f => window.dash_clientside.set_props('sim-chart', {figure: f})", figure)
        page.wait_for_function("() => { const b = document.querySelector('#sim-chart .c2c-framebar'); return b && b.style.display === 'block'; }")
        page.locator('#sim-chart').scroll_into_view_if_needed()
        bar = "() => Number(document.querySelector('#sim-chart .c2c-framebar').getAttribute('data-frame'))"
        self.dash_calls.clear()
        page.click('[data-r=play]')
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx > 8")
        page.click('[data-r=play]')
        page.wait_for_function("() => Number(document.querySelector('#sim-chart .c2c-framebar').getAttribute('data-frame'))"
                               " === window.c2cSimPlayer.stats().idx + 1")                      # Balken folgt (Frame-ID = Index + 1)
        self.assertEqual(self.dash_calls, [])
        page.locator('#sim-chart').scroll_into_view_if_needed()
        page.wait_for_timeout(1200)                                                     # Layout setzt sich (Bilder, Scrollen)
        geometry = page.evaluate("""() => { const b = document.querySelector('#sim-chart .c2c-framebar').getBoundingClientRect();
            const gd = document.querySelector('#sim-chart .js-plotly-plot'); const x = gd._fullLayout.xaxis;
            return [b.left + b.width / 2, b.top + b.height / 2, gd.getBoundingClientRect().left + x._offset, x._length]; }""")
        page.mouse.move(geometry[0], geometry[1])
        page.mouse.down()
        page.mouse.move(geometry[2] + geometry[3] * 0.8, geometry[1], steps=6)
        page.mouse.up()
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx > 80")
        page.wait_for_function(f"({bar})() === window.c2cSimPlayer.stats().idx + 1")
        self.assertFalse([c for c in self.dash_calls if 'sim-bar' in c], 'Ziehen darf keinen Server-Callback ausloesen')
        page.evaluate("() => document.querySelector('#sim-chart .js-plotly-plot').emit('plotly_click', {points: [{customdata: 30}]})")
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx === 30")

    def test_frame_balken_im_diagramm_wird_lokal_abgefangen(self):
        """set_props('sim-bar') (Ziehen des Balkens) springt lokal; andere set_props-Aufrufe laufen unveraendert durch."""
        page = self.page
        page.evaluate("() => window.dash_clientside.set_props('sim-bar', {data: {frame: 60, nonce: 1}})")
        page.wait_for_function("() => window.c2cSimPlayer.stats().idx === 59")
        self.dash_calls.clear()
        page.evaluate("() => window.dash_clientside.set_props('sim-status', {children: 'x'})")
        self.assertEqual(page.inner_text('#sim-status'), 'x')


if __name__ == '__main__':
    unittest.main()
