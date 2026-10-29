"""Paket H2b: Fahrmodus-Register (kind), Modusname im HUD, aktiver Modus bei Stopp-Grund und Diagnose.

Hardwarefrei: Attrappen, keine Motoransteuerung, keine echte Kamera. Der Dummy-Modus wird nur hier
registriert (und nach jedem Test wieder entfernt), nie im Produktcode.
"""

import csv
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402

try:
    import dash  # noqa: F401
    import dash_bootstrap_components  # noqa: F401
    HAS_DASH = True
except ImportError:
    HAS_DASH = False


class BareMode:
    """Fahrmodus ohne last_stop_reason/last_diagnostic (wie der Gamepad-Modus)."""

    def __init__(self, car=None):
        self.car = car
        self._running = False

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def is_running(self):
        return self._running


class DummyMode(BareMode):
    """Test-Fahrmodus mit Stopp-Grund und Diagnose."""

    def __init__(self, car=None):
        super().__init__(car)
        self.last_stop_reason = None
        self.last_diagnostic = None


def register_dummy(testcase, registry):
    registry.register('dummy_auto', 'Dummy (autonom)', order=5, kind='auto', short='Dummy')(DummyMode)
    testcase.addCleanup(registry.unregister, 'dummy_auto')


# ------------------------------------------------------------------ Register und Anwendung
class RegistryApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                    cameracar_controller={'lost_line_timeout': 5})
        os.chdir(self.tmp.name)
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        import mode_registry
        self.reg = mode_registry
        register_dummy(self, self.reg)
        from car_application import CarApplication
        self.app = CarApplication(start_camera=False)

    def tearDown(self):
        self.app.shutdown()
        self.stubs.__exit__(None, None, None)
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_dropdown_register_und_modes_haben_dieselben_schluessel_in_derselben_reihenfolge(self):
        keys = [spec.key for spec in self.reg.specs()]
        self.assertEqual([o['value'] for o in self.reg.dropdown_options()], keys)
        self.assertEqual(set(self.app.modes), set(keys))
        for needed in ('dummy_auto', 'camera', 'gamepad'):
            self.assertIn(needed, keys)
        self.assertLess(keys.index('dummy_auto'), keys.index('camera'))      # order 5 < 10
        self.assertLess(keys.index('camera'), keys.index('gamepad'))        # order 10 < 20

    def test_ungueltiges_kind_wird_abgelehnt_und_gleiche_fabrik_darf_erneut_registrieren(self):
        with self.assertRaises(ValueError):
            self.reg.register('kaputt', 'Kaputt', kind='turbo')(DummyMode)
        self.reg.register('dummy_auto', 'Dummy (autonom)', order=5, kind='auto', short='Dummy')(DummyMode)  # Neuimport
        with self.assertRaises(ValueError):
            self.reg.register('dummy_auto', 'Anderer', kind='auto')(BareMode)             # andere Fabrik, gleicher Schluessel

    def test_unbekannter_schluessel_gibt_meldung_statt_absturz(self):
        with self.assertRaises(ValueError) as caught:
            self.app.start_mode('turbo')
        self.assertIn('turbo', str(caught.exception))
        self.assertIsNone(self.app.active_mode)
        self.assertFalse(self.app.running())

    def test_vehicle_mode_nutzt_kind_aus_dem_register(self):
        self.app.modes['camera'] = DummyMode(self.app.car)      # keine echte Regelung starten
        self.app.modes['gamepad'] = DummyMode(self.app.car)
        self.assertEqual(self.app.vehicle_mode()[0], 'idle')
        self.assertEqual(self.app.vehicle_state()[3], None)
        for key, expected in (('dummy_auto', 'auto'), ('camera', 'auto'), ('gamepad', 'manual')):
            self.app.start_mode(key)
            self.assertEqual(self.app.vehicle_mode()[0], expected, key)
            self.assertEqual(len(self.app.vehicle_mode()), 3, key)               # alte Signatur
            self.assertEqual(self.app.vehicle_state()[3], key)
            self.app.stop_all()
            self.assertEqual(self.app.vehicle_mode()[0], 'idle')
            self.assertIsNone(self.app.vehicle_state()[3])

    def test_unbekannte_art_ist_sicher_manual_und_bergung_hat_vorrang(self):
        self.assertEqual(self.reg.kind_of('gibt_es_nicht'), 'manual')
        self.assertEqual(self.reg.kind_of(None), 'manual')
        self.app.enable_recovery()
        try:
            self.assertEqual(self.app.vehicle_state()[0], 'recovery')
            self.assertIsNone(self.app.vehicle_state()[3])
        finally:
            self.app.disable_recovery()

    def test_sensor_bekommt_den_vier_werte_provider(self):
        provider = getattr(self.app.sensor, 'mode_provider', None)
        self.assertEqual(provider, self.app.vehicle_state)

    def test_stop_reason_und_diagnose_lesen_den_zuletzt_gestarteten_modus(self):
        camera = self.app.modes['camera']
        camera.last_stop_reason = 'CAMERA_STALE'
        camera.last_diagnostic = {'frame_id': 1}
        self.assertEqual(self.app.stop_reason(), 'CAMERA_STALE')                 # Rueckfall CameraMode
        self.assertEqual(self.app.last_diagnostic(), {'frame_id': 1})
        dummy = self.app.modes['dummy_auto']
        self.app.start_mode('dummy_auto')
        dummy.last_stop_reason = 'DUMMY_STOP'
        dummy.last_diagnostic = {'frame_id': 7}
        self.assertEqual(self.app.stop_reason(), 'DUMMY_STOP')                   # Stopp-Grund des neuen Modus
        self.assertEqual(self.app.last_diagnostic()['frame_id'], 7)
        self.app.stop_all()
        self.assertEqual(self.app.stop_reason(), 'Benutzer-STOP')                # Benutzer-STOP hat Vorrang
        self.assertEqual(self.app.last_diagnostic()['frame_id'], 7)              # Diagnose der letzten Fahrt bleibt
        self.app.start_mode('dummy_auto')                                        # neuer Start: Sperre weg
        self.assertEqual(self.app.stop_reason(), 'DUMMY_STOP')
        self.app.stop_all()

    def test_modus_ohne_stopp_attribute_liefert_none(self):
        self.app.modes['gamepad'] = BareMode(self.app.car)
        self.app.start_mode('gamepad')
        self.assertIsNone(self.app.stop_reason())
        self.assertIsNone(self.app.last_diagnostic())
        self.app.stop_all()

    def test_fehlschlag_beim_start_setzt_aktiven_modus_zurueck(self):
        def boom():
            raise RuntimeError('Start kaputt')
        self.app.modes['dummy_auto'].start = boom
        with self.assertRaises(RuntimeError):
            self.app.start_mode('dummy_auto')
        self.assertIsNone(self.app.active_mode)
        self.assertEqual(self.app.vehicle_mode()[0], 'idle')

    def test_geschwindigkeit_und_fahrregelung_bleiben_cameramode_spezifisch(self):
        from driving.camera_mode import CameraMode
        self.assertEqual(CameraMode.SPEED_CONTROL_VALUE, 30)
        self.assertIs(self.app.speed_policy(), self.app.modes['camera'].speed_policy)


