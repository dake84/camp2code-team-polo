"""Paket F: Prozessor-Verzeichnis, Mock, Bildquellen, Uhr im CameraMode, ReplayCar,
Simulation, Cockpit-Umschalter, Batch-Report, Offline-Anwendung. Ohne Hardware, ohne Motoren."""
import ast
import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
ROOT = Path(__file__).resolve().parents[1]

import hardware_stubs  # noqa: E402
from models import CameraData, DrivingState  # noqa: E402

PARAMS = [[100, 130], [80, 255], [40, 255], [0.15, 0.55], 3, 5, [50, 100],
          {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True}]
FRAME_NS = 40_000_000
BLANK = range(20, 61)          # 41 Frames ohne Linien = 1,64 s


def lane_image(lanes=True, shift=0):
    """320x240 BGR, zwei blaue Linien (HSV-Hue ca. 117)."""
    image = np.full((240, 320, 3), (180, 160, 200), dtype=np.uint8)
    if lanes:
        cv2.line(image, (64 + shift, 239), (134 + shift, 0), (200, 40, 20), 10)
        cv2.line(image, (256 + shift, 239), (186 + shift, 0), (200, 40, 20), 10)
    return image


def write_session(root, name='session_test', count=80):
    path = Path(root) / name
    (path / 'frames').mkdir(parents=True)
    rows = []
    for frame_id in range(1, count + 1):
        filename = f'frames/frame_{frame_id:010d}.jpg'
        cv2.imwrite(str(path / filename), lane_image(frame_id not in BLANK, shift=(frame_id % 9) - 4))
        rows.append({'frame_id': frame_id, 'image_filename': filename, 'data_status': 'complete',
                     'camera_timestamp_ns': 1_700_000_000_000_000_000 + frame_id * FRAME_NS,
                     'camera_monotonic_ns': 10_000_000_000 + frame_id * FRAME_NS, 'processing_ns': 4_000_000,
                     'steering_angle': 90, 'requested_angle': 90, 'steering_angle_car': 90, 'speed': 30,
                     'processor_name': 'CopilotLaneProcessor', 'processing_parameters_json': json.dumps(PARAMS),
                     'lane_count': 0 if frame_id in BLANK else 2, 'lanes_json': '[]'})
    with (path / 'frames.csv').open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (path / 'events.csv').open('w', newline='', encoding='utf-8') as fh:
        fh.write('timestamp_ns,frame_id,code,detail\n1,20,LANE_LOST,0 Linien\n')
    return name


class TempConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                                  cameracar_controller={'lost_line_timeout': 1})
        os.chdir(self.tmp.name)
        self.root = os.path.join(self.tmp.name, 'Data_AI', 'sessions')

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def engine(self):
        import ConfigReader
        from sim_engine import SimEngine
        return SimEngine(ConfigReader.ConfigReader('cameracar_controller', self.config), v_min=20, v_max=100)


class RegistryTests(unittest.TestCase):
    def test_verzeichnis(self):
        import processor_registry as reg
        live = [o['value'] for o in reg.processor_options(live_only=True)]
        self.assertEqual(set(live), {'copilot', 'legacy'})
        self.assertIn('mock', [o['value'] for o in reg.processor_options()])
        self.assertFalse(reg.is_live_processor('mock'))
        self.assertEqual(type(reg.create_processor('mock')).__name__, 'MockProcessor')
        self.assertEqual(reg.key_for_class('LaneProcessor'), 'legacy')
        self.assertEqual(reg.key_for_class('MockProcessor'), 'mock')
        self.assertEqual(reg.key_for_class('Unbekannt'), 'copilot')
        with self.assertRaises(ValueError):
            reg.create_processor('gibt_es_nicht')

    def test_replay_studio_nutzt_verzeichnis(self):
        from replay_studio import processor_key
        self.assertEqual(processor_key('MockProcessor'), 'mock')
        self.assertEqual(processor_key('LaneProcessor'), 'legacy')

    def test_ab_labor_mit_mock(self):
        from pipeline_ab import PipelineAB
        lab = PipelineAB()
        lab.freeze(SimpleNamespace(raw_image=lane_image(), frame_id=1), tuple(PARAMS), 'copilot')
        comparison = lab.compare(tuple(PARAMS), 'mock')
        self.assertEqual(len(comparison.lanes_a), 2)


class MockTests(unittest.TestCase):
    def test_verschiebt_und_laesst_weg(self):
        from mock_processor import MockProcessor
        from pipeline_processors import CopilotLaneProcessor
        mock, base = MockProcessor(), CopilotLaneProcessor()
        ref = base.process(lane_image(), PARAMS).lanes
        counts = [len(mock.process(lane_image(), PARAMS).lanes) for _ in range(MockProcessor.DROP_EVERY)]
        self.assertEqual(counts[-1], 1)
        self.assertEqual(counts[0], 2)
        shifted = MockProcessor().process(lane_image(), PARAMS).lanes
        m, b = ref[0]
        self.assertAlmostEqual(shifted[0][1], b - m * MockProcessor.SHIFT_PX, places=6)


