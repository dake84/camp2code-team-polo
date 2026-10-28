"""Paket H1b: Kamera-/Reglereinstellungen und Frame-Balken in den Diagrammen.

Ohne Hardware und ohne Motoransteuerung (Hardware-Attrappen). Echtes Dash 4 wird ueber den Flask-Testclient
geprueft, sofern installiert. Ein Browsertest (Playwright mit Headless-Chromium) laeuft nur, wenn die Umgebungsvariable
C2C_CHROMIUM auf eine Browser-Datei zeigt; er ist auf dem Pi nicht vorgesehen und wird dort uebersprungen.
Nicht getestet: Bedienung im Browser des Nutzers, Fahrt am Auto, Performance auf dem Pi.
"""
import importlib.util
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import dash_stub  # noqa: E402
import hardware_stubs  # noqa: E402
from models import CameraData  # noqa: E402
from test_paket_f import write_session  # noqa: E402  (vor jeder Dash-Attrappe importieren)
from test_paket_h1 import FRAME_NS, make_lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
TWO_LANES = ((-1.19, 166.5), (1.585, -317.1))
KEYS = ('cam_scan', 'ctl_scan', 'lost_timeout', 'steer_gain', 'v_min', 'v_max')
DEFAULTS = {'cam_scan': 25, 'ctl_scan': 100, 'lost_timeout': 1.0, 'steer_gain': 2.0, 'v_min': 20, 'v_max': 100}


def write_json(path, data):
    Path(path).write_text(json.dumps(data), encoding='utf-8')
    return str(path)


class Quiet:
    """Erwartete Warnungen (Rueckfall auf Standardwerte) nicht auf die Konsole schreiben."""

    def silence(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)


# ------------------------------------------------------------------ ConfigReader
class ConfigReaderTests(Quiet, unittest.TestCase):
    def setUp(self):
        self.silence()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = write_json(Path(self.tmp.name) / 'config.json',
                               {'car': {'v_min': 20, 'v_max': 'abc', 'x': None, 'f': [1], 'ok': '7', 'g': 2.5}})
        import ConfigReader
        self.reader = ConfigReader.ConfigReader('car', self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ungueltige_werte_ergeben_den_standardwert(self):
        self.assertEqual(self.reader.get_int('v_max', 100), 100)          # Text
        self.assertEqual(self.reader.get_int('x', 5), 5)                  # null
        self.assertEqual(self.reader.get_float('f', 1.5), 1.5)            # Liste
        self.assertEqual(self.reader.get_int('fehlt', 7), 7)

    def test_gueltige_werte_bleiben_unveraendert(self):
        self.assertEqual(self.reader.get_int('v_min', 1), 20)
        self.assertEqual(self.reader.get_int('ok', 1), 7)                 # numerischer Text wie bisher
        self.assertEqual(self.reader.get_float('g', 1.0), 2.5)
        self.assertEqual(self.reader.get_int('g', 1), 2)

    def test_set_value_legt_namespace_an_und_speichert_nicht(self):
        import ConfigReader
        other = ConfigReader.ConfigReader('neu', self.path)
        self.assertEqual(other.get_int('a', 3), 3)
        other.set_value('a', 9)
        self.assertEqual(other.get_int('a', 3), 9)
        self.assertEqual(json.loads(Path(self.path).read_text()).get('neu'), None)   # Datei unberuehrt
        self.reader.set_value('v_min', 33)
        self.assertEqual(self.reader.get_int('v_min', 1), 33)


# ------------------------------------------------------------------ runtime_settings (rein)
class RuntimeSettingsTests(Quiet, unittest.TestCase):
    def setUp(self):
        self.silence()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'config.json'
        self.backups = Path(self.tmp.name) / 'Data_AI' / 'config_backups'
        write_json(self.path, {'car': {'turning_offset': 3, 'v_min': 20}, 'cameracar_controller': {'speed_mode': 'fixed'}})
        import runtime_settings
        self.rs = runtime_settings

    def tearDown(self):
        self.tmp.cleanup()

    def good(self, **change):
        return {**DEFAULTS, **change}

    def test_standardwerte_sind_die_des_codes(self):
        self.assertEqual(self.rs.defaults(), DEFAULTS)
        with hardware_stubs.installed():
            from driving.camera_mode import CameraMode
            self.assertEqual(self.rs.BY_KEY['ctl_scan'].default, CameraMode.DEFAULT_POLL_HZ)
        self.assertEqual(self.rs.validate(DEFAULTS), DEFAULTS)

    def test_jede_einstellung_hat_einen_hilfetext_und_einen_bereich(self):
        from help_texts import HELP
        for setting in self.rs.SETTINGS:
            self.assertIn(setting.help_key, HELP, setting.key)
            self.assertLess(setting.minimum, setting.maximum)
            self.assertTrue(setting.minimum <= setting.default <= setting.maximum, setting.key)
        self.assertEqual({s.key for s in self.rs.SETTINGS if s.behavior}, {'lost_timeout', 'steer_gain', 'v_min', 'v_max'})

    def test_hardware_kalibrierung_ist_nicht_enthalten(self):
        names = {s.name for s in self.rs.SETTINGS}
        for forbidden in ('turning_offset', 'forward_A', 'forward_B', 'width', 'height', 'colormode'):
            self.assertNotIn(forbidden, names)

    def test_validierung(self):
        for key, bad in (('cam_scan', 4), ('cam_scan', 201), ('cam_scan', 25.5), ('cam_scan', True), ('cam_scan', '25'),
                         ('cam_scan', None), ('cam_scan', float('nan')), ('cam_scan', float('inf')),
                         ('ctl_scan', 9), ('lost_timeout', 0.2), ('lost_timeout', 6), ('steer_gain', 0.4),
                         ('steer_gain', 4.1), ('v_min', 9), ('v_min', 61), ('v_max', 29), ('v_max', 101)):
            with self.assertRaises(ValueError, msg=(key, bad)):
                self.rs.validate(self.good(**{key: bad}))
        with self.assertRaises(ValueError) as caught:
            self.rs.validate(self.good(v_min=50, v_max=40))
        self.assertIn('Minimaler', str(caught.exception))
        with self.assertRaises(ValueError):
            self.rs.validate({'cam_scan': 25})                                  # Schluessel fehlen
        ok = self.rs.validate(self.good(cam_scan=30.0, steer_gain=1.2345, v_min=40, v_max=40))
        self.assertEqual((ok['cam_scan'], ok['steer_gain'], ok['v_min'], ok['v_max']), (30, 1.234, 40, 40))
        self.assertIsInstance(ok['cam_scan'], int)
        with self.assertRaises(ValueError) as all_errors:                       # alle Meldungen zugleich
            self.rs.validate(self.good(cam_scan=1, ctl_scan=1))
        self.assertIn('Kamera-Abtastung', str(all_errors.exception))
        self.assertIn('Regler-Abtastung', str(all_errors.exception))

    def test_speichern_atomar_mit_sicherung_und_ohne_fremde_schluessel_zu_verlieren(self):
        self.rs.save(self.good(cam_scan=30, steer_gain=1.5, v_min=25, v_max=70), self.path, self.backups)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['cam_sensor'], {'scan_frequency': 30})
        self.assertEqual(data['cameracar_controller'], {'speed_mode': 'fixed', 'scan_frequency': 100,
                                                        'lost_line_timeout': 1.0, 'steering_gain': 1.5})
        self.assertEqual(data['car'], {'turning_offset': 3, 'v_min': 25, 'v_max': 70})
        self.assertEqual(len(list(self.backups.glob('config_*.json'))), 1)
        self.assertEqual([p.name for p in self.path.parent.iterdir() if p.name.endswith('.tmp')], [])

    def test_speichern_nur_nach_erfolgreichem_lesen_und_nie_ungueltig(self):
        import config_store
        missing = Path(self.tmp.name) / 'fehlt.json'
        with self.assertRaises(config_store.ConfigStoreError):
            self.rs.save(DEFAULTS, missing, self.backups)
        self.assertFalse(missing.exists())
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.rs.save(self.good(cam_scan=1), self.path, self.backups)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.backups.exists())

    def test_gespeicherte_werte_lesen_und_ruckfall(self):
        self.assertTrue(all(e['state'] == 'absent' for k, e in self.rs.stored_values(self.path).items()
                            if k != 'v_min'))
        self.assertEqual(self.rs.stored_values(self.path)['v_min'], {'value': 20, 'state': 'ok', 'raw': 20})
        write_json(self.path, {'cam_sensor': {'scan_frequency': 'abc'},
                               'cameracar_controller': {'steering_gain': -5, 'lost_line_timeout': 0.5,
                                                        'scan_frequency': 25.5},
                               'car': {'v_min': 50, 'v_max': 40}})
        stored = self.rs.stored_values(self.path)
        self.assertEqual({k: stored[k]['state'] for k in KEYS},
                         {'cam_scan': 'invalid', 'ctl_scan': 'invalid', 'lost_timeout': 'ok', 'steer_gain': 'invalid',
                          'v_min': 'invalid', 'v_max': 'invalid'})                # v_min > v_max: beide ungueltig
        startup = self.rs.startup_values(self.path)
        self.assertEqual(startup, {'cam_scan': 25, 'ctl_scan': 100, 'lost_timeout': 0.5, 'steer_gain': 2.0,
                                   'v_min': 20, 'v_max': 100})
        self.path.write_text('{kaputt')
        self.assertEqual(self.rs.startup_values(self.path), {})                  # nichts anwenden, nie scheitern
        self.assertEqual(self.rs.stored_values(Path(self.tmp.name) / 'fehlt')['cam_scan']['state'], 'absent')

    def test_statuszeilen(self):
        setting = self.rs.BY_KEY['steer_gain']
        text = lambda active, stored, state='ok': self.rs.describe(  # noqa: E731
            setting, {'active': active, 'stored': stored, 'state': state})
        self.assertEqual(text(2.0, 2.0), ('aktiv 2 · gespeichert', 'ok'))
        self.assertEqual(text(2.0, None, 'absent'), ('aktiv 2 · nicht gespeichert (Standard 2)', 'ok'))
        self.assertEqual(text(1.5, 2.5), ('aktiv 1.5 · gespeichert 2.5 (gilt ab nächstem Start)', 'warn'))
        self.assertEqual(text(2.0, None, 'invalid'), ('aktiv 2 · gespeicherter Wert ungültig (Standard)', 'warn'))
        self.assertEqual(text(None, 1.5)[1], 'off')
        self.assertIn('gespeichert 1.5', text(None, 1.5)[0])
        self.assertEqual(self.rs.describe(self.rs.BY_KEY['cam_scan'], {'active': 25, 'stored': 25, 'state': 'ok'})[0],
                         'aktiv 25 Hz · gespeichert')
        self.assertEqual(self.rs.number_text(None), '–')