# ------------------------------------------------------------------ CameraSensor
class SensorProviderTests(unittest.TestCase):
    def test_provider_mit_drei_und_vier_werten(self):
        frames = [hardware_stubs.test_image() for _ in range(5)]
        with tempfile.TemporaryDirectory() as folder, hardware_stubs.installed(frames=frames):
            import ConfigReader
            from CameraSensor import CameraSensor
            from telemetry import FrameRingBuffer
            config = hardware_stubs.write_config(os.path.join(folder, 'config.json'))
            published = []

            class Car:
                frame_history = FrameRingBuffer()

                @property
                def camera_data(self):
                    return published[-1]

                @camera_data.setter
                def camera_data(self, value):
                    published.append(value)

            answers = iter([('auto', 99, 30),                    # alter Provider mit 3 Werten
                            ('auto', 99, 30, 'camera'),          # neuer Provider
                            ('manual', 90, 0, 7),                # Schluessel kein Text -> None
                            ('manual', 90, 0, ''),               # leer -> None
                            ('unbekannt', 90, 0, 'camera')])     # ungueltige Art -> alles None
            sensor = CameraSensor(Car(), ConfigReader.ConfigReader('cam_sensor', config),
                                  mode_provider=lambda: next(answers))
            with self.assertRaises(RuntimeError):                # Testbilder erschoepft
                sensor.read_loop(threading.Event())
            self.assertEqual([c.vehicle_mode for c in published], ['auto', 'auto', 'manual', 'manual', None])
            self.assertEqual([c.vehicle_mode_key for c in published], [None, 'camera', None, None, None])


