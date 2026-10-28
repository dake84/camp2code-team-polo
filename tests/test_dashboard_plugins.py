"""Plug-in-Architektur des Control Centers: Tab-Klassen, dynamische Pipeline-Stufen, Modus Opus.

Prueft (ohne Hardware, ohne Browser):
    * Prozessoren beschreiben ihre Stufen selbst (``dashboard_stages``) - passend zu dem, was sie liefern
    * Modus Opus erkennt dunkle Linien, wo die HSV-Farbmaske nichts findet
    * Tabs sind einsteckbar; jeder Tab besitzt seine Callbacks; das Paket ``dashboard`` importiert Dash nie
    * die dynamischen Pattern-Matching-Callbacks laufen mit der echten Dash-Bibliothek (Flask-Testclient)
"""
import ast
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from test_dashboard_callbacks import (FakeApplication, layout_of, load_dashboard, stage_image_ids,  # noqa: E402
                                      studio_controls)

ROOT = Path(__file__).resolve().parents[1]
HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
PARAMS = ((100, 130), (80, 255), (40, 255), (0.0, 1.0), 3, 5, (50, 100),
          {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True})
LIVE_PROCESSORS = ('legacy', 'copilot', 'opus')


def dark_line_frame(shift=0):
    """Helle Fahrbahn mit zwei SCHWARZEN Linien und einem gruenen Reflex (HSV-Blau findet hier nichts)."""
    image = np.full((240, 320, 3), (120, 140, 170), np.uint8)
    noise = np.random.default_rng(1).normal(0, 6, image.shape)
    image = np.clip(image + noise, 0, 255).astype(np.uint8)
    cv2.line(image, (60 + shift, 239), (130 + shift, 0), (40, 35, 45), 9)
    cv2.line(image, (260 + shift, 239), (190 + shift, 0), (40, 35, 45), 9)
    cv2.circle(image, (160, 200), 30, (40, 220, 60), -1)
    return image


# ------------------------------------------------------------------ Prozessor-Beschreibung
class StageDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        import pipeline_config
        import pipeline_processors
        import processor_registry
        self.config, self.pp, self.reg = pipeline_config, pipeline_processors, processor_registry

    def tearDown(self):
        self.stubs.__exit__(None, None, None)

    def test_beschreibung_passt_zu_den_gelieferten_stufen(self):
        """Jede beschriebene Stufe (ausser raw/overlay vom Dashboard) liefert der Prozessor auch - und umgekehrt."""
        for key in ('copilot', 'opus'):
            described = {s.key for s in self.reg.processor_stages(key)} - set(self.pp.DASHBOARD_STAGES)
            produced = set(self.reg.create_processor(key).process(hardware_stubs.test_image(), PARAMS).stages)
            self.assertEqual(described, produced, key)

    def test_roh_und_linien_kommen_vom_dashboard(self):
        for key in LIVE_PROCESSORS:
            stages = self.reg.processor_stages(key)
            self.assertEqual((stages[0].key, stages[-1].key), ('raw', 'overlay'), key)
            self.assertIsNone(stages[0].switch)
            self.assertEqual(stages[-1].switch, 'lanes')

    def test_regler_sind_schluessel_der_studio_konfiguration(self):
        """Regler schreiben in die Studio-Konfiguration; unbekannte Schluessel wuerden verloren gehen."""
        for key in LIVE_PROCESSORS:
            for stage in self.reg.processor_stages(key):
                for control in stage.controls:
                    self.assertIn(control.key, self.config.DEFAULT, (key, control.key))
                    self.assertLess(control.minimum, control.maximum)

    def test_jeder_regler_hat_einen_hilfetext(self):
        from help_texts import HELP
        for key in LIVE_PROCESSORS:
            for stage in self.reg.processor_stages(key):
                for control in stage.controls:
                    self.assertIn(control.help_key or control.key, HELP, (key, control.key))
        opus_blur = next(c for s in self.reg.processor_stages('opus') for c in s.controls if c.key == 'blur')
        self.assertEqual(opus_blur.help_key, 'blurgauss')

    def test_unterschiedliche_prozessoren_unterschiedliche_stufen(self):
        legacy = [s.key for s in self.reg.processor_stages('copilot')]
        opus = [s.key for s in self.reg.processor_stages('opus')]
        self.assertIn('hsv', legacy)
        self.assertNotIn('hsv', opus)
        self.assertEqual(opus[2:7], ['gray', 'clahe', 'blur', 'blackhat', 'threshold'])
        self.assertEqual([s.switch for s in self.reg.processor_stages('opus') if s.switch],
                         ['roi', 'clahe', 'blur', 'blackhat', 'threshold', 'morph', 'canny', 'lanes'])

    def test_doppelte_schluessel_werden_abgelehnt(self):
        pp = self.pp
        with self.assertRaises(ValueError):
            pp.validate_stages([pp.PipelineStage('a', 'A'), pp.PipelineStage('a', 'B')])
        with self.assertRaises(ValueError):
            control = pp.PipelineControl('blur', 'Blur', 1, 15)
            pp.validate_stages([pp.PipelineStage('a', 'A', None, (control,)), pp.PipelineStage('b', 'B', None, (control,))])
        with self.assertRaises(ValueError):
            pp.validate_stages([pp.PipelineStage('a', 'A', 'x'), pp.PipelineStage('b', 'B', 'x')])

    def test_prozessor_ohne_beschreibung_zeigt_nur_rohbild_und_linien(self):
        self.reg.register('plain_test', 'Plain', __name__, 'PlainProcessor', live=False)
        try:
            self.assertEqual([s.key for s in self.reg.processor_stages('plain_test')], ['raw', 'overlay'])
        finally:
            self.reg._SPECS.pop('plain_test')
        with self.assertRaises(ValueError):
            self.reg.processor_stages('gibt_es_nicht')

    def test_opus_ist_fahrfreigegeben_und_im_verzeichnis(self):
        self.assertTrue(self.reg.is_live_processor('opus'))
        self.assertEqual(self.reg.key_for_class('OpusLaneProcessor'), 'opus')
        self.assertIn('opus', [o['value'] for o in self.reg.processor_options(live_only=True)])