class ReplayCarTests(unittest.TestCase):
    def test_begrenzung_wie_basecar(self):
        from replay_car import ReplayCar
        car = ReplayCar(v_min=20, v_max=80)
        car.drive(10, 200)
        self.assertEqual((car.speed, car.steering_angle), (20, 135))
        car.drive(-90, 10)
        self.assertEqual((car.speed, car.steering_angle), (-80, 45))
        car.stop()
        self.assertEqual((car.speed, car.steering_angle), (0, 90))

    def test_keine_hardwaremodule(self):
        for name in ('replay_car.py', 'sim_engine.py', 'frame_source.py', 'offline_application.py', 'sim_lab.py'):
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imported |= {(n.module or '').split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertFalse(imported & {'basisklassen', 'basisklassen_cam', 'BaseCar', 'CameraCar',
                                         'CameraSensor', 'car_application'}, name)


class ClockTests(TempConfig):
    def test_live_uhr_unveraendert_und_simulationsuhr(self):
        import ConfigReader
        from driving.camera_mode import CameraMode
        from replay_car import ReplayCar
        car = ReplayCar()
        mode = CameraMode(car=car, cfg=ConfigReader.ConfigReader('cameracar_controller', self.config))
        self.assertIs(mode._clock, time.monotonic_ns)
        now = [5_000_000_000]
        mode._clock = lambda: now[0]
        mode._pre_run()
        for frame_id in range(1, 30):      # eine Linie: Vorwert halten, dann LANE_TIMEOUT nach > 1 s Simulationszeit
            now[0] += FRAME_NS
            car.camera_data = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.empty(0),
                                         frame_id=frame_id, monotonic_ns=now[0], lanes=((1.0, 2.0),))
            if not mode._run():
                break
        self.assertEqual(mode.last_stop_reason, 'LANE_TIMEOUT')
        self.assertEqual(frame_id, 27)     # (27-1)*40 ms = 1,04 s > 1 s


class SimulationTests(TempConfig):
    def setUp(self):
        super().setUp()
        self.name = write_session(self.root)

    def source(self):
        from frame_source import SessionSource
        return SessionSource(self.name, self.root)

    def test_lane_timeout_mit_aufgezeichneter_zeit(self):
        result = self.engine().run(self.source(), 'copilot', warmup=0)
        stops = [f.frame_id for f in result.frames if f.stop_reason]
        self.assertEqual(stops, [46])                  # (46-20)*40 ms > 1 s, danach neu scharf
        self.assertEqual(result.frames[-1].car_angle is not None, True)
        self.assertEqual(result.errors, 0)

    def test_bereich_vorlauf_und_kennzahlen(self):
        result = self.engine().run(self.source(), 'copilot', lo=70, hi=75, warmup=10)
        self.assertEqual([f.frame_id for f in result.measured], list(range(70, 76)))
        self.assertEqual(sum(f.warmup for f in result.frames), 10)
        stats = result.stats()
        self.assertEqual(stats['frames'], 6)
        self.assertEqual(stats['two_lanes_now'], 1.0)
        self.assertIsNotNone(stats['process_ms_median'])

    def test_mock_unterscheidet_sich(self):
        engine = self.engine()
        a = engine.run(self.source(), 'copilot', lo=61, hi=80, warmup=0)
        b = engine.run(self.source(), 'mock', lo=61, hi=80, warmup=0)
        differs = [x.raw_angle != y.raw_angle or x.lane_count != y.lane_count for x, y in zip(a.frames, b.frames)]
        self.assertTrue(any(differs))

    def test_studio_parameter(self):
        studio = list(PARAMS)
        studio[0] = [0, 5]                              # Hue passt nicht -> keine Linien
        result = self.engine().run(self.source(), 'copilot', tuple(tuple(x) if isinstance(x, list) else x
                                                                     for x in studio), lo=1, hi=5, warmup=0)
        self.assertEqual({f.lane_count for f in result.frames}, {0})
        self.assertEqual(result.param_mode, 'studio')

    def test_ringbuffer_quelle(self):
        from frame_source import RingbufferSource
        from telemetry import FrameRingBuffer
        ring = FrameRingBuffer(10)
        from replay_studio import parameters_from_record
        for frame_id in range(1, 6):
            ring.add_camera(CameraData(process_image=np.empty(0), raw_image=lane_image(), frame_id=frame_id,
                                       monotonic_ns=frame_id * FRAME_NS, processing_parameters=parameters_from_record(PARAMS),
                                       processor_name='CopilotLaneProcessor'))
            ring.add_driving(DrivingState(frame_id, 0, 90, 30, steering_angle_car=92))
        result = self.engine().run(RingbufferSource(ring.snapshot()), 'mock', warmup=0)
        self.assertEqual(len(result.frames), 5)
        self.assertEqual(result.frames[0].then_car_angle, 92)

    def test_batch_report(self):
        out = os.path.join(self.tmp.name, 'out')
        proc = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'sim_report.py'), self.name,
                               '--proc', 'copilot', '--proc', 'mock', '--root', self.root, '--out', out,
                               '--warmup', '5'], cwd=self.tmp.name, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn('Regler-Stopps', proc.stdout)
        report = Path(out) / f'c2c-sim-{self.name}.html'
        self.assertIn('<svg', report.read_text(encoding='utf-8'))
        self.assertTrue((Path(out) / f'c2c-sim-{self.name}.csv').is_file())