# ------------------------------------------------------------------ CarApplication
class ApplicationRuntimeTests(Quiet, unittest.TestCase):
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

    def start(self, raw=None, **sections):
        """Anwendung starten; ``raw`` = komplette config.json, sonst die Test-Konfiguration (cam 200 Hz, v_max 80)."""
        if raw is not None:
            write_json(os.path.join(self.tmp.name, 'config.json'), raw)
        else:
            hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'), **sections)
        os.chdir(self.tmp.name)
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        from car_application import CarApplication
        self.app = CarApplication(start_camera=False)
        return self.app

    def config(self):
        return json.loads(Path(self.tmp.name, 'config.json').read_text())

    def values(self, **change):
        return {**DEFAULTS, **change}

    def publish(self, frame_id, lanes=TWO_LANES):
        self.app.car.camera_data = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.empty(0),
                                              frame_id=frame_id, monotonic_ns=time.monotonic_ns(), lanes=lanes)

    def test_standardwerte_bleiben_ohne_konfiguration_und_fahrregelung_unveraendert(self):
        app = self.start(raw={'car': {}, 'cam_sensor': {}, 'cameracar_controller': {}})
        snap = app.runtime_settings()
        self.assertEqual({k: snap[k]['active'] for k in KEYS}, DEFAULTS)
        self.assertTrue(all(snap[k]['state'] == 'absent' for k in KEYS))
        self.assertEqual(app.modes['camera'].SPEED_CONTROL_VALUE, 30)
        self.assertEqual((app.speed_policy().mode, app.speed_policy().fixed_speed), ('fixed', 30))
        self.assertEqual(app.settings_revision, 0)

    def test_uebernehmen_live_und_dauerhaft(self):
        app = self.start()
        result = app.set_runtime_settings(self.values(cam_scan=30, ctl_scan=50, lost_timeout=0.5, steer_gain=1.5,
                                                      v_min=25, v_max=70))
        self.assertTrue(result['saved'])
        self.assertEqual(result['applied'], {k: True for k in KEYS})
        self.assertEqual(app.sensor.scan_frequency, 30)
        mode = app.modes['camera']
        self.assertEqual((mode.poll_frequency, mode._lost_timeout), (50, 0.5))
        self.assertEqual((app.car.v_min, app.car.v_max), (25, 70))
        snap = app.runtime_settings()
        self.assertEqual({k: (snap[k]['active'], snap[k]['stored']) for k in KEYS},
                         {'cam_scan': (30, 30), 'ctl_scan': (50, 50), 'lost_timeout': (0.5, 0.5),
                          'steer_gain': (1.5, 1.5), 'v_min': (25, 25), 'v_max': (70, 70)})
        data = self.config()
        self.assertEqual(data['cam_sensor']['scan_frequency'], 30)
        self.assertEqual(data['cameracar_controller']['steering_gain'], 1.5)
        self.assertEqual((data['car']['v_min'], data['car']['v_max']), (25, 70))
        self.assertEqual(data['car']['turning_offset'], 0)                       # Kalibrierung unberuehrt
        self.assertEqual(app.settings_revision, 1)
        codes = [e.code for e in app.health.events()]
        self.assertIn('CONFIG_SAVED', codes)
        self.assertEqual(app.modes['camera'].SPEED_CONTROL_VALUE, 30)           # Speed-Steuerwert bleibt 30

    def test_lenkverstaerkung_wirkt_auf_die_entscheidung(self):
        app = self.start()
        mode = app.modes['camera']
        mode._pre_run()
        angles = {}
        for frame_id, gain in ((1, 2.0), (2, 1.0)):
            app.set_runtime_settings(self.values(steer_gain=gain))
            self.publish(frame_id)
            self.assertTrue(mode._run())
            angles[gain] = (app.car.driving_state.steering_angle, app.car.driving_state.requested_angle)
        raw = angles[2.0][0]
        self.assertEqual(angles[2.0][1], int(max(45, min(135, 90 + (raw - 90) * 2.0))))
        self.assertEqual(angles[1.0][1], int(max(45, min(135, 90 + (raw - 90) * 1.0))))
        self.assertNotEqual(angles[2.0][1], angles[1.0][1])

    def test_linien_timeout_wirkt_auf_den_regler(self):
        app = self.start()
        app.set_runtime_settings(self.values(lost_timeout=0.3))
        mode = app.modes['camera']
        now = [5_000_000_000]
        mode._clock = lambda: now[0]
        mode._pre_run()
        self.publish(1, lanes=((1.0, 2.0),))                                       # nur eine Linie: Vorwert halten
        self.assertTrue(mode._run())
        now[0] += int(0.2e9)
        self.assertTrue(mode._run())
        now[0] += int(0.2e9)                                                       # 0,4 s > 0,3 s
        self.assertFalse(mode._run())
        self.assertEqual(mode.last_stop_reason, 'LANE_TIMEOUT')

    def test_steuerwert_grenzen_wirken_auf_fahrbefehle(self):
        app = self.start()
        app.set_runtime_settings(self.values(v_min=35, v_max=45))
        app.car.drive(10, 90)
        self.assertEqual(app.car.speed, 35)
        app.car.drive(90, 90)
        self.assertEqual(app.car.speed, 45)
        app.car.stop()

    def test_nur_im_stillstand_und_ungueltig_aendert_nichts(self):
        app = self.start()
        app.start_mode('camera')
        with self.assertRaises(RuntimeError):
            app.set_runtime_settings(self.values(steer_gain=1.0))
        app.stop_all()
        app.enable_recovery()
        with self.assertRaises(RuntimeError):
            app.set_runtime_settings(self.values(steer_gain=1.0))
        app.disable_recovery()
        before = Path('config.json').read_bytes()
        for bad in (self.values(cam_scan=1), self.values(v_min=60, v_max=40), {'cam_scan': 25}):
            with self.assertRaises(ValueError):
                app.set_runtime_settings(bad)
        self.assertEqual(Path('config.json').read_bytes(), before)
        snap = app.runtime_settings()
        self.assertEqual({k: snap[k]['active'] for k in KEYS}, {**DEFAULTS, 'cam_scan': 200, 'v_max': 80})   # Test-Konfiguration

        self.assertEqual(app.settings_revision, 0)

    def test_gespeicherte_werte_beim_start_und_fehlerhafte_ergeben_standard(self):
        app = self.start(cam_sensor={'scan_frequency': 20},
                         cameracar_controller={'scan_frequency': 40, 'lost_line_timeout': 0.6, 'steering_gain': 1.4},
                         car={'v_min': 30, 'v_max': 55})
        snap = app.runtime_settings()
        self.assertEqual({k: snap[k]['active'] for k in KEYS},
                         {'cam_scan': 20, 'ctl_scan': 40, 'lost_timeout': 0.6, 'steer_gain': 1.4, 'v_min': 30, 'v_max': 55})
        self.assertTrue(all(snap[k]['state'] == 'ok' and snap[k]['stored'] == snap[k]['active'] for k in KEYS))

    def test_fehlerhafte_konfiguration_laesst_den_start_nicht_scheitern(self):
        app = self.start(cam_sensor={'scan_frequency': 'abc'},
                         cameracar_controller={'scan_frequency': None, 'lost_line_timeout': 'x', 'steering_gain': -5},
                         car={'v_min': 90, 'v_max': 40})
        snap = app.runtime_settings()
        self.assertEqual({k: snap[k]['active'] for k in KEYS},
                         {'cam_scan': 25, 'ctl_scan': 100, 'lost_timeout': 1.0, 'steer_gain': 2.0, 'v_min': 20,
                          'v_max': 40})                                  # v_max 40 ist gueltig, v_min 90 nicht
        self.assertEqual(snap['steer_gain']['state'], 'invalid')
        self.assertEqual(snap['v_max']['state'], 'ok')

    def test_nicht_speicherbar_gilt_bis_zum_neustart(self):
        app = self.start()
        with patch('config_store.os.replace', side_effect=OSError('schreibgeschuetzt')):
            result = app.set_runtime_settings(self.values(steer_gain=1.2))
        self.assertIs(result['saved'], False)
        self.assertEqual(app.runtime_settings()['steer_gain'], {'active': 1.2, 'stored': None, 'state': 'absent',
                                                                'default': 2.0})
        self.assertIn('CONFIG_NOT_SAVED', [e.code for e in app.health.events()])
        text, level = __import__('runtime_settings').describe(
            __import__('runtime_settings').BY_KEY['steer_gain'], app.runtime_settings()['steer_gain'])
        self.assertEqual(level, 'ok')
        self.assertIn('nicht gespeichert', text)

    def test_abweichung_aktiv_gespeichert_wird_angezeigt(self):
        """Wert aus der Datei geaendert, Anwendung laeuft noch mit dem alten: 'gilt ab naechstem Start'."""
        app = self.start()
        data = self.config()
        data['cameracar_controller']['steering_gain'] = 3.0
        write_json(Path('config.json'), data)
        import runtime_settings
        entry = app.runtime_settings()['steer_gain']
        self.assertEqual((entry['active'], entry['stored']), (2.0, 3.0))
        self.assertIn('gilt ab nächstem Start', runtime_settings.describe(runtime_settings.BY_KEY['steer_gain'], entry)[0])

    def test_simulation_liest_nach_aenderungen_neu(self):
        from sim_lab import SimLab
        app = self.start()
        lab = SimLab(app, SimpleNamespace(), lambda image: 'x')
        with patch('sim_engine.SimEngine') as engine:
            engine.side_effect = lambda: object()
            first = lab._get_engine()
            self.assertIs(lab._get_engine(), first)
            app.set_runtime_settings(self.values(steer_gain=1.2))
            self.assertIsNot(lab._get_engine(), first)

    def test_offline_anwendung(self):
        os.chdir(self.tmp.name)
        write_json(Path('config.json'), {'cameracar_controller': {'steering_gain': 1.5}})
        from offline_application import OfflineApplication
        app = OfflineApplication()
        snap = app.runtime_settings()
        self.assertTrue(all(snap[k]['active'] is None for k in KEYS))               # offline nichts aktiv
        self.assertEqual(snap['steer_gain']['stored'], 1.5)
        result = app.set_runtime_settings(self.values(steer_gain=1.8))
        self.assertTrue(result['saved'])
        self.assertEqual(result['applied'], {k: False for k in KEYS})
        self.assertEqual(json.loads(Path('config.json').read_text())['cameracar_controller']['steering_gain'], 1.8)
        with self.assertRaises(ValueError):
            app.set_runtime_settings(self.values(cam_scan=1))
        Path('config.json').unlink()
        self.assertIs(app.set_runtime_settings(self.values())['saved'], False)       # config.json fehlt: kein Anlegen
        self.assertFalse(Path('config.json').exists())


