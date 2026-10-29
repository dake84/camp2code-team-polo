"""Paket H1: dauerhafte Einstellungen, Fahrzeugzustand, HUD, Diagramme, Simulation, Hilfetexte.

Ohne Hardware und ohne Motoransteuerung (Hardware-Attrappen, ReplayCar). Echtes Dash 4 wird ueber den
Flask-Testclient geprueft, sofern installiert; nicht getestet sind Browserbedienung und Fahrt am Auto.
"""
import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import dash_stub  # noqa: E402
import hardware_stubs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
NO_UPDATE = object()


def write_json(path, data):
    Path(path).write_text(json.dumps(data), encoding='utf-8')
    return str(path)


# ------------------------------------------------------------------ config_store
class QuietLogs:
    """Erwartete Warnungen (Rueckfall auf Standardwerte) nicht auf die Konsole schreiben."""

    def silence(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)


class ConfigStoreTests(QuietLogs, unittest.TestCase):
    def setUp(self):
        self.silence()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'config.json'
        self.backups = Path(self.tmp.name) / 'Data_AI' / 'config_backups'
        write_json(self.path, {'car': {'v_min': 20}, 'cameracar_controller': {'lost_line_timeout': 1}})
        import config_store
        self.cs = config_store

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomar_schreiben_und_andere_schluessel_erhalten(self):
        from speed_policy import SpeedPolicy
        self.cs.save_speed_policy(SpeedPolicy('adaptive', 30, 32, 50), self.path, self.backups)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['car'], {'v_min': 20})
        self.assertEqual(data['cameracar_controller'],
                         {'lost_line_timeout': 1, 'speed_mode': 'adaptive', 'speed_fixed': 30,
                          'speed_vmin': 32, 'speed_vmax': 50})
        self.assertEqual([p.name for p in self.path.parent.iterdir() if p.name.endswith('.tmp')], [])
        self.assertEqual(self.cs.load_speed_policy(self.path), SpeedPolicy('adaptive', 30, 32, 50))

    def test_sicherung_die_letzten_fuenf(self):
        for quality in (60, 65, 70, 75, 80, 85, 90, 95):
            self.cs.save_jpeg_quality(quality, self.path, self.backups)
            time.sleep(0.002)
        files = sorted(self.backups.glob('config_*.json'))
        self.assertEqual(len(files), 5)
        # Die Sicherung ist der Stand VOR der Aenderung: die letzte enthaelt Qualitaet 90.
        self.assertEqual(json.loads(files[-1].read_text())['session_recorder']['jpeg_quality'], 90)
        self.assertEqual(self.cs.load_jpeg_quality(self.path), 95)

    def test_legt_nie_eine_datei_neu_an(self):
        missing = Path(self.tmp.name) / 'fehlt.json'
        with self.assertRaises(self.cs.ConfigStoreError):
            self.cs.update({'car': {'x': 1}}, missing, self.backups)
        self.assertFalse(missing.exists())
        self.assertFalse(self.backups.exists())

    def test_defekte_datei_bleibt_unveraendert(self):
        for content in ('{kaputt', '[1, 2]', ''):
            self.path.write_text(content, encoding='utf-8')
            with self.assertRaises(self.cs.ConfigStoreError):
                self.cs.update({'car': {'x': 1}}, self.path, self.backups)
            self.assertEqual(self.path.read_text(encoding='utf-8'), content)
        self.assertFalse(self.backups.exists())

    def test_fehler_beim_ersetzen_laesst_original_und_raeumt_auf(self):
        before = self.path.read_bytes()
        with patch.object(self.cs.os, 'replace', side_effect=OSError('Platte voll')):
            with self.assertRaises(self.cs.ConfigStoreError):
                self.cs.save_jpeg_quality(80, self.path, self.backups)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir() if p.name.endswith('.tmp')], [])

    def test_laden_faellt_auf_standard_zurueck(self):
        from speed_policy import SpeedPolicy
        default = SpeedPolicy()
        self.assertEqual(self.cs.load_speed_policy(self.path), default)              # keine Schluessel
        self.assertEqual(self.cs.load_speed_policy(Path(self.tmp.name) / 'fehlt'), default)
        for bad in ({'speed_mode': 'turbo'}, {'speed_fixed': 20}, {'speed_vmax': 80}, {'speed_vmin': 30.5},
                    {'speed_fixed': True}, {'speed_mode': 'adaptive', 'speed_vmin': 50, 'speed_vmax': 40},
                    {'speed_fixed': '35'}):
            write_json(self.path, {'cameracar_controller': bad})
            self.assertEqual(self.cs.load_speed_policy(self.path), default, bad)
        self.path.write_text('{kaputt')
        self.assertEqual(self.cs.load_speed_policy(self.path), default)
        self.assertEqual(self.cs.load_jpeg_quality(self.path), 95)
        for bad in (10, 101, '90', 90.5, True, None):
            write_json(self.path, {'session_recorder': {'jpeg_quality': bad}})
            self.assertEqual(self.cs.load_jpeg_quality(self.path), 95, bad)
        with self.assertRaises(ValueError):
            self.cs.save_jpeg_quality(10, self.path, self.backups)


class StartupAndPersistenceTests(QuietLogs, unittest.TestCase):
    def setUp(self):
        self.silence()
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        self.stubs = None
        self.app = None

    def tearDown(self):
        if self.app is not None:
            self.app.shutdown()
        if self.stubs is not None:
            self.stubs.__exit__(None, None, None)
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def start(self, **sections):
        hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'), **sections)
        os.chdir(self.tmp.name)
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        from car_application import CarApplication
        self.app = CarApplication(start_camera=False)
        return self.app

    def test_gespeicherte_werte_werden_beim_start_geladen(self):
        app = self.start(cameracar_controller={'speed_mode': 'adaptive', 'speed_fixed': 30, 'speed_vmin': 31,
                                               'speed_vmax': 55}, session_recorder={'jpeg_quality': 85})
        self.assertEqual((app.speed_policy().mode, app.speed_policy().v_min, app.speed_policy().v_max),
                         ('adaptive', 31, 55))
        self.assertEqual(app.recorder.jpeg_quality, 85)

    def test_fehlerhafte_konfiguration_ergibt_fest_30(self):
        app = self.start(cameracar_controller={'speed_mode': 'turbo', 'speed_vmax': 99},
                         session_recorder={'jpeg_quality': 7})
        self.assertEqual((app.speed_policy().mode, app.speed_policy().fixed_speed), ('fixed', 30))
        self.assertEqual(app.recorder.jpeg_quality, 95)
        self.assertEqual(app.modes['camera'].SPEED_CONTROL_VALUE, 30)

    def test_aenderungen_werden_gespeichert(self):
        from speed_policy import SpeedPolicy
        app = self.start()
        self.assertTrue(app.set_speed_policy(SpeedPolicy('adaptive', 30, 30, 45)))
        self.assertTrue(app.set_jpeg_quality(80))
        data = json.loads(Path('config.json').read_text())
        self.assertEqual(data['cameracar_controller']['speed_mode'], 'adaptive')
        self.assertEqual(data['session_recorder']['jpeg_quality'], 80)
        self.assertEqual(len(list(Path('Data_AI/config_backups').glob('config_*.json'))), 2)

    def test_ohne_schreibbare_konfiguration_gilt_der_wert_bis_zum_neustart(self):
        from speed_policy import SpeedPolicy
        app = self.start()
        with patch('config_store.os.replace', side_effect=OSError('schreibgeschuetzt')):
            self.assertIs(app.set_speed_policy(SpeedPolicy('adaptive', 30, 30, 40)), False)
        self.assertEqual(app.speed_policy().mode, 'adaptive')
        self.assertIn('CONFIG_NOT_SAVED', [e.code for e in app.health.events()])

    def test_offline_anwendung_nutzt_dasselbe(self):
        write_json(Path(self.tmp.name) / 'config.json',
                   {'cameracar_controller': {'speed_mode': 'adaptive', 'speed_fixed': 30,
                                             'speed_vmin': 30, 'speed_vmax': 42}})
        os.chdir(self.tmp.name)
        from offline_application import OfflineApplication
        from speed_policy import SpeedPolicy
        app = OfflineApplication()
        self.assertEqual(app.speed_policy().v_max, 42)
        self.assertTrue(app.set_speed_policy(SpeedPolicy('fixed', 35, 30, 45)))
        self.assertEqual(json.loads(Path('config.json').read_text())['cameracar_controller']['speed_fixed'], 35)
        Path('config.json').unlink()
        self.assertIs(app.set_speed_policy(SpeedPolicy()), False)
        self.assertFalse(Path('config.json').exists())


