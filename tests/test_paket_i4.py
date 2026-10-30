"""Paket I4: Replay Lab im Browser (replay_player.py, assets/replay_player.js), HUD-Stil mit Lenkspur/Streak, Vollbild.

Hardwarefrei und kurz. Geprueft werden: Meta-Daten, Bereichsauswahl, HUD-Bilder (Stile, Verlauf, Verkleinern), Cache mit
Duplikatschutz und Neuladen bei Dateiaenderung, Zustandsmeldung (``note_view``), Layout-Einbau ohne verlorene IDs, keine
zusaetzlichen Dash-Callbacks, HTTP-Routen mit echtem Dash und - mit Headless-Chromium (``C2C_CHROMIUM=<Pfad>``,
Playwright und Dash noetig, auf dem Pi nicht vorgesehen) - das Verhalten im Browser: Wiedergabe ohne serverseitiges Rendern,
Pause/Slider wirken sofort bei langsamem Server, Rueckschreiben der Position, Sprung durch den Server, HUD-Stil, Vollbild,
Umschalter, Live-Ringbuffer, Frame-Balken und das Vollbild-Zusatzskript der Simulation.
Nicht getestet: Optik (die Sandbox hat kein Bootstrap-CSS), Laufzeit auf dem Raspberry Pi.
"""
import csv
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
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import hud_render  # noqa: E402
import hud_style  # noqa: E402
import replay_player  # noqa: E402

HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
ROOT = Path(__file__).resolve().parent.parent
LANES = [[-1.2, 166.0], [1.6, -317.0]]