class PlainProcessor:
    """Prozessor ohne ``dashboard_stages`` (Test fuer den Rueckfall)."""


# ------------------------------------------------------------------ Modus Opus
class OpusTests(unittest.TestCase):
    def setUp(self):
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        import processor_registry
        self.reg = processor_registry

    def tearDown(self):
        self.stubs.__exit__(None, None, None)

    def test_dunkle_linien_werden_gefunden_wo_hsv_nichts_findet(self):
        frame = dark_line_frame()
        legacy = self.reg.create_processor('legacy').process(frame, PARAMS)
        opus = self.reg.create_processor('opus').process(frame, PARAMS)
        self.assertEqual(len(legacy.lanes), 0)             # HSV 100-130 passt nicht auf schwarz
        self.assertEqual(len(opus.lanes), 2)
        (m0, _), (m1, _) = opus.lanes
        self.assertLess(m0 * m1, 0)                        # eine Linie steigt, die andere faellt
        self.assertEqual(set(opus.stages['blackhat'].shape), {opus.stages['roi'].shape[0], opus.stages['roi'].shape[1]})

    def test_rohbild_bleibt_unveraendert_und_stufen_sind_einkanalig(self):
        frame = dark_line_frame()
        before = frame.copy()
        result = self.reg.create_processor('opus').process(frame, PARAMS)
        np.testing.assert_array_equal(frame, before)
        for name in ('gray', 'clahe', 'blur', 'blackhat', 'threshold', 'morph', 'edges'):
            self.assertEqual(result.stages[name].ndim, 2, name)
        self.assertTrue(set(np.unique(result.stages['threshold'])) <= {0, 255})

    def test_jeder_schalter_laesst_die_pipeline_lauffaehig(self):
        frame = dark_line_frame()
        for switch in ('roi', 'clahe', 'blur', 'blackhat', 'threshold', 'morph', 'canny', 'lanes'):
            enabled = {**PARAMS[7], switch: False}
            result = self.reg.create_processor('opus').process(frame, (*PARAMS[:7], enabled))
            self.assertEqual(len(result.stages), 8, switch)
        no_lanes = self.reg.create_processor('opus').process(frame, (*PARAMS[:7], {**PARAMS[7], 'lanes': False}))
        self.assertEqual(no_lanes.lanes, ())

    def test_gerade_blur_kerngroesse_und_ungueltiger_ausschnitt(self):
        frame = dark_line_frame()
        result = self.reg.create_processor('opus').process(frame, (*PARAMS[:4], 4, 6, *PARAMS[6:]))   # gerade Kerne
        self.assertEqual(len(result.lanes), 2)
        with self.assertRaises(ValueError):
            self.reg.create_processor('opus').process(frame, ((1, 2), (1, 2), (1, 2), (0.9, 0.2), 3, 3))
        with self.assertRaises(ValueError):
            self.reg.create_processor('opus').process(np.empty(0), PARAMS)