# ------------------------------------------------------------------ Fahrzeugzustand
class VehicleModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                    cameracar_controller={'lost_line_timeout': 5})
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

    def test_zustaende_winkel_und_speed(self):
        self.assertEqual(self.app.vehicle_mode(), ('idle', 90, 0))
        self.app.car.drive(-30, 80)
        self.assertEqual(self.app.vehicle_mode(), ('idle', 80, -30))          # Rueckwaerts negativ
        self.app.car.stop()
        self.app.start_mode('camera')
        self.assertEqual(self.app.vehicle_mode()[0], 'auto')
        self.app.stop_all()
        self.app.enable_recovery()
        self.assertEqual(self.app.vehicle_mode()[0], 'recovery')
        self.app.disable_recovery()
        with patch.object(self.app, 'running', return_value=True):
            self.app.active_mode = 'gamepad'
            self.assertEqual(self.app.vehicle_mode()[0], 'manual')

    def test_nimmt_das_anwendungs_lock_nicht(self):
        """Ein STOP haelt das Lock bis zu 2 s; die Kameraschleife darf dadurch nie blockieren."""
        held, release, result = threading.Event(), threading.Event(), []

        def hold():
            with self.app._lock:
                held.set()
                release.wait(3)

        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(1)
        reader = threading.Thread(target=lambda: result.append(self.app.vehicle_mode()))
        reader.start()
        reader.join(1)
        finished = not reader.is_alive()
        release.set()
        holder.join()
        self.assertTrue(finished, 'vehicle_mode() blockiert am Anwendungs-Lock')

    def test_camera_sensor_setzt_felder_und_ignoriert_fehler(self):
        import ConfigReader
        from CameraSensor import CameraSensor
        from telemetry import FrameRingBuffer
        images = [hardware_stubs.test_image() for _ in range(4)]
        self.stubs.__exit__(None, None, None)
        with hardware_stubs.installed(frames=images):
            from CameraSensor import CameraSensor
            config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'sensor.json'))
            published = []

            class Car:
                frame_history = FrameRingBuffer()

                @property
                def camera_data(self):
                    return published[-1]

                @camera_data.setter
                def camera_data(self, value):
                    published.append(value)

            calls = []

            def provider():
                calls.append(1)
                if len(calls) == 1:
                    return 'recovery', 105, -30
                if len(calls) == 2:
                    raise RuntimeError('Provider kaputt')
                if len(calls) == 3:
                    return 'unbekannt', 1, 2
                return 'auto', 99, 30

            sensor = CameraSensor(Car(), ConfigReader.ConfigReader('cam_sensor', config), mode_provider=provider)
            with self.assertLogs('CameraSensor', level='WARNING') as logs:
                with self.assertRaises(RuntimeError):                 # Testbilder erschoepft
                    sensor.read_loop(threading.Event())
            self.assertEqual(sum('Fahrzeugzustand nicht lesbar' in line for line in logs.output), 1)   # nur einmal
            self.assertEqual(len(published), 4)
            self.assertEqual((published[0].vehicle_mode, published[0].vehicle_angle, published[0].vehicle_speed),
                             ('recovery', 105, -30))
            self.assertEqual((published[1].vehicle_mode, published[1].vehicle_angle), (None, None))   # Fehler
            self.assertIsNone(published[2].vehicle_mode)                                            # ungueltig
            self.assertEqual(published[3].vehicle_mode, 'auto')
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()

    def test_camera_data_standardwerte(self):
        from models import CameraData
        data = CameraData(process_image=np.empty(0), raw_image=np.empty(0))
        self.assertEqual((data.vehicle_mode, data.vehicle_angle, data.vehicle_speed), (None, None, None))


