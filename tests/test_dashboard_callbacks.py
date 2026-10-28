"""Hardware- und browserfreie Tests des Control Centers (Dash-Attrappe).

Geprueft werden vollstaendige Callback-Registrierung, Komponenten-IDs,
doppelte Outputs, Callback-Signaturen und das Verhalten zentraler Callbacks
mit einer Fake-Anwendung. Es wird kein Motor angesteuert.
"""
import json
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


class DashboardSession:
    """Haelt Hardware-/Dash-Attrappen und die fixierte Pipeline-Konfiguration; ``__exit__`` raeumt alles auf."""

    def __init__(self, context, pin):
        self.context, self.pin = context, pin

    def __exit__(self, *exc):
        self.pin.stop()
        return self.context.__exit__(*exc)


def layout_of(app):
    """Layout-Baum; die Anwendung setzt eine Layout-Funktion (jeder Seitenaufruf baut neu)."""
    return app.layout() if callable(app.layout) else app.layout


def id_key(component_id):
    """Vergleichbarer Schluessel fuer String- und Pattern-Matching-IDs (dict)."""
    return json.dumps(component_id, sort_keys=True, default=repr) if isinstance(component_id, dict) else component_id


def studio_controls(processor='copilot', values=None, switches=None):
    """(Regler-IDs, Regler-Werte, Schalter-IDs, Schalter-Werte) wie Dash sie liefert, aus der Prozessorbeschreibung.

    Standardwerte aus ``pipeline_config.DEFAULT``; ``values``/``switches`` ueberschreiben einzelne Eintraege.
    """
    import pipeline_config
    from processor_registry import processor_stages
    values, switches = dict(values or {}), dict(switches or {})
    stages = processor_stages(processor)
    control_ids = [{'type': 'pipeline-control', 'key': c.key} for s in stages for c in s.controls]
    control_values = [values.get(i['key'], pipeline_config.DEFAULT[i['key']]) for i in control_ids]
    switch_ids = [{'type': 'pipeline-switch', 'key': s.switch} for s in stages if s.switch]
    switch_values = [['on'] if switches.get(i['key'], pipeline_config.DEFAULT['enabled'].get(i['key'], True))
                     else [] for i in switch_ids]
    return control_ids, control_values, switch_ids, switch_values


def stage_image_ids(processor='copilot'):
    from processor_registry import processor_stages
    return [{'type': 'pipeline-stage-image', 'stage': s.key} for s in processor_stages(processor)]


def load_dashboard():
    """Importiert Camp2CodeDashboard mit Dash-Attrappe (ohne Hardware, Pipeline-Konfiguration = Standard)."""
    extra = dash_stub.modules()
    context = hardware_stubs.installed(extra=extra)
    context.__enter__()
    pin = hardware_stubs.pin_pipeline_config()
    module = hardware_stubs.fresh_import('Camp2CodeDashboard')
    return module, DashboardSession(context, pin), extra['dash'].ctx


class DashboardStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.app_state = FakeApplication()
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
        return [c.id for c in layout_of(self.app).walk() if c.id is not None]

    def test_ids_eindeutig(self):
        duplicates = [i for i, n in Counter(id_key(i) for i in self.layout_ids()).items() if n > 1]
        self.assertEqual(duplicates, [])

    def test_alle_abhaengigkeiten_im_layout(self):
        """String-IDs muessen im Layout stehen; Pattern-IDs (``ALL``) brauchen mindestens eine passende Komponente."""
        ids = self.layout_ids()
        plain = {i for i in ids if isinstance(i, str)}
        patterns = [i for i in ids if isinstance(i, dict)]

        def present(component_id):
            if isinstance(component_id, str):
                return component_id in plain
            fixed = {k: v for k, v in component_id.items() if not isinstance(v, dash_stub._Wildcard)}
            return any(all(i.get(k) == v for k, v in fixed.items()) for i in patterns)
        missing = {(name, id_key(d.component_id)) for name, cb in self.callbacks.items()
                   for d in cb.outputs + cb.inputs + cb.states if not present(d.component_id)}
        self.assertEqual(missing, set())

    def test_keine_doppelten_outputs(self):
        outputs = Counter((id_key(d.component_id), d.component_property)
                          for cb in self.callbacks.values() for d in cb.outputs)
        self.assertEqual([k for k, n in outputs.items() if n > 1], [])

    def test_kein_output_als_eigener_input(self):
        for name, cb in self.callbacks.items():
            outputs = {(id_key(o.component_id), o.component_property) for o in cb.outputs}
            inputs = {(id_key(i.component_id), i.component_property) for i in cb.inputs}
            self.assertEqual(outputs & inputs, set(), name)

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
        expected = {'rebuild_stage_cards', 'tab_intervals', 'handle_actions', 'global_status', 'recovery_pulse', 'cockpit_view', 'configure',
                    'preset_actions', 'freeze', 'inspect', 'pipeline_view', 'ab_calibrate', 'telemetry_view',
                    'recording_view', 'refresh_sessions', 'replay_event_options', 'seek', 'replay_limits',
                    'replay_toggle', 'replay_play_label', 'replay_render', 'export_range', 'replay_chart', 'replay_to_studio', 'sim_view_mode', 'sim_run', 'sim_playback', 'sim_render', 'kpi_bar', 'settings_apply', 'settings_fill', 'rt_settings'}  # PAKET-H1B  # PAKET-H1  # PAKET-G  # PAKET-F  # PAKET-C
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
        hidden = self.callbacks['pipeline_view'](1, 'overlay', None, 'drive', 0, stage_image_ids(), 'copilot', None)
        self.assertTrue(all(v is no or (isinstance(v, list) and all(x is no for x in v)) for v in hidden))

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
        ids = stage_image_ids()
        names = [i['stage'] for i in ids]
        images, infos, inspector, title, label, _lock, key = self.callbacks['pipeline_view'](
            1, 'roi', None, 'pipeline', 0, ids, 'copilot', None)
        self.assertEqual(len(images), len(ids))
        self.assertEqual(inspector, images[names.index('roi')])       # Inspector = Karte, nur einmal kodiert
        self.assertEqual(title, 'ROI')
        self.assertEqual(infos[names.index('roi')], 'Verfügbar')
        self.assertIsNone(images[names.index('hsv')])                  # Stufe nicht veroeffentlicht
        frozen = self.callbacks['pipeline_view'](2, 'roi', 11, 'pipeline', 0, ids, 'copilot', key)
        self.assertEqual(frozen[0], [self.module.no_update] * len(ids))

    def test_pipeline_stufen_folgen_dem_prozessor(self):
        """Dieselbe Funktion bedient Prozessoren mit anderen Stufen (Karten kommen aus der Beschreibung)."""
        self.app_state.publish(12)
        ids = stage_image_ids('opus')
        images, infos, inspector, title, *_ = self.callbacks['pipeline_view'](
            1, 'blackhat', None, 'pipeline', 0, ids, 'opus', None)
        self.assertEqual(len(images), len(ids))
        self.assertEqual(title, 'Blackhat (dunkle Linie)')
        self.assertIsNone(inspector)                                    # Fake-Frame hat keine Blackhat-Stufe
        names = [i['stage'] for i in ids]
        self.assertTrue(images[names.index('raw')].startswith('data:image'))
        # Inspector auf eine Stufe, die der Prozessor nicht hat: Rueckfall auf die Linien
        fallback = self.callbacks['pipeline_view'](2, 'hsv', None, 'pipeline', 0, ids, 'opus', None)
        self.assertEqual(fallback[3], 'Linien')

    def test_konfiguration_wird_angewendet(self):
        self.ctx.set('cfg-processor', 'value')
        control_ids, control_values, switch_ids, switch_values = studio_controls(
            'copilot', {'crop': [0.1, 0.9]}, {'blur': False, 'canny': False})
        message = self.callbacks['configure'](None, None, 'copilot', control_values, switch_values,
                                              control_ids, switch_ids)
        self.assertIn('übernommen', message)
        settings = self.app_state.calls[-1][1]
        self.assertEqual(settings['crop'], [0.1, 0.9])
        self.assertEqual(settings['enabled'], {'roi': True, 'mask': True, 'blur': False, 'morph': True,
                                               'canny': False, 'lanes': True})

    def test_speichern_und_nicht_sichtbare_regler_bleiben_erhalten(self):
        """Modus Opus zeigt keine HSV-Regler; deren Werte gehen beim Uebernehmen nicht verloren."""
        self.ctx.set('cfg-save')
        control_ids, control_values, switch_ids, switch_values = studio_controls('opus', {'blur': 5})
        self.assertNotIn('hue', [i['key'] for i in control_ids])
        message = self.callbacks['configure'](None, 1, 'opus', control_values, switch_values,
                                              control_ids, switch_ids)
        self.assertIn('gespeichert', message)
        action, settings = self.app_state.calls[-1]
        self.assertEqual(action, 'save_pipeline')
        self.assertEqual((settings['processor'], settings['blur']), ('opus', 5))
        self.assertEqual(settings['hue'], __import__('pipeline_config').DEFAULT['hue'])
        self.assertTrue({'clahe', 'blackhat', 'threshold'} <= set(settings['enabled']))

    def test_karten_werden_aus_dem_prozessor_aufgebaut(self):
        from processor_registry import processor_options, processor_stages
        self.ctx.set('cfg-processor', 'value')
        for option in processor_options(live_only=True):
            key = option['value']
            control_ids, control_values, switch_ids, switch_values = studio_controls('copilot')
            cards, version = self.callbacks['rebuild_stage_cards'](
                key, None, control_ids, control_values, switch_ids, switch_values)
            components = list(cards.walk())
            stages = processor_stages(key)
            ids = [c.id for c in components if isinstance(c.id, dict)]
            for kind in ('pipeline-stage-image', 'pipeline-stage-info', 'pipeline-inspect'):
                self.assertEqual([i['stage'] for i in ids if i['type'] == kind], [s.key for s in stages], key)
            self.assertEqual([i['key'] for i in ids if i['type'] == 'pipeline-control'],
                             [c.key for s in stages for c in s.controls], key)
            self.assertEqual([i['key'] for i in ids if i['type'] == 'pipeline-switch'],
                             [s.switch for s in stages if s.switch], key)
            self.assertGreater(version, 0)
        _, first = self.callbacks['rebuild_stage_cards']('opus', None, [], [], [], [])
        _, second = self.callbacks['rebuild_stage_cards']('opus', None, [], [], [], [])
        self.assertNotEqual(first, second)           # neue Version -> Anzeige rendert die neuen Bilder

    def test_neue_karten_uebernehmen_aktuelle_reglerwerte(self):
        self.ctx.set('cfg-processor', 'value')
        control_ids, control_values, switch_ids, switch_values = studio_controls(
            'copilot', {'blur': 9, 'canny': [10, 20]}, {'morph': False})
        cards, _ = self.callbacks['rebuild_stage_cards']('opus', None, control_ids, control_values,
                                                         switch_ids, switch_values)
        values = {c.id['key']: c.props.get('value') for c in cards.walk()
                  if isinstance(c.id, dict) and c.id['type'] == 'pipeline-control'}
        switches = {c.id['key']: c.props.get('value') for c in cards.walk()
                    if isinstance(c.id, dict) and c.id['type'] == 'pipeline-switch'}
        self.assertEqual((values['blur'], values['canny']), (9, [10, 20]))
        self.assertEqual(switches['morph'], [])
        self.assertEqual(switches['clahe'], ['on'])

    def test_preset_laden_baut_die_karten_mit_den_preset_werten(self):
        import pipeline_config
        preset = pipeline_config.load()
        preset.update(processor='opus', blur=7)
        with patch.object(pipeline_config, 'list_presets', return_value=['p1']), \
                patch.object(pipeline_config, 'load_preset', return_value=preset):
            self.ctx.set('preset-load')
            processor, data, options, message = self.callbacks['preset_actions'](
                1, None, 'pipeline', 'p1', None, 'copilot', *studio_controls('copilot'))
        self.assertEqual(processor, 'opus')
        self.assertIn('Geladen', message)
        self.assertEqual([o['value'] for o in options], ['__active__', 'p1'])
        self.ctx.set('pipeline-preset-data', 'data')
        cards, _ = self.callbacks['rebuild_stage_cards']('opus', data, [], [], [], [])
        values = {c.id['key']: c.props.get('value') for c in cards.walk()
                  if isinstance(c.id, dict) and c.id['type'] == 'pipeline-control'}
        self.assertEqual(values['blur'], 7)
        self.app_state._running = True
        try:
            with patch.object(pipeline_config, 'load_preset', return_value=preset):
                self.ctx.set('preset-load')
                refused = self.callbacks['preset_actions'](1, None, 'pipeline', 'p1', None, 'copilot',
                                                           *studio_controls('copilot'))
            self.assertEqual(refused[3], 'Laden nur im Stillstand')
            self.assertIs(refused[0], self.module.no_update)
        finally:
            self.app_state._running = False

    def test_inspector_ignoriert_neu_erzeugte_karten(self):
        no = self.module.no_update
        self.ctx.set({'type': 'pipeline-inspect', 'stage': 'edges'}, 'n_clicks', 1)
        self.assertEqual(self.callbacks['inspect']([None, 1]), 'edges')
        self.ctx.set({'type': 'pipeline-inspect', 'stage': 'edges'}, 'n_clicks', None)   # frisch gerendert
        self.assertIs(self.callbacks['inspect']([None, None]), no)

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
        self.assertEqual(per_tick['refresh'], 2)         # Statusleiste + Kennzahlen (PAKET-G)
        self.assertLessEqual(max(per_tick[f'tick-{t}'] for t in ('drive', 'pipeline', 'telemetry', 'record', 'replay')), 3)

    def test_ereignisliste(self):
        self.app_state.health.record('TEST_EVENT', 'Prüfung', 3)
        status, details, items, preview = self.callbacks['recording_view'](1, None, 'record')
        texts = [c.children for item in items for c in item.walk() if isinstance(c.children, str)]
        self.assertIn('TEST_EVENT', texts)


if __name__ == '__main__':
    unittest.main()

