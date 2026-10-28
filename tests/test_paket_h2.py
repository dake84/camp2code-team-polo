"""Paket H2: fuenf Reiter, Ereignisprotokoll, Auslastung, gekoppeltes Telemetrie-Diagramm, Speicherplatz, Cockpit->Studio.

Ohne Hardware und ohne Motoransteuerung (Hardware-Attrappen, FakeApplication). Echtes Dash 4 laeuft ueber den
Flask-Testclient, sofern installiert. Nicht getestet: Bedienung im Browser, Fahrt am Auto, Performance auf dem Pi
(es gibt keine Messung der CPU-Last des Dashboards selbst).
"""
import csv
import importlib.util
import json
import logging
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

import sys

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from models import CameraData  # noqa: E402
from test_paket_f import PARAMS, write_session  # noqa: E402  (vor jeder Dash-Attrappe importieren)

ROOT = Path(__file__).resolve().parents[1]
HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))


def walk(node):
    """Alle Komponenten eines Baums; funktioniert mit der Dash-Attrappe (``walk``) und mit echtem Dash."""
    nodes = node if isinstance(node, (list, tuple)) else [node]
    found = []
    for item in nodes:
        found.extend(item.walk() if hasattr(item, 'walk') else [item, *item._traverse()])
    return found


def prop(component, name, default=None):
    """Eigenschaft einer Komponente (Attrappe: ``props``-Dict, echtes Dash: Attribut)."""
    props = getattr(component, 'props', None)
    if isinstance(props, dict) and name in props:
        return props[name]
    return getattr(component, name, default)


def texts(node):
    return [c.children for c in walk(node) if isinstance(getattr(c, 'children', None), str)]


def events(*rows):
    return [SimpleNamespace(timestamp_ns=ts, frame_id=frame, code=code, detail=detail)
            for ts, frame, code, detail in rows]


# ------------------------------------------------------------------ system_stats
class SystemStatsPureTests(unittest.TestCase):
    def setUp(self):
        import system_stats
        self.s = system_stats

    def test_ampel_cpu(self):
        s = self.s
        self.assertEqual([s.cpu_level(v) for v in (0, 59.9, 60, 84.9, 85, 100)],
                         ['ok', 'ok', 'warn', 'warn', 'bad', 'bad'])
        self.assertEqual(s.cpu_level(10, 79.9), 'ok')
        self.assertEqual(s.cpu_level(10, 80), 'bad')                      # ab 80 Grad C immer rot
        self.assertEqual(s.cpu_level(None, 85), 'bad')                    # auch ohne CPU-Wert
        self.assertEqual(s.cpu_level(None), 'off')
        self.assertEqual((s.temp_level(79.9), s.temp_level(80), s.temp_level(None)), ('ok', 'bad', 'off'))

    def test_fehlende_werte_sind_gedankenstrich_nie_null(self):
        s = self.s
        summary = s.load_summary(s.empty())
        self.assertEqual((summary['cpu'], summary['ram'], summary['temp']), ('–', '–', '–'))
        self.assertEqual((summary['cpu_level'], summary['ram_level'], summary['temp_level']), ('off',) * 3)
        zero = s.load_summary({'cpu': 0.0, 'ram': 0.0, 'temp': 0.0})        # echte Null bleibt eine Null
        self.assertEqual((zero['cpu'], zero['ram'], zero['temp']), ('0 %', '0 %', '0 °C'))
        full = s.load_summary({'cpu': 42.4, 'ram': 61.0, 'temp': 55.5})
        self.assertEqual((full['cpu'], full['ram'], full['temp'], full['cpu_level']), ('42 %', '61 %', '56 °C', 'ok'))
        self.assertEqual(s.load_summary({'cpu': 10, 'ram': 5, 'temp': 81})['cpu_level'], 'bad')

    def test_temperatur_aus_sysfs_mit_fallback(self):
        s = self.s
        with tempfile.TemporaryDirectory() as folder:
            good = Path(folder) / 'temp'
            good.write_text('51234\n')
            self.assertAlmostEqual(s.read_temperature(str(good)), 51.234)
            bad = Path(folder) / 'bad'
            bad.write_text('kaputt')
            with patch.object(s, 'psutil', None):
                self.assertIsNone(s.read_temperature(str(bad)))
                self.assertIsNone(s.read_temperature(str(Path(folder) / 'fehlt')))
                good.write_text('999999999')                                # unplausibel
                self.assertIsNone(s.read_temperature(str(good)))
            fake = SimpleNamespace(sensors_temperatures=lambda: {'cpu': [SimpleNamespace(current=47.5)]})
            with patch.object(s, 'psutil', fake):
                self.assertEqual(s.read_temperature(str(bad)), 47.5)         # Fallback psutil
            broken = SimpleNamespace(sensors_temperatures=lambda: (_ for _ in ()).throw(OSError('x')))
            with patch.object(s, 'psutil', broken):
                self.assertIsNone(s.read_temperature(str(bad)))
            nosensor = SimpleNamespace()                                    # z. B. Windows: kein sensors_temperatures
            with patch.object(s, 'psutil', nosensor):
                self.assertIsNone(s.read_temperature(str(bad)))


class Session:
    """Hilfsklasse: Session-Verzeichnis mit JPEG-Dateien bekannter Groesse und Manifest."""

    @staticmethod
    def make(root, name, sizes, fps=25.0, with_manifest=True):
        path = Path(root) / name
        (path / 'frames').mkdir(parents=True)
        (path / 'frames.csv').write_text('frame_id\n')
        for index, size in enumerate(sizes):
            (path / 'frames' / f'frame_{index:010d}.jpg').write_bytes(b'x' * size)
        if with_manifest and fps is not None:
            (path / 'manifest.json').write_text(json.dumps({'capture': {'median_fps': fps}}))
        return path


class RecordingEstimateTests(unittest.TestCase):
    def setUp(self):
        import system_stats
        self.s = system_stats
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'sessions'
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ohne_session_keine_schaetzung(self):
        self.assertIsNone(self.s.recording_estimate(10e9, self.root, 25))
        self.assertIsNone(self.s.recording_estimate(10e9, self.root / 'fehlt', 25))
        Session.make(self.root, 'session_leer', [])                          # Session ohne Bilder
        self.assertIsNone(self.s.recording_estimate(10e9, self.root, 25))

    def test_schaetzung_aus_mittlerer_jpeg_groesse_und_fps(self):
        Session.make(self.root, 'session_a', [100_000, 200_000], fps=10.0)
        result = self.s.recording_estimate(15e9, self.root, 25.0)            # live-fps hat Vorrang
        self.assertAlmostEqual(result['mean_kb'], 150_000 / 1024)
        self.assertAlmostEqual(result['minutes'], 15e9 / (150_000 * 25.0) / 60)
        self.assertEqual((result['fps'], result['session']), (25.0, 'session_a'))
        fallback = self.s.recording_estimate(15e9, self.root, None)           # sonst Median aus dem Manifest
        self.assertEqual(fallback['fps'], 10.0)
        Session.make(self.root, 'session_b', [50_000], fps=None, with_manifest=False)   # neueste Session ohne fps
        self.assertEqual(self.s.recording_estimate(15e9, self.root, 20.0)['session'], 'session_b')
        self.assertIsNone(self.s.recording_estimate(15e9, self.root, None))   # weder live noch Manifest: keine Schaetzung
        self.assertIsNone(self.s.recording_estimate(15e9, self.root, 0))

    def test_hoechstens_40_dateien_werden_gemessen(self):
        Session.make(self.root, 'session_viele', [1000] * 400)
        calls = []
        original = Path.stat

        def counting(path, *args, **kwargs):
            if path.suffix == '.jpg':
                calls.append(path.name)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'stat', counting):
            result = self.s.recording_estimate(1e9, self.root, 25)
        self.assertLessEqual(len(calls), 40)
        self.assertAlmostEqual(result['mean_kb'], 1000 / 1024)

    def test_anzeigetexte(self):
        s = self.s
        self.assertEqual(s.disk_text(None), '–')
        base = {'free_gb': 12.34, 'free_pct': 56.7, 'estimate': None}
        self.assertIn('Frei 12.3 GB (57 %)', s.disk_text(base))
        self.assertIn('nicht schätzbar', s.disk_text(base))
        with_estimate = dict(base, estimate={'minutes': 123.4, 'mean_kb': 150.0, 'fps': 25.0})
        self.assertIn('reicht für ca. 123 min Aufnahme (Schätzung', s.disk_text(with_estimate))