# ------------------------------------------------------------------ Recorder
class RecorderH1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, 'sessions')

    def tearDown(self):
        self.tmp.cleanup()

    def camera(self, frame_id, ns, **vehicle):
        from models import CameraData
        return CameraData(process_image=np.zeros((4, 4), np.uint8), raw_image=np.zeros((240, 320, 3), np.uint8),
                          frame_id=frame_id, timestamp=time.time_ns(), monotonic_ns=ns, crop=(0.25, 0.65),
                          lanes=((-1.0, 150.0), (1.5, -300.0)), **vehicle)

    def record(self, cameras):
        from session_recorder import SessionRecorder
        from telemetry import FrameRingBuffer
        history = FrameRingBuffer(50)
        for camera in cameras:
            history.add_camera(camera)
        recorder = SessionRecorder(SimpleNamespace(frame_history=history), self.root, is_driving=lambda: False)
        recorder.start()
        recorder._stop.set()
        recorder._thread.join(2)
        recorder.capture_once()
        recorder._thread = SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None)
        recorder.stop()
        return recorder

    def test_spalten_manifest_und_median_fps(self):
        from session_recorder import FIELDS, load_manifest, load_session
        base = time.monotonic_ns()
        cameras = [self.camera(i, base + i * 70_000_000, vehicle_mode='recovery', vehicle_angle=105,
                               vehicle_speed=-30) for i in range(1, 13)]
        cameras.insert(5, self.camera(99, base + 5 * 70_000_000 + 5_000_000))      # Ausreisser stoert den Median nicht
        recorder = self.record(sorted(cameras, key=lambda c: c.monotonic_ns))
        self.assertEqual(FIELDS[-4:], ('vehicle_mode', 'vehicle_angle', 'vehicle_speed', 'vehicle_mode_key'))
        _, rows = load_session(recorder.path.name, self.root)
        first = rows[0]
        self.assertEqual((first['vehicle_mode'], first['vehicle_angle'], first['vehicle_speed']),
                         ('recovery', '105', '-30'))
        manifest = load_manifest(recorder.path.name, self.root)
        self.assertEqual(manifest['schema_version'], 2)
        self.assertEqual(manifest['schema_revision'], 3)
        self.assertIn('vehicle_mode', manifest['frames_fields'])
        fps = manifest['capture']['median_fps']
        self.assertAlmostEqual(fps, 1e9 / 70_000_000, delta=1.5)
        self.assertIn('Median', manifest['capture']['fps_definition'])

    def test_ohne_zeitstempel_keine_fps_erfunden(self):
        from session_recorder import load_manifest
        recorder = self.record([self.camera(1, 0)])
        self.assertIsNone(load_manifest(recorder.path.name, self.root)['capture']['median_fps'])

    def test_alte_session_bleibt_lesbar(self):
        from replay_hud import draw_hud, vehicle_state
        old = {'frame_id': '5', 'image_filename': 'frames/x.jpg', 'data_status': 'no_driving_state',
               'steering_angle': '', 'roi_top_px': '86', 'roi_bottom_px': '182', 'lanes_json': '[]'}
        self.assertEqual(vehicle_state(old), ('KEIN FAHRMODUS', 'none'))
        self.assertEqual(draw_hud(np.zeros((240, 320, 3), np.uint8), old).shape, (240, 320, 3))


# ------------------------------------------------------------------ HUD
class HudTests(unittest.TestCase):
    ROW = {'frame_id': '7', 'roi_top_px': '86', 'roi_bottom_px': '182', 'lanes_json': '[]'}

    def row(self, **kwargs):
        return dict(self.ROW, **kwargs)

    def texts(self, row, **kwargs):
        import replay_hud
        seen = []
        with patch.object(replay_hud, 'put_text', side_effect=lambda image, text, *a, **k: seen.append(text)):
            replay_hud.draw_hud(np.zeros((240, 320, 3), np.uint8), row, **kwargs)
        return seen

    def test_rangfolge_der_zustaende(self):
        from replay_hud import vehicle_state
        events = [{'frame_id': '7', 'code': 'STOP_REASON', 'detail': 'LANE_TIMEOUT'}]
        cases = [
            (self.row(event_codes='STOP_REASON', data_status='complete', vehicle_mode='recovery'),
             events, 'GESTOPPT: LANE_TIMEOUT'),
            (self.row(event_codes='CONTROLLER_STOPPED', data_status='complete'), None, 'GESTOPPT: UNBEKANNT'),
            (self.row(vehicle_mode='recovery', data_status='complete'), None, 'BERGUNG'),
            (self.row(vehicle_mode='manual', data_status='no_driving_state'), None, 'MANUELL'),
            (self.row(vehicle_mode='idle', data_status='complete'), None, 'AUTONOM'),
            (self.row(vehicle_mode='auto', data_status='missing_decision'), None, 'AUTONOM / ENTSCHEIDUNG FEHLT'),
            (self.row(vehicle_mode='auto', data_status='no_driving_state'), None, 'AUTONOM / ENTSCHEIDUNG FEHLT'),
            (self.row(data_status='missing_decision'), None, 'AUTONOM / ENTSCHEIDUNG FEHLT'),
            (self.row(vehicle_mode='idle', data_status='no_driving_state'), None, 'STEHT'),
            (self.row(data_status='no_driving_state'), None, 'KEIN FAHRMODUS'),
            (self.row(), None, 'KEIN FAHRMODUS'),
            (self.row(event_codes='STOP_REASON', stop_reason='Benutzer-STOP'), None, 'GESTOPPT: Benutzer-STOP'),
        ]
        for row, ev, expected in cases:
            self.assertEqual(vehicle_state(row, ev)[0], expected, row)
        other = [{'frame_id': '8', 'code': 'STOP_REASON', 'detail': 'X'}]      # Ereignis eines anderen Frames
        self.assertEqual(vehicle_state(self.row(data_status='complete'), other)[0], 'AUTONOM')

    def test_kein_na_keine_erfundene_null_nur_ascii(self):
        for row in (self.row(), self.row(data_status='complete', steering_angle='', requested_angle='',
                                         steering_angle_car='', speed=''),
                    self.row(vehicle_mode='recovery', vehicle_angle='105', vehicle_speed='-30'),
                    self.row(event_codes='STOP_REASON', stop_reason='Ursache nicht veröffentlicht – Ärger'),
                    self.row(vehicle_mode='idle', event_codes='LANE_LOST')):
            seen = self.texts(row, footer='JETZT (Simulation) · Größe')
            joined = ' '.join(seen)
            self.assertNotIn('N/A', joined)
            self.assertTrue(all(t.isascii() for t in seen), seen)
        seen = self.texts(self.row())
        self.assertFalse(any(t.startswith(('STELL', 'ROH', 'STEUER')) for t in seen), seen)   # nichts erfunden
        self.assertIn('Ursache nicht veroeffentlicht - Aerger',
                      ' '.join(self.texts(self.row(event_codes='STOP_REASON',
                                                   stop_reason='Ursache nicht veröffentlicht – Ärger'))).replace(
                          'GESTOPPT: ', ''))

    def test_roh_stell_und_soll_nur_bei_abweichung(self):
        from replay_hud import angle_line
        self.assertEqual(angle_line(99, 99, 97), 'ROH 97 > STELL 99')
        self.assertEqual(angle_line(99, 99.4, 97), 'ROH 97 > STELL 99')            # unter 1 Grad: ausgeblendet
        self.assertEqual(angle_line(99, 100, 97), 'ROH 97 > STELL 99  SOLL 100')   # genau 1 Grad: sichtbar
        self.assertEqual(angle_line(99, 98, None), 'STELL 99  SOLL 98')
        self.assertEqual(angle_line(None, 90, 97), 'ROH 97')                       # Stellwert fehlt: kein SOLL
        self.assertEqual(angle_line(None, None, None), '')

    def test_speed_vorzeichenbehaftet_und_fahrzeugzustand_bei_bergung(self):
        seen = self.texts(self.row(vehicle_mode='recovery', vehicle_angle='105', vehicle_speed='-30',
                                   data_status='no_driving_state'))
        self.assertIn('STEUER-SPEED -30', seen)
        self.assertIn('STELL 105', seen)
        self.assertIn('BERGUNG', seen)
        self.assertIn('STEUER-SPEED +30', self.texts(self.row(speed='30', data_status='complete')))
        self.assertIn('STEUER-SPEED 0', self.texts(self.row(vehicle_mode='idle', vehicle_speed='0')))

    def test_kennzeichnung_unten_ueberlagert_das_hud_nicht(self):
        from replay_hud import draw_hud
        raw = np.full((240, 320, 3), 120, np.uint8)
        row = self.row(data_status='complete', vehicle_mode='auto', steering_angle_car='90', speed='30',
                       event_codes='LANE_LOST')
        plain = draw_hud(raw, row)
        marked = draw_hud(raw, row, footer='JETZT (Simulation) - COPILOT')
        self.assertFalse(raw.tolist() != np.full((240, 320, 3), 120, np.uint8).tolist())     # Rohbild unveraendert
        np.testing.assert_array_equal(plain[:80], marked[:80])                  # Kopfzeilen identisch
        self.assertTrue(np.abs(marked[236, 315].astype(int) - (40, 28, 12)).sum() < 6)      # Fussleiste unten
        self.assertFalse(np.abs(plain[236, 315].astype(int) - (40, 28, 12)).sum() < 6)

    def test_row_from_camera_und_legende(self):
        from models import CameraData
        from replay_hud import LEGEND, legend_block, row_from_camera, vehicle_state
        camera = CameraData(process_image=np.empty(0), raw_image=np.zeros((240, 320, 3), np.uint8), frame_id=9,
                            crop=(0.15, 0.55), lanes=((1.0, 2.0),), vehicle_mode='manual', vehicle_angle=60,
                            vehicle_speed=40)
        events = [{'frame_id': 9, 'code': 'LANE_LOST', 'detail': ''}, {'frame_id': 9, 'code': 'LANE_LOST', 'detail': ''}]
        row = row_from_camera(camera, None, events=events)
        self.assertEqual(row['event_codes'], 'LANE_LOST')
        self.assertEqual(vehicle_state(row)[0], 'MANUELL')
        html = dash_stub.modules()['dash.html']
        legend = legend_block(html)
        self.assertEqual(type(legend).__name__, 'Details')
        cells = [c.children for c in legend.walk() if type(c).__name__ == 'Td']
        for field, meaning, values in LEGEND:
            self.assertIn(field, cells)
        for needed in ('GESTOPPT: <Ursache>', 'BERGUNG / MANUELL', 'AUTONOM', 'AUTONOM / ENTSCHEIDUNG FEHLT',
                       'STEHT', 'KEIN FAHRMODUS', 'STEUER-SPEED', 'ROH', 'STELL', 'SOLL'):
            self.assertIn(needed, cells)


