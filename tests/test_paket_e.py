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
        chart = self.callbacks['telemetry_view'](1, 'telemetry')[0]        # PAKET-H2: EIN gekoppeltes Diagramm
        self.assertIsInstance(chart, dict)
        self.assertIn('layout', chart)
        self.ctx.set('tick-telemetry', 'n_intervals')
        self.assertIs(self.callbacks['telemetry_view'](2, 'telemetry')[0], self.module.no_update)
        self.assertIsInstance(self.callbacks['telemetry_view'](4, 'telemetry')[0], dict)

    def test_neueste_session_oeffnet_das_replay_lab(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                for name in ('session_20261028_100000_000000', 'session_20261028_110000_000000'):
                    os.makedirs(os.path.join('Data_AI', 'sessions', name))
                    open(os.path.join('Data_AI', 'sessions', name, 'frames.csv'), 'w').write('frame_id\n')
                self.fake.recorder.path = None                         # auch ohne laufende Aufnahme
                self.ctx.set('replay-open-latest')
                result = self.callbacks['replay_to_studio'](None, 1, None, None, 0)
                self.assertEqual(result[0], 'replay')
                self.assertEqual(result[4], 'session_20261028_110000_000000')
            finally:
                os.chdir(cwd)

    def test_ohne_session_kein_sprung_aber_meldung(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                self.ctx.set('replay-open-latest')
                result = self.callbacks['replay_to_studio'](None, 1, None, None, 0)
                self.assertEqual(result[1], 'Keine gespeicherte Session vorhanden')
                self.assertTrue(all(v is self.module.no_update for i, v in enumerate(result) if i != 1))
            finally:
                os.chdir(cwd)


if __name__ == '__main__':
    unittest.main()

