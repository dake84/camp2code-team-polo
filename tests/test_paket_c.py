"""Paket C: Recorder-Vorlauf, Replay-Uebergabe, Thread-CPU, Replay-Callbacks (ohne Hardware)."""
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from replay_studio import parameters_from_record, processor_key  # noqa: E402
from telemetry.thread_stats import ThreadCpuSampler  # noqa: E402

PARAMS = [[100, 130], [80, 255], [40, 255], [0.15, 0.55], 3, 5, [50, 100],
          {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True}]


class ReplayStudioTests(unittest.TestCase):
    def test_parameter_aus_json_und_tupel(self):
        params = parameters_from_record(json.dumps(PARAMS))
        self.assertEqual(params[3], (0.15, 0.55))
        self.assertEqual(params[7]['canny'], False)
        self.assertEqual(parameters_from_record(params), params)

    def test_fehlende_parameter(self):
        for value in (None, '', '[1, 2]', 'kein json'):
            with self.assertRaises(ValueError):
                parameters_from_record(value)

    def test_prozessor(self):
        self.assertEqual(processor_key('LaneProcessor'), 'legacy')
        self.assertEqual(processor_key('CopilotLaneProcessor'), 'copilot')


class ThreadCpuTests(unittest.TestCase):
    def test_zeilen(self):
        sampler = ThreadCpuSampler(min_interval=0.05)
        self.assertTrue(all(isinstance(x, str) for x in sampler.lines()))
        time.sleep(0.1)
        self.assertTrue(sampler.lines()[0].startswith('Thread-CPU'))


class RecorderVorlaufTests(unittest.TestCase):
    def test_frames_vor_fahrstart_sind_kein_missing_decision(self):
        from models import CameraData
        from session_recorder import SessionRecorder, load_session
        from telemetry import FrameRingBuffer
        now = time.monotonic_ns()
        history = FrameRingBuffer(20)
        for frame_id, age_s in ((1, 2.0), (2, 1.0)):
            history.add_camera(CameraData(process_image=np.zeros((4, 4), np.uint8),
                                          raw_image=np.zeros((24, 32, 3), np.uint8), frame_id=frame_id,
                                          timestamp=time.time_ns(), monotonic_ns=now - int(age_s * 1e9)))
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(SimpleNamespace(frame_history=history), folder,
                                       is_driving=lambda: True, driving_since=lambda: now - int(1.5e9),
                                       commit_grace_s=0.2)
            recorder.start()
            recorder._stop.set()
            recorder._thread.join(2)
            recorder.capture_once()
            _, rows = load_session(recorder.path.name, folder)
            status = {r['frame_id']: r['data_status'] for r in rows}
            self.assertEqual(status, {'1': 'no_driving_state', '2': 'missing_decision'})
            self.assertEqual(recorder.missing_decisions, 1)


class ReplayCallbackTests(unittest.TestCase):
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

    def test_diagramm_mit_cursor_und_uebergabe(self):
        for frame_id in range(30, 34):
            camera = self.fake.publish(frame_id, with_decision=True)
            camera.processing_parameters = parameters_from_record(PARAMS)
            camera.processor_name = 'CopilotLaneProcessor'
        self.ctx.set('session-choice', 'value')
        figure = self.callbacks['replay_chart']('__live__', None, 1, 'replay')
        self.assertEqual(len(figure.data), 3)                    # Stellwert, Linien, Cursor
        tab, message, source, _token, _s = self.callbacks['replay_to_studio'](1, None, '__live__', None, 1)  # PAKET-E
        self.assertEqual(tab, 'pipeline')
        self.assertIn('Referenz A', source)
        self.ctx.set('ab-run')
        from test_dashboard_callbacks import studio_controls
        status = self.callbacks['ab_calibrate'](None, 1, None, 'copilot', *studio_controls('copilot'))
        self.assertTrue(status[0].startswith('A/B auf Frame'), status[0])

    def test_session_takt_allein_rendert_nicht(self):
        self.ctx.set('tick-replay', 'n_intervals')
        result = self.callbacks['replay_render'](0, 'session_gibt_es_nicht', None, 1, 'replay')
        self.assertTrue(all(v is self.module.no_update for v in result))


if __name__ == '__main__':
    unittest.main()