# ------------------------------------------------------------------ Diagramme
class ChartKitTests(unittest.TestCase):
    def test_leere_figur_dunkel_mit_hinweis_ohne_hoehe(self):
        import chart_kit
        figure = chart_kit.empty_figure('Noch keine Daten')
        layout = figure['layout']
        self.assertNotIn('height', layout)
        self.assertNotIn('width', layout)
        self.assertEqual(layout['plot_bgcolor'], chart_kit.BG)
        self.assertEqual(layout['annotations'][0]['text'], 'Noch keine Daten')
        self.assertEqual(figure['data'], [])

    def test_konfiguration_interaktiv(self):
        import chart_kit
        config = chart_kit.CHART_CONFIG
        self.assertTrue(config['scrollZoom'])
        self.assertTrue(config['displayModeBar'])
        self.assertEqual(config['doubleClick'], 'reset+autosize')
        self.assertEqual(config['toImageButtonOptions']['format'], 'png')
        for kept in ('zoom2d', 'pan2d', 'resetScale2d', 'toImage'):
            self.assertNotIn(kept, config['modeBarButtonsToRemove'])

    def test_css_ohne_padding_am_plotly_element(self):
        css = (ROOT / 'assets' / 'studio.css').read_text(encoding='utf-8')
        for rule in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
            selector, body = rule
            if re.search(r'plotly|plot-container|svg-container|c2c-graph|c2c-chart', selector):
                self.assertNotIn('padding', body, selector)
        self.assertIn('.c2c-chart-box:fullscreen', css)


class DashboardChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
        cls.app = cls.module.create_app(cls.fake)
        cls.cb = cls.app.callbacks

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def test_jedes_diagramm_hat_feste_hoehe_und_vollbild_geschwister(self):
        from test_dashboard_callbacks import layout_of
        components = list(layout_of(self.app).walk())
        graphs = [c for c in components if type(c).__name__ == 'Graph']
        ids = {g.id for g in graphs}
        self.assertEqual(ids, {'telemetry-chart', 'replay-chart', 'sim-chart'})  # PAKET-H2
        boxes = {c.id: c for c in components if isinstance(c.id, str) and c.id.endswith('-box')}
        for graph in graphs:
            props = graph.props
            self.assertRegex(props['style']['height'], r'^\d+px$')
            self.assertTrue(props['responsive'])
            self.assertTrue(props['config']['scrollZoom'])
            layout = props['figure']['layout']
            self.assertNotIn('height', layout)                                 # keine layout.height
            self.assertTrue(props['figure']['layout']['annotations'][0]['text'])  # Hinweistext
            box = boxes[f'{graph.id}-box']
            bar, inner = box.children
            self.assertIs(inner, graph)
            buttons = [c for c in bar.walk() if type(c).__name__ == 'Button']
            self.assertEqual(buttons[0].props['data-target'], box.id)           # Geschwister, nicht im Graph
            self.assertNotIn('padding', json.dumps(props['style']))

    def figures_without_height(self, *figures):
        for figure in figures:
            layout = figure['layout'] if isinstance(figure, dict) else figure.layout
            self.assertNotIn('height', layout)

    def test_telemetrie_leer_und_mit_daten(self):
        """PAKET-H2: EIN gekoppeltes Diagramm statt drei; leer = dunkle Figur mit Hinweis, ohne layout.height."""
        self.fake.sample = lambda: ((), (), ())
        self.ctx.set('main-tabs', 'active_tab')
        chart = self.cb['telemetry_view'](1, 'telemetry')[0]
        self.figures_without_height(chart)
        self.assertEqual(chart['data'], [])
        self.assertTrue(chart['layout']['annotations'][0]['text'])
        now = time.monotonic()
        rows = tuple((now - 3 + i * .5, 100 + i, 97, 30, 0, 2, 99) for i in range(6))
        freqs = tuple((now - 3 + i * .5, 25, 24) for i in range(6))
        self.fake.sample = lambda: (rows, freqs, ())
        chart = self.cb['telemetry_view'](1, 'telemetry')[0]
        self.figures_without_height(chart)
        names = [trace['name'] for trace in chart['data']]
        for needed in ('Kamera (Ist)', 'Entscheidungen (Ist)', 'Kamera (Soll)', 'Rohwinkel', 'Stellwert',
                       'Speed-Steuerwert', 'Linienanzahl'):
            self.assertIn(needed, names)
        self.assertEqual(chart['layout']['uirevision'], 'telemetry')            # Zoom/Legende ueberleben Updates
        self.fake.sample = lambda: ((), (), ())
    def test_replay_diagramm_uirevision_und_leere_figur(self):
        for frame_id in range(30, 34):
            self.fake.publish(frame_id, with_decision=True)
        self.ctx.set('session-choice', 'value')
        figure = self.cb['replay_chart']('__live__', None, 1, 'replay')
        self.assertNotIn('height', figure.layout)
        self.assertEqual(len(figure.data), 2)                                    # Stellwert, Linien (PAKET-H1B)
        self.assertEqual(figure.layout['uirevision'], '__live__')                # Legendenzustand je Quelle
        bounded = self.cb['replay_chart']('__live__', [30, 32], 1, 'replay')
        self.assertEqual(bounded.layout['xaxis']['uirevision'], '__live__|[30, 32]')   # Achsen je Bereich
        self.assertEqual(bounded.layout['uirevision'], figure.layout['uirevision'])
        self.assertTrue(bounded.layout['yaxis']['fixedrange'])                    # Mausrad: nur X (Bereichsauswahl)
        old = list(self.fake.car.frame_history._items)
        self.fake.car.frame_history._items.clear()
        empty = self.cb['replay_chart']('__live__', None, 0, 'replay')
        self.assertEqual(empty['data'], [])
        self.assertNotIn('height', empty['layout'])
        self.fake.car.frame_history._items.extend(old)

    def test_bereichsauswahl_im_replay_bleibt_erhalten(self):
        for frame_id in range(20, 26):
            self.fake.publish(frame_id, with_decision=frame_id % 2 == 0)
        self.ctx.set('replay-chart', 'relayoutData')
        index, bounds = self.cb['seek'](None, None, None, {'xaxis.range[0]': 21, 'xaxis.range[1]': 23},
                                        None, None, 1, 2, False, '__live__', None, 30, 30, 'replay')
        self.assertEqual(bounds, [21.0, 23.0])
        index, bounds = self.cb['seek'](None, None, None, {'xaxis.autorange': True, 'yaxis.autorange': True},
                                        None, None, 1, 2, False, '__live__', [21, 23], 30, 30, 'replay')
        self.assertIsNone(bounds)                                                # Doppelklick = alles
        index, bounds = self.cb['seek'](None, None, None, {'autosize': True},
                                        None, None, 1, 2, False, '__live__', [21, 23], 30, 30, 'replay')
        self.assertIs(bounds, self.module.no_update)                             # Groessenaenderung ignoriert

    def test_replay_hud_auch_fuer_ringbuffer(self):
        self.fake.publish(60, with_decision=True)
        self.ctx.set('replay-index', 'value')
        image, label, details = self.cb['replay_render'](0, '__live__', None, 1, 'replay')
        self.assertTrue(image.startswith('data:image/jpeg'))

    def test_settings_zeigen_aktive_werte(self):
        from speed_policy import SpeedPolicy
        no = self.module.no_update
        self.assertEqual(self.cb['settings_fill']('drive'), (no,) * 6)
        self.assertEqual(self.cb['settings_fill']('settings'), (no,) * 6)         # Attrappe ohne Strategie
        self.fake.speed_policy = lambda: SpeedPolicy('adaptive', 30, 31, 52)
        self.fake.recorder.jpeg_quality = 80
        try:
            result = self.cb['settings_fill']('settings')
            self.assertEqual(result[:5], ('adaptive', 30, 31, 52, 80))
            self.assertIn(80, [o['value'] for o in result[5]])                    # nicht-Standardwert bleibt waehlbar
            self.fake.recorder.jpeg_quality = 95
            self.assertEqual(len(self.cb['settings_fill']('settings')[5]), 4)
        finally:
            del self.fake.speed_policy
            del self.fake.recorder.jpeg_quality

    def test_settings_rueckmeldung_zur_speicherung(self):
        results = iter((True, False, None))
        self.fake.set_speed_policy = lambda policy: next(results)
        try:
            texts = []
            for _ in range(3):
                messages = self.cb['settings_apply'](1, 'fixed', 30, 30, 45, 95)
                texts.append(' '.join(str(c.children) for m in messages for c in m.walk()
                                      if isinstance(c.children, str)))
            self.assertIn('dauerhaft gespeichert', texts[0])
            self.assertIn('nur bis zum Neustart', texts[1])
            self.assertNotIn('gespeichert', texts[2].split('Aufnahme')[0])
        finally:
            del self.fake.set_speed_policy