class SystemSamplerTests(unittest.TestCase):
    def setUp(self):
        import system_stats
        self.s = system_stats
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [100.0]
        self.sampler = system_stats.SystemSampler(data_dir=Path(self.tmp.name) / 'Data_AI', thermal_path='/nonexistent',
                                                  clock=lambda: self.now[0], disk_every_s=30.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_werte_und_none_statt_null(self):
        with patch.object(self.s, 'psutil', None):
            values = self.sampler.sample()
        self.assertIsNone(values['cpu'])
        self.assertIsNone(values['ram'])
        self.assertIsNone(values['temp'])
        self.assertEqual(self.sampler.last()['cpu'], None)
        self.assertIsNotNone(values['disk'])                                  # shutil.disk_usage laeuft auch ohne psutil
        fake = SimpleNamespace(cpu_percent=lambda interval=None: 37.5,
                               virtual_memory=lambda: SimpleNamespace(percent=64.0))
        with patch.object(self.s, 'psutil', fake):
            values = self.sampler.sample()
        self.assertEqual((values['cpu'], values['ram']), (37.5, 64.0))
        broken = SimpleNamespace(cpu_percent=lambda interval=None: (_ for _ in ()).throw(RuntimeError('x')),
                                 virtual_memory=lambda: (_ for _ in ()).throw(RuntimeError('y')))
        with patch.object(self.s, 'psutil', broken):
            values = self.sampler.sample()
        self.assertEqual((values['cpu'], values['ram']), (None, None))         # Fehler -> None, nie 0 und nie Ausnahme

    def test_cpu_percent_ohne_blockieren_und_genau_ein_aufrufer(self):
        calls = []

        def cpu(interval=None):
            calls.append(interval)
            return 12.0
        fake = SimpleNamespace(cpu_percent=cpu, virtual_memory=lambda: SimpleNamespace(percent=1.0))
        with patch.object(self.s, 'psutil', fake):
            sampler = self.s.SystemSampler(data_dir=self.tmp.name, clock=lambda: 0.0)
            self.assertEqual(calls, [None])                                    # Bezugspunkt beim Anlegen
            sampler.sample()
            self.assertEqual(calls, [None, None])
            other = []
            thread = threading.Thread(target=lambda: other.append(sampler.sample()))
            with self.assertLogs(self.s.log, 'WARNING'):                         # einmalige Warnung beim zweiten Aufrufer
                thread.start()
                thread.join()
            self.assertEqual(len(calls), 2)                                    # zweiter Aufrufer misst NICHT
            self.assertEqual(other[0]['cpu'], 12.0)                            # bekommt den letzten Wert
            self.assertEqual(sampler.last()['cpu'], 12.0)                      # lesen ist fuer alle erlaubt
        self.assertTrue(all(interval is None for interval in calls))           # nie blockierend (interval=None)

    def test_speicherplatz_nur_alle_30_sekunden(self):
        with patch.object(self.s, 'psutil', None):
            disk_calls = []
            original = self.sampler._disk
            self.sampler._disk = lambda: disk_calls.append(1) or original()
            for _ in range(8):                                                 # 8 Takte zu 0,25 s = 2 s
                self.sampler.sample()
                self.now[0] += 0.25
            self.assertEqual(len(disk_calls), 1)
            self.now[0] += 30.0
            self.sampler.sample()
            self.assertEqual(len(disk_calls), 2)

    def test_speicherplatz_fehler_wird_none(self):
        with patch.object(self.s, 'psutil', None):
            self.sampler._disk = lambda: (_ for _ in ()).throw(OSError('weg'))
            self.assertIsNone(self.sampler.sample()['disk'])

    def test_nicht_vorhandenes_verzeichnis_nutzt_den_naechsten_vorhandenen_ordner(self):
        with patch.object(self.s, 'psutil', None):
            disk = self.sampler.sample()['disk']                               # Data_AI existiert hier noch nicht
        self.assertGreater(disk['free_gb'], 0)
        self.assertTrue(0 <= disk['free_pct'] <= 100)


# ------------------------------------------------------------------ event_log
class EventLogTests(unittest.TestCase):
    def setUp(self):
        import event_log
        self.e = event_log

    def test_codes_gegen_den_quelltext_geprueft(self):
        """Jeder Code der Filterliste muss im Quelltext tatsaechlich vorkommen (nicht geraten)."""
        source = ''
        for name in ('driving_health.py', 'car_application.py', 'session_recorder.py', 'recovery_control.py',
                     'driving/camera_mode.py', 'speed_policy.py', 'config_store.py'):
            path = ROOT / name
            if path.is_file():
                source += path.read_text(encoding='utf-8')
        for code in self.e.IMPORTANT_CODES:
            self.assertIn(f"'{code}'", source, code)
        for prefix in self.e.IMPORTANT_PREFIXES:
            self.assertRegex(source, rf"'{prefix}[A-Z_]+'", prefix)
        for noisy in ('LANE_PARTIAL', 'LANES_DETECTED', 'STEERING_COMPUTED', 'STEERING_HELD'):
            self.assertFalse(self.e.is_important(noisy), noisy)

    def test_filter_wichtig(self):
        e = self.e
        for code in ('STOP_REASON', 'CONTROLLER_STARTED', 'CONTROLLER_STOPPED', 'LANE_LOST', 'RECOVERY_ENABLED',
                     'RECOVERY_DISABLED', 'RECORDING_STARTED', 'RECORDING_STOPPED', 'RECORDER_FRAME_GAP',
                     'SPEED_POLICY', 'CONFIG_SAVED'):
            self.assertTrue(e.is_important(code), code)
        self.assertFalse(e.is_important('TEST_EVENT'))
        self.assertFalse(e.is_important(None))
        self.assertEqual((e.valid_mode('all'), e.valid_mode('x'), e.valid_mode(None)), ('all', 'important', 'important'))

    def test_frame_nummer(self):
        e = self.e
        self.assertEqual([e.frame_number(v) for v in (5, '7', 0, 3.0)], [5, 7, 0, 3])
        self.assertEqual([e.frame_number(v) for v in (-1, None, '', 'x', float('nan'), [1])], [None] * 6)

    def test_link_ziel_session_vor_ringbuffer(self):
        e = self.e
        self.assertIsNone(e.link_target(None, 's', lambda f: True, {1}))
        self.assertEqual(e.link_target(5, 'sess', lambda f: f == 5, {5}), 'sess')       # Session hat Vorrang
        self.assertEqual(e.link_target(5, 'sess', lambda f: False, {5}), e.LIVE)
        self.assertEqual(e.link_target(5, None, None, {5}), e.LIVE)
        self.assertIsNone(e.link_target(6, 'sess', lambda f: False, {5}))
        self.assertEqual(e.jump_href('s', 5, 99), '#jump/s/5/99')

    def test_live_eintraege_neueste_zuerst(self):
        e = self.e
        rows = events((1e9, 5, 'STOP_REASON', 'a'), (2e9, 6, 'LANE_PARTIAL', 'b'), (3e9, -1, 'RECORDING_STARTED', 'c'),
                      (4e9, None, 'LANE_LOST', 'd'))
        everything = e.live_entries(rows, 5e9, 'all', 'sess', lambda f: f == 5, {6})
        self.assertEqual([x['code'] for x in everything], ['LANE_LOST', 'RECORDING_STARTED', 'LANE_PARTIAL', 'STOP_REASON'])
        self.assertEqual([x['target'] for x in everything], [None, None, e.LIVE, 'sess'])
        self.assertEqual([x['frame'] for x in everything], [None, None, 6, 5])         # -1 und None: kein Frame
        important = e.live_entries(rows, 5e9, 'important', 'sess', lambda f: f == 5, {6})
        self.assertEqual([x['code'] for x in important], ['LANE_LOST', 'RECORDING_STARTED', 'STOP_REASON'])
        self.assertEqual(everything[0]['when'], 'vor 1.0 s')
        self.assertEqual(len(e.live_entries(rows, 5e9, 'all', limit=2)), 2)
        self.assertEqual(e.live_entries([], 5e9), [])

    def test_session_eintraege(self):
        e = self.e
        event_rows = [{'timestamp_ns': '12000000000', 'frame_id': '3', 'code': 'LANE_LOST', 'detail': 'x'},
                      {'timestamp_ns': '13000000000', 'frame_id': '', 'code': 'LANES_DETECTED', 'detail': ''},
                      {'timestamp_ns': '14000000000', 'frame_id': '999', 'code': 'STOP_REASON', 'detail': 'weg'},
                      {'timestamp_ns': 'kaputt', 'frame_id': '-1', 'code': 'RECORDING_STOPPED', 'detail': ''}]
        frame_rows = [{'frame_id': str(i), 'camera_monotonic_ns': str(10_000_000_000 + i * 40_000_000)}
                      for i in range(1, 6)]
        entries = e.session_entries(event_rows, frame_rows, 'sess', 'important')
        self.assertEqual([x['code'] for x in entries], ['RECORDING_STOPPED', 'STOP_REASON', 'LANE_LOST'])
        self.assertEqual([x['target'] for x in entries], [None, None, 'sess'])        # 999 liegt nicht in der Session
        self.assertEqual(entries[2]['when'], '+2.0 s')          # 12,00 s - 10,04 s (erster Frame) = 1,96 s
        self.assertEqual(len(e.session_entries(event_rows, frame_rows, 'sess', 'all')), 4)
        self.assertEqual(e.session_entries([], [], 's'), [])

    def test_tabelle_link_grau_und_leer(self):
        from dash import dcc, html
        e = self.e
        entries = [{'ts': 7, 'when': 'vor 1.0 s', 'frame': 5, 'code': 'STOP_REASON', 'detail': 'x', 'target': 's'},
                   {'ts': 8, 'when': 'vor 2.0 s', 'frame': 6, 'code': 'LANE_LOST', 'detail': 'y', 'target': None},
                   {'ts': 9, 'when': 'vor 3.0 s', 'frame': None, 'code': 'RECORDING_STARTED', 'detail': 'z',
                    'target': None}]
        table = e.event_table(html, dcc, entries)
        self.assertEqual(texts(table)[:4], ['Zeit', 'Frame', 'Ereignistyp', 'Beschreibung'])
        links = [c for c in walk(table) if type(c).__name__ == 'Link']
        self.assertEqual([(prop(c, 'href'), c.children) for c in links], [('#jump/s/5/7', '5')])
        grey = [c for c in walk(table) if prop(c, 'title') == 'Weder in einer Session noch im Ringbuffer']
        self.assertEqual([c.children for c in grey], ['6'])
        self.assertIn('–', texts(table))                                      # Frame None: kein Link, kein Tooltip
        self.assertEqual(texts(e.event_table(html, dcc, [])), ['Keine Ereignisse'])

    def test_aufnahmezusammenfassung(self):
        e = self.e
        recorder = SimpleNamespace(is_running=lambda: True,
                                   stats=lambda: {'frames': 12, 'gap_frames': 2, 'missing_decisions': 3,
                                                  'error': None, 'path': '/x/Data_AI/sessions/session_1'})
        text = e.recording_summary(recorder)
        for part in ('Aufnahme läuft', 'Frames 12', 'Lücken 2', 'fehlende Entscheidungen 3', 'Session session_1',
                     'Fehler keiner'):
            self.assertIn(part, text)
        idle = SimpleNamespace(is_running=lambda: False, stats=lambda: {'frames': 0, 'path': None, 'error': 'Platte voll'})
        self.assertIn('keine Aufnahme aktiv', e.recording_summary(idle))
        self.assertIn('Session –', e.recording_summary(idle))
        self.assertIn('Fehler Platte voll', e.recording_summary(idle))
        broken = SimpleNamespace(is_running=lambda: (_ for _ in ()).throw(OSError('x')))
        self.assertEqual(e.recording_summary(broken), 'Aufnahmezustand nicht lesbar')


# ------------------------------------------------------------------ SessionRecorder.has_frame
class HasFrameTests(unittest.TestCase):
    def test_has_frame(self):
        from session_recorder import SessionRecorder
        with tempfile.TemporaryDirectory() as folder:
            def camera(frame_id):
                return SimpleNamespace(frame_id=frame_id, raw_image=np.zeros((12, 16, 3), dtype=np.uint8), timestamp=100,
                                       monotonic_ns=80 + frame_id, lanes=())
            records = [SimpleNamespace(camera=camera(7), driving=None)]
            car = SimpleNamespace(frame_history=SimpleNamespace(snapshot=lambda: tuple(records)))
            recorder = SessionRecorder(car, folder)
            self.assertFalse(recorder.has_frame(7))
            recorder.start()
            recorder.stop()
            recorder.capture_once()
            self.assertTrue(recorder.has_frame(7))
            self.assertTrue(recorder.has_frame('7'))
            for other in (8, -1, None, 'x', 7.5):
                self.assertFalse(recorder.has_frame(other), other)
            records.clear()                                                   # sonst schreibt der Aufnahme-Thread Frame 7 erneut
            recorder.start()                                                  # neue Session: alte Frames gelten nicht mehr
            self.assertFalse(recorder.has_frame(7))
            recorder.stop()


# ------------------------------------------------------------------ telemetry_chart
class TelemetryChartTests(unittest.TestCase):
    NOW = 1000.0

    def setUp(self):
        import telemetry_chart
        self.t = telemetry_chart

    def data(self, count=12, step=0.25, gap_from=None):
        now = self.NOW
        freqs = [(now - 3 + i * step, None if gap_from is not None and i >= gap_from else 25.0,
                  None if gap_from is not None and i >= gap_from else 20.0) for i in range(count)]
        latency = [(now - 3 + i * step, 5.0, 4.0, None) for i in range(count)]
        system = [(now - 3 + i * step, 40.0, 30.0, 55.0) for i in range(count)]
        driving = [(now - 3 + i * step, i, 97, 30, 0, 2, 99) for i in range(count)]
        return freqs, latency, system, driving

    def figure(self, **kw):
        freqs, latency, system, driving = self.data(**kw)
        return self.t.build_figure(self.NOW, 25.0, freqs, latency, system, driving)

    def test_einfaches_dict_vier_spuren_gemeinsame_zeitachse(self):
        figure = self.figure()
        self.assertIs(type(figure), dict)
        self.assertEqual(set(figure), {'data', 'layout'})
        self.assertNotIn('height', figure['layout'])                          # feste Hoehe liegt am dcc.Graph
        self.assertEqual(figure['layout']['uirevision'], 'telemetry')
        self.assertEqual(figure['layout']['xaxis']['range'], [-60, 0])
        self.assertEqual(figure['layout']['xaxis']['title']['text'], 'Sekunden vor jetzt')
        self.assertEqual(figure['layout']['xaxis']['anchor'], 'y4')           # Achse unten an der vierten Spur
        names = {tr['name']: tr['yaxis'] for tr in figure['data']}
        self.assertEqual({names[k] for k in ('Kamera (Soll)', 'Kamera (Ist)', 'Entscheidungen (Ist)')}, {'y'})
        self.assertEqual({names[k] for k in ('Datenalter', 'Bildverarbeitung')}, {'y2'})
        self.assertEqual({names[k] for k in ('Rohwinkel', 'Stellwert', 'Speed-Steuerwert')}, {'y3'})
        self.assertEqual(names['Linienanzahl'], 'y5')
        self.assertEqual({names[k] for k in ('CPU', 'RAM', 'Temperatur')}, {'y4'})
        domains = [figure['layout'][k]['domain'] for k in ('yaxis', 'yaxis2', 'yaxis3', 'yaxis4')]
        self.assertTrue(all(a[0] > b[1] for a, b in zip(domains, domains[1:])))   # Spuren ueberlappen nicht
        self.assertEqual(len(figure['layout']['annotations']), 4)                  # Spurtitel
        json.dumps(figure)                                                         # serialisierbar (kein go.Figure)

    def test_zeitachse_ist_sekunden_vor_jetzt(self):
        figure = self.figure()
        cpu = next(tr for tr in figure['data'] if tr['name'] == 'CPU')
        self.assertAlmostEqual(cpu['x'][0], -3.0)
        self.assertAlmostEqual(cpu['x'][-1], -0.25)
        soll = next(tr for tr in figure['data'] if tr['name'] == 'Kamera (Soll)')
        self.assertEqual((soll['x'], soll['y']), ([-60, 0], [25.0, 25.0]))

    def test_nur_letzte_60_sekunden(self):
        freqs = [(self.NOW - 90 + i, 25.0, 20.0) for i in range(100)]             # 90 s bis -10 s
        figure = self.t.build_figure(self.NOW, 25, freqs, [], [], [])
        ist = next(tr for tr in figure['data'] if tr['name'] == 'Kamera (Ist)')
        self.assertGreaterEqual(min(ist['x']), -60)

    def test_luecken_statt_fortgeschriebener_werte(self):
        figure = self.figure(gap_from=6)                                          # ab Index 6 keine Messwerte (Stillstand)
        ist = next(tr for tr in figure['data'] if tr['name'] == 'Kamera (Ist)')
        self.assertEqual(ist['y'][:6], [25.0] * 6)
        self.assertTrue(all(v is None for v in ist['y'][6:]))                     # None, nicht 0 und nicht der letzte Wert
        self.assertIs(ist['connectgaps'], False)
        driving = [(self.NOW - 30, 1, 97, 30, 0, 2, 99), (self.NOW - 3, 2, 98, 30, 0, 2, 99)]
        figure = self.t.build_figure(self.NOW, 25, [], [], [], driving)
        raw = next(tr for tr in figure['data'] if tr['name'] == 'Rohwinkel')
        self.assertEqual(raw['y'], [97, None, 98])                                # grosser Zeitsprung = Luecke
        self.assertEqual(len(raw['x']), 3)

    def test_ohne_einen_messwert_keine_figur_und_leere_spuren_entfallen(self):
        self.assertIsNone(self.t.build_figure(self.NOW, 25, [], [], [], []))
        none_rows = [(self.NOW - 1, None, None, None)]
        self.assertIsNone(self.t.build_figure(self.NOW, 25, [], none_rows, none_rows, []))
        figure = self.t.build_figure(self.NOW, 25, [], [], [(self.NOW - 1, 20.0, None, None)], [])
        self.assertEqual([tr['name'] for tr in figure['data']], ['CPU'])        # nur vorhandene Messwerte

    def test_soll_linie_nur_mit_frequenzkurven(self):
        figure = self.t.build_figure(self.NOW, 25, [], [], [(self.NOW - 1, 20.0, 5.0, 40.0)], [])
        self.assertNotIn('Kamera (Soll)', [tr['name'] for tr in figure['data']])

    def test_hoechstens_240_punkte_je_kurve(self):
        rows = [(self.NOW - 59 + i * 0.01, 25.0, 20.0) for i in range(5000)]
        rows = [r for r in rows if r[0] <= self.NOW]
        figure = self.t.build_figure(self.NOW, 25, rows, [], [], [])
        for trace in figure['data']:
            self.assertLessEqual(len(trace['x']), 240 + 5, trace['name'])      # + eingefuegte Luecken
        self.assertLessEqual(len(self.t.thin(list(range(1000)))), 240)
        self.assertEqual(self.t.thin([1, 2, 3]), [1, 2, 3])


# ------------------------------------------------------------------ CarApplication
class ApplicationSamplingTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'))
        os.chdir(self.tmp.name)
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        from car_application import CarApplication
        self.app = CarApplication(start_camera=False)

    def tearDown(self):
        self.app.shutdown()
        self.stubs.__exit__(None, None, None)
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_sampler_thread_ist_einziger_aufrufer(self):
        import system_stats
        calls = []
        fake = SimpleNamespace(cpu_percent=lambda interval=None: calls.append(threading.current_thread().name) or 10.0,
                               virtual_memory=lambda: SimpleNamespace(percent=20.0))
        with patch.object(system_stats, 'psutil', fake):
            before = len(calls)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(self.app.sample_extra()[1]) < 3:
                time.sleep(0.05)
            for _ in range(5):                                                   # Dash-Callbacks lesen nur
                self.app.system_stats()
                self.app.sample_extra()
            sampled = calls[before:]
        self.assertGreaterEqual(len(sampled), 1)
        self.assertEqual(set(sampled), {self.app._telemetry_thread.name})        # nur der Telemetrie-Thread

    def test_verlaeufe_und_systemwerte(self):
        import system_stats
        fake = SimpleNamespace(cpu_percent=lambda interval=None: 33.0,
                               virtual_memory=lambda: SimpleNamespace(percent=44.0))
        with patch.object(system_stats, 'psutil', fake), patch.object(system_stats, 'read_temperature',
                                                                      return_value=52.0):
            self.app.stop_event.set()                                            # Hintergrund-Thread beenden
            self.app._telemetry_thread.join(2)
            self.app.system = system_stats.SystemSampler(data_dir=self.tmp.name)  # frischer Besitzer: dieser Thread
            camera = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.empty(0), frame_id=4,
                                monotonic_ns=time.monotonic_ns() - 20_000_000, processing_ns=5_000_000)
            self.app.car.camera_data = camera
            self.app._sample_once()
        latency, system = self.app.sample_extra()
        self.assertEqual(system[-1][1:], (33.0, 44.0, 52.0))
        self.assertAlmostEqual(latency[-1][1], 20, delta=25)                      # Datenalter in ms
        self.assertEqual(latency[-1][2], 5.0)                                     # Bildverarbeitung
        self.assertIsNone(latency[-1][3])                                         # im Stillstand keine Reaktionszeit
        stats = self.app.system_stats()
        self.assertEqual((stats['cpu'], stats['ram'], stats['temp']), (33.0, 44.0, 52.0))
        self.assertIsNotNone(stats['disk'])

    def test_veraltete_frequenzen_werden_none_statt_null(self):
        self.app.stop_event.set()
        self.app._telemetry_thread.join(2)
        self.app._freq_history.clear()
        self.app._sample_once()
        rows = self.app.sample()[1]
        self.assertEqual((rows[-1][1], rows[-1][2]), (None, None))                # kein Tick seit 2 s: Luecke, nicht 0