# ------------------------------------------------------------------ nearest_position
class NearestPositionTests(unittest.TestCase):
    def test_naechster_frame(self):
        from replay_selection import nearest_position
        ids = [10, 11, 13, 20]
        self.assertEqual(nearest_position(ids, 11), 1)
        self.assertEqual(nearest_position(ids, 12), 1)                  # 11 und 13 gleich weit: der kleinere
        self.assertEqual(nearest_position(ids, 16), 2)
        self.assertEqual(nearest_position(ids, 99), 3)
        self.assertEqual(nearest_position(ids, -5), 0)
        self.assertEqual(nearest_position(ids, 12.6), 2)
        self.assertEqual(nearest_position([None, 5, None], 1), 1)       # unbekannte IDs uebergangen

    def test_ungueltige_eingaben(self):
        from replay_selection import nearest_position
        for frame in (None, True, '5', float('nan'), float('inf'), [3], {'a': 1}):
            self.assertIsNone(nearest_position([1, 2, 3], frame), frame)
        self.assertIsNone(nearest_position([], 3))
        self.assertIsNone(nearest_position([None, None], 3))


# ------------------------------------------------------------------ Dash-Attrappe
class FakeRuntime:
    """Bildet runtime_settings()/set_runtime_settings() der Anwendung mit dem echten Modul nach."""

    def __init__(self, tmp):
        import runtime_settings
        self.rs = runtime_settings
        self.path = Path(tmp) / 'config.json'
        write_json(self.path, {})
        self.active = dict(DEFAULTS)
        self.running_now = False
        self.saved = True
        self.calls = []

    def runtime_settings(self):
        stored = self.rs.stored_values(self.path)
        return {k: {'active': self.active[k], 'stored': stored[k]['value'], 'state': stored[k]['state'],
                    'default': self.rs.BY_KEY[k].default} for k in KEYS}

    def set_runtime_settings(self, values):
        if self.running_now:
            raise RuntimeError('Kamera- und Reglereinstellungen nur im Stillstand ändern')
        values = self.rs.validate(values)
        self.calls.append(values)
        self.active = dict(values)
        if self.saved:
            self.rs.save(values, self.path, Path(self.path.parent) / 'bk')
        return {'saved': self.saved, 'applied': {k: True for k in values}, 'values': values}