@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = FakeApplication()
        cls.pin = hardware_stubs.pin_pipeline_config()
        cls.app = module.create_app(cls.fake)
        cls.client = cls.app.server.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)

    def walk(self, node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from self.walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from self.walk(value)

    def test_layout_graphs_mit_hoehe_und_ohne_layout_hoehe(self):
        layout = self.client.get('/_dash-layout').get_json()
        graphs = [n for n in self.walk(layout) if n.get('type') == 'Graph']
        self.assertEqual({g['props']['id'] for g in graphs},
                         {'telemetry-chart', 'replay-chart', 'sim-chart'})  # PAKET-H2
        for graph in graphs:
            props = graph['props']
            self.assertRegex(props['style']['height'], r'^\d+px$')
            self.assertTrue(props['responsive'])
            self.assertTrue(props['config']['scrollZoom'])
            self.assertNotIn('height', props['figure']['layout'])
            self.assertNotEqual(props['figure']['layout']['plot_bgcolor'], None)
            self.assertNotIn('padding', json.dumps(props['style']))
        boxes = {n['props']['id'] for n in self.walk(layout) if n.get('type') == 'Div'
                 and isinstance(n.get('props', {}).get('id'), str)}
        self.assertTrue({'telemetry-chart-box', 'replay-chart-box', 'sim-chart-box'} <= boxes)

    def post(self, outputs, inputs, state, changed):
        output = ('..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..' if len(outputs) > 1
                  else f'{outputs[0][0]}.{outputs[0][1]}')
        specs = [{'id': i, 'property': p} for i, p in outputs]
        body = {'output': output, 'outputs': specs if len(specs) > 1 else specs[0],
                'inputs': [{'id': i, 'property': p, 'value': v} for i, p, v in inputs],
                'state': [{'id': i, 'property': p, 'value': v} for i, p, v in state], 'changedPropIds': changed}
        return self.client.post('/_dash-update-component', json=body)

    def test_replay_diagramm_ueber_http_ohne_hoehe(self):
        for frame_id in range(70, 74):
            self.fake.publish(frame_id, with_decision=True)
        response = self.post([('replay-chart', 'figure')],
                             [('session-choice', 'value', '__live__'), ('replay-bounds', 'data', [70, 72]),
                              ('replay-index', 'value', 1)], [('main-tabs', 'active_tab', 'replay')],
                             ['replay-bounds.data'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        figure = response.get_json()['response']['replay-chart']['figure']
        self.assertNotIn('height', figure['layout'])
        self.assertEqual(figure['layout']['xaxis']['uirevision'], '__live__|[70, 72]')
        self.assertEqual(len(figure['data']), 2)  # PAKET-H1B: Frame-Balken ist ein DOM-Element

    def test_telemetrie_und_settings_ueber_http(self):
        outputs = [('telemetry-chart', 'figure'), ('data-age', 'children'), ('system-stats', 'children'),
                   ('telemetry-disk-free', 'children'), ('telemetry-disk-estimate', 'children'),
                   ('telemetry-preview', 'src')]  # PAKET-H2
        response = self.post(outputs, [('tick-telemetry', 'n_intervals', 1), ('main-tabs', 'active_tab', 'telemetry')],
                             [], ['main-tabs.active_tab'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertNotIn('height', response.get_json()['response']['telemetry-chart']['figure']['layout'])
        outputs = [('set-speed-mode', 'value'), ('set-speed-fixed', 'value'), ('set-speed-vmin', 'value'),
                   ('set-speed-vmax', 'value'), ('set-rec-quality', 'value'), ('set-rec-quality', 'options')]
        response = self.post(outputs, [('main-tabs', 'active_tab', 'settings')], [], ['main-tabs.active_tab'])
        self.assertIn(response.status_code, (200, 204), response.get_data(as_text=True)[:400])


# ------------------------------------------------------------------ Simulation
def make_frame(index, ns, **kwargs):
    from sim_engine import SimFrame
    values = dict(index=index, frame_id=100 + index, warmup=False, crop=(0.15, 0.55), lanes=(), lane_count=0,
                  plausible=False, raw_angle=None, requested_angle=None, car_angle=None, speed=None, held=False,
                  stop_reason=None, process_ms=1.0, then_raw_angle=None, then_car_angle=None, then_speed=None,
                  then_lane_count=None, then_plausible=None, then_status=None, captured_ns=ns)
    values.update(kwargs)
    return SimFrame(**values)


def make_lab(stamps_ns, warmup=0):
    from sim_engine import SimResult
    from sim_lab import SimLab
    ui = SimpleNamespace(no_update=NO_UPDATE)
    lab = SimLab(SimpleNamespace(running=lambda: False), ui, lambda image: 'data:x')
    result = SimResult('Test', 'copilot', 'recorded',
                       frames=[make_frame(i, ns, warmup=i < warmup) for i, ns in enumerate(stamps_ns)])
    lab._results[7] = (result, SimpleNamespace(median_fps=14.3, fps_source='Manifest'))
    return lab, 7, result


FRAME_NS = 70_000_000      # 14,3 Hz wie bei den Aufnahmen


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.lab, self.token, self.result = make_lab([i * FRAME_NS for i in range(60)])
        self.keep = NO_UPDATE

    def tick(self, index, now, speed=1.0, timing='realtime'):
        return self.lab.playback('sim-play-tick', self.token, speed, timing, index, False, now=now)

    def test_play_erst_nach_erfolgreicher_berechnung(self):
        self.assertEqual(self.lab.playback('sim-play', None, 1.0, 'realtime', 0, True, now=1.0), (0, True, '▶ Play'))
        self.assertEqual(self.lab.playback('sim-play', 999, 1.0, 'realtime', 5, True, now=1.0), (0, True, '▶ Play'))
        out = self.lab.playback('sim-play', self.token, 1.0, 'realtime', 0, True, now=1.0)
        self.assertEqual(out, (0, False, '⏸ Pause'))

    def test_neues_ergebnis_pausiert_und_springt_zum_ersten_messframe(self):
        lab, token, _ = make_lab([i * FRAME_NS for i in range(20)], warmup=5)
        self.assertEqual(lab.playback('sim-result-token', token, 1.0, 'realtime', 9, False), (5, True, '▶ Play'))

    def test_echtzeit_haelt_das_tempo_nach_zeitstempeln_und_ueberspringt(self):
        self.lab.playback('sim-play', self.token, 1.0, 'realtime', 0, True, now=10.0)
        index, _, _ = self.tick(0, 10.15)
        self.assertEqual(index, 2)                    # 150 ms Medienzeit -> Frame bei 140 ms
        self.assertEqual(self.lab._skipped, 1)
        index, _, _ = self.tick(2, 10.30)
        self.assertEqual(index, 4)                    # 300 ms -> Frame bei 280 ms
        index, _, _ = self.tick(4, 10.45)
        self.assertEqual(index, 6)

    def test_tempo_bezieht_sich_auf_zeitstempel_nicht_auf_feste_fps(self):
        for speed, expected in ((0.25, 0), (0.5, 1), (1.0, 2), (2.0, 4), (4.0, 8)):
            lab, token, _ = make_lab([i * FRAME_NS for i in range(60)])
            lab.playback('sim-play', token, speed, 'realtime', 0, True, now=0.0)
            index, _, _ = lab.playback('sim-play-tick', token, speed, 'realtime', 0, False, now=0.15)
            self.assertEqual(index if index is not NO_UPDATE else 0, expected, speed)
        irregular = [0, 40_000_000, 150_000_000, 160_000_000, 400_000_000, 410_000_000]      # schwankend
        lab, token, _ = make_lab(irregular)
        lab.playback('sim-play', token, 1.0, 'realtime', 0, True, now=0.0)
        self.assertEqual(lab.playback('sim-play-tick', token, 1.0, 'realtime', 0, False, now=0.2)[0], 3)

    def test_jeder_frame_ueberspringt_nie_und_laeuft_langsamer(self):
        self.lab.playback('sim-play', self.token, 1.0, 'every', 0, True, now=10.0)
        index, now, seen = 0, 10.0, [0]
        for _ in range(10):
            now += 0.15                                # Anzeigetakt 150 ms, Frames kommen alle 70 ms
            new_index, _, _ = self.tick(index, now, 1.0, 'every')
            index = index if new_index is NO_UPDATE else new_index
            seen.append(index)
        self.assertEqual(seen, list(range(11)))        # genau ein Frame je Tick, keiner uebersprungen
        self.assertEqual(self.lab._skipped, 0)
        # 4x: Anzeige kann nicht schneller als ein Frame je Tick -> weiter kein Ueberspringen
        lab, token, _ = make_lab([i * FRAME_NS for i in range(60)])
        lab.playback('sim-play', token, 4.0, 'every', 0, True, now=0.0)
        index = 0
        for step in range(1, 6):
            new_index, _, _ = lab.playback('sim-play-tick', token, 4.0, 'every', index, False, now=step * 0.15)
            index = index if new_index is NO_UPDATE else new_index
        self.assertEqual(index, 5)

    def test_jeder_frame_langsames_tempo_wartet_auf_den_zeitstempel(self):
        self.lab.playback('sim-play', self.token, 0.25, 'every', 0, True, now=0.0)
        self.assertIs(self.tick(0, 0.15, 0.25, 'every')[0], NO_UPDATE)           # 37,5 ms < 70 ms
        self.assertEqual(self.tick(0, 0.30, 0.25, 'every')[0], 1)                # 75 ms >= 70 ms

    def test_am_ende_automatisch_pausieren_und_neustart(self):
        lab, token, _ = make_lab([i * FRAME_NS for i in range(6)])
        lab.playback('sim-play', token, 4.0, 'realtime', 0, True, now=0.0)
        index, paused, label = lab.playback('sim-play-tick', token, 4.0, 'realtime', 0, False, now=1.0)
        self.assertEqual((index, paused, label), (5, True, '▶ Play'))
        self.assertIsNone(lab._play)
        self.assertEqual(lab.playback('sim-play', token, 4.0, 'realtime', 5, True, now=2.0), (0, False, '⏸ Pause'))

    def test_tempo_und_modus_aendern_ohne_neuberechnung(self):
        self.lab.playback('sim-play', self.token, 1.0, 'realtime', 0, True, now=0.0)
        self.assertEqual(self.lab.playback('sim-speed', self.token, 4.0, 'realtime', 0, False, now=0.0),
                         (NO_UPDATE,) * 3)
        index, _, _ = self.lab.playback('sim-play-tick', self.token, 4.0, 'realtime', 0, False, now=0.15)
        self.assertEqual(index, 8)                                                # neuer Anker, 4x
        self.assertIn(7, self.lab._results)                                       # Ergebnis bleibt erhalten
        self.assertEqual(self.lab.valid_speed('abc'), 1.0)
        self.assertEqual(self.lab.valid_speed(3), 1.0)
        self.assertEqual(self.lab.valid_speed(0.5), 0.5)

    def test_sprung_des_nutzers_verankert_die_uhr_neu(self):
        self.lab.playback('sim-play', self.token, 1.0, 'realtime', 0, True, now=0.0)
        index, _, _ = self.lab.playback('sim-play-tick', self.token, 1.0, 'realtime', 30, False, now=5.0)
        self.assertIs(index, NO_UPDATE)                                           # Slider auf 30: neuer Anker
        index, _, _ = self.lab.playback('sim-play-tick', self.token, 1.0, 'realtime', 30, False, now=5.15)
        self.assertEqual(index, 32)

    def test_anzeigerate_gemessen_und_effektives_tempo(self):
        source = self.lab._results[7][1]
        self.assertIn('wird gemessen', self.lab.rate_text(source))
        for i in range(7):                                                        # 1 Frame je 150 ms, 70 ms Medienzeit
            self.lab.note_render(self.result.frames[i], now=100 + i * 0.15)
        text = self.lab.rate_text(source)
        self.assertIn('6.7 Frames/s', text)
        self.assertIn('effektives Tempo 0.47x', text)
        self.assertIn('Median 14.3 Hz (Manifest)', text)
        self.assertIn('Aufnahme: Median 14.3 Hz', self.lab.baseline_text(source))


class SimulationIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_paket_f import write_session
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                                  cameracar_controller={'lost_line_timeout': 1})
        os.chdir(self.tmp.name)
        self.root = os.path.join(self.tmp.name, 'Data_AI', 'sessions')
        self.name = write_session(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_engine_liefert_aufgezeichnete_zeitstempel_und_median_fps(self):
        import ConfigReader
        from frame_source import SessionSource
        from sim_engine import SimEngine
        source = SessionSource(self.name, self.root)
        self.assertAlmostEqual(source.median_fps, 25.0, places=3)                 # 40 ms Abstand
        self.assertEqual(source.fps_source, 'berechnet aus Zeitstempeln')
        engine = SimEngine(ConfigReader.ConfigReader('cameracar_controller', self.config), v_min=20, v_max=100)
        result = engine.run(source, 'copilot', lo=10, hi=14, warmup=2)
        self.assertEqual([f.captured_ns for f in result.frames], [10_000_000_000 + i * 40_000_000 for i in range(8, 15)])
        manifest = Path(self.root) / self.name / 'manifest.json'
        write_json(manifest, {'capture': {'median_fps': 14.29}})
        self.assertEqual(SessionSource(self.name, self.root).fps_source, 'Manifest')
        self.assertAlmostEqual(SessionSource(self.name, self.root).median_fps, 14.29)

    def test_render_frame_hud_damals_und_jetzt_mit_kennzeichnung_unten(self):
        import ConfigReader
        from frame_source import SessionSource
        from sim_engine import SimEngine
        from sim_lab import SimLab
        source = SessionSource(self.name, self.root)
        engine = SimEngine(ConfigReader.ConfigReader('cameracar_controller', self.config), v_min=20, v_max=100)
        result = engine.run(source, 'copilot', lo=40, hi=48, warmup=0)
        lab = SimLab(SimpleNamespace(running=lambda: False), SimpleNamespace(no_update=NO_UPDATE), lambda i: 'x',
                     self.root)
        stop = next((i for i, f in enumerate(result.frames) if f.stop_reason), None)
        position = stop if stop is not None else 0
        then, now = lab.render_frame(result, source, position)
        for image in (then, now):
            self.assertTrue(np.abs(image[236, 315].astype(int) - (40, 28, 12)).sum() < 6)   # Fussleiste in beiden
        self.assertEqual(then.shape, now.shape)
        calls = []
        import replay_hud
        with patch.object(replay_hud, 'put_text', side_effect=lambda image, text, *a, **k: calls.append(text)):
            lab.render_frame(result, source, position)
        self.assertTrue(any(t.startswith('JETZT (Simulation) - COPILOT') for t in calls), calls)
        self.assertTrue(any(t.startswith('DAMALS') for t in calls), calls)
        self.assertTrue(all(t.isascii() for t in calls), calls)
        header = [t for t in calls if t.startswith('JETZT')]
        self.assertEqual(len(header), 1)

    def test_aenderung_der_einstellungen_verwirft_das_ergebnis(self):
        from sim_lab import SimLab
        import dash_stub
        from dash_stub import Dash, Input, Output, State, ctx, no_update
        fake = SimpleNamespace(running=lambda: False, car=SimpleNamespace(frame_history=None), offline=True)
        modules = dash_stub.modules()
        ui = SimpleNamespace(dbc=modules['dash_bootstrap_components'], dcc=modules['dash.dcc'],
                             html=modules['dash.html'], Input=Input, Output=Output, State=State, ctx=ctx,
                             no_update=no_update)
        lab = SimLab(fake, ui, lambda image: 'data:x', self.root)
        app = Dash(__name__)
        lab.register(app)
        cb = app.callbacks
        self.assertEqual(set(cb), set(SimLab.CALLBACKS))
        ctx.set('sim-run')
        status, stats, maximum, chart, token = cb['sim_run'](1, self.name, 'copilot', 'recorded', 40, 44, 2)
        self.assertTrue(token)
        self.assertIsNotNone(lab.lookup(token))
        self.assertGreater(maximum, 1)
        ctx.set('sim-result-token', 'data')
        self.assertEqual(cb['sim_playback'](None, None, None, None, None, token, 1.0, 'realtime', 0, True)[2], '▶ Play')
        ctx.set('sim-play')
        index, paused, label = cb['sim_playback'](1, None, 1, None, None, token, 1.0, 'realtime', 2, True)
        self.assertFalse(paused)                                                  # Play mit Ergebnis moeglich
        self.assertEqual(label, '⏸ Pause')
        for trigger in ('sim-session', 'sim-processor', 'sim-params', 'sim-from', 'sim-to', 'sim-warmup'):
            ctx.set('sim-run')
            token = cb['sim_run'](1, self.name, 'copilot', 'recorded', 40, 44, 2)[4]
            ctx.set(trigger, 'value')
            status, stats, maximum, chart, new_token = cb['sim_run'](1, self.name, 'mock', 'studio', 1, 9, 3)
            self.assertEqual(status, 'Einstellungen geändert - bitte neu berechnen', trigger)
            self.assertIsNone(new_token)
            self.assertEqual(chart['data'], [])
            self.assertIn('neu berechnen', chart['layout']['annotations'][0]['text'])
            self.assertIsNone(lab.lookup(token))                                  # altes Ergebnis verworfen
            ctx.set('sim-result-token', 'data')
            self.assertEqual(cb['sim_playback'](None, None, None, None, None, None, 1.0, 'realtime', 5, False),
                             (0, True, '▶ Play'))                                 # pausiert, kein Play ohne Ergebnis
            self.assertEqual(cb['sim_render'](5, None, True), (None, None, 'Noch keine Simulation berechnet', '', ''))
        ctx.set('sim-speed', 'value')                                             # Tempo: keine Neuberechnung
        ctx.set('sim-run')
        token = cb['sim_run'](1, self.name, 'copilot', 'recorded', 40, 44, 2)[4]
        ctx.set('sim-speed', 'value')
        cb['sim_playback'](None, None, None, None, None, token, 2.0, 'realtime', 0, True)
        self.assertIsNotNone(lab.lookup(token))


@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashSimulationTests(unittest.TestCase):
    def test_invalidierung_und_wiedergabe_ueber_http(self):
        from test_paket_f import write_session
        import dash_bootstrap_components as dbc
        from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
        from sim_lab import SimLab
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                root = os.path.join(folder, 'Data_AI', 'sessions')
                name = write_session(root)
                hardware_stubs.write_config(os.path.join(folder, 'config.json'))
                fake = SimpleNamespace(running=lambda: False, car=SimpleNamespace(frame_history=None), offline=True)
                app = Dash(__name__)
                app.layout = html.Div([SimLab.wrap_cockpit(html.Div('live'), dbc, dcc, html)])
                sim = SimLab(fake, SimpleNamespace(dbc=dbc, dcc=dcc, html=html, Input=Input, Output=Output,
                                                   State=State, ctx=ctx, no_update=no_update),
                             lambda image: 'data:x', root)
                sim.register(app)
                client = app.server.test_client()
                deps = client.get('/_dash-dependencies').get_json()
                self.assertEqual(len(deps), len(SimLab.CALLBACKS))
                outputs = [('sim-status', 'children'), ('sim-stats', 'children'), ('sim-index', 'max'),
                           ('sim-chart', 'figure'), ('sim-result-token', 'data')]
                names = ('sim-run', 'sim-session', 'sim-processor', 'sim-params', 'sim-from', 'sim-to', 'sim-warmup')
                values = (1, name, 'copilot', 'recorded', 40, 44, 2)

                def post(changed):
                    body = {'output': '..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..',
                            'outputs': [{'id': i, 'property': p} for i, p in outputs],
                            'inputs': [{'id': n, 'property': 'n_clicks' if n == 'sim-run' else 'value', 'value': v}
                                       for n, v in zip(names, values)],
                            'state': [], 'changedPropIds': [changed]}
                    return client.post('/_dash-update-component', json=body)

                response = post('sim-run.n_clicks')
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
                token = response.get_json()['response']['sim-result-token']['data']
                self.assertTrue(token)
                self.assertNotIn('height', response.get_json()['response']['sim-chart']['figure']['layout'])
                response = post('sim-params.value')
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
                payload = response.get_json()['response']
                self.assertEqual(payload['sim-status']['children'], 'Einstellungen geändert - bitte neu berechnen')
                self.assertIsNone(payload['sim-result-token']['data'])
                self.assertIsNone(sim.lookup(token))
                layout = client.get('/_dash-layout').get_json()
                interval = next(n for n in self._walk(layout) if n.get('props', {}).get('id') == 'sim-play-tick')
                self.assertEqual(interval['props']['interval'], 150)
            finally:
                os.chdir(cwd)

    def _walk(self, node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from self._walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from self._walk(value)


# ------------------------------------------------------------------ Hilfetexte
class HelpTextTests(unittest.TestCase):
    def test_jeder_text_genau_ein_satz_mit_herkunft(self):
        from help_texts import HELP, ORIGINS
        self.assertGreaterEqual(len(HELP), 28)
        for key, (title, origin, text) in HELP.items():
            self.assertIn(origin, ORIGINS, key)
            self.assertTrue(text.endswith('.'), key)
            self.assertIsNone(re.search(r'[.!?]\s+\S', text), f'{key}: mehr als ein Satz? {text}')
            self.assertNotIn('\n', text)
            self.assertTrue(title.strip(), key)

    def test_beispiel_reaktionszeit_und_herkunftsmarkierung(self):
        from help_texts import HELP
        title, origin, text = HELP['lat']
        self.assertEqual(origin, 'gemessen')
        for part in ('Bildverarbeitung', 'Regler-Abtastung', '100 Hz = bis 10 ms', 'CPU-Last'):
            self.assertIn(part, text)
        self.assertEqual(HELP['speed'][1], 'Befehlswert')
        self.assertEqual(HELP['stell'][1], 'Befehlswert')
        self.assertEqual(HELP['roh'][1], 'berechnet')
        self.assertEqual(HELP['crop'][1], 'Einstellung')

    def test_info_suffix_fuer_mehrfachverwendung(self):
        from help_texts import info, label
        modules = dash_stub.modules()
        dbc, html = modules['dash_bootstrap_components'], modules['dash.html']
        plain = info('hud', dbc, html)
        replay = info('hud', dbc, html, suffix='replay')
        self.assertEqual(plain[0].id, 'info-hud')
        self.assertEqual(replay[0].id, 'info-hud-replay')
        self.assertEqual(replay[1].props['target'], 'info-hud-replay')
        self.assertEqual(label('X', 'lat', dbc, html, suffix='a').children[2].id, 'info-lat-a')


class StaticFileSearchTests(unittest.TestCase):
    def test_verzeichnis_pruning_liefert_dieselben_dateien_wie_rglob(self):
        import test_static_checks as checks
        skip = {'.venv', 'venv', 'legacy', 'camp2code', '__pycache__', '.git'}
        old = sorted(p for p in checks.ROOT.rglob('*.py') if not skip & set(p.relative_to(checks.ROOT).parts))
        new = sorted(p for p in checks._py_files() if 'Data_AI' not in p.relative_to(checks.ROOT).parts)
        self.assertEqual(new, old)


if __name__ == '__main__':
    unittest.main()