def write_session(root, name='session_i4', count=60, size=(1296, 972), step_ns=40_000_000, geometry=True):
    """Session mit grossen Bildern und Geometrie (wie Revision 4); ``geometry=False`` = alte Session ohne Geometrie."""
    path = Path(root) / name
    (path / 'frames').mkdir(parents=True)
    width, height = size
    rows = []
    for frame_id in range(1, count + 1):
        filename = f'frames/frame_{frame_id:010d}.jpg'
        image = np.full((height, width, 3), (180, 160, 200), np.uint8)
        cv2.line(image, (width // 5 + frame_id, height - 1), (width // 2 - 60, 0), (200, 40, 20), 18)
        cv2.line(image, (width * 4 // 5 + frame_id, height - 1), (width // 2 + 60, 0), (200, 40, 20), 18)
        cv2.imwrite(str(path / filename), image)
        row = {'frame_id': frame_id, 'image_filename': filename, 'data_status': 'complete',
               'camera_monotonic_ns': 10_000_000_000 + frame_id * step_ns, 'processing_ns': 4_000_000,
               'steering_angle': 90 + (frame_id % 7), 'requested_angle': 91, 'steering_angle_car': 90 + (frame_id % 5),
               'speed': 30, 'vehicle_mode_key': 'camera', 'event_codes': 'LANE_LOST' if frame_id == 20 else '',
               'lane_count': 1 if frame_id % 11 == 0 else 2,
               'lanes_json': json.dumps(LANES[:1] + [None] if frame_id % 11 == 0 else LANES)}
        if geometry:
            row.update({'process_width': 320, 'process_height': 240, 'roi_top_px': int(height * 0.45),
                        'roi_bottom_px': int(height * 0.85), 'roi_high': 0.55})
        rows.append(row)
    with (path / 'frames.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (path / 'events.csv').write_text('timestamp_ns,frame_id,code,detail\n1,20,LANE_LOST,0 Linien\n', encoding='utf-8')
    return name


class Base(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        os.chdir(self.tmp.name)
        self.root = os.path.join(self.tmp.name, 'Data_AI', 'sessions')
        self.addCleanup(hud_style.set_current, hud_style.current())
        self.addCleanup(setattr, replay_player, '_ACTIVE', None)

    def decode(self, data):
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


# ------------------------------------------------------------------ Server: Daten und Bilder
class BackendTests(Base):
    def setUp(self):
        super().setUp()
        self.name = write_session(self.root)
        self.backend = replay_player.ReplayBackend(self.root)

    def test_meta_zeitachse_werte_und_bereich(self):
        meta = self.backend.meta(self.name, None, None)
        self.assertEqual((meta['n'], meta['ids'][0], meta['ids'][-1]), (60, 1, 60))
        for key in ('t', 'status', 'sc', 'rq', 'rw', 'sp', 'ln', 'ev'):
            self.assertEqual(len(meta[key]), 60, key)
        self.assertEqual(meta['t'][0], 0.0)
        self.assertAlmostEqual(meta['t'][1], 40.0, delta=0.2)                          # 40 ms Abstand aus camera_monotonic_ns
        self.assertEqual(meta['t'], sorted(meta['t']))
        self.assertEqual((meta['status'][0], meta['sp'][0], meta['ev'][19], meta['ln'][10]), ('complete', '30', 'LANE_LOST', '1'))
        self.assertEqual([s['value'] for s in meta['styles']], ['follow', 'classic', 'jetpilot', 'arcade', 'none'])
        self.assertEqual(meta['widths'], [0, 640, 960, 1280])
        json.dumps(meta)
        bounded = self.backend.meta(self.name, 10.0, 20.0)                              # genau die Auswahl des Sliders
        self.assertEqual((bounded['n'], bounded['ids'][0], bounded['ids'][-1], bounded['t'][0]), (11, 10, 20, 0.0))
        with self.assertRaises(LookupError):
            self.backend.meta(self.name, 500.0, 600.0)

    def test_zeiten_luecken_und_fehlende_spalte(self):
        rows = [{'camera_monotonic_ns': str(v)} for v in ('1000000000', '', '1080000000', 'x', '1160000000')]
        times = replay_player.SessionData._times(rows)
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(times[2] - times[0], 0.08, places=6)
        self.assertGreater(times[1], times[0])                                          # Luecke: Median-Abstand (80 ms)
        self.assertEqual(replay_player.SessionData._times([{}, {}]), [0.0, 0.04])       # ohne Zeiten: 40 ms Standard

    def test_ungueltige_sessions_und_bereiche(self):
        for bad in ('../x', '', None, replay_player.LIVE, 'gibt_es_nicht'):
            with self.assertRaises(ValueError, msg=bad):
                self.backend.session(bad)
        with self.assertRaises(LookupError):
            self.backend.image(self.name, None, None, 60)
        with self.assertRaises(LookupError):
            self.backend.image(self.name, None, None, -1)

    def test_bild_ist_jpeg_breite_und_originalgroesse(self):
        for width, expected in ((640, 640), (960, 960), (1280, 1280), (0, 1296), (777, 960), (None, 960)):
            image = self.decode(self.backend.image(self.name, None, None, 3, width))
            self.assertEqual(image.shape[1], expected, width)
            self.assertEqual(image.shape[0], round(972 * expected / 1296), width)       # Seitenverhaeltnis bleibt

    def test_hud_wird_auf_dem_originalbild_gezeichnet_dann_verkleinert(self):
        """Wie im bisherigen Replay Lab: HUD auf dem grossen Bild (auch fuer Sessions ohne Geometriespalten), danach verkleinern."""
        for geometry in (True, False):
            name = write_session(self.root, f'session_g{int(geometry)}', count=6, geometry=geometry)
            spy = mock.Mock(wraps=replay_player.draw_hud)
            with mock.patch.object(replay_player, 'draw_hud', spy):
                image = self.decode(self.backend.image(name, None, None, 2, 640))
            self.assertEqual(image.shape[1], 640)
            self.assertEqual(spy.call_args[0][0].shape[1], 1296)

    def test_ergebnis_entspricht_dem_bisherigen_hud(self):
        from replay_hud import draw_hud
        from replay_studio import load_session_image
        from session_recorder import load_session
        path, rows = load_session(self.name, self.root)
        for style in ('classic', 'jetpilot'):
            got = self.decode(self.backend.image(self.name, None, None, 30, 960, style)).astype(np.int16)
            full = draw_hud(load_session_image(path, rows[30]), rows[30], style=style, trail=hud_render.HudTrail())
            expected = cv2.resize(full, (960, 720), interpolation=cv2.INTER_AREA).astype(np.int16)
            self.assertLess(float(np.abs(got - expected).mean()), 2.0, style)                  # nur JPEG-Kodierung

    def test_stile_unterscheiden_sich_none_ist_das_rohbild(self):
        shots = {style: self.decode(self.backend.image(self.name, None, None, 25, 640, style))
                 for style in ('classic', 'jetpilot', 'arcade', 'none')}
        names = list(shots)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                self.assertGreater(int(np.abs(shots[a].astype(int) - shots[b].astype(int)).sum()), 1000, (a, b))
        from replay_studio import load_session_image
        from session_recorder import load_session
        path, rows = load_session(self.name, self.root)
        raw = cv2.resize(load_session_image(path, rows[25]), (640, 480), interpolation=cv2.INTER_AREA)
        self.assertLess(float(np.abs(shots['none'].astype(int) - raw.astype(int)).mean()), 3.0)

    def test_stil_folgt_dem_cockpit_wenn_nichts_gewaehlt(self):
        hud_style.set_current('arcade')
        follow = self.backend.image(self.name, None, None, 5, 640, None)
        self.assertEqual(follow, self.backend.image(self.name, None, None, 5, 640, 'arcade'))
        hud_style.set_current('off')                                                    # "aus" = klassisch (immer ein HUD)
        self.assertEqual(self.backend.image(self.name, None, None, 6, 640, None),
                         self.backend.image(self.name, None, None, 6, 640, 'classic'))

    def test_bereich_index_entspricht_dem_absoluten_frame(self):
        in_range = self.backend.image(self.name, 10.0, 20.0, 0, 640, 'classic')
        absolute = self.backend.image(self.name, None, None, 9, 640, 'classic')
        self.assertEqual(in_range, absolute)                                            # Frame 10 = Index 9 der Session
        with self.assertRaises(LookupError):
            self.backend.image(self.name, 10.0, 20.0, 11, 640)

    def test_verlauf_fuer_lenkspur_und_streak(self):
        """Der Verlauf enthaelt die vorangehenden Frames (Zeiten der Aufnahme) und den aktuellen genau einmal als letzten."""
        seen = []
        real = hud_render.draw

        def spy(style, image, info, trail=None):
            seen.append((style, trail.snapshot() if trail is not None else None))
            return real(style, image, info, trail)
        with mock.patch.object(hud_render, 'draw', spy):
            self.backend.image(self.name, None, None, 0, 640, 'arcade')
            self.backend.image(self.name, None, None, 49, 640, 'arcade')
            self.backend.image(self.name, None, None, 49, 640, 'classic')              # classic: kein Verlauf noetig
        (_, first), (_, later) = seen
        self.assertEqual([item[0] for item in first], [1])
        self.assertEqual([item[0] for item in later], list(range(1, 51)))                 # Frames 1..50 (Index 0..49), kein Duplikat
        times = [item[1] for item in later]
        self.assertAlmostEqual(times[-1] - times[0], 49 * 0.04, places=3)                 # Zeiten der Aufnahme, nicht der Anzeige
        self.assertEqual(len(seen), 2)

    def test_verlauf_ist_auf_90_sekunden_begrenzt(self):
        name = write_session(self.root, 'session_lang', count=300, size=(320, 240), step_ns=500_000_000)   # 150 s
        data = self.backend.session(name)
        trail = replay_player.ReplayBackend._trail(data, 299)
        items = trail.snapshot()
        self.assertLessEqual(items[-1][1] - items[0][1], hud_render.TRAIL_WINDOW_S + 1e-6)
        self.assertGreater(len(items), 150)
        self.assertEqual(items[-1][0], 300)
        streak = hud_render.streak_seconds(items)
        self.assertIsNotNone(streak)

    def test_cache_dedupliziert_gleichzeitige_anfragen(self):
        calls = []
        real = self.backend._render

        def slow(*args):
            calls.append(args[1])
            time.sleep(0.15)
            return real(*args)
        results = []
        with mock.patch.object(self.backend, '_render', slow):
            threads = [threading.Thread(target=lambda: results.append(self.backend.image(self.name, None, None, 7, 640, 'classic')))
                       for _ in range(6)]
            [t.start() for t in threads]
            [t.join(15) for t in threads]
        self.assertEqual(calls, [7])
        self.assertEqual(len(results), 6)
        self.assertEqual(len({bytes(r) for r in results}), 1)
        self.assertEqual(self.backend.images.misses, 1)

    def test_cache_ist_begrenzt(self):
        cache = replay_player.LruCache(3)
        for i in range(8):
            cache.get(i, lambda i=i: b'x' * i)
        self.assertEqual(len(cache), 3)
        with self.assertRaises(RuntimeError):
            cache.get('x', mock.Mock(side_effect=RuntimeError('x')))
        self.assertEqual(cache.get('x', lambda: b'ok'), b'ok')                          # Fehler wird nicht gemerkt

    def test_session_aenderung_laedt_neu(self):
        first = self.backend.session(self.name)
        self.assertIs(self.backend.session(self.name), first)
        csv_path = Path(self.root, self.name, 'frames.csv')
        lines = csv_path.read_text(encoding='utf-8').splitlines()
        csv_path.write_text('\n'.join(lines[:-5]) + '\n', encoding='utf-8')               # laufende Aufnahme: Datei aendert sich
        second = self.backend.session(self.name)
        self.assertIsNot(second, first)
        self.assertEqual(len(second.rows), 55)
        self.assertNotEqual(first.version, second.version)
        self.assertNotEqual(self.backend.meta(self.name, None, None)['v'], first.version)

    def test_fehlendes_bild_ist_ein_fehler_kein_absturz(self):
        Path(self.root, self.name, 'frames', 'frame_0000000004.jpg').unlink()
        with self.assertRaises(ValueError):
            self.backend.image(self.name, None, None, 3, 640)
        self.assertTrue(self.backend.image(self.name, None, None, 4, 640))


class StateTests(Base):
    def setUp(self):
        super().setUp()
        self.backend = replay_player.ReplayBackend(self.root)

    def test_zustand_und_revision(self):
        self.assertEqual(self.backend.state()['live'], True)
        self.backend.note_view('s', None, 3)
        first = self.backend.state()
        self.assertEqual((first['session'], first['index'], first['live']), ('s', 3, False))
        self.backend.note_view('s', None, 3)                                            # derselbe Zustand (Takt): keine neue Revision
        self.assertEqual(self.backend.state()['rev'], first['rev'])
        self.backend.note_view('s', [10, 20.5], 3)
        second = self.backend.state()
        self.assertEqual((second['lo'], second['hi'], second['rev'] > first['rev']), (10.0, 20.5, True))
        self.backend.note_view('__live__', None, 0)
        self.assertTrue(self.backend.state()['live'])
        self.backend.note_view('s', 'kaputt', 'x')                                      # ungueltige Eingaben: nie eine Ausnahme
        self.assertEqual(self.backend.state()['index'], 0)

    def test_server_rendert_nur_nicht_wenn_der_browser_abspielt(self):
        self.assertFalse(self.backend.note_view('s', None, 0))                          # kein Herzschlag
        self.backend.heartbeat(True)
        self.assertTrue(self.backend.note_view('s', None, 1))
        self.assertFalse(self.backend.note_view('__live__', None, 1))                   # Live-Ringbuffer: immer serverseitig
        self.assertFalse(self.backend.note_view(None, None, 1))
        self.backend.heartbeat(False)                                                   # Wechsel auf "alt"
        self.assertFalse(self.backend.note_view('s', None, 2))
        self.backend.heartbeat(True)
        with mock.patch.object(replay_player.time, 'monotonic', return_value=time.monotonic() + 60):
            self.assertFalse(self.backend.note_view('s', None, 3))                      # Herzschlag abgelaufen: Rueckfall auf Server

    def test_aeltere_herzschlagmeldung_uebersteuert_keine_neuere(self):
        """Parallele Anfragen koennen vertauscht eintreffen: die Meldung mit dem juengeren Zeitstempel gilt."""
        self.backend.heartbeat(False, 2000.0)
        self.backend.heartbeat(True, 1000.0)                                            # aelter, trifft spaeter ein: ignorieren
        self.assertFalse(self.backend.note_view('s', None, 1))
        self.backend.heartbeat(True, 3000.0)
        self.assertTrue(self.backend.note_view('s', None, 2))
        self.backend.heartbeat(False)                                                   # ohne Zeitstempel: wie bisher

    def test_modulfunktion_ohne_backend_ist_harmlos(self):
        replay_player._ACTIVE = None
        self.assertFalse(replay_player.note_view('s', None, 0))
        replay_player._ACTIVE = mock.Mock(note_view=mock.Mock(side_effect=RuntimeError('x')))
        self.assertFalse(replay_player.note_view('s', None, 0))                         # Fehler im Backend: kein Fehler im Callback


# ------------------------------------------------------------------ Layout und Dash (Attrappe)
class DashStubTests(Base):
    LEGACY_IDS = ('session-choice', 'session-refresh', 'replay-open-latest', 'replay-frame-label', 'replay-image',
                  'replay-details', 'replay-prev', 'replay-play', 'replay-next', 'replay-index', 'replay-range',
                  'replay-event', 'replay-pre', 'replay-post', 'replay-range-reset', 'replay-export', 'replay-download',
                  'replay-to-studio', 'replay-studio-msg', 'replay-chart', 'replay-events-filter', 'replay-events',
                  'replay-playing', 'replay-bounds')

    def setUp(self):
        super().setUp()
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        self.module, self.context, self.ctx = load_dashboard()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.fake = FakeApplication()
        self.app = self.module.create_app(self.fake)
        self.tab = next(t for t in self.app.c2c_tabs if t.tab_id == 'replay')
        self.cb = self.app.callbacks
        self.name = write_session(self.root, count=20, size=(320, 240))

    @staticmethod
    def ids(node):
        found = []

        def walk(item):
            if getattr(item, 'id', None) is not None:
                found.append(item.id)
            for child in replay_player._children(item):
                walk(child)
        walk(node)
        return found

    def find(self, node, wanted):
        if getattr(node, 'id', None) == wanted:
            return node
        for child in replay_player._children(node):
            found = self.find(child, wanted)
            if found is not None:
                return found
        return None

    def test_layout_behaelt_alle_ids_und_baut_den_spieler_ein(self):
        ids = self.ids(self.tab.layout())
        for wanted in self.LEGACY_IDS:
            self.assertIn(wanted, ids, wanted)
        for new in ('replay-viewer-host', 'replay-player-switch', 'replay-player-wrap', 'replay-player-root',
                    'replay-legacy-view', 'replay-legacy-controls'):
            self.assertEqual(ids.count(new), 1, new)
        self.assertEqual(len(ids), len(set(ids)), 'doppelte IDs')
        layout = self.tab.layout()
        legacy_view = self.find(layout, 'replay-legacy-view')
        self.assertTrue({'replay-image', 'replay-details', 'replay-frame-label'} <= set(self.ids(legacy_view)))
        controls = self.find(layout, 'replay-legacy-controls')
        self.assertTrue({'replay-prev', 'replay-play', 'replay-next', 'replay-index'} <= set(self.ids(controls)))
        self.assertNotIn('replay-range', self.ids(controls))                                # Bereichstext bleibt sichtbar

    def test_install_layout_ist_idempotent_und_unbekannter_aufbau_bleibt_unveraendert(self):
        layout = self.tab.layout()
        before = self.ids(layout)
        replay_player.install_layout(layout, self.tab.ctx)
        self.assertEqual(self.ids(layout), before)
        tree = self.tab.ctx.html.Div([self.tab.ctx.html.Div(id='nichts'), self.tab.ctx.html.Img(id='replay-image')])
        replay_player.install_layout(tree, self.tab.ctx)
        self.assertEqual(self.ids(tree), ['nichts', 'replay-image'])

    def test_keine_zusaetzlichen_dash_callbacks(self):
        legacy = {'refresh_sessions', 'replay_event_options', 'seek', 'replay_limits', 'replay_toggle', 'replay_play_label',
                  'replay_render', 'replay_chart', 'replay_to_studio', 'export_range'}
        self.assertTrue(legacy <= set(self.cb))
        self.assertFalse([name for name in self.cb if 'player' in name])
        self.assertEqual(len(self.app.clientside), 2)                                       # nur die beiden Frame-Balken
        self.assertIsNotNone(self.tab.player_backend)

    def test_replay_render_rendert_nicht_wenn_der_browser_abspielt(self):
        render = self.cb['replay_render']
        backend = self.tab.player_backend
        with mock.patch('dashboard.tabs.replay.draw_hud', wraps=__import__('replay_hud').draw_hud) as spy:
            first = render(0, self.name, None, 1, 'replay')                                   # bisheriges Verhalten
            self.assertTrue(first[0].startswith('data:image'))
            self.assertEqual(spy.call_count, 1)
            backend.heartbeat(True)
            self.assertEqual(render(3, self.name, None, 1, 'replay'), (self.module.no_update,) * 3)
            self.assertEqual(spy.call_count, 1)                                              # Server hat nichts gerendert
            self.assertEqual(backend.state()['index'], 3)                                    # ... aber den Zustand gemerkt
            live = render(2, '__live__', None, 1, 'replay')                                  # Live-Ringbuffer: wie bisher
            self.assertEqual(self.tab.player_backend.state()['live'], True)
            self.assertNotEqual(live, (self.module.no_update,) * 3)
            backend.heartbeat(False)
            self.assertTrue(render(4, self.name, None, 1, 'replay')[0].startswith('data:image'))

    def test_javascript_syntax_und_ids(self):
        node = shutil.which('node')
        js_files = ('replay_player.js', 'sim_player_fullscreen.js')
        if node:
            for name in js_files:
                done = subprocess.run([node, '--check', str(ROOT / 'assets' / name)], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, f'{name}: {done.stderr}')
        js = (ROOT / 'assets' / 'replay_player.js').read_text(encoding='utf-8')
        used = set(re.findall(r"\$\('([a-z0-9-]+)'\)", js)) | set(re.findall(r"'(replay-[a-z0-9-]+)'", js))
        used -= {'replay-bar', 'replay-chart-box', 'replay-player-note', 'replay-index'}   # Store/Box/vom Skript erzeugt/Dash-Schreibziel
        layout_ids = set(self.ids(self.tab.layout()))
        self.assertEqual({i for i in used if i not in layout_ids}, set())
        for needed in ('replay-index', 'replay-chart-box'):
            self.assertIn(needed, js)
        self.assertIn('replay-index', layout_ids)


# ------------------------------------------------------------------ Echtes Dash/Flask ohne Browser
@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashTests(Base):
    def setUp(self):
        super().setUp()
        from test_dashboard_callbacks import FakeApplication
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        self.addCleanup(self.stubs.__exit__, None, None, None)
        self.pin = hardware_stubs.pin_pipeline_config()
        self.addCleanup(self.pin.stop)
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        self.name = write_session(self.root, count=30, size=(640, 480))
        self.app = module.create_app(FakeApplication())
        self.tab = next(t for t in self.app.c2c_tabs if t.tab_id == 'replay')
        self.client = self.app.server.test_client()

    def test_routen_und_assets(self):
        client, backend = self.client, self.tab.player_backend
        state = client.get('/_c2c/replay/state.json').get_json()
        self.assertTrue(state['live'])
        backend.note_view(self.name, None, 4)
        state = client.get('/_c2c/replay/state.json?a=1').get_json()
        self.assertEqual((state['session'], state['index'], state['live']), (self.name, 4, False))
        self.assertTrue(backend.note_view(self.name, None, 5))                                 # a=1: Herzschlag
        client.get('/_c2c/replay/state.json?a=0&q=5000')
        self.assertFalse(backend.note_view(self.name, None, 6))
        client.get('/_c2c/replay/state.json?a=1&q=4000')                                        # aelter: ignoriert
        self.assertFalse(backend.note_view(self.name, None, 7))
        meta = client.get(f'/_c2c/replay/meta.json?s={self.name}&lo=&hi=')
        self.assertEqual((meta.status_code, meta.headers['Cache-Control'], meta.get_json()['n']), (200, 'no-store', 30))
        self.assertEqual(client.get(f'/_c2c/replay/meta.json?s={self.name}&lo=5&hi=9').get_json()['n'], 5)
        image = client.get(f'/_c2c/replay/img/3.jpg?s={self.name}&lo=&hi=&w=640&st=arcade&v=x')
        self.assertEqual((image.status_code, image.mimetype), (200, 'image/jpeg'))
        self.assertIn('max-age', image.headers['Cache-Control'])
        self.assertEqual(self.decode(image.data).shape[1], 640)
        for path in ('/_c2c/replay/img/999.jpg?s=' + self.name, '/_c2c/replay/img/1.jpg?s=gibt_es_nicht',
                     '/_c2c/replay/img/1.jpg?s=..%2Fx', '/_c2c/replay/img/1.jpg', '/_c2c/replay/meta.json?s=nein',
                     f'/_c2c/replay/meta.json?s={self.name}&lo=900&hi=950'):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        for asset in ('replay_player.js', 'replay_player.css', 'sim_player_fullscreen.js'):
            response = client.get(f'/assets/{asset}')
            self.assertEqual(response.status_code, 200, asset)
            response.close()
        page = client.get('/').get_data(as_text=True)
        for asset in ('replay_player.js', 'sim_player_fullscreen.js'):
            self.assertIn(asset, page)

    def test_abhaengigkeiten_unveraendert_keine_ausgabe_doppelt(self):
        deps = self.client.get('/_dash-dependencies').get_json()
        outputs = [d['output'] for d in deps]
        self.assertEqual(len(outputs), len(set(outputs)))
        self.assertFalse([d for d in deps if 'player' in d['output']])
        layout = json.dumps(self.client.get('/_dash-layout').get_json())
        for wanted in ('replay-player-root', 'replay-legacy-view', 'replay-legacy-controls', 'replay-image', 'replay-index'):
            self.assertIn(wanted, layout)


# ------------------------------------------------------------------ Browser (optional)
CHROMIUM = os.environ.get('C2C_CHROMIUM', '')
BROWSER = bool(CHROMIUM and os.path.exists(CHROMIUM) and HAS_DASH and importlib.util.find_spec('playwright'))


@unittest.skipUnless(BROWSER, 'Browsertest: C2C_CHROMIUM=<Pfad zu chrome-headless-shell> setzen (Playwright und Dash noetig)')
class BrowserTests(unittest.TestCase):
    """Echter Browser (Headless-Chromium): der Spieler laeuft lokal; Pause/Slider wirken auch bei langsamem Server."""

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
        hardware_stubs.write_config(os.path.join(cls.tmp.name, 'config.json'), cameracar_controller={'lost_line_timeout': 1})
        cls.root = os.path.join(cls.tmp.name, 'Data_AI', 'sessions')
        cls.session = write_session(cls.root, count=120, size=(640, 480))
        cls.fake = FakeApplication()
        for frame_id in range(1, 11):
            cls.fake.publish(frame_id, with_decision=True)
        cls.app = module.create_app(cls.fake)
        cls.tab = next(t for t in cls.app.c2c_tabs if t.tab_id == 'replay')
        from werkzeug.serving import make_server
        cls.server = make_server('127.0.0.1', 0, cls.app.server, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=CHROMIUM, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        os.chdir(cls.cwd)
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)
        cls.tmp.cleanup()
        replay_player._ACTIVE = None

    def setUp(self):
        self.delay = 0.0
        backend = self.tab.player_backend
        real = backend._render

        def slow(*args, **kwargs):
            if self.delay:
                time.sleep(self.delay)
            return real(*args, **kwargs)
        patcher = mock.patch.object(backend, '_render', slow)
        patcher.start()
        self.addCleanup(patcher.stop)
        legacy = mock.patch('dashboard.tabs.replay.draw_hud', wraps=__import__('replay_hud').draw_hud)
        self.legacy_draws = legacy.start()
        self.addCleanup(legacy.stop)
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 1300})
        self.page.set_default_timeout(30000)
        self.errors, self.requests = [], []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda r: self.requests.append(r.url) if '/_c2c/replay/' in r.url else None)
        self.page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page.goto(self.url, wait_until='domcontentloaded')
        self.page.wait_for_selector('#main-tabs')
        self.page.evaluate("() => window.localStorage.clear()")
        self.page.get_by_text('Replay Lab', exact=True).click()
        self.page.wait_for_selector('#replay-player-root .c2c-rp', state='attached')

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def open_session(self):
        page = self.page
        page.locator('#session-choice').click()
        page.locator('[role=option]', has_text=self.session).click()
        page.wait_for_function("() => { const s = window.c2cReplayPlayer.stats(); return !s.live && s.n === 120 && s.ready >= 1; }")
        page.wait_for_function("() => { const i = document.querySelector('#replay-player-root [data-r=img]'); return i.complete && i.naturalWidth > 0; }")

    def stats(self):
        return self.page.evaluate('() => window.c2cReplayPlayer.stats()')

    def test_live_zeigt_die_bisherige_anzeige_und_gespeicherte_session_den_spieler(self):
        page = self.page
        self.assertTrue(page.is_visible('#replay-legacy-view'))
        self.assertFalse(page.is_visible('#replay-player-wrap'))
        self.assertIn('Live-Ringbuffer', page.inner_text('#replay-player-note'))
        self.open_session()
        self.assertFalse(page.is_visible('#replay-legacy-view'))
        self.assertFalse(page.is_visible('#replay-legacy-controls'))
        self.assertTrue(page.is_visible('#replay-player-wrap'))
        self.assertTrue(page.is_visible('#replay-range'))                                    # Bereichstext bleibt
        self.assertIn('Frame #1', page.inner_text('#replay-player-root [data-r=label]'))
        self.assertIn('Status complete', page.inner_text('#replay-player-root [data-r=details]'))

    def test_wiedergabe_laeuft_lokal_ohne_serverseitiges_bild(self):
        page = self.page
        self.open_session()
        self.legacy_draws.reset_mock()
        page.click('#replay-player-root [data-r=play]')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx >= 12")
        self.assertEqual(self.legacy_draws.call_count, 0, 'waehrend der Wiedergabe darf der Server kein Bild zeichnen')
        page.click('#replay-player-root [data-r=play]')
        self.assertFalse(self.stats()['playing'])
        self.assertTrue([u for u in self.requests if '/img/' in u])

    def test_pause_wirkt_sofort_auch_bei_langsamem_server(self):
        page = self.page
        self.open_session()
        self.delay = 0.35
        page.click('#replay-player-root [data-r=play]')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx > 3")
        # synchron im Browser: der Klick wirkt im selben Aufruf (kein Warten auf eine Antwort des Servers)
        playing_after_click = page.evaluate("""() => { document.querySelector('#replay-player-root [data-r=play]').click();
            return window.c2cReplayPlayer.stats().playing; }""")
        self.assertFalse(playing_after_click)
        stopped = self.stats()['idx']
        page.wait_for_timeout(1500)
        self.assertEqual(self.stats()['idx'], stopped, 'nach Pause darf der Index nicht weiterlaufen')
        self.assertEqual(page.inner_text('#replay-player-root [data-r=play]'), '▶ Play')

    def test_slider_springt_waehrend_der_wiedergabe_und_position_geht_nach_dash(self):
        page = self.page
        self.open_session()
        self.delay = 0.15
        page.click('#replay-player-root [data-r=play]')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx > 2")
        page.evaluate("() => { const s = document.querySelector('#replay-player-root [data-r=slider]'); s.value = '20';"
                      " s.dispatchEvent(new Event('input', {bubbles: true})); }")
        self.assertEqual(self.stats()['idx'], 20)
        self.assertIn('Frame #21', page.inner_text('#replay-player-root [data-r=label]'))
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx > 22")
        still_playing = page.evaluate("""() => { document.querySelector('#replay-player-root [data-r=play]').click();
            return window.c2cReplayPlayer.stats().playing; }""")
        self.assertFalse(still_playing)
        stopped = self.stats()['idx']
        self.assertGreater(stopped, 22)
        self.assertLess(stopped, 119)                                                        # nicht am Ende angekommen
        deadline = time.monotonic() + 10                                                   # Pause schreibt die Position nach Dash
        while time.monotonic() < deadline and self.tab.player_backend.state()['index'] != stopped:
            page.wait_for_timeout(100)             # nicht time.sleep: Playwright (sync) bedient die Netzwerk-Hooks nur in seinen Aufrufen
        self.assertEqual(self.tab.player_backend.state()['index'], stopped)
        page.wait_for_timeout(2500)                                                        # ... und das kommt nicht als Sprung zurueck
        self.assertEqual(self.stats()['idx'], stopped)

    def test_einzelschritte_tempo_und_jeder_frame(self):
        page = self.page
        self.open_session()
        start = self.stats()['idx']
        for key in ('next', 'next', 'prev'):
            page.click(f'#replay-player-root [data-r={key}]')
        self.assertEqual(self.stats()['idx'], start + 1)
        page.locator('#replay-player-root').get_by_label('Jeder Frame').check()
        page.locator('#replay-player-root').get_by_label('4x').check()
        page.evaluate("() => { window.__seen = []; new MutationObserver(() => window.__seen.push(window.c2cReplayPlayer.stats().idx))"
                      ".observe(document.querySelector('#replay-player-root [data-r=count]'), {childList: true}); }")
        page.click('#replay-player-root [data-r=play]')
        page.wait_for_function(f"() => window.c2cReplayPlayer.stats().idx >= {start + 12}")
        page.click('#replay-player-root [data-r=play]')
        seen = page.evaluate('() => window.__seen')
        steps = [b - a for a, b in zip(seen, seen[1:]) if b >= a]
        self.assertTrue(steps and set(steps) <= {0, 1}, steps)
        self.assertEqual(self.stats()['skipped'], 0)

    def test_sprung_durch_den_server_bereich_und_index(self):
        page = self.page
        self.open_session()
        # Dash ist die Quelle der Wahrheit: Bereich und Index werden dort gesetzt (wie Ereignissprung/Zoom im Diagramm)
        page.evaluate("() => { window.dash_clientside.set_props('replay-bounds', {data: [30, 60]});"
                      " window.dash_clientside.set_props('replay-index', {value: 5}); }")
        page.wait_for_function("() => { const s = window.c2cReplayPlayer.stats(); return s.n === 31 && s.idx === 5 && s.lo === 30; }")
        self.assertIn('Frame #35', page.inner_text('#replay-player-root [data-r=label]'))
        page.wait_for_timeout(2000)                                                          # lokale Bedienung davor: Sprung des Servers gilt erst danach
        page.evaluate("() => window.dash_clientside.set_props('replay-index', {value: 12})")
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx === 12")
        self.assertTrue([u for u in self.requests if 'lo=30' in u and 'hi=60' in u])

    def test_hud_stil_und_bildgroesse_laden_die_bilder_neu(self):
        page = self.page
        self.open_session()
        self.requests.clear()
        page.select_option('#replay-player-root [data-r=hudsel]', 'arcade')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().hud === 'arcade' && window.c2cReplayPlayer.stats().ready >= 1")
        self.assertTrue([u for u in self.requests if 'st=arcade' in u])
        page.select_option('#replay-player-root [data-r=width]', '640')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().width === 640 && window.c2cReplayPlayer.stats().ready >= 1")
        self.assertTrue([u for u in self.requests if 'w=640' in u])
        page.reload(wait_until='domcontentloaded')                                          # Auswahl wird gemerkt
        self.assertEqual(page.evaluate("() => window.localStorage.getItem('c2c.rp.hud')"), 'arcade')

    def test_vollbild_knopf_taste_und_bedienleiste(self):
        page = self.page
        self.open_session()
        page.click('#replay-player-root [data-r=fs]')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().fullscreen === true")
        self.assertIn('beenden', page.inner_text('#replay-player-root [data-r=fs]'))
        self.assertTrue(page.evaluate("() => document.querySelector('#replay-player-root .c2c-rp').classList.contains('c2c-rp-full')"))
        self.assertTrue(page.is_visible('#replay-player-root [data-r=slider]'))              # Bedienleiste bleibt im Vollbild
        page.keyboard.press(' ')                                                            # Leertaste = Play
        page.wait_for_function("() => window.c2cReplayPlayer.stats().playing === true")
        page.keyboard.press(' ')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().playing === false")
        page.keyboard.press('f')                                                            # F beendet das Vollbild
        page.wait_for_function("() => window.c2cReplayPlayer.stats().fullscreen === false")

    def test_umschalter_alt_und_zurueck(self):
        page = self.page
        self.open_session()
        page.click('#replay-player-root [data-r=next]')
        page.click('#replay-player-root [data-r=next]')
        index = self.stats()['idx']
        page.locator('#replay-player-switch').get_by_label('Über den Server (alt)').check()
        self.assertTrue(page.is_visible('#replay-play'))
        self.assertFalse(page.is_visible('#replay-player-wrap'))
        page.wait_for_function("() => (document.getElementById('replay-image').getAttribute('src') || '').startsWith('data:image')")
        self.assertTrue(page.is_visible('#replay-image'))
        page.wait_for_function(f"() => document.getElementById('replay-frame-label').innerText.includes('Frame #{index + 1}')")  # gleiche Position
        page.locator('#replay-player-switch').get_by_label('Im Browser (schnell)').check()
        self.assertTrue(page.is_visible('#replay-player-wrap'))
        self.assertEqual(self.stats()['idx'], index)

    def test_frame_balken_im_diagramm_folgt_wird_lokal_gezogen_und_klick_springt(self):
        page = self.page
        self.open_session()
        page.wait_for_function("() => { const b = document.querySelector('#replay-chart .c2c-framebar'); return b && b.style.display === 'block'; }")
        page.locator('#replay-chart').scroll_into_view_if_needed()
        page.click('#replay-player-root [data-r=play]')
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx > 8")
        page.click('#replay-player-root [data-r=play]')
        page.wait_for_function("() => Number(document.querySelector('#replay-chart .c2c-framebar').getAttribute('data-frame'))"
                               " === window.c2cReplayPlayer.stats().idx + 1")
        page.locator('#replay-chart').scroll_into_view_if_needed()
        page.wait_for_timeout(1200)
        geometry = page.evaluate("""() => { const b = document.querySelector('#replay-chart .c2c-framebar').getBoundingClientRect();
            const gd = document.querySelector('#replay-chart .js-plotly-plot'); const x = gd._fullLayout.xaxis;
            return [b.left + b.width / 2, b.top + b.height / 2, gd.getBoundingClientRect().left + x._offset, x._length]; }""")
        self.requests.clear()
        page.mouse.move(geometry[0], geometry[1])
        page.mouse.down()
        page.mouse.move(geometry[2] + geometry[3] * 0.8, geometry[1], steps=6)
        page.mouse.up()
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx > 80")
        page.evaluate("() => document.querySelector('#replay-chart .js-plotly-plot').emit('plotly_click', {points: [{customdata: 31}]})")
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx === 30")

    def test_set_props_huelle_stapelt_sich_nicht_mit_dem_simulationsskript(self):
        """sim_player.js umhuellt set_props ebenfalls (Flag _c2cSim) und prueft alle 0,7 s: es darf kein endloses Stapeln geben."""
        page = self.page
        self.open_session()
        page.evaluate("""() => {
            window.__layers = 0;
            const dc = window.dash_clientside;
            setInterval(() => {                                   // wie patchSetProps in sim_player.js
                if (dc.set_props._c2cSim) return;
                const original = dc.set_props;
                const wrapped = function () { window.__layers += 1; return original.apply(this, arguments); };
                wrapped._c2cSim = true;
                dc.set_props = wrapped;
            }, 700);
        }""")
        page.wait_for_timeout(4500)
        depth = page.evaluate("""() => { window.__layers = 0; window.dash_clientside.set_props('replay-pre', {value: 30}); return window.__layers; }""")
        self.assertLessEqual(depth, 2)                                                       # stabil, nicht wachsend
        page.evaluate("() => window.dash_clientside.set_props('replay-bar', {data: {frame: 50, nonce: 1}})")
        page.wait_for_function("() => window.c2cReplayPlayer.stats().idx === 49")


@unittest.skipUnless(BROWSER, 'Browsertest: C2C_CHROMIUM=<Pfad zu chrome-headless-shell> setzen (Playwright und Dash noetig)')
class SimFullscreenAddonTests(unittest.TestCase):
    """Das Zusatzskript haengt Vollbild an die Oberflaeche des Simulations-Spielers (Paket I3), ohne sim_player.js zu aendern."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=CHROMIUM, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_knopf_taste_leertaste_und_ausblenden(self):
        page = self.browser.new_page(viewport={'width': 1200, 'height': 800})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.set_content("""<div id="host"><div class="c2c-sp" tabindex="0"><div class="c2c-sp-label">Frame #1</div>
            <div class="c2c-sp-grid"><div><img class="c2c-sp-img" alt=""></div><div><img class="c2c-sp-img" alt=""></div></div>
            <div class="c2c-sp-controls"><button id="b">x</button></div><div class="c2c-sp-opts">opts</div></div></div>
            <script>window.__calls = []; window.__playing = false;
              window.c2cSimPlayer = {stats: () => ({playing: window.__playing}),
                play: () => { window.__playing = true; window.__calls.push('play'); },
                pause: () => { window.__playing = false; window.__calls.push('pause'); }};</script>""")
        page.add_style_tag(path=str(ROOT / 'assets' / 'replay_player.css'))
        page.add_script_tag(path=str(ROOT / 'assets' / 'sim_player_fullscreen.js'))
        page.wait_for_selector('.c2c-sp-fs')
        self.assertEqual(page.locator('.c2c-sp-fs').count(), 1)
        page.wait_for_timeout(1600)
        self.assertEqual(page.locator('.c2c-sp-fs').count(), 1, 'Knopf darf nicht mehrfach angehaengt werden')
        page.click('.c2c-sp-fs')
        page.wait_for_function("() => document.fullscreenElement && document.fullscreenElement.classList.contains('c2c-sp')")
        self.assertIn('beenden', page.inner_text('.c2c-sp-fs'))
        page.keyboard.press(' ')
        page.keyboard.press(' ')
        self.assertEqual(page.evaluate('() => window.__calls'), ['play', 'pause'])
        page.keyboard.press(' ')                                                            # Abspielen: Leiste blendet sich aus
        page.wait_for_function("() => document.querySelector('.c2c-sp').classList.contains('c2c-sp-idle')", timeout=6000)
        page.mouse.move(100, 100)
        page.wait_for_function("() => !document.querySelector('.c2c-sp').classList.contains('c2c-sp-idle')")
        page.keyboard.press('f')
        page.wait_for_function("() => !document.fullscreenElement")
        self.assertEqual(errors, [])
        page.close()


if __name__ == '__main__':
    unittest.main()