# ------------------------------------------------------------------ Tabs als Plug-ins
class MinimalTab:
    """Wird in den Tests als eigener Tab eingesteckt (Klasse reicht, kein Eingriff in Shell oder Paket)."""


class PluginTests(unittest.TestCase):
    TAB_CALLBACKS = {
        'DrivingTab': {'cockpit_view'},
        'PipelineTab': {'rebuild_stage_cards', 'configure', 'preset_actions', 'freeze', 'inspect',
                        'pipeline_view', 'ab_calibrate'},
        'TelemetryTab': {'telemetry_view'},
        'RecordingTab': {'recording_view'},
        'ReplayTab': {'refresh_sessions', 'replay_event_options', 'seek', 'replay_limits', 'replay_toggle',
                      'replay_play_label', 'replay_render', 'replay_chart', 'replay_to_studio', 'export_range'},
        'SettingsTab': {'settings_apply', 'settings_fill'},
        'Shell': {'kpi_bar', 'handle_actions', 'tab_intervals', 'global_status', 'recovery_pulse'},
    }

    @classmethod
    def setUpClass(cls):
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
        cls.app = cls.module.create_app(cls.fake)

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def test_sechs_tabs_in_dieser_reihenfolge(self):
        self.assertEqual([t.tab_id for t in self.app.c2c_tabs],
                         ['drive', 'pipeline', 'telemetry', 'record', 'replay', 'settings'])
        tabs = [c for c in layout_of(self.app).walk() if type(c).__name__ == 'Tab']
        self.assertEqual([c.props['tab_id'] for c in tabs], [t.tab_id for t in self.app.c2c_tabs])
        self.assertEqual([c.props['label'] for c in tabs],
                         ['Driving Cockpit', 'Pipeline Studio', 'Telemetrie & Performance',
                          'Recording & Events', 'Replay Lab', 'Settings'])

    def test_jeder_tab_besitzt_seine_callbacks(self):
        owners = {}
        for name, callback in self.app.callbacks.items():
            owner = callback.func.__qualname__.split('.')[0]
            if name.startswith('sim_'):
                owner = 'SimLab'
            owners.setdefault(owner, set()).add(name)
        self.assertEqual({k: v for k, v in owners.items() if k != 'SimLab'}, self.TAB_CALLBACKS)
        self.assertEqual(owners['SimLab'], {'sim_view_mode', 'sim_run', 'sim_playback', 'sim_render'})

    def test_nur_tabs_mit_takt_haben_ein_intervall(self):
        ticks = {c.id for c in layout_of(self.app).walk() if isinstance(c.id, str) and c.id.startswith('tick-')}
        self.assertEqual(ticks, {'tick-drive', 'tick-pipeline', 'tick-telemetry', 'tick-record', 'tick-replay'})
        self.assertIsNone(self.app.c2c_tabs[-1].interval_component())
        starts = {c.id: c.props['disabled'] for c in layout_of(self.app).walk()
                  if isinstance(c.id, str) and c.id.startswith('tick-')}
        self.assertEqual([k for k, v in starts.items() if not v], ['tick-drive'])      # nur der Starttab pollt

    def test_eigener_tab_wird_eingesteckt(self):
        from dashboard.tabs import DEFAULT_TABS, DashboardTab

        class EchoTab(DashboardTab):
            tab_id = 'echo'
            label = 'Echo'

            def layout(self):
                return self.ctx.html.Div([self.ctx.html.Div(id='echo-out'), self.ctx.html.Button(id='echo-in')])

            def register_callbacks(self, app):
                c = self.ctx

                @app.callback(c.Output('echo-out', 'children'), c.Input('echo-in', 'n_clicks'),
                              c.Input(self.tick_id, 'n_intervals'))
                def echo(clicks, _tick):
                    return f'{clicks}'

        app = self.module.create_app(FakeApplication(), tabs=(*DEFAULT_TABS, EchoTab))
        self.assertEqual(app.c2c_tabs[-1].tab_id, 'echo')
        self.assertIn('echo', app.callbacks)
        self.assertEqual(len(app.callbacks['tab_intervals'].outputs), 6)              # 5 Standardtakte + Echo
        self.assertEqual(app.callbacks['echo'](3, 0), '3')

    def test_doppelte_tab_ids_werden_abgelehnt(self):
        from dashboard.tabs import DEFAULT_TABS
        with self.assertRaises(ValueError):
            self.module.create_app(FakeApplication(), tabs=(DEFAULT_TABS[0], DEFAULT_TABS[0]))

    def test_basisklasse_ist_abstrakt(self):
        from dashboard.tabs import DashboardTab
        with self.assertRaises(TypeError):
            DashboardTab(None)

    def test_paket_dashboard_importiert_dash_nie(self):
        """Die Dash-Bausteine kommen ueber den Kontext; so laeuft dasselbe Paket mit echtem Dash und Attrappe."""
        forbidden = ('dash', 'dash_bootstrap_components', 'plotly')
        for path in sorted((ROOT / 'dashboard').rglob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imported |= {(n.module or '').split('.')[0] for n in ast.walk(tree)
                         if isinstance(n, ast.ImportFrom) and not n.level}
            self.assertFalse(imported & set(forbidden), path.name)

    def test_startdatei_enthaelt_keine_callbacks_und_keine_layouts(self):
        source = (ROOT / 'Camp2CodeDashboard.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        self.assertNotIn('.callback', source)
        self.assertLess(len(source.splitlines()), 130)
        functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(functions, {'create_app', '_port_from_argv', 'main'})

    def test_tabs_greifen_nicht_aufeinander_zu(self):
        """Tabs importieren einander nicht (gemeinsames nur ueber Kontext/Helfer)."""
        for path in sorted((ROOT / 'dashboard' / 'tabs').glob('*.py')):
            if path.name in ('__init__.py', 'base.py'):
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            relative = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 1}
            self.assertLessEqual(relative, {'base'}, path.name)


def configure_args(processor='copilot', values=None, switches=None):
    """Argumente des Callbacks ``configure`` (Reihenfolge wie die Abhaengigkeiten)."""
    ids, vals, switch_ids, switch_vals = studio_controls(processor, values, switches)
    return None, None, processor, vals, switch_vals, ids, switch_ids


class StudioStateTests(unittest.TestCase):
    """Das Studio zeigt und wendet nur an, was wirklich wirksam bzw. geaendert ist."""

    def setUp(self):
        self.module, self.context, self.ctx = load_dashboard()
        self.fake = FakeApplication()
        self.app = self.module.create_app(self.fake)
        self.cb = self.app.callbacks

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def slider_values(self):
        return {c.id['key']: c.props.get('value') for c in layout_of(self.app).walk()
                if isinstance(c.id, dict) and c.id['type'] == 'pipeline-control'}

    def test_seitenaufruf_zeigt_die_aktuell_wirksamen_einstellungen(self):
        """Das Layout wird je Seitenaufruf neu gebaut: Live-Aenderungen gehen beim Neuladen nicht verloren."""
        self.assertEqual(self.slider_values()['crop'], [0.1, 0.9])                  # Standardwerte
        self.fake.sensor.processing_parameters = ((100, 130), (80, 255), (40, 255), (0.15, 0.55), 7, 5, (50, 100),
                                                  {'roi': True, 'blur': False})
        self.fake.sensor.processor_name = 'OpusLaneProcessor'
        values = self.slider_values()
        self.assertEqual((values['crop'], values['blur']), ([0.15, 0.55], 7))
        self.assertNotIn('hue', values)                                             # Modus Opus: keine HSV-Regler
        switches = {c.id['key']: c.props.get('value') for c in layout_of(self.app).walk()
                    if isinstance(c.id, dict) and c.id['type'] == 'pipeline-switch'}
        self.assertEqual((switches['blur'], switches['roi']), ([], ['on']))
        dropdown = next(c for c in layout_of(self.app).walk() if c.id == 'cfg-processor')
        self.assertEqual(dropdown.props['value'], 'opus')

    def test_unveraenderte_werte_werden_nicht_erneut_angewendet(self):
        """Dash ruft Wildcard-Callbacks auch beim Laden und beim Kartenaufbau auf; das darf nichts anwenden."""
        args = configure_args()
        self.ctx.set({'type': 'pipeline-control', 'key': 'crop'}, 'value', [0.1, 0.9])
        self.assertIs(self.cb['configure'](*args), self.module.no_update)
        self.assertEqual([c for c in self.fake.calls if c[0] == 'set_pipeline'], [])
        self.ctx.set('cfg-apply')                       # ausdrueckliches "Uebernehmen" wendet immer an
        self.assertIn('übernommen', self.cb['configure'](*args))
        self.assertEqual(self.fake.calls[-1][0], 'set_pipeline')
        self.ctx.set({'type': 'pipeline-control', 'key': 'blur'}, 'value', 9)
        self.assertIn('übernommen', self.cb['configure'](*configure_args(values={'blur': 9})))
        self.assertEqual(self.fake.calls[-1][1]['blur'], 9)

    def test_ansicht_waehrend_der_fahrt_meldet_beim_laden_nichts(self):
        self.fake._running = True
        try:
            self.ctx.set({'type': 'pipeline-control', 'key': 'crop'}, 'value', [0.1, 0.9])
            self.assertIs(self.cb['configure'](*configure_args()), self.module.no_update)   # keine Meldung beim Laden
        finally:
            self.fake._running = False


class AssetTests(unittest.TestCase):
    def test_visibility_js_wartet_auf_das_layout(self):
        """'pageshow' kann vor dem Layout eintreffen; set_props darf dann nicht fehlschlagen."""
        source = (ROOT / 'assets' / 'visibility.js').read_text(encoding='utf-8')
        self.assertIn("getElementById('main-tabs')", source)
        self.assertIn('try {', source)
        self.assertIn("addEventListener('pageshow', applyWhenReady)", source)


# ------------------------------------------------------------------ Dynamisches Studio mit echtem Dash
def wildcard(ids, prop, values=None):
    """Wildcard-Eingang wie ihn der Dash-Client schickt: Liste der konkreten Komponenten."""
    return [{'id': i, 'property': prop, **({} if values is None else {'value': v})}
            for i, v in zip(ids, values if values is not None else ids)]


@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashDynamicStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        cls.pin = hardware_stubs.pin_pipeline_config()
        cls.module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = FakeApplication()
        cls.fake.publish(5)
        cls.app = cls.module.create_app(cls.fake)
        cls.client = cls.app.server.test_client()
        deps = json.loads(cls.client.get('/_dash-dependencies').get_data(as_text=True))
        cls.output = {name: next(d['output'] for d in deps if marker in d['output'])
                      for name, marker in (('view', 'inspect-image.src'), ('rebuild', 'pipeline-stage-cards.children'),
                                           ('configure', 'cfg-message.children'),
                                           ('inspect', 'inspected-stage.data'), ('ab', 'ab-status.children'))}

    @classmethod
    def tearDownClass(cls):
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)

    def post(self, which, outputs, inputs, state, changed):
        body = {'output': self.output[which], 'outputs': outputs, 'inputs': inputs, 'state': state,
                'changedPropIds': changed}
        return self.client.post('/_dash-update-component', json=body)

    def walk(self, node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from self.walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from self.walk(value)

    def test_layout_enthaelt_karten_des_gespeicherten_prozessors(self):
        layout = self.client.get('/_dash-layout').get_json()
        images = [n['props']['id']['stage'] for n in self.walk(layout) if isinstance(n.get('props', {}).get('id'), dict)
                  and n['props']['id'].get('type') == 'pipeline-stage-image']
        self.assertEqual(images, ['raw', 'roi', 'hsv', 'mask', 'blur', 'binary', 'morph', 'edges', 'overlay'])

    def test_anzeige_der_stufen_ueber_http(self):
        for processor in ('copilot', 'opus'):
            ids = stage_image_ids(processor)
            infos = [{'type': 'pipeline-stage-info', 'stage': i['stage']} for i in ids]
            outputs = [wildcard(ids, 'src', None), wildcard(infos, 'children', None),
                       {'id': 'inspect-image', 'property': 'src'}, {'id': 'inspect-title', 'property': 'children'},
                       {'id': 'pipeline-frame-label', 'property': 'children'},
                       {'id': 'pipeline-lock', 'property': 'children'}, {'id': 'pipeline-rendered', 'property': 'data'}]
            inputs = [{'id': 'tick-pipeline', 'property': 'n_intervals', 'value': 1},
                      {'id': 'inspected-stage', 'property': 'data', 'value': 'roi'},
                      {'id': 'pipeline-frozen', 'property': 'data', 'value': None},
                      {'id': 'main-tabs', 'property': 'active_tab', 'value': 'pipeline'},
                      {'id': 'pipeline-card-version', 'property': 'data', 'value': 0}]
            state = [wildcard(ids, 'id', ids), {'id': 'cfg-processor', 'property': 'value', 'value': processor},
                     {'id': 'pipeline-rendered', 'property': 'data', 'value': None}]
            response = self.post('view', outputs, inputs, state, ['tick-pipeline.n_intervals'])
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
            payload = response.get_json()['response']
            for item in ids:
                key = json.dumps(item, sort_keys=True, separators=(',', ':'))
                self.assertIn(key, payload, processor)
            raw = payload[json.dumps(ids[0], sort_keys=True, separators=(',', ':'))]['src']
            self.assertTrue(raw.startswith('data:image'))
            self.assertTrue(payload['pipeline-frame-label']['children'].startswith('LIVE · Frame 5'))

    def test_prozessorwechsel_baut_karten_ueber_http_neu(self):
        control_ids, control_values, switch_ids, switch_values = studio_controls('copilot', {'blur': 9})
        inputs = [{'id': 'cfg-processor', 'property': 'value', 'value': 'opus'},
                  {'id': 'pipeline-preset-data', 'property': 'data', 'value': None}]
        state = [wildcard(control_ids, 'id', control_ids), wildcard(control_ids, 'value', control_values),
                 wildcard(switch_ids, 'id', switch_ids), wildcard(switch_ids, 'value', switch_values)]
        outputs = [{'id': 'pipeline-stage-cards', 'property': 'children'},
                   {'id': 'pipeline-card-version', 'property': 'data'}]
        response = self.post('rebuild', outputs, inputs, state, ['cfg-processor.value'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        payload = response.get_json()['response']
        ids = [n['props']['id'] for n in self.walk(payload['pipeline-stage-cards']['children'])
               if isinstance(n.get('props', {}).get('id'), dict)]
        stages = [i['stage'] for i in ids if i['type'] == 'pipeline-stage-image']
        self.assertEqual(stages, ['raw', 'roi', 'gray', 'clahe', 'blur', 'blackhat', 'threshold', 'morph',
                                  'edges', 'overlay'])
        self.assertNotIn('hue', [i.get('key') for i in ids])
        blur = next(n['props'] for n in self.walk(payload['pipeline-stage-cards']['children'])
                    if n.get('props', {}).get('id') == {'type': 'pipeline-control', 'key': 'blur'})
        self.assertEqual(blur['value'], 9)                  # bisheriger Reglerwert bleibt beim Wechsel erhalten
        self.assertGreater(payload['pipeline-card-version']['data'], 0)

    def test_uebernehmen_ueber_http(self):
        control_ids, control_values, switch_ids, switch_values = studio_controls('opus', {'blur': 5})
        inputs = [{'id': 'cfg-apply', 'property': 'n_clicks', 'value': None},
                  {'id': 'cfg-save', 'property': 'n_clicks', 'value': None},
                  {'id': 'cfg-processor', 'property': 'value', 'value': 'opus'},
                  wildcard(control_ids, 'value', control_values), wildcard(switch_ids, 'value', switch_values)]
        state = [wildcard(control_ids, 'id', control_ids), wildcard(switch_ids, 'id', switch_ids)]
        response = self.post('configure', {'id': 'cfg-message', 'property': 'children'}, inputs, state,
                             ['cfg-processor.value'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertIn('Live übernommen', response.get_data(as_text=True))
        action, settings = self.fake.calls[-1]
        self.assertEqual((action, settings['processor'], settings['blur']), ('set_pipeline', 'opus', 5))

    def test_inspector_ueber_http(self):
        ids = [{'type': 'pipeline-inspect', 'stage': s} for s in ('raw', 'roi', 'edges')]
        inputs = [wildcard(ids, 'n_clicks', [None, None, 1])]
        trigger = json.dumps(ids[2], sort_keys=True, separators=(',', ':')) + '.n_clicks'
        response = self.post('inspect', {'id': 'inspected-stage', 'property': 'data'}, inputs, [], [trigger])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertEqual(response.get_json()['response']['inspected-stage']['data'], 'edges')


if __name__ == '__main__':
    unittest.main()