# ------------------------------------------------------------------ Dashboard
def fake_stats(cpu=42.0, ram=61.0, temp=55.0, disk=True):
    return {'cpu': cpu, 'ram': ram, 'temp': temp, 'time': 1.0,
            'disk': {'free_gb': 20.5, 'free_pct': 40.0, 'total_gb': 50.0,
                     'estimate': {'minutes': 95.0, 'mean_kb': 120.0, 'fps': 25.0, 'session': 's'}} if disk else None}


class DashboardH2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
        cls.stats = fake_stats()
        cls.fake.system_stats = lambda: cls.stats
        cls.latency, cls.system = (), ()
        cls.fake.sample_extra = lambda: (cls.latency, cls.system)
        cls.fake.recorder = SimpleNamespace(
            is_running=lambda: False, path=None, has_frame=lambda f: False,
            stats=lambda: {'frames': 7, 'gap_frames': 1, 'missing_decisions': 2, 'error': None, 'path': None})
        cls.app = cls.module.create_app(cls.fake)
        cls.cb = cls.app.callbacks

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def setUp(self):
        self.stats = type(self).stats = fake_stats()
        self.fake.health = type(self.fake.health)()                                 # frisches Ereignisprotokoll je Test
        self.fake.car.frame_history = type(self.fake.car.frame_history)(10)         # frischer Ringbuffer je Test
        self.fake.car.camera_data = None

    # ---- (a) fuenf Reiter
    def test_fuenf_reiter_ohne_recording_ids_und_callbacks(self):
        from test_dashboard_callbacks import layout_of
        components = list(layout_of(self.app).walk())
        tabs = [c for c in components if type(c).__name__ == 'Tab']
        self.assertEqual([c.props['label'] for c in tabs],
                         ['Driving Cockpit', 'Pipeline Studio', 'Telemetrie & Performance', 'Replay Lab', 'Settings'])
        self.assertEqual([t.tab_id for t in self.app.c2c_tabs], ['drive', 'pipeline', 'telemetry', 'replay', 'settings'])
        ids = {c.id for c in components if isinstance(c.id, str)}
        for gone in ('tick-record', 'record-open-replay', 'record-status', 'record-details', 'record-events',
                     'record-preview', 'session-stats', 'event-list'):
            self.assertNotIn(gone, ids)
        self.assertFalse([i for i in ids if i.startswith('record-') and i != 'record-toggle'])
        self.assertNotIn('recording_view', self.cb)
        self.assertFalse((ROOT / 'dashboard' / 'tabs' / 'recording.py').exists())
        source = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'dashboard').rglob('*.py'))
        self.assertNotIn('RecordingTab', source)
        self.assertNotIn('record-open-replay', source)
        self.assertNotIn("'tick-record'", source)

    def test_takte_je_reiter_und_tuple_laenge(self):
        self.assertEqual(self.cb['tab_intervals']('telemetry', False), (True, True, False, True))
        self.assertEqual(self.cb['tab_intervals']('replay', False), (True, True, True, False))
        self.assertEqual(self.cb['tab_intervals']('settings', False), (True,) * 4)
        self.assertEqual(self.cb['tab_intervals']('drive', True), (True,) * 4)
        self.assertEqual(len(self.cb['tab_intervals'].outputs), 4)

    def test_main_tabs_hat_genau_einen_besitzer(self):
        owners = [name for name, cb in self.cb.items()
                  if any(o.component_id == 'main-tabs' and o.component_property == 'active_tab' for o in cb.outputs)]
        self.assertEqual(owners, ['replay_to_studio'])
        seen = {}                                                                 # und keine doppelten Outputs ueberhaupt
        for name, cb in self.cb.items():
            for o in cb.outputs:
                key = (str(o.component_id), o.component_property)
                self.assertNotIn(key, seen, f'{key}: {name} und {seen.get(key)}')
                seen[key] = name

    def test_rec_chip_tooltip_und_cockpit_zeile(self):
        self.ctx.set('refresh', 'n_intervals')
        result = self.cb['global_status'](1, None)
        self.assertEqual(len(result), 19)
        tooltip = result[-1]
        for part in ('keine Aufnahme aktiv', 'Frames 7', 'Lücken 1', 'fehlende Entscheidungen 2', 'Fehler keiner'):
            self.assertIn(part, tooltip)
        cockpit = self.cb['cockpit_view'](1, 'drive', None, [])
        self.assertEqual(len(cockpit), 14)
        self.assertIn('Frames 7', cockpit[-1])
        self.assertTrue(cockpit[-1].startswith('keine Aufnahme aktiv'))

    # ---- (b) Ereignisprotokoll
    def test_cockpit_ereignisprotokoll_filter_und_links(self):
        for frame_id in range(50, 55):
            self.fake.publish(frame_id, with_decision=True)
        self.fake.health.record('LANE_LOST', 'Test', 52)
        self.fake.health.record('LANE_PARTIAL', 'laut', 53)
        self.fake.health.record('STOP_REASON', 'alt', 3)
        self.fake.health.record('RECORDING_STARTED', 'ohne Frame', -1)
        important = self.cb['drive_events'](1, 'important', 'drive')
        codes = [t for t in texts(important) if re.fullmatch(r'[A-Z_]+', t) and t not in ('Zeit',)]
        self.assertIn('LANE_LOST', codes)
        self.assertNotIn('LANE_PARTIAL', codes)
        self.assertLess(codes.index('RECORDING_STARTED'), codes.index('LANE_LOST'))   # neueste zuerst
        everything = self.cb['drive_events'](1, 'all', 'drive')
        self.assertIn('LANE_PARTIAL', texts(everything))
        links = [prop(c, 'href') for c in walk(everything) if type(c).__name__ == 'Link']
        self.assertEqual([h.rsplit('/', 1)[0] for h in links], ['#jump/__live__/53', '#jump/__live__/52'])   # neueste zuerst
        grey = [c for c in walk(everything) if prop(c, 'title') == 'Weder in einer Session noch im Ringbuffer']
        self.assertEqual([c.children for c in grey], ['3'])
        self.assertIs(self.cb['drive_events'](1, 'all', 'telemetry'), self.module.no_update)

    def test_frame_aus_laufender_session_ist_ein_link_auch_ausserhalb_des_ringbuffers(self):
        self.fake.recorder.path = '/x/Data_AI/sessions/session_live'
        self.fake.recorder.has_frame = lambda f: f == 3
        try:
            self.fake.health.record('STOP_REASON', 'alt', 3)
            table = self.cb['drive_events'](1, 'all', 'drive')
            links = [prop(c, 'href') for c in walk(table) if type(c).__name__ == 'Link' and '/3/' in prop(c, 'href')]
            self.assertTrue(links and links[0].startswith('#jump/session_live/3/'))
        finally:
            self.fake.recorder.path = None
            self.fake.recorder.has_frame = lambda f: False

    def test_replay_ereignisse_session_und_live(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                name = write_session(os.path.join(folder, 'Data_AI', 'sessions'), count=30)
                with open(os.path.join(folder, 'Data_AI', 'sessions', name, 'events.csv'), 'w') as fh:
                    fh.write('timestamp_ns,frame_id,code,detail\n'
                             '10000000000,20,LANE_LOST,0 Linien\n10100000000,21,LANES_DETECTED,2\n'
                             '10200000000,999,STOP_REASON,weg\n')
                self.ctx.set('session-choice', 'value')
                _max, _text, table = self.cb['replay_limits'](None, name, 1, 'replay', 'important')
                self.assertEqual(_max, 29)
                self.assertIn('LANE_LOST', texts(table))
                self.assertNotIn('LANES_DETECTED', texts(table))
                links = [prop(c, 'href') for c in walk(table) if type(c).__name__ == 'Link']
                self.assertEqual(len(links), 1)
                self.assertTrue(links[0].startswith(f'#jump/{name}/20/'))                # 999 liegt nicht in der Session
                self.assertIn('LANES_DETECTED', texts(self.cb['replay_limits'](None, name, 1, 'replay', 'all')[2]))
                self.ctx.set('tick-replay', 'n_intervals')
                self.assertIs(self.cb['replay_limits'](None, name, 2, 'replay', 'all')[2], self.module.no_update)
                self.assertTrue(all(v is self.module.no_update for v in self.cb['replay_limits'](None, name, 3, 'drive')))
                self.fake.health.record('LANE_LOST', 'live', 51)
                live = self.cb['replay_limits'](None, '__live__', 4, 'replay', 'important')[2]
                self.assertIn('live', texts(live))                                         # Live: Takt aktualisiert
                self.ctx.set('session-choice', 'value')
                broken = self.cb['replay_limits'](None, 'session_gibt_es_nicht', 5, 'replay', 'all')[2]
                self.assertIn('Session nicht lesbar', texts(broken))
                self.assertIn('Session nicht lesbar', texts(self.cb['replay_limits'](None, '../x', 6, 'replay', 'all')[2]))
            finally:
                os.chdir(cwd)

    def test_sprunglink_aus_dem_protokoll_oeffnet_replay_mit_vor_und_nachlauf(self):
        for frame_id in range(50, 55):
            self.fake.publish(frame_id, with_decision=True)
        self.fake.health.record('LANE_LOST', 'x', 52)
        table = self.cb['drive_events'](1, 'all', 'drive')
        href = next(prop(c, 'href') for c in walk(table) if type(c).__name__ == 'Link' and '/52/' in prop(c, 'href'))
        self.ctx.set('jump-url', 'hash')
        result = self.cb['replay_to_studio'](None, None, None, None, 0, href)
        self.assertEqual((result[0], result[4]), ('replay', '__live__'))
        index, bounds = self.cb['seek'](None, None, None, None, None, None, 1, 0, False, '__live__', None, 1, 1, 'replay',
                                        href)
        self.assertEqual((index, bounds), (1, [51, 53]))

    def test_neueste_session_oeffnen_ohne_laufende_aufnahme(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                for name in ('session_20261028_100000_000000', 'session_20261028_110000_000000'):
                    Session.make(os.path.join(folder, 'Data_AI', 'sessions'), name, [10])
                self.ctx.set('replay-open-latest')
                result = self.cb['replay_to_studio'](None, 1, None, None, 0, None, None, None)
                self.assertEqual((result[0], result[4], len(result)), ('replay', 'session_20261028_110000_000000', 6))
                self.assertTrue(all(v is self.module.no_update for i, v in enumerate(result) if i in (1, 2, 3, 5)))
            finally:
                os.chdir(cwd)
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                result = self.cb['replay_to_studio'](None, 1, None, None, 0, None, None, None)
                self.assertEqual(result[1], 'Keine gespeicherte Session vorhanden')
                self.assertIs(result[0], self.module.no_update)
            finally:
                os.chdir(cwd)

    def test_replay_lab_hat_den_neuen_button_und_keinen_alten(self):
        from test_dashboard_callbacks import layout_of
        components = list(layout_of(self.app).walk())
        ids = {c.id for c in components if isinstance(c.id, str)}
        self.assertTrue({'replay-open-latest', 'replay-events', 'replay-events-filter', 'drive-events',
                         'drive-events-filter', 'drive-to-studio', 'drive-studio-msg'} <= ids)
        self.assertNotIn('Letzte Session im Replay öffnen', ' '.join(str(c.children) for c in components
                                                                    if isinstance(c.children, str)))
        radios = [c for c in components if c.id in ('drive-events-filter', 'replay-events-filter')]
        for radio in radios:
            self.assertEqual([o['label'] for o in radio.props['options']], ['Wichtig', 'Alle'])
            self.assertEqual(radio.props['value'], 'important')

    # ---- (c) Auslastung
    def test_auslastung_in_kennzahlenleiste_und_cockpit(self):
        rate, lat, qual, load = self.cb['kpi_bar'](2)
        self.assertEqual(load[0].children, '42 %')
        self.assertIn('text-success', str(prop(load[0], 'className')))
        self.assertIn('RAM 61 %', load[1].children)
        self.assertIn('55 °C', load[2].children)
        self.assertIn('Arbeitsspeicher', prop(load[1], 'title'))                              # Tooltip je Wert (ein Satz)
        self.stats['cpu'] = 72.0
        self.assertIn('text-warning', str(prop(self.cb['kpi_bar'](2)[3][0], 'className')))
        self.stats['cpu'] = 90.0
        self.assertIn('text-danger', str(prop(self.cb['kpi_bar'](2)[3][0], 'className')))
        self.stats.update(cpu=10.0, temp=80.0)
        self.assertIn('text-danger', str(prop(self.cb['kpi_bar'](2)[3][0], 'className')))             # ab 80 Grad immer rot
        self.stats.update(cpu=None, ram=None, temp=None)
        load = self.cb['kpi_bar'](2)[3]
        self.assertEqual((load[0].children, load[1].children.strip(), load[2].children.strip('· ')),
                         ('–', 'RAM –', '–'))                                        # fehlend: Gedankenstrich, nie 0
        cockpit = self.cb['cockpit_view'](1, 'drive', None, [])
        self.assertEqual((cockpit[10].children, cockpit[11].children, cockpit[12].children), ('–', '–', '–'))
        self.stats.update(cpu=33.0, ram=44.0, temp=51.0)
        cockpit = self.cb['cockpit_view'](1, 'drive', None, [])
        self.assertEqual((cockpit[10].children, cockpit[11].children, cockpit[12].children), ('33 %', '44 %', '51 °C'))

    def test_jede_neue_anzeige_hat_einen_hilfetext_mit_einem_satz(self):
        from help_texts import HELP, tip
        for key in ('cpu', 'ram', 'temp', 'disk', 'diskest', 'decisions', 'telechart', 'eventlog'):
            title, origin, text = HELP[key]
            self.assertIn(origin, ('gemessen', 'berechnet', 'Befehlswert', 'Einstellung'), key)
            self.assertEqual(len(re.findall(r'[.!?](?:\s|$)', text)), 1, f'{key}: genau ein Satz erwartet: {text}')
            self.assertTrue(text.endswith('.'), key)
            self.assertIn(title, tip(key))
        self.assertIn('neuen Kameraframe', HELP['decisions'][2])
        self.assertIn('höchstens so hoch wie die Kamerarate', HELP['decisions'][2])
        from test_dashboard_callbacks import layout_of
        popovers = [c.props['target'] for c in layout_of(self.app).walk() if type(c).__name__ == 'Popover']
        self.assertEqual(len(popovers), len(set(popovers)))                          # IDs eindeutig (Suffix bei Mehrfachnutzung)
        for key in ('cpu', 'ram', 'temp', 'disk', 'diskest', 'decisions', 'telechart', 'eventlog'):
            self.assertTrue(any(p.startswith(f'info-{key}') for p in popovers), key)

    # ---- (d) Telemetrie
    def test_telemetrie_ein_diagramm_dritter_takt_und_speicherplatz(self):
        from test_dashboard_callbacks import layout_of
        components = list(layout_of(self.app).walk())
        graphs = [c.id for c in components if type(c).__name__ == 'Graph']
        self.assertEqual(sorted(g for g in graphs if g.startswith(('freq', 'drive-', 'telemetry'))), ['telemetry-chart'])
        details = [c for c in components if type(c).__name__ == 'Details']
        self.assertTrue(any('data-age' in [d.id for d in c.walk()] for c in details))   # Textblock einklappbar
        self.assertIsNone(next(c for c in components if c.id == 'telemetry-chart').props.get('figure', {}).get('layout',
                                                                                                    {}).get('height'))
        now = time.monotonic()
        rows = tuple((now - 3 + i * .5, 100 + i, 97, 30, 0, 2, 99) for i in range(6))
        freqs = tuple((now - 3 + i * .5, 25, None if i > 3 else 24) for i in range(6))
        self.fake.sample = lambda: (rows, freqs, ())
        type(self).latency = tuple((now - 3 + i * .5, 8.0, 3.0, 40.0) for i in range(6))
        type(self).system = tuple((now - 3 + i * .5, 30.0, 40.0, 50.0) for i in range(6))
        try:
            self.ctx.set('main-tabs', 'active_tab')
            first = self.cb['telemetry_view'](1, 'telemetry')
            self.assertEqual(len(first), 6)
            chart, _age, _sys, free, estimate, _preview = first
            self.assertIsInstance(chart, dict)
            names = [t['name'] for t in chart['data']]
            for needed in ('Kamera (Ist)', 'Datenalter', 'Reaktionszeit', 'Rohwinkel', 'CPU', 'RAM', 'Temperatur'):
                self.assertIn(needed, names)
            self.assertEqual(free, '20.5 GB (40 %)')
            self.assertIn('reicht für ca. 95 min Aufnahme (Schätzung', estimate)
            self.ctx.set('tick-telemetry', 'n_intervals')
            updates = [self.cb['telemetry_view'](tick, 'telemetry')[0] is not self.module.no_update
                       for tick in range(1, 10)]
            self.assertEqual(updates, [True, False, False, True, False, False, True, False, False])   # jeden 3. Takt
            self.stats['disk'] = None
            self.assertEqual(self.cb['telemetry_view'](2, 'telemetry')[3:5], ('–', '–'))
            self.stats['disk'] = {'free_gb': 5.0, 'free_pct': 10.0, 'total_gb': 50.0, 'estimate': None}
            self.assertIn('nicht schätzbar', self.cb['telemetry_view'](2, 'telemetry')[4])
            self.assertTrue(all(v is self.module.no_update for v in self.cb['telemetry_view'](1, 'drive')))
        finally:
            self.fake.sample = lambda: ((), (), ())
            type(self).latency = type(self).system = ()

    def test_telemetrie_ohne_messwerte_zeigt_leere_figur(self):
        self.fake.sample = lambda: ((), (), ())
        self.ctx.set('main-tabs', 'active_tab')
        chart = self.cb['telemetry_view'](1, 'telemetry')[0]
        self.assertEqual(chart['data'], [])
        self.assertTrue(chart['layout']['annotations'][0]['text'])
        self.assertNotIn('height', chart['layout'])

    # ---- (e) Cockpit -> Pipeline Studio
    def test_frame_aus_dem_cockpit_ins_pipeline_studio(self):
        from replay_studio import parameters_from_record
        camera = self.fake.publish(88, with_decision=False)
        camera.processing_parameters = parameters_from_record(PARAMS)
        camera.processor_name = 'CopilotLaneProcessor'
        self.ctx.set('drive-to-studio')
        result = self.cb['replay_to_studio'](None, None, None, None, 0, None, 1, 'cal:88')
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0], 'pipeline')                                       # wechselt auf den Pipeline-Reiter
        self.assertIn('Frame 88 aus dem Cockpit', result[2])
        self.assertEqual((result[3]['frame_id'], result[3]['source']), (88, 'Cockpit'))   # replay-studio-token
        self.assertEqual((result[1], result[4]), (self.module.no_update, self.module.no_update))
        self.assertEqual(result[5], '')                                               # Cockpit-Meldung geleert
        frozen = self.cb['freeze'](None, result[3], None)                              # gleicher Weg wie im Replay Lab
        self.assertTrue(frozen)
        result = self.cb['replay_to_studio'](None, None, None, None, 0, None, 1, 'drive:88:hud')   # Schluessel mit HUD
        self.assertEqual(result[0], 'pipeline')

    def test_frame_ohne_verarbeitungsparameter_meldet_im_cockpit(self):
        camera = self.fake.publish(89, with_decision=False)
        camera.processing_parameters = None
        self.ctx.set('drive-to-studio')
        result = self.cb['replay_to_studio'](None, None, None, None, 0, None, 1, 'cal:89')
        self.assertEqual(result[:5], (self.module.no_update,) * 5)
        self.assertIn('Übergabe nicht möglich', result[5])

    def test_cockpit_uebergabe_fehlermeldungen_im_cockpit(self):
        no = self.module.no_update
        self.ctx.set('drive-to-studio')
        for key in (None, '', 'cal:None', 'cal:-1', 'kaputt', 'drive:x'):
            result = self.cb['replay_to_studio'](None, None, None, None, 0, None, 1, key)
            self.assertEqual(result[:5], (no,) * 5, key)
            self.assertIn('Übergabe nicht möglich', result[5])
            self.assertIn('kein Frame', result[5])
        result = self.cb['replay_to_studio'](None, None, None, None, 0, None, 1, 'drive:123456')   # nicht mehr im Ringbuffer
        self.assertEqual(result[:5], (no,) * 5)
        self.assertIn('nicht mehr im Ringbuffer', result[5])
        self.fake.car.camera_data = None

    def test_parse_drive_key(self):
        from dashboard.tabs.replay import parse_drive_key
        self.assertEqual([parse_drive_key(k) for k in ('drive:5', 'cal:7:hud', 'drive:0')], [5, 7, 0])
        self.assertEqual([parse_drive_key(k) for k in (None, 3, 'x', 'cal:None', 'cal:-1', 'drive:')], [None] * 6)

    def test_bergung_und_fahrregelung_sind_nicht_beruehrt(self):
        calls_before = list(self.fake.calls)
        self.ctx.set('drive-to-studio')
        self.cb['replay_to_studio'](None, None, None, None, 0, None, 1, None)
        self.assertEqual(self.fake.calls, calls_before)                               # keine Anwendungsaufrufe (Hardware)
        self.assertEqual(self.fake.car.speed, 0)


