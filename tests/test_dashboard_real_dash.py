"""Integrationstest mit der echten Dash-Bibliothek (ohne Browser, ohne Hardware).

Wird uebersprungen, wenn dash/dash_bootstrap_components nicht installiert sind.
Prueft Layout-Auslieferung, Callback-Abhaengigkeiten und echte Callback-Aufrufe
ueber den Flask-Testclient.
"""
import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))


@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hardware_stubs
        from test_dashboard_callbacks import FakeApplication
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = FakeApplication()
        import pipeline_config
        with patch.object(module, 'load', return_value=pipeline_config.DEFAULT):
            cls.app = module.create_app(cls.fake)
        cls.client = cls.app.server.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.stubs.__exit__(None, None, None)

    def test_seite_und_layout(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        layout = self.client.get('/_dash-layout')
        self.assertEqual(layout.status_code, 200)
        self.assertIn('main-tabs', layout.get_data(as_text=True))

    def test_abhaengigkeiten(self):
        deps = json.loads(self.client.get('/_dash-dependencies').get_data(as_text=True))
        self.assertEqual(len(deps), 30)  # PAKET-G  # PAKET-F  # PAKET-C

    def post(self, outputs, inputs, state, changed):
        output = ('..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..' if len(outputs) > 1
                  else f'{outputs[0][0]}.{outputs[0][1]}')
        specs = [{'id': i, 'property': p} for i, p in outputs]
        body = {'output': output, 'outputs': specs if len(specs) > 1 else specs[0],
                'inputs': [{'id': i, 'property': p, 'value': v} for i, p, v in inputs],
                'state': [{'id': i, 'property': p, 'value': v} for i, p, v in state],
                'changedPropIds': changed}
        return self.client.post('/_dash-update-component', json=body)

    def test_globaler_status_ueber_http(self):
        outputs = [('global-camera-state', 'children'), ('global-camera-state', 'color'),
                   ('global-mode-state', 'children'), ('global-mode-state', 'color'),
                   ('global-rec-state', 'children'), ('global-rec-state', 'color'),
                   ('global-frame', 'children'), ('global-start', 'disabled'), ('global-stop', 'outline'),
                   ('recovery-toggle', 'children'), ('recovery-toggle', 'outline'),
                   ('recovery-panel', 'is_open'), ('recovery-pulse', 'disabled'),
                   ('record-toggle', 'children'), ('record-toggle', 'color'), ('record-toggle', 'outline'),
                   ('mode', 'disabled'), ('global-action-message', 'children')]
        response = self.post(outputs, [('refresh', 'n_intervals', 1), ('last-action', 'data', None)], [],
                             ['refresh.n_intervals'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        self.assertIn('FAHRMODUS AUS', response.get_data(as_text=True))

    def test_start_aktion_ueber_http(self):
        response = self.post([('last-action', 'data')],
                             [('global-start', 'n_clicks', 1), ('global-stop', 'n_clicks', None),
                              ('recovery-toggle', 'n_clicks', None), ('record-toggle', 'n_clicks', None)],
                             [('mode', 'value', 'camera')], ['global-start.n_clicks'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        self.assertIn(('start_mode', 'camera'), self.fake.calls)
        self.fake.stop_all()

    def test_cockpit_ueber_http(self):
        self.fake.publish(3)
        outputs = [('drive-overlay', 'src'), ('drive-rendered', 'data'), ('drive-angle', 'children'),
                   ('drive-speed', 'children'), ('drive-requested', 'children'), ('drive-raw', 'children'),
                   ('drive-mode-status', 'children'), ('drive-diagnostic', 'children'),
                   ('drive-frame-badge', 'children'), ('drive-age', 'children')]
        response = self.post(outputs, [('refresh', 'n_intervals', 1)],
                             [('main-tabs', 'active_tab', 'drive'), ('drive-rendered', 'data', None),
                              ('cockpit-hud', 'value', [])],  # PAKET-G
                             ['refresh.n_intervals'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        self.assertIn('data:image', response.get_data(as_text=True))
        # Anderer Tab: keine Aktualisierung, es werden keine Bilddaten uebertragen.
        response = self.post(outputs, [('refresh', 'n_intervals', 2)],
                             [('main-tabs', 'active_tab', 'telemetry'), ('drive-rendered', 'data', None),
                              ('cockpit-hud', 'value', [])],
                             ['refresh.n_intervals'])
        self.assertIn(response.status_code, (200, 204))
        self.assertNotIn('data:image', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