class SettingsCallbackTests(Quiet, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fake = FakeApplication()
        cls.runtime = FakeRuntime(cls.tmp.name)
        cls.fake.runtime_settings = cls.runtime.runtime_settings
        cls.fake.set_runtime_settings = cls.runtime.set_runtime_settings
        cls.app = cls.module.create_app(cls.fake)
        cls.cb = cls.app.callbacks

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.silence()
        self.runtime.running_now = False
        self.runtime.saved = True
        self.runtime.active = dict(DEFAULTS)
        write_json(self.runtime.path, {})

    def texts(self, node):
        nodes = node if isinstance(node, (list, tuple)) else [node]
        return ' '.join(str(c.children) for n in nodes for c in n.walk() if isinstance(c.children, str))

    def test_layout_felder_hilfetexte_und_kennzeichnung(self):
        from test_dashboard_callbacks import layout_of
        components = list(layout_of(self.app).walk())
        ids = {c.id for c in components if isinstance(c.id, str)}
        for key in KEYS:
            self.assertIn(f'rt-{key}', ids)
            self.assertIn(f'rt-status-{key}', ids)
        self.assertTrue({'rt-apply', 'rt-msg'} <= ids)
        inputs = {c.id: c.props for c in components if isinstance(c.id, str) and c.id.startswith('rt-') and c.type == 'Input'}
        self.assertEqual(inputs['rt-steer_gain']['min'], 0.5)
        self.assertEqual(inputs['rt-steer_gain']['max'], 4.0)
        self.assertEqual(inputs['rt-v_max']['step'], 1)
        popovers = [c.props['target'] for c in components if type(c).__name__ == 'Popover']
        for help_key in ('camfreq', 'ctlfreq', 'losttimeout', 'steergain', 'vmin', 'vmax'):
            self.assertIn(f'info-{help_key}', popovers)
        badges = [c for c in components if type(c).__name__ == 'Badge' and c.children == 'verhaltensrelevant']
        self.assertEqual(len(badges), 4)                               # Linien-Timeout, Lenkverstaerkung, v_min, v_max
        self.assertEqual(len(popovers), len(set(popovers)))            # eindeutige Info-IDs

    def test_anzeige_beim_oeffnen_aktiv_und_gespeichert(self):
        import runtime_settings as rs
        no = self.module.no_update
        self.ctx.set('main-tabs', 'active_tab')
        hidden = self.cb['rt_settings']('drive', None, *([None] * 6))
        self.assertEqual(hidden, (no,) * 13)
        write_json(self.runtime.path, {'cameracar_controller': {'steering_gain': 3.0}})
        self.runtime.active['steer_gain'] = 1.5
        result = self.cb['rt_settings']('settings', None, *([None] * 6))
        self.assertEqual(len(result), 13)
        self.assertEqual(result[:6], (25, 100, 1.0, 1.5, 20, 100))
        status = {k: self.texts(result[6 + i]) for i, k in enumerate(KEYS)}
        self.assertIn('gilt ab nächstem Start', status['steer_gain'])
        self.assertIn('nicht gespeichert', status['cam_scan'])
        self.assertIs(result[12], no)

    def test_uebernehmen_speichert_und_meldet(self):
        self.ctx.set('rt-apply')
        values = (30, 50, 0.5, 1.5, 25, 70)
        result = self.cb['rt_settings']('settings', 1, *values)
        self.assertEqual(self.runtime.calls[-1]['steer_gain'], 1.5)
        message = self.texts(result[12])
        self.assertIn('übernommen', message)
        self.assertIn('dauerhaft gespeichert', message)
        self.assertEqual(result[:6], (self.module.no_update,) * 6)        # eingegebene Werte bleiben stehen
        self.assertIn('gespeichert', self.texts(result[6]))
        self.assertEqual(json.loads(self.runtime.path.read_text())['car']['v_max'], 70)
        self.runtime.saved = False
        self.assertIn('nur bis zum Neustart', self.texts(self.cb['rt_settings']('settings', 2, *values)[12]))

    def test_fehler_ungueltig_und_fahrt_laeuft(self):
        self.ctx.set('rt-apply')
        before = len(self.runtime.calls)
        message = self.texts(self.cb['rt_settings']('settings', 1, 1, 100, 1.0, 2.0, 20, 100)[12])
        self.assertIn('✖', message)
        self.assertIn('Kamera-Abtastung', message)
        message = self.texts(self.cb['rt_settings']('settings', 1, None, 100, 1.0, 2.0, 20, 100)[12])   # leeres Feld
        self.assertIn('Zahl erwartet', message)
        self.runtime.running_now = True
        message = self.texts(self.cb['rt_settings']('settings', 1, *DEFAULTS.values())[12])
        self.assertIn('nur im Stillstand', message)
        self.assertEqual(len(self.runtime.calls), before)

    def test_nicht_live_wird_gekennzeichnet(self):
        self.ctx.set('rt-apply')
        original = self.runtime.set_runtime_settings
        self.fake.set_runtime_settings = lambda values: {**original(values), 'applied': {**{k: True for k in KEYS},
                                                                                         'steer_gain': False}}
        try:
            message = self.texts(self.cb['rt_settings']('settings', 1, *DEFAULTS.values())[12])
            self.assertIn('Gilt erst ab dem nächsten Start: Lenkverstärkung', message)
        finally:
            self.fake.set_runtime_settings = original


# ------------------------------------------------------------------ Frame-Balken: Serverseite
class FrameBarServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
        for frame_id in range(20, 30):
            cls.fake.publish(frame_id, with_decision=frame_id % 2 == 0)
        cls.app = cls.module.create_app(cls.fake)
        cls.cb = cls.app.callbacks

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def test_layout_bloecke_tragen_balken_und_speicher(self):
        from test_dashboard_callbacks import layout_of
        components = list(layout_of(self.app).walk())
        by_id = {c.id: c for c in components if isinstance(c.id, str)}
        for graph, store in (('replay-chart', 'replay-bar'), ('sim-chart', 'sim-bar')):
            box = by_id[f'{graph}-box']
            self.assertEqual(box.props['data-framebar'], store)
            self.assertEqual(len(box.children), 2)                          # Leiste und Diagramm bleiben Geschwister
            self.assertIn(store, by_id)
            self.assertIn(f'{graph}-barsync', by_id)
            self.assertEqual(type(by_id[store]).__name__, 'Store')
        for graph in ('freq-bars', 'freq-history', 'drive-history'):
            self.assertNotIn('data-framebar', by_id[f'{graph}-box'].props)    # nur Replay und Simulation

    def test_clientseitige_callbacks_sind_registriert(self):
        entries = {fn.function_name: (fn, deps) for fn, deps in self.app.clientside}
        self.assertEqual(len(self.app.clientside), 2)
        for graph, index in (('replay-chart', 'replay-index'), ('sim-chart', 'sim-index')):
            found = [deps for fn, deps in self.app.clientside
                     if any(getattr(d, 'component_id', None) == f'{graph}-barsync' for d in deps)]
            self.assertEqual(len(found), 1, graph)
            deps = found[0]
            self.assertEqual({(d.component_id, d.component_property) for d in deps if type(d).__name__ == 'Input'},
                             {(index, 'value'), (graph, 'figure')})
            self.assertEqual([(d.component_id, d.component_property) for d in deps if type(d).__name__ == 'State'],
                             [(f'{graph}-box', 'id')])
        self.assertEqual(set(entries), {'move'})
        self.assertEqual({fn.namespace for fn, _ in self.app.clientside}, {'c2c_framebar'})

    def seek(self, **kw):
        defaults = dict(index=0, playing=False, name='__live__', bounds=None, bar=None)
        defaults.update(kw)
        self.ctx.set('replay-bar', 'data')
        return self.cb['seek'](None, None, None, None, None, None, 1, defaults['index'], defaults['playing'],
                               defaults['name'], defaults['bounds'], 30, 30, 'replay', None, defaults['bar'])

    def test_balken_setzt_index_ohne_bereich_zu_aendern(self):
        no = self.module.no_update
        self.assertEqual(self.seek(bar={'frame': 24, 'nonce': 1}), (4, no))
        self.assertEqual(self.seek(bar={'frame': 24.4, 'nonce': 2}), (4, no))
        self.assertEqual(self.seek(bar={'frame': 999, 'nonce': 3}), (9, no))         # ausserhalb: letzter Frame
        self.assertEqual(self.seek(bar={'frame': -4, 'nonce': 4}), (0, no))
        self.assertEqual(self.seek(bar={'frame': 24}, bounds=[22, 26]), (2, no))     # Position im Bereich
        for bad in (None, {}, {'frame': None}, {'frame': 'x'}, {'frame': True}, {'frame': float('nan')}):
            self.assertEqual(self.seek(bar=bad), (no, no), bad)

    def test_bereich_und_wiedergabe_bleiben_erhalten(self):
        no = self.module.no_update
        self.ctx.set('replay-chart', 'relayoutData')                      # Mausrad-Zoom: Bereichsauswahl wie in H1
        index, bounds = self.cb['seek'](None, None, None, {'xaxis.range[0]': 22, 'xaxis.range[1]': 26}, None, None, 1, 3,
                                        False, '__live__', None, 30, 30, 'replay', None, None)
        self.assertEqual(bounds, [22.0, 26.0])
        self.ctx.set('replay-chart', 'relayoutData')                      # Balkenbewegung per relayout meldet nichts
        index, bounds = self.cb['seek'](None, None, None, {'shapes[0].x0': 25}, None, None, 1, 3, False, '__live__',
                                        None, 30, 30, 'replay', None, None)
        self.assertEqual((index, bounds), (no, no))
        self.ctx.set('tick-replay', 'n_intervals')                         # Wiedergabe laeuft weiter
        self.assertEqual(self.cb['seek'](None, None, None, None, None, None, 2, 3, True, '__live__', None, 30, 30, 'replay',
                                         None, None), (4, no))

    def test_diagramm_baut_bei_indexwechsel_einer_session_nicht_neu(self):
        no = self.module.no_update
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                name = write_session(os.path.join(folder, 'Data_AI', 'sessions'), count=10)
                self.ctx.set('replay-index', 'value')
                self.assertIs(self.cb['replay_chart'](name, None, 3, 'replay'), no)
                self.ctx.set('session-choice', 'value')
                figure = self.cb['replay_chart'](name, None, 3, 'replay')
                self.assertEqual(len(figure.data), 2)                              # Stellwert, Linienanzahl
                self.assertNotIn('Aktueller Frame', [d[1].get('name') for d in figure.data])
                self.ctx.set('main-tabs', 'active_tab')                            # Reiter oeffnen baut auf
                self.assertEqual(len(self.cb['replay_chart'](name, None, 3, 'replay').data), 2)
                self.assertIs(self.cb['replay_chart'](name, None, 3, 'drive'), no)  # anderer Reiter: nichts
            finally:
                os.chdir(cwd)

    def test_live_diagramm_beim_oeffnen_des_reiters(self):
        self.ctx.set('main-tabs', 'active_tab')
        figure = self.cb['replay_chart']('__live__', None, 0, 'replay')
        self.assertEqual(len(figure.data), 2)
        self.assertEqual([d[1]['x'] for d in figure.data][0], list(range(20, 30)))   # x = Frame-IDs = Balken-Skala

    def test_simulation_balken(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                hardware_stubs.write_config(os.path.join(folder, 'config.json'),
                                            cameracar_controller={'lost_line_timeout': 1})
                name = write_session(os.path.join(folder, 'Data_AI', 'sessions'), count=40)
                self.ctx.set('sim-run')
                status, stats, maximum, chart, token = self.cb['sim_run'](1, name, 'copilot', 'recorded', 10, 20, 5)
                self.assertTrue(token)
                ids = chart['data'][0]['x']
                self.assertEqual(ids, list(range(5, 21)))                              # x = Frame-IDs (mit Vorlauf)
                self.assertEqual(chart['data'][0]['customdata'], list(range(len(ids))))
                keep = self.module.no_update
                self.ctx.set('sim-bar', 'data')
                out = self.cb['sim_playback'](None, None, None, None, None, token, 1.0, 'realtime', 0, True,
                                              {'frame': 12, 'nonce': 1})
                self.assertEqual(out, (7, keep, keep))
                out = self.cb['sim_playback'](None, None, None, None, None, token, 1.0, 'realtime', 0, True,
                                              {'frame': 'x', 'nonce': 2})
                self.assertEqual(out, (keep, keep, keep))
                out = self.cb['sim_playback'](None, None, None, None, None, None, 1.0, 'realtime', 0, True,
                                              {'frame': 12})
                self.assertEqual(out, (0, True, '▶ Play'))                           # ohne Ergebnis: nichts wiedergeben
            finally:
                os.chdir(cwd)

    def test_balken_bei_laufender_simulation_verankert_die_uhr_neu(self):
        lab, token, _ = make_lab([i * FRAME_NS for i in range(60)])
        keep = lab.ui.no_update
        lab.playback('sim-play', token, 1.0, 'realtime', 0, True, now=0.0)
        index, paused, label = lab.playback('sim-bar', token, 1.0, 'realtime', 0, False, bar={'frame': 130}, now=1.0)
        self.assertEqual((index, paused, label), (30, keep, keep))                     # Frame 130 = Position 30
        self.assertIs(lab.playback('sim-play-tick', token, 1.0, 'realtime', 30, False, now=1.0)[0], keep)  # neuer Anker
        self.assertEqual(lab.playback('sim-play-tick', token, 1.0, 'realtime', 30, False, now=1.15)[0], 32)


# ------------------------------------------------------------------ Frame-Balken und Settings mit echtem Dash
@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashH1bTests(Quiet, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        cls.pin = hardware_stubs.pin_pipeline_config()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fake = FakeApplication()
        cls.runtime = FakeRuntime(cls.tmp.name)
        cls.fake.runtime_settings = cls.runtime.runtime_settings
        cls.fake.set_runtime_settings = cls.runtime.set_runtime_settings
        for frame_id in range(20, 30):
            cls.fake.publish(frame_id, with_decision=True)
        cls.client = module.create_app(cls.fake).server.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.silence()
        write_json(self.runtime.path, {})
        self.runtime.active = dict(DEFAULTS)

    def post(self, outputs, inputs, state, changed):
        output = ('..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..' if len(outputs) > 1
                  else f'{outputs[0][0]}.{outputs[0][1]}')
        specs = [{'id': i, 'property': p} for i, p in outputs]
        body = {'output': output, 'outputs': specs if len(specs) > 1 else specs[0],
                'inputs': [{'id': i, 'property': p, 'value': v} for i, p, v in inputs],
                'state': [{'id': i, 'property': p, 'value': v} for i, p, v in state], 'changedPropIds': changed}
        return self.client.post('/_dash-update-component', json=body)

    def test_abhaengigkeiten_enthalten_clientseitige_balken_callbacks(self):
        deps = self.client.get('/_dash-dependencies').get_json()
        client_side = [d for d in deps if d.get('clientside_function')]
        self.assertEqual(len(client_side), 2)
        self.assertEqual({d['clientside_function']['namespace'] for d in client_side}, {'c2c_framebar'})
        self.assertEqual({d['clientside_function']['function_name'] for d in client_side}, {'move'})
        self.assertEqual({d['output'] for d in client_side}, {'replay-chart-barsync.data', 'sim-chart-barsync.data'})
        for dep in client_side:
            self.assertEqual(sorted(i['property'] for i in dep['inputs']), ['figure', 'value'])
            self.assertEqual([s['property'] for s in dep['state']], ['id'])

    def test_layout_traegt_data_attribute_und_speicher(self):
        layout = self.client.get('/_dash-layout').get_json()

        def walk(node):
            if isinstance(node, dict):
                yield node
                for value in node.values():
                    yield from walk(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value)
        nodes = {n['props']['id']: n for n in walk(layout) if isinstance(n.get('props', {}).get('id'), str)}
        self.assertEqual(nodes['replay-chart-box']['props']['data-framebar'], 'replay-bar')
        self.assertEqual(nodes['sim-chart-box']['props']['data-framebar'], 'sim-bar')
        for store in ('replay-bar', 'sim-bar', 'replay-chart-barsync', 'sim-chart-barsync'):
            self.assertEqual(nodes[store]['type'], 'Store')

    def test_balken_seek_ueber_http_signaturreihenfolge(self):
        """Inputs nach den States kommen als letzte Argumente an (replay-bar nach jump-url)."""
        outputs = [('replay-index', 'value'), ('replay-bounds', 'data')]
        inputs = [('replay-prev', 'n_clicks', None), ('replay-next', 'n_clicks', None),
                  ('replay-chart', 'clickData', None), ('replay-chart', 'relayoutData', None),
                  ('replay-event', 'value', None), ('replay-range-reset', 'n_clicks', None),
                  ('tick-replay', 'n_intervals', 1), ('jump-url', 'hash', ''),
                  ('replay-bar', 'data', {'frame': 25, 'nonce': 7})]
        state = [('replay-index', 'value', 0), ('replay-playing', 'data', False), ('session-choice', 'value', '__live__'),
                 ('replay-bounds', 'data', None), ('replay-pre', 'value', 30), ('replay-post', 'value', 30),
                 ('main-tabs', 'active_tab', 'replay')]
        response = self.post(outputs, inputs, state, ['replay-bar.data'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        payload = response.get_json()['response']
        self.assertEqual(payload['replay-index']['value'], 5)                  # Frame 25 = Position 5 (20..29)
        self.assertNotIn('replay-bounds', payload)                             # Bereich bleibt unberuehrt
        inputs[-1] = ('replay-bar', 'data', {'frame': 'kaputt', 'nonce': 8})
        response = self.post(outputs, inputs, state, ['replay-bar.data'])
        self.assertIn(response.status_code, (200, 204))
        self.assertNotIn('replay-index', response.get_data(as_text=True))

    def test_replay_diagramm_ohne_cursorkurve_ueber_http(self):
        response = self.post([('replay-chart', 'figure')],
                             [('session-choice', 'value', '__live__'), ('replay-bounds', 'data', None),
                              ('replay-index', 'value', 2), ('main-tabs', 'active_tab', 'replay')],
                             [], ['main-tabs.active_tab'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        figure = response.get_json()['response']['replay-chart']['figure']
        self.assertEqual([t['name'] for t in figure['data']], ['Stellwert', 'Linienanzahl'])
        self.assertNotIn('height', figure['layout'])

    def rt_body(self, trigger, values=None):
        outputs = [(f'rt-{k}', 'value') for k in KEYS] + [(f'rt-status-{k}', 'children') for k in KEYS] + [('rt-msg', 'children')]
        inputs = [('main-tabs', 'active_tab', 'settings'), ('rt-apply', 'n_clicks', 1 if trigger == 'rt-apply.n_clicks' else None)]
        state = [(f'rt-{k}', 'value', v) for k, v in zip(KEYS, values or [None] * 6)]
        return self.post(outputs, inputs, state, [trigger])

    def test_settings_anzeigen_und_uebernehmen_ueber_http(self):
        write_json(self.runtime.path, {'cameracar_controller': {'steering_gain': 3.0}})
        self.runtime.active['steer_gain'] = 1.5
        response = self.rt_body('main-tabs.active_tab')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        payload = response.get_json()['response']
        self.assertEqual(payload['rt-steer_gain']['value'], 1.5)
        self.assertEqual(payload['rt-cam_scan']['value'], 25)
        self.assertIn('gilt ab nächstem Start', json.dumps(payload['rt-status-steer_gain'], ensure_ascii=False))
        response = self.rt_body('rt-apply.n_clicks', [30, 50, 0.5, 1.2, 25, 70])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        text = response.get_data(as_text=True)
        self.assertIn('übernommen', text)
        self.assertEqual(self.runtime.calls[-1]['steer_gain'], 1.2)
        response = self.rt_body('rt-apply.n_clicks', [1, 50, 0.5, 1.2, 25, 70])
        self.assertIn('✖', json.dumps(response.get_json()['response']['rt-msg'], ensure_ascii=False))


# ------------------------------------------------------------------ Frame-Balken: Asset
class AssetTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / 'assets' / 'framebar.js').read_text(encoding='utf-8')

    def test_kein_plotly_relayout_beim_ziehen(self):
        """relayout wuerde je Bewegung eine relayoutData-Anfrage ausloesen und Plotly-Zustand veraendern."""
        for forbidden in ('Plotly.relayout', 'Plotly.react', 'Plotly.restyle', 'Plotly.update', 'Plotly.newPlot'):
            self.assertNotIn(forbidden, self.source)

    def test_bestandteile(self):
        for needed in ('c2c_framebar', "'pointerdown'", 'setPointerCapture', 'set_props', 'data-framebar', 'SEND_MS',
                       'plotly_afterplot', 'plotly_relayout', 'no_update', 'nonce'):
            self.assertIn(needed, self.source)
        css = (ROOT / 'assets' / 'studio.css').read_text(encoding='utf-8')
        for needed in ('.c2c-framebar', '.c2c-graph{position:relative}', 'ew-resize'):
            self.assertIn(needed, css)

    @unittest.skipUnless(shutil.which('node'), 'node nicht installiert')
    def test_syntax_mit_node(self):
        result = subprocess.run(['node', '--check', str(ROOT / 'assets' / 'framebar.js')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_programmatisches_setzen_meldet_nichts_zurueck(self):
        """move() ruft nur schedule()/render() auf; set_props kommt nur in send() (aus pointermove/pointerup)."""
        move = self.source[self.source.index('move: function'):]
        self.assertNotIn('set_props', move.split('return window.dash_clientside.no_update')[0])
        calls = [i for i in range(len(self.source)) if self.source.startswith('set_props(', i)]
        self.assertEqual(len(calls), 1)


# ------------------------------------------------------------------ Browsertest (optional)
CHROMIUM = os.environ.get('C2C_CHROMIUM', '')


@unittest.skipUnless(CHROMIUM and os.path.exists(CHROMIUM) and HAS_DASH and importlib.util.find_spec('playwright'),
                     'Browsertest: C2C_CHROMIUM=<Pfad zu chrome-headless-shell> setzen (Playwright und Dash noetig)')
class FrameBarBrowserTests(unittest.TestCase):
    """Echter Browser (Headless-Chromium, Dash 4.3): Balken folgt dem Index, Ziehen, Legende, Mausrad, Wiedergabe.

    Die Sandbox hat nur einen CPU-Kern; deshalb pausiert der Test das globale Polling (refresh.disabled) und
    wartet grosszuegig. Das ist NICHT der Browser des Nutzers und nicht der Pi.
    """

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
        cls.session = write_session(os.path.join(cls.tmp.name, 'Data_AI', 'sessions'), count=80)
        cls.fake = FakeApplication()
        for frame_id in range(1, 11):
            cls.fake.publish(frame_id, with_decision=True)
        app = module.create_app(cls.fake)
        from werkzeug.serving import make_server
        cls.server = make_server('127.0.0.1', 0, app.server, threaded=True)
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

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 1300})
        self.page.set_default_timeout(60000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page.goto(self.url, wait_until='domcontentloaded')
        self.page.wait_for_selector('#main-tabs')

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    BAR = "document.querySelector('#replay-chart .c2c-framebar')"

    def stable_geometry(self, graph):
        """[x, y, frame] des Balkens in ``graph``; wartet, bis sich das Layout nicht mehr verschiebt (Bilder laden)."""
        script = (f"() => {{ const b = document.querySelector('#{graph} .c2c-framebar'); const r = b.getBoundingClientRect();"
                  f" return [r.left + r.width / 2, r.top + r.height / 2, Number(b.getAttribute('data-frame'))]; }}")
        self.page.locator(f'#{graph}').scroll_into_view_if_needed()
        last = self.page.evaluate(script)
        for _ in range(20):
            self.page.wait_for_timeout(400)
            now = self.page.evaluate(script)
            if abs(now[0] - last[0]) < 0.5 and abs(now[1] - last[1]) < 0.5:
                return now
            last = now
        return last

    def open_session(self):
        page = self.page
        page.get_by_text('Replay Lab', exact=True).click()
        page.evaluate("() => window.dash_clientside.set_props('refresh', {disabled: true})")     # Polling pausieren
        page.locator('#session-choice').click()
        page.locator('[role=option]', has_text=self.session).click()
        page.wait_for_function(                                      # die Session (80 Frames), nicht der Live-Ringbuffer
            f"() => {{ const b = {self.BAR}; const gd = document.querySelector('#replay-chart .js-plotly-plot'); "
            f"return b && b.style.display === 'block' && b.getAttribute('data-frame') === '1' && gd.data.length && "
            f"gd.data[0].x.length === 80; }}")
        page.wait_for_function("() => document.getElementById('replay-range').innerText.includes('gespeicherte Session')")
        page.wait_for_function("() => document.getElementById('replay-frame-label').innerText === 'Frame #1'")
        page.wait_for_function("() => { const i = document.getElementById('replay-image'); return i && i.complete && i.naturalWidth > 0; }")
        page.locator('#replay-chart').scroll_into_view_if_needed()

    def bar_state(self, stable=True):
        """Zustand des Balkens; wartet, bis seine Position sich nicht mehr verschiebt (Bild laedt, Layout setzt sich)."""
        state = self.read_bar()
        for _ in range(20 if stable else 0):
            self.page.wait_for_timeout(400)
            again = self.read_bar()
            if abs(again['x'] - state['x']) < 0.5 and abs(again['y'] - state['y']) < 0.5:
                return again
            state = again
        return state

    def read_bar(self):
        return self.page.evaluate(f"""() => {{ const b = {self.BAR}; const r = b.getBoundingClientRect();
            const gd = document.querySelector('#replay-chart .js-plotly-plot'); const xa = gd._fullLayout.xaxis;
            return {{frame: Number(b.getAttribute('data-frame')), x: r.left + r.width / 2, y: r.top + r.height / 2,
                    display: b.style.display, range: xa.range.slice(), n: gd.data[0].x.length,
                    visible: gd.data.map(t => t.visible), label: document.getElementById('replay-frame-label').innerText}}; }}""")

    def test_balken_ziehen_ohne_neuaufbau_mit_legende_und_zoom(self):
        page = self.page
        self.open_session()
        page.evaluate("""() => { window.__sets = []; window.__reacts = 0;
            const original = window.dash_clientside.set_props;
            window.dash_clientside.set_props = function (id, props) {
                if (id === 'replay-bar') window.__sets.push(props.data.frame);
                return original.apply(this, arguments); };
            document.querySelector('#replay-chart .js-plotly-plot').on('plotly_react', () => window.__reacts++); }""")
        page.evaluate("() => Plotly.restyle(document.querySelector('#replay-chart .js-plotly-plot'), {visible: 'legendonly'}, [1])")
        start = self.bar_state()
        self.assertEqual((start['frame'], start['n']), (1, 80))
        page.mouse.move(start['x'], start['y'])
        page.mouse.down()
        page.mouse.move(start['x'] + 90, start['y'], steps=6)
        page.mouse.move(start['x'] + 150, start['y'], steps=6)
        page.mouse.up()
        target = page.evaluate(f"() => Number({self.BAR}.getAttribute('data-frame'))")
        self.assertGreater(target, 8)
        self.assertLess(target, 14)
        page.wait_for_function(f"() => document.getElementById('replay-frame-label').innerText === 'Frame #{target}'")
        end = self.bar_state()
        sets = page.evaluate('() => window.__sets')
        self.assertEqual(sets[-1], target)                                       # letzter Wert wird immer gemeldet
        self.assertLessEqual(len(sets), 8)                                       # gedrosselt, nicht je Pixel
        self.assertEqual(page.evaluate('() => window.__reacts'), 0)              # Diagramm nicht neu gebaut
        self.assertEqual(end['range'], start['range'])                           # Zoom unveraendert
        self.assertEqual(end['visible'][1], 'legendonly')                        # Legendenzustand erhalten
        self.assertGreater(end['x'], start['x'] + 100)

    def test_mausrad_ueber_dem_balken_zoomt_weiter(self):
        self.open_session()
        start = self.bar_state()
        self.page.mouse.move(start['x'], start['y'])
        self.page.mouse.wheel(0, -400)
        self.page.wait_for_function(
            "(r) => { const gd = document.querySelector('#replay-chart .js-plotly-plot'); "
            "const x = gd._fullLayout.xaxis.range; return Math.abs(x[1] - x[0]) < Math.abs(r[1] - r[0]); }",
            arg=start['range'])

    def test_wiedergabe_bewegt_den_balken_ohne_rueckmeldung(self):
        page = self.page
        self.open_session()
        page.evaluate("""() => { window.__sets = 0; const original = window.dash_clientside.set_props;
            window.dash_clientside.set_props = function (id, props) { if (id === 'replay-bar') window.__sets++;
                return original.apply(this, arguments); }; }""")
        page.evaluate("() => window.dash_clientside.set_props('tick-replay', {disabled: false})")
        page.locator('#replay-play').click()
        page.wait_for_function(f"() => Number({self.BAR}.getAttribute('data-frame')) >= 4", timeout=120000)
        page.locator('#replay-play').click()
        page.evaluate("() => window.dash_clientside.set_props('tick-replay', {disabled: true})")
        state = self.bar_state()
        page.wait_for_function(f"() => document.getElementById('replay-frame-label').innerText === 'Frame #{state['frame']}'")
        self.assertEqual(page.evaluate('() => window.__sets'), 0)                # programmatisch: keine Meldung

    def test_balken_im_live_ringbuffer(self):
        """Im Live-Ringbuffer baut jeder Indexwechsel das Diagramm neu auf; der gezogene Frame muss trotzdem stehen bleiben."""
        page = self.page
        page.get_by_text('Replay Lab', exact=True).click()
        page.evaluate("() => window.dash_clientside.set_props('refresh', {disabled: true})")
        page.wait_for_function(f"() => {{ const b = {self.BAR}; return b && b.style.display === 'block' && "
                               f"b.getAttribute('data-frame') === '1'; }}")
        page.wait_for_function("() => { const i = document.getElementById('replay-image'); return i && i.complete && i.naturalWidth > 0; }")
        x, y, frame = self.stable_geometry('replay-chart')
        self.assertEqual(frame, 1)
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 400, y, steps=8)
        page.mouse.up()
        target = page.evaluate(f"() => Number({self.BAR}.getAttribute('data-frame'))")
        self.assertGreater(target, 1)
        page.wait_for_function(f"() => document.getElementById('replay-frame-label').innerText === 'Frame #{target}'")
        page.wait_for_timeout(1500)
        self.assertEqual(page.evaluate(f"() => Number({self.BAR}.getAttribute('data-frame'))"), target)   # bleibt stehen

    def test_balken_in_der_simulation(self):
        page = self.page
        page.get_by_text('Simulation (Session-Replay)').click()
        page.evaluate("() => window.dash_clientside.set_props('refresh', {disabled: true})")
        page.locator('#sim-session').click()
        page.locator('[role=option]', has_text=self.session).click()
        page.locator('#sim-from').fill('20')
        page.locator('#sim-from').press('Enter')
        page.locator('#sim-to').fill('50')
        page.locator('#sim-to').press('Enter')
        page.get_by_text('Simulation berechnen').click()
        bar = "document.querySelector('#sim-chart .c2c-framebar')"
        page.wait_for_function(f"() => {{ const b = {bar}; return b && b.style.display === 'block'; }}", timeout=120000)
        page.wait_for_function("() => { const i = document.getElementById('sim-img-now'); return i && i.complete && i.naturalWidth > 0; }",
                               timeout=120000)
        geometry = self.stable_geometry('sim-chart')
        page.mouse.move(geometry[0], geometry[1])
        page.mouse.down()
        page.mouse.move(geometry[0] + 120, geometry[1], steps=6)
        page.mouse.up()
        frame = page.evaluate(f"() => Number({bar}.getAttribute('data-frame'))")
        self.assertGreater(frame, geometry[2] + 3)
        page.wait_for_function(f"() => document.getElementById('sim-frame-label').innerText.includes('#{frame}')",
                               timeout=120000)


if __name__ == '__main__':
    unittest.main()
