"""Paket G: Geschwindigkeitsstrategie, Settings, Kennzahlen, Frame-Links, HUD, Info-Buttons.

Ohne Hardware und ohne Motoransteuerung (ReplayCar bzw. Hardware-Attrappen).
"""
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from models import CameraData  # noqa: E402
from speed_policy import SpeedPolicy  # noqa: E402

TWO_LANES = ((-1.19, 166.5), (1.585, -317.1))


class SpeedPolicyTests(unittest.TestCase):
    def test_standard_ist_fest_30(self):
        policy = SpeedPolicy()
        self.assertEqual(policy.speed_for(135), 30)
        self.assertEqual(policy.speed_for(90, held=True), 30)

    def test_adaptiv(self):
        policy = SpeedPolicy('adaptive', 30, 30, 45)
        self.assertEqual(policy.speed_for(90), 45)
        self.assertEqual(policy.speed_for(135), 30)
        self.assertEqual(policy.speed_for(45), 30)
        self.assertEqual(policy.speed_for(112.5), 38)          # halber Einschlag
        self.assertEqual(policy.speed_for(90, held=True), 30)

    def test_grenzen(self):
        for args in (('turbo', 30, 30, 45), ('fixed', 20, 30, 45), ('adaptive', 30, 50, 40),
                     ('adaptive', 30, 30, 80)):
            with self.assertRaises(ValueError):
                SpeedPolicy(*args)
        with self.assertRaises(ValueError):
            SpeedPolicy.from_values('fixed', 'abc', 30, 45)
        self.assertEqual(SpeedPolicy.from_values('fixed', '35', 30, 45).fixed_speed, 35)


class CameraModeSpeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'))

    def tearDown(self):
        self.tmp.cleanup()

    def run_frames(self, policy, lanes, count=3):
        import ConfigReader
        from driving.camera_mode import CameraMode
        from replay_car import ReplayCar
        car = ReplayCar()
        mode = CameraMode(car=car, cfg=ConfigReader.ConfigReader('cameracar_controller', self.config))
        if policy is not None:
            mode.speed_policy = policy
        mode._pre_run()
        for frame_id in range(1, count + 1):
            car.camera_data = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.empty(0),
                                         frame_id=frame_id, monotonic_ns=time.monotonic_ns(), lanes=lanes)
            mode._run()
        return car

    def test_standard_unveraendert_30(self):
        car = self.run_frames(None, TWO_LANES)
        self.assertEqual(car.speed, 30)

    def test_adaptiv_im_regler(self):
        car = self.run_frames(SpeedPolicy('adaptive', 30, 30, 50), TWO_LANES)
        angle = car.driving_state.requested_angle
        self.assertEqual(car.speed, SpeedPolicy('adaptive', 30, 30, 50).speed_for(angle))
        held = self.run_frames(SpeedPolicy('adaptive', 30, 30, 50), ((1.0, 2.0),))
        self.assertEqual(held.speed, 30)                       # Vorwert gehalten -> v_min


