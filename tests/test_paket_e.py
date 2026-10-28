"""Paket E: Telemetrie als dict-Figuren mit Drosselung, Recording -> Replay (ohne Hardware)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))


class PaketETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
        cls.app = cls.module.create_app(cls.fake)
        cls.callbacks = cls.app.callbacks

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def test_telemetrie_dicts_und_drosselung(self):
        self.ctx.set('main-tabs', 'active_tab')
        bars = self.callbacks['telemetry_view'](1, 'telemetry')[0]
        self.assertIsInstance(bars, dict)
        self.assertEqual(len(bars['data']), 2)
        self.ctx.set('tick-telemetry', 'n_intervals')
        self.assertIs(self.callbacks['telemetry_view'](2, 'telemetry')[0], self.module.no_update)
        self.assertIsInstance(self.callbacks['telemetry_view'](4, 'telemetry')[0], dict)

    def test_recording_springt_ins_replay(self):
        self.fake.recorder.path = '/tmp/Data_AI/sessions/session_test'
        self.ctx.set('record-open-replay')
        result = self.callbacks['replay_to_studio'](None, 1, None, None, 0)
        self.assertEqual(result[0], 'replay')
        self.assertEqual(result[4], 'session_test')

    def test_ohne_session_kein_sprung(self):
        self.fake.recorder.path = None
        self.ctx.set('record-open-replay')
        result = self.callbacks['replay_to_studio'](None, 1, None, None, 0)
        self.assertTrue(all(v is self.module.no_update for v in result))


if __name__ == '__main__':
    unittest.main()