class OfflineTests(TempConfig):
    def test_offline_anwendung(self):
        from offline_application import OfflineApplication
        app = OfflineApplication()
        self.assertFalse(app.running())
        for action in (lambda: app.start_mode('camera'), app.enable_recovery, app.toggle_recording):
            with self.assertRaises(RuntimeError):
                action()
        with self.assertRaises(ValueError):
            app.set_processor('mock')
        import pipeline_config
        settings = dict(pipeline_config.DEFAULT, processor='legacy', crop=[0.2, 0.6])
        app.set_pipeline(settings)
        self.assertEqual(app.sensor.processing_parameters[3], (0.2, 0.6))
        self.assertEqual(app.sensor.processor_name, 'legacy')


class DashboardSimTests(TempConfig):
    def setUp(self):
        super().setUp()
        self.name = write_session(self.root)
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        self.module, self.context, self.ctx = load_dashboard()
        self.fake = FakeApplication()
        import pipeline_config
        with patch.object(self.module, 'load', return_value=pipeline_config.DEFAULT):
            self.app = self.module.create_app(self.fake)
        self.cb = self.app.callbacks

    def tearDown(self):
        self.context.__exit__(None, None, None)
        super().tearDown()

    def call_actions(self, trigger):
        cb = self.cb['handle_actions']
        args = [None] * (len(cb.inputs) + len(cb.states))
        args[0], args[-1] = 1, 'camera'
        self.ctx.set(trigger)
        return cb(*args)

    def start_disabled(self):
        cb = self.cb['global_status']
        values = cb(1, None)
        return values[[tuple(o) for o in cb.outputs].index(('global-start', 'disabled'))]

    def test_umschalter_sperrt_start(self):
        live_open, sim_open, options, _msg = self.cb['sim_view_mode']('sim')
        self.assertEqual((live_open, sim_open), (False, True))
        self.assertIn(self.name, [o['value'] for o in options])
        result = self.call_actions('global-start')
        self.assertFalse(result['ok'])
        self.assertNotIn(('start_mode', 'camera'), getattr(self.fake, 'calls', []))
        self.assertTrue(self.start_disabled())
        self.cb['sim_view_mode']('live')
        self.assertFalse(self.start_disabled())

    def test_simulation_im_cockpit(self):
        self.cb['sim_view_mode']('sim')
        status, stats, maximum, chart, token = self.cb['sim_run'](1, self.name, 'mock', 'recorded', 40, 50, 5)
        self.assertIn('MOCK', status)
        self.assertEqual(maximum, 15)
        self.assertGreaterEqual(len(chart['data']), 4)
        self.ctx.set('sim-result-token', 'data')
        index = self.cb['sim_seek'](None, None, None, None, token, 0)
        self.assertEqual(index, 5)                     # erster Frame nach dem Vorlauf
        then, now, table, label = self.cb['sim_render'](index, token)
        self.assertTrue(then.startswith('data:image') and now.startswith('data:image'))

    def test_keine_simulation_waehrend_fahrt(self):
        self.fake._running = True
        status = self.cb['sim_run'](1, self.name, 'copilot', 'recorded', None, None, 0)[0]
        self.assertIn('Fahrt', status)
        self.fake._running = False


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components')),
                     'Dash nicht installiert')
class RealDashSimTests(TempConfig):
    def test_layout_und_callbacks_mit_echtem_dash(self):
        import dash_bootstrap_components as dbc
        from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
        from sim_lab import SimLab
        self.name = write_session(self.root)
        fake = SimpleNamespace(running=lambda: False, car=SimpleNamespace(frame_history=None), offline=True)
        app = Dash(__name__)
        app.layout = html.Div([SimLab.wrap_cockpit(html.Div('live'), dbc, dcc, html)])
        sim = SimLab(fake, SimpleNamespace(dbc=dbc, dcc=dcc, html=html, Input=Input, Output=Output,
                                           State=State, ctx=ctx, no_update=no_update), lambda img: 'data:x')
        sim.register(app)
        client = app.server.test_client()
        self.assertEqual(client.get('/_dash-layout').status_code, 200)
        self.assertEqual(len(client.get('/_dash-dependencies').get_json()), len(SimLab.CALLBACKS))
        outputs = [('cockpit-live-wrap', 'is_open'), ('sim-wrap', 'is_open'), ('sim-session', 'options'),
                   ('sim-mode-msg', 'children')]
        body = {'output': '..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..',
                'outputs': [{'id': i, 'property': p} for i, p in outputs],
                'inputs': [{'id': 'cockpit-source', 'property': 'value', 'value': 'sim'}], 'state': [],
                'changedPropIds': ['cockpit-source.value']}
        response = client.post('/_dash-update-component', json=body)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        self.assertIn(self.name, response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