class ApplicationSettingsTests(unittest.TestCase):
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

    def test_nur_im_stillstand(self):
        policy = SpeedPolicy('adaptive', 30, 30, 40)
        self.app.set_speed_policy(policy)
        self.assertIs(self.app.speed_policy(), policy)
        self.app.start_mode('camera')
        with self.assertRaises(RuntimeError):
            self.app.set_speed_policy(SpeedPolicy())
        self.app.stop_all()

    def test_jpeg_qualitaet(self):
        self.app.set_jpeg_quality(85)
        self.assertEqual(self.app.recorder.jpeg_quality, 85)
        with self.assertRaises(ValueError):
            self.app.set_jpeg_quality(10)
        self.app.toggle_recording()
        with self.assertRaises(RuntimeError):
            self.app.set_jpeg_quality(70)
        self.app.toggle_recording()

    def test_veraltete_frequenz_ist_null(self):
        from telemetry import FrequencyCounter
        counter = FrequencyCounter()
        counter.tick()
        counter.tick()
        self.assertGreater(self.app._fresh_hz(counter), 0)
        with patch('telemetry.frequency_counter.monotonic', return_value=time.monotonic() + 5):
            self.assertEqual(self.app._fresh_hz(counter), 0)
        self.assertEqual(self.app._fresh_hz(None), 0)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
        cls.fake.policy = None
        cls.fake.set_speed_policy = lambda policy: setattr(cls.fake, 'policy', policy)
        import pipeline_config
        with patch.object(cls.module, 'load', return_value=pipeline_config.DEFAULT):
            cls.app = cls.module.create_app(cls.fake)
        cls.cb = cls.app.callbacks

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def texts(self, items):
        items = items if isinstance(items, (list, tuple)) else [items]
        return ' '.join(str(c.children) for item in items for c in item.walk() if isinstance(c.children, str))

    def test_settings(self):
        messages = self.cb['settings_apply'](1, 'adaptive', 30, 30, 50, 85)
        self.assertEqual(self.fake.policy.mode, 'adaptive')
        self.assertIn('adaptiv 30–50', self.texts(messages))
        self.assertIn('Aufnahme', self.texts(messages))          # Fake ohne Aufnahme-Einstellung
        messages = self.cb['settings_apply'](2, 'fixed', 10, 30, 50, 85)
        self.assertIn('✖ Geschwindigkeit', self.texts(messages))

    def test_kennzahlen(self):
        for frame_id in range(1, 6):
            self.fake.publish(frame_id, with_decision=True)
        rate, lat, qual = self.cb['kpi_bar'](2)
        self.assertTrue(qual.children.endswith('%'))
        self.assertTrue(lat.children.endswith('ms'))

    def test_frame_links_und_sprung(self):
        for frame_id in range(50, 55):
            self.fake.publish(frame_id, with_decision=True)
        self.fake.recorder.path = None
        self.fake.health.record('LANE_LOST', 'Test', 52)
        self.fake.health.record('ALT', 'nicht mehr im Ringbuffer', 3)
        _status, _details, items, _preview = self.cb['recording_view'](1, None, 'record')
        links = [c for item in items for c in item.walk() if type(c).__name__ == 'Link']
        self.assertTrue(any(link.props['href'].startswith('#jump/__live__/52/') for link in links))
        self.assertFalse(any('/3/' in link.props['href'] for link in links))
        href = next(link.props['href'] for link in links if '/52/' in link.props['href'])
        self.ctx.set('jump-url', 'hash')
        result = self.cb['replay_to_studio'](None, None, None, None, 0, href)
        self.assertEqual((result[0], result[4]), ('replay', '__live__'))
        index, bounds = self.cb['seek'](None, None, None, None, None, None, 1, 0, False, '__live__',
                                        None, 1, 1, 'replay', href)
        self.assertEqual(bounds, [51, 53])
        self.assertEqual(index, 1)
        self.assertIsNone(self.module.parse_jump('#kaputt'))

    def test_cockpit_hud(self):
        self.fake._running = False
        self.fake.publish(70)
        plain = self.cb['cockpit_view'](1, 'drive', None, [])
        hud = self.cb['cockpit_view'](2, 'drive', None, ['on'])
        self.assertTrue(hud[1].endswith(':hud'))
        self.assertTrue(hud[0].startswith('data:image') and hud[0] != plain[0])

    def test_info_ziele_existieren(self):
        components = list(self.app.layout.walk())
        ids = {c.id for c in components if c.id}
        targets = [c.props['target'] for c in components if type(c).__name__ == 'Popover']
        self.assertGreaterEqual(len(targets), 20)
        self.assertEqual([t for t in targets if t not in ids], [])
        tabs = [c.props.get('tab_id') for c in components if type(c).__name__ == 'Tab']
        self.assertIn('settings', tabs)


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components')),
                     'Dash nicht installiert')
class RealDashJumpTests(unittest.TestCase):
    """Echte Dash-Bibliothek: Input nach den States kommt als letztes Argument an."""

    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = FakeApplication()
        for frame_id in range(10, 14):
            cls.fake.publish(frame_id)
        import pipeline_config
        with patch.object(module, 'load', return_value=pipeline_config.DEFAULT):
            cls.client = module.create_app(cls.fake).server.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.stubs.__exit__(None, None, None)

    def test_sprung_ueber_http(self):
        outputs = [('main-tabs', 'active_tab'), ('replay-studio-msg', 'children'), ('ab-source', 'children'),
                   ('replay-studio-token', 'data'), ('session-choice', 'value')]
        body = {'output': '..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..',
                'outputs': [{'id': i, 'property': p} for i, p in outputs],
                'inputs': [{'id': 'replay-to-studio', 'property': 'n_clicks', 'value': None},
                           {'id': 'record-open-replay', 'property': 'n_clicks', 'value': None},
                           {'id': 'jump-url', 'property': 'hash', 'value': '#jump/__live__/11/1'}],
                'state': [{'id': 'session-choice', 'property': 'value', 'value': '__live__'},
                          {'id': 'replay-bounds', 'property': 'data', 'value': None},
                          {'id': 'replay-index', 'property': 'value', 'value': 0}],
                'changedPropIds': ['jump-url.hash']}
        response = self.client.post('/_dash-update-component', json=body)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertIn('"replay"', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