# ------------------------------------------------------------------ Recorder
class RecorderH2bTests(unittest.TestCase):
    def test_spalte_revision_und_alte_session_ohne_spalte(self):
        from models import CameraData
        from session_recorder import FIELDS, SessionRecorder, load_session
        from telemetry import FrameRingBuffer
        with tempfile.TemporaryDirectory() as folder:
            history = FrameRingBuffer(20)
            history.add_camera(CameraData(
                process_image=np.zeros((96, 320), np.uint8), raw_image=np.zeros((240, 320, 3), np.uint8),
                frame_id=1, timestamp=time.time_ns(), monotonic_ns=time.monotonic_ns(), crop=(0.25, 0.65),
                lanes=((-1.0, 150.0), (1.5, -300.0)), vehicle_mode='auto', vehicle_mode_key='camera'))
            recorder = SessionRecorder(SimpleNamespace(frame_history=history), folder, is_driving=lambda: False)
            recorder.start()
            recorder._stop.set()
            recorder._thread.join(2)
            recorder.capture_once()
            recorder._thread = SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None)
            recorder.stop()
            self.assertIn('vehicle_mode_key', FIELDS)  # PAKET-H3
            _, rows = load_session(recorder.path.name, folder)
            self.assertEqual(rows[0]['vehicle_mode_key'], 'camera')
            manifest = json.loads((recorder.path / 'manifest.json').read_text())
            self.assertEqual(manifest['schema_version'], 2)
            self.assertEqual(manifest['schema_revision'], 4)  # PAKET-H3
            self.assertIn('vehicle_mode_key', manifest['revision_note'])
            # alte Session (Revision 2): Spalte fehlt, bleibt lesbar, kein Name statt falscher Name
            old = os.path.join(folder, 'session_alt')
            os.makedirs(os.path.join(old, 'frames'))
            with open(os.path.join(old, 'frames.csv'), 'w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=('frame_id', 'image_filename', 'data_status', 'vehicle_mode'))
                writer.writeheader()
                writer.writerow({'frame_id': 5, 'image_filename': 'frames/x.jpg', 'data_status': 'complete',
                                 'vehicle_mode': 'auto'})
            _, old_rows = load_session('session_alt', folder)
            self.assertIsNone(old_rows[0].get('vehicle_mode_key'))


# ------------------------------------------------------------------ HUD
class HudTests(unittest.TestCase):
    ROW = dict(frame_id=5, data_status='complete', vehicle_mode='auto', steering_angle=97,
               steering_angle_car=99, speed=30)

    def setUp(self):
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        import replay_hud
        self.hud = replay_hud
        register_dummy(self, replay_hud.mode_registry)

    def tearDown(self):
        self.stubs.__exit__(None, None, None)

    def texts(self, row, width=320):
        seen = []
        with mock.patch.object(self.hud, 'put_text', lambda image, text, *args, **kwargs: seen.append(text)):
            image = self.hud.draw_hud(np.zeros((240, width, 3), np.uint8), row)
        return seen, image

    def test_modusname_steht_im_hud_wenn_schluessel_bekannt(self):
        seen, _ = self.texts(dict(self.ROW, vehicle_mode_key='dummy_auto'))
        self.assertIn('Dummy', seen)

    def test_alte_zeile_ohne_schluessel_zeigt_keinen_namen(self):
        seen, _ = self.texts(dict(self.ROW))
        self.assertNotIn('Dummy', seen)
        seen, _ = self.texts(dict(self.ROW, vehicle_mode_key=''))
        self.assertNotIn('Dummy', seen)

    def test_unbekannter_schluessel_stuerzt_nicht_ab(self):
        seen, image = self.texts(dict(self.ROW, vehicle_mode_key='unbekannt_xyz'))
        self.assertEqual(image.shape, (240, 320, 3))

    def test_schmales_bild_ueberlagert_die_winkelzeile_nicht(self):
        seen, image = self.texts(dict(self.ROW, vehicle_mode_key='dummy_auto'), width=120)
        self.assertEqual(image.shape, (240, 120, 3))
        self.assertNotIn('Dummy', seen)

    def test_namen_sind_ascii(self):
        self.hud.mode_registry.register('umlaut', 'Ümläut', order=50, kind='manual', short='Fährmodus')(BareMode)
        self.addCleanup(self.hud.mode_registry.unregister, 'umlaut')
        seen, _ = self.texts(dict(self.ROW, vehicle_mode_key='umlaut'))
        for text in seen:
            self.assertTrue(str(text).isascii(), text)

    def test_row_from_camera_uebernimmt_den_schluessel(self):
        from models import CameraData
        with_key = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.zeros((240, 320, 3), np.uint8),
                              frame_id=3, vehicle_mode='auto', vehicle_mode_key='dummy_auto')
        without = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.zeros((240, 320, 3), np.uint8),
                             frame_id=4)
        self.assertEqual(self.hud.row_from_camera(with_key)['vehicle_mode_key'], 'dummy_auto')
        self.assertIsNone(self.hud.row_from_camera(without)['vehicle_mode_key'])

    def test_legende_nennt_den_modus(self):
        self.assertTrue(any('MODUS' in str(item) for item in self.hud.LEGEND))