# ------------------------------------------------------------------ echtes Dash ueber HTTP
@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashH2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        cls.pin = hardware_stubs.pin_pipeline_config()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = FakeApplication()
        cls.fake.system_stats = lambda: fake_stats()
        cls.fake.sample_extra = lambda: ((), ())
        from replay_studio import parameters_from_record
        for frame_id in range(20, 30):
            camera = cls.fake.publish(frame_id, with_decision=True)
            camera.processing_parameters = parameters_from_record(PARAMS)
            camera.processor_name = 'CopilotLaneProcessor'
        cls.fake.health.record('LANE_LOST', 'Test', 25)
        cls.client = module.create_app(cls.fake).server.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)

    def post(self, outputs, inputs, state, changed):
        output = ('..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..' if len(outputs) > 1
                  else f'{outputs[0][0]}.{outputs[0][1]}')
        specs = [{'id': i, 'property': p} for i, p in outputs]
        body = {'output': output, 'outputs': specs if len(specs) > 1 else specs[0],
                'inputs': [{'id': i, 'property': p, 'value': v} for i, p, v in inputs],
                'state': [{'id': i, 'property': p, 'value': v} for i, p, v in state], 'changedPropIds': changed}
        return self.client.post('/_dash-update-component', json=body)

    def test_abhaengigkeiten_keine_doppelten_outputs_und_ein_main_tabs_besitzer(self):
        deps = self.client.get('/_dash-dependencies').get_json()
        outputs = []
        for dep in deps:
            names = dep['output'].strip('.').split('...') if dep['output'].startswith('..') else [dep['output']]
            outputs.extend(names)
        self.assertEqual(len(outputs), len(set(outputs)), [o for o in outputs if outputs.count(o) > 1])
        owners = [d for d in deps if 'main-tabs.active_tab' in d['output']]
        self.assertEqual(len(owners), 1)
        self.assertFalse([d for d in deps if 'recording' in d['output'] or 'record-events' in d['output']
                          or 'session-stats' in d['output']])
        layout = json.dumps(self.client.get('/_dash-layout').get_json(), ensure_ascii=False)
        for gone in ('Recording & Events', 'record-open-replay', 'tick-record', 'Letzte Session im Replay'):
            self.assertNotIn(gone, layout)
        for present in ('Neueste Session öffnen', 'telemetry-chart', 'drive-to-studio', 'Ereignisprotokoll', '"Details"'):
            self.assertIn(present, layout)
        tab_ids = re.findall(r'"tab_id": "([a-z]+)"', layout)
        self.assertEqual(tab_ids, ['drive', 'pipeline', 'telemetry', 'replay', 'settings'])

    def test_telemetrie_ueber_http(self):
        outputs = [('telemetry-chart', 'figure'), ('data-age', 'children'), ('system-stats', 'children'),
                   ('telemetry-disk-free', 'children'), ('telemetry-disk-estimate', 'children'),
                   ('telemetry-preview', 'src')]
        now = time.monotonic()
        self.fake.sample = lambda: (tuple((now - 3 + i * .5, 100 + i, 97, 30, 0, 2, 99) for i in range(6)),
                                    tuple((now - 3 + i * .5, 25, 24) for i in range(6)), ())
        try:
            response = self.post(outputs, [('tick-telemetry', 'n_intervals', 1), ('main-tabs', 'active_tab', 'telemetry')],
                                 [], ['main-tabs.active_tab'])
        finally:
            self.fake.sample = lambda: ((), (), ())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        payload = response.get_json()['response']
        figure = payload['telemetry-chart']['figure']
        self.assertNotIn('height', figure['layout'])
        self.assertEqual(figure['layout']['uirevision'], 'telemetry')
        self.assertEqual(payload['telemetry-disk-free']['children'], '20.5 GB (40 %)')

    def test_kennzahlen_cockpit_und_status_ueber_http(self):
        response = self.post([('kpi-rate', 'children'), ('kpi-lat', 'children'), ('kpi-qual', 'children'),
                              ('kpi-load', 'children')], [('refresh', 'n_intervals', 2)], [], ['refresh.n_intervals'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        load = json.dumps(response.get_json()['response']['kpi-load']['children'], ensure_ascii=False)
        self.assertIn('42 %', load)
        self.assertIn('55 °C', load)
        outputs = [('global-camera-state', 'children'), ('global-camera-state', 'color'),
                   ('global-mode-state', 'children'), ('global-mode-state', 'color'),
                   ('global-rec-state', 'children'), ('global-rec-state', 'color'),
                   ('global-frame', 'children'), ('global-start', 'disabled'), ('global-stop', 'outline'),
                   ('recovery-toggle', 'children'), ('recovery-toggle', 'outline'),
                   ('recovery-panel', 'is_open'), ('recovery-pulse', 'disabled'),
                   ('record-toggle', 'children'), ('record-toggle', 'color'), ('record-toggle', 'outline'),
                   ('mode', 'disabled'), ('global-action-message', 'children'), ('global-rec-tooltip', 'children')]
        response = self.post(outputs, [('refresh', 'n_intervals', 1), ('last-action', 'data', None)], [],
                             ['refresh.n_intervals'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        self.assertIn('keine Aufnahme aktiv', response.get_data(as_text=True))

    def test_ereignisprotokoll_ueber_http(self):
        response = self.post([('drive-events', 'children')],
                             [('tick-drive', 'n_intervals', 1), ('drive-events-filter', 'value', 'important'),
                              ('main-tabs', 'active_tab', 'drive')], [], ['tick-drive.n_intervals'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        text = json.dumps(response.get_json(), ensure_ascii=False)
        self.assertIn('LANE_LOST', text)
        self.assertIn('#jump/__live__/25/', text)

    def test_replay_limits_mit_ereignissen_ueber_http(self):
        outputs = [('replay-index', 'max'), ('replay-range', 'children'), ('replay-events', 'children')]
        response = self.post(outputs, [('replay-bounds', 'data', None), ('session-choice', 'value', '__live__'),
                                       ('tick-replay', 'n_intervals', 1), ('replay-events-filter', 'value', 'all')],
                             [('main-tabs', 'active_tab', 'replay')], ['replay-events-filter.value'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        self.assertIn('LANE_LOST', response.get_data(as_text=True))

    def test_navigation_cockpit_zum_studio_ueber_http_signaturreihenfolge(self):
        outputs = [('main-tabs', 'active_tab'), ('replay-studio-msg', 'children'), ('ab-source', 'children'),
                   ('replay-studio-token', 'data'), ('session-choice', 'value'), ('drive-studio-msg', 'children')]
        inputs = [('replay-to-studio', 'n_clicks', None), ('replay-open-latest', 'n_clicks', None),
                  ('jump-url', 'hash', ''), ('drive-to-studio', 'n_clicks', 1)]
        state = [('session-choice', 'value', '__live__'), ('replay-bounds', 'data', None), ('replay-index', 'value', 0),
                 ('drive-rendered', 'data', 'cal:25')]
        response = self.post(outputs, inputs, state, ['drive-to-studio.n_clicks'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:600])
        payload = response.get_json()['response']
        self.assertEqual(payload['main-tabs']['active_tab'], 'pipeline')
        self.assertEqual(payload['replay-studio-token']['data']['source'], 'Cockpit')
        state[-1] = ('drive-rendered', 'data', None)                                    # kein Frame: Fehler im Cockpit
        response = self.post(outputs, inputs, state, ['drive-to-studio.n_clicks'])
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()['response']
        self.assertNotIn('main-tabs', payload)
        self.assertIn('Übergabe nicht möglich', payload['drive-studio-msg']['children'])
        inputs[3] = ('drive-to-studio', 'n_clicks', None)
        inputs[1] = ('replay-open-latest', 'n_clicks', 1)
        response = self.post(outputs, inputs, state, ['replay-open-latest.n_clicks'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])


# ------------------------------------------------------------------ Offline
class OfflineH2Tests(unittest.TestCase):
    def test_offline_ohne_messwerte(self):
        from offline_application import OfflineApplication
        tmp = tempfile.TemporaryDirectory()
        cwd = os.getcwd()
        os.chdir(tmp.name)
        try:
            app = OfflineApplication()
            stats = app.system_stats()
            self.assertEqual((stats['cpu'], stats['ram'], stats['temp'], stats['disk']), (None, None, None, None))
            self.assertEqual(app.sample_extra(), ((), ()))
        finally:
            os.chdir(cwd)
            tmp.cleanup()


# ------------------------------------------------------------------ Patchskript
class PatchScriptTests(unittest.TestCase):
    def test_marker_exakt_und_h1b_ist_voraussetzung(self):
        source = (ROOT / 'scripts' / 'paket_h2_patch.py').read_text(encoding='utf-8')
        self.assertIn("MARKER = 'PAKET-H2'", source)
        for name in ('car_application.py', 'help_texts.py', 'dashboard/shell.py', 'session_recorder.py'):
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('PAKET-H2', text, name)
            self.assertIn('PAKET-H1B', (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
