"""Hardware- und browserfreie Tests des Control Centers (Dash-Attrappe).

Geprueft werden vollstaendige Callback-Registrierung, Komponenten-IDs,
doppelte Outputs, Callback-Signaturen und das Verhalten zentraler Callbacks
mit einer Fake-Anwendung. Es wird kein Motor angesteuert.
"""
import os
import sys
import tempfile
import time
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import dash_stub  # noqa: E402
import hardware_stubs  # noqa: E402
from driving_health import DrivingHealthMonitor  # noqa: E402
from models import CameraData, DrivingState  # noqa: E402
from telemetry import DurationStats, FrameRingBuffer, FrequencyCounter  # noqa: E402


class FakeRecovery:
    def __init__(self):
        self.enabled = False
        self.commands = []

    def command(self, **kwargs):
        self.commands.append(kwargs)


class FakeApplication:
    """Bildet die oeffentliche Schnittstelle von CarApplication nach."""

    def __init__(self):
        self.car = SimpleNamespace(camera_data=None, frame_history=FrameRingBuffer(10),
                                   thread_metrics={'camera': FrequencyCounter(), 'controller': FrequencyCounter()},
                                   steering_angle=90, speed=0, driving_state=None)
        self.health = DrivingHealthMonitor()
        self.recovery = FakeRecovery()
        self.recorder = SimpleNamespace(is_running=lambda: False,
                                        stats=lambda: {'frames': 0, 'path': None, 'error': None})
        self.sensor = SimpleNamespace(scan_frequency=25)
        self.timings = DurationStats()
        self.controller_poll_hz = 500.0
        self.active_mode = None
        self._running = False
        self.calls = []

    def running(self):
        return self._running

    def camera_alive(self):
        return True

    def stop_reason(self):
        return None

    def last_diagnostic(self):
        return None

    def start_mode(self, key):
        self.calls.append(('start_mode', key))
        if self.recovery.enabled:
            raise RuntimeError('Bergung aktiv')
        self._running = True
        self.active_mode = key

    def stop_all(self):
        self.calls.append(('stop_all',))
        self._running = False

    def enable_recovery(self):
        self.calls.append(('enable_recovery',))
        self.recovery.enabled = True

    def disable_recovery(self):
        self.calls.append(('disable_recovery',))
        self.recovery.enabled = False

    def toggle_recording(self):
        self.calls.append(('toggle_recording',))
        return True

    def set_pipeline(self, settings):
        self.calls.append(('set_pipeline', settings))

    def save_pipeline(self, settings):
        self.calls.append(('save_pipeline', settings))

    def sample(self):
        return (), (), ()

    def publish(self, frame_id, with_decision=False):
        raw = hardware_stubs.test_image()
        camera = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=raw, frame_id=frame_id,
                            monotonic_ns=time.monotonic_ns(), crop=(0.15, 0.55),
                            lanes=((-1.2, 166.0), (1.6, -317.0)),
                            stages={'roi': raw[86:182], 'mask': np.zeros((96, 320), np.uint8)})
        self.car.camera_data = camera
        self.car.frame_history.add_camera(camera)
        if with_decision:
            state = DrivingState(frame_id, time.time_ns(), 95, 30, camera.lanes, 99, 'camera', 99,
                                 time.monotonic_ns())
            self.car.frame_history.add_driving(state)
        return camera


def load_dashboard():
    """Importiert Camp2CodeDashboard mit Dash-Attrappe (ohne Hardware)."""
    extra = dash_stub.modules()
    context = hardware_stubs.installed(extra=extra)
    context.__enter__()
    module = hardware_stubs.fresh_import('Camp2CodeDashboard')
    return module, context, extra['dash'].ctx


class DashboardStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.app_state = FakeApplication()
        with patch.object(cls.module, 'load', return_value=__import__('pipeline_config').DEFAULT):
            cls.app = cls.module.create_app(cls.app_state)
        os.chdir(cls.tmp.name)
        cls.callbacks = cls.app.callbacks

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)
        cls.context.__exit__(None, None, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.app_state.calls.clear()

    def layout_ids(self):
        return [c.id for c in self.app.layout.walk() if c.id is not None]

    def test_ids_eindeutig(self):
        duplicates = [i for i, n in Counter(self.layout_ids()).items() if n > 1]
        self.assertEqual(duplicates, [])

    def test_alle_abhaengigkeiten_im_layout(self):
        ids = set(self.layout_ids())
        missing = {(name, d.component_id) for name, cb in self.callbacks.items()
                   for d in cb.outputs + cb.inputs + cb.states if d.component_id not in ids}
        self.assertEqual(missing, set())

    def test_keine_doppelten_outputs(self):
        outputs = Counter((d.component_id, d.component_property) for cb in self.callbacks.values() for d in cb.outputs)
        self.assertEqual([k for k, n in outputs.items() if n > 1], [])

    def test_kein_output_als_eigener_input(self):
        for name, cb in self.callbacks.items():
            self.assertFalse(set(cb.outputs) & {tuple(i) for i in cb.inputs}, name)
            overlap = {tuple(o) for o in cb.outputs} & {tuple(i) for i in cb.inputs}
            self.assertEqual(overlap, set(), name)

    def test_signaturen_passen_zu_abhaengigkeiten(self):
        import inspect
        for name, cb in self.callbacks.items():
            params = inspect.signature(cb.func).parameters.values()
            variadic = any(p.kind == p.VAR_POSITIONAL for p in params)
            positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            expected = len(cb.inputs) + len(cb.states)
            if variadic:
                self.assertLessEqual(len(positional), expected, name)
            else:
                self.assertEqual(len(positional), expected, name)

    def test_erwartete_callbacks_registriert(self):
        expected = {'tab_intervals', 'handle_actions', 'global_status', 'recovery_pulse', 'cockpit_view', 'configure',
                    'preset_actions', 'freeze', 'inspect', 'pipeline_view', 'ab_calibrate', 'telemetry_view',
                    'recording_view', 'refresh_sessions', 'replay_event_options', 'seek', 'replay_limits',
                    'replay_toggle', 'replay_play_label', 'replay_render', 'export_range', 'replay_chart', 'replay_to_studio'}  # PAKET-C
        self.assertEqual(set(self.callbacks), expected)

    def test_aktionen_und_zustand(self):
        cb = self.callbacks['handle_actions']
        self.ctx.set('global-start')
        result = cb(1, None, None, None, 'camera')
        self.assertTrue(result['ok'])
        self.assertIn(('start_mode', 'camera'), self.app_state.calls)
        status = self.callbacks['global_status'](1, result)
        self.assertEqual(len(status), len(self.callbacks['global_status'].outputs))
        self.assertEqual(status[2], 'FAHRT AKTIV')
        self.assertTrue(status[7])           # START gesperrt waehrend der Fahrt
        self.ctx.set('global-stop')
        cb(1, 1, None, None, 'camera')
        self.assertIn(('stop_all',), self.app_state.calls)
        status = self.callbacks['global_status'](2, None)
        self.assertEqual(status[2], 'FAHRMODUS AUS')
        self.assertFalse(status[7])

    def test_bergung_sperrt_start_und_aktiviert_puls(self):
        self.ctx.set('recovery-toggle')
        self.callbacks['handle_actions'](None, None, 1, None, 'camera')
        status = self.callbacks['global_status'](1, None)
        self.assertTrue(status[11])   # Panel offen
        self.assertFalse(status[12])  # Puls aktiv
        self.assertTrue(status[7])    # START gesperrt
        self.ctx.set('global-start')
        result = self.callbacks['handle_actions'](2, None, 1, None, 'camera')
        self.assertFalse(result['ok'])
        self.ctx.set('recovery-toggle')
        self.callbacks['handle_actions'](2, None, 2, None, 'camera')
        status = self.callbacks['global_status'](2, None)
        self.assertTrue(status[12])   # Puls wieder aus

    def test_ansichten_nur_im_aktiven_tab(self):
        self.app_state.publish(1)
        no = self.module.no_update
        self.assertTrue(all(v is no for v in self.callbacks['cockpit_view'](1, 'pipeline', None)))
        self.assertTrue(all(v is no for v in self.callbacks['telemetry_view'](1, 'drive')))
        self.assertTrue(all(v is no for v in self.callbacks['recording_view'](1, None, 'drive')))
        self.assertTrue(all(v is no for v in self.callbacks['pipeline_view'](1, 'overlay', None, 'drive', None)))

    def test_cockpit_kodiert_nur_neue_frames(self):
        self.app_state._running = False
        self.app_state.publish(5)
        first = self.callbacks['cockpit_view'](1, 'drive', None)
        self.assertTrue(first[0].startswith('data:image/jpeg'))
        second = self.callbacks['cockpit_view'](2, 'drive', first[1])
        self.assertIs(second[0], self.module.no_update)

    def test_cockpit_zeigt_entscheidung_mit_gleicher_frame_id(self):
        self.app_state._running = True
        self.app_state.active_mode = 'camera'
        self.app_state.publish(7, with_decision=True)
        self.app_state.publish(8)  # neuester Frame noch ohne Entscheidung
        result = self.callbacks['cockpit_view'](1, 'drive', None)
        self.assertEqual(result[1], 'drive:7')
        self.app_state._running = False

    def test_pipeline_inspector_ohne_doppelte_kodierung(self):
        self.app_state.publish(11)
        result = self.callbacks['pipeline_view'](1, 'roi', None, 'pipeline', None)
        stages = result[:8]
        inspector = result[16]
        self.assertEqual(inspector, stages[1])
        frozen = self.callbacks['pipeline_view'](2, 'roi', 11, 'pipeline', result[-1])
        self.assertIs(frozen[0], self.module.no_update)

    def test_konfiguration_wird_angewendet(self):
        self.ctx.set('cfg-crop', 'value')
        message = self.callbacks['configure'](None, None, 'copilot', [0.1, 0.9], [100, 130], [80, 255],
                                              [40, 255], 3, 5, [50, 100], ['on'], ['on'], [], ['on'], [], ['on'])
        self.assertIn('übernommen', message)
        settings = self.app_state.calls[-1][1]
        self.assertEqual(settings['enabled'], {'roi': True, 'mask': True, 'blur': False, 'morph': True,
                                               'canny': False, 'lanes': True})

    def test_telemetrie_ohne_messwerte(self):
        self.app_state.car.camera_data = None
        result = self.callbacks['telemetry_view'](1, 'telemetry')
        self.assertEqual(len(result), 6)

    def test_replay_live_und_export(self):
        for frame_id in range(20, 26):
            self.app_state.publish(frame_id, with_decision=frame_id % 2 == 0)
        self.ctx.set('replay-index', 'value')
        image, label, details = self.callbacks['replay_render'](2, '__live__', None, 1, 'replay')
        self.ctx.set('tick-replay', 'n_intervals')
        self.assertTrue(self.callbacks['replay_render'](2, '__live__', None, 2, 'replay')[0].startswith('data:'))
        self.assertTrue(image.startswith('data:image/jpeg'))
        self.ctx.set('replay-next')
        index, bounds = self.callbacks['seek'](None, 1, None, None, None, None, 1, 2, False, '__live__',
                                               None, 30, 30, 'replay')
        self.assertEqual(index, 3)
        self.ctx.set('replay-chart', 'relayoutData')
        index, bounds = self.callbacks['seek'](None, None, None, {'xaxis.range[0]': 21, 'xaxis.range[1]': 23},
                                               None, None, 1, 2, False, '__live__', None, 30, 30, 'replay')
        self.assertEqual(bounds, [21.0, 23.0])
        maximum, text = self.callbacks['replay_limits'](bounds, '__live__', 1, 'replay')
        self.assertEqual(maximum, 2)
        self.ctx.set('replay-export')
        download = self.callbacks['export_range'](1, '__live__', bounds)
        self.assertTrue(download['path'].endswith('.zip'))

    def test_nur_sichtbarer_tab_pollt(self):
        self.assertEqual(self.callbacks['tab_intervals']('telemetry', False), (True, True, False, True, True))
        self.assertEqual(self.callbacks['tab_intervals']('telemetry', True), (True,) * 5)
        per_tick = Counter(i.component_id for cb in self.callbacks.values() for i in cb.inputs
                           if i.component_property == 'n_intervals')
        self.assertEqual(per_tick['refresh'], 1)         # nur die globale Statusleiste
        self.assertLessEqual(max(per_tick[f'tick-{t}'] for t in ('drive', 'pipeline', 'telemetry', 'record', 'replay')), 3)

    def test_ereignisliste(self):
        self.app_state.health.record('TEST_EVENT', 'Prüfung', 3)
        status, details, items, preview = self.callbacks['recording_view'](1, None, 'record')
        texts = [c.children for item in items for c in item.walk() if isinstance(c.children, str)]
        self.assertIn('TEST_EVENT', texts)


if __name__ == '__main__':
    unittest.main()