# ------------------------------------------------------------------ Dashboard (Attrappe)
class DashboardH2bTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.app = cls.module.create_app(FakeApplication())

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def test_dropdown_zeigt_das_register_und_hat_camera_als_standard(self):
        from test_dashboard_callbacks import layout_of
        import dashboard.helpers as helpers
        register_dummy(self, helpers.mode_registry)
        dropdown = next(c for c in layout_of(self.app).walk() if c.id == 'mode')
        values = [o['value'] for o in dropdown.props['options']]
        self.assertEqual(values, [spec.key for spec in helpers.mode_registry.specs()])
        self.assertIn('dummy_auto', values)                       # Layout wird je Seitenaufruf neu gebaut
        self.assertEqual(dropdown.props['value'], 'camera')


# ------------------------------------------------------------------ echtes Dash ueber den Flask-Testclient
@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashH2bTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        cls.pin = hardware_stubs.pin_pipeline_config()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = FakeApplication()
        cls.client = module.create_app(cls.fake).server.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)

    def test_layout_dropdown_und_unveraenderte_callbacks(self):
        layout = json.dumps(self.client.get('/_dash-layout').get_json(), ensure_ascii=False)
        self.assertIn('"value": "camera"', layout)
        self.assertIn('"value": "gamepad"', layout)
        deps = self.client.get('/_dash-dependencies').get_json()
        outputs = []
        for dep in deps:
            names = dep['output'].strip('.').split('...') if dep['output'].startswith('..') else [dep['output']]
            outputs.extend(names)
        self.assertEqual(len(outputs), len(set(outputs)), [o for o in outputs if outputs.count(o) > 1])
        self.assertEqual(len([d for d in deps if 'main-tabs.active_tab' in d['output']]), 1)


if __name__ == '__main__':
    unittest.main()