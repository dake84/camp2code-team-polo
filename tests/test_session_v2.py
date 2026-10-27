"""Session-Recorder Schema v2: Konsistenz, Wartefrist, Luecken, Replay, Export."""
import csv
import json
import os
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace

import numpy as np

from models import CameraData, DrivingState
from models.sensor_snapshot import SensorSnapshot
from replay_hud import draw_hud
from session_recorder import (EVENT_FIELDS, FIELDS, SessionRecorder, export_live_records,
                              export_session_range, load_events, load_session)
from telemetry import FrameRingBuffer


def camera(frame_id, age_s=0.0, image=True):
    raw = np.zeros((240, 320, 3), np.uint8) if image else np.empty(0)
    return CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=raw, frame_id=frame_id,
                      timestamp=time.time_ns(), monotonic_ns=time.monotonic_ns() - int(age_s * 1e9),
                      crop=(0.25, 0.65), lanes=((-1.0, 150.0), (1.5, -300.0)),
                      processing_parameters=((100, 130),), processor_name='CopilotLaneProcessor',
                      processing_ns=5_000_000)


def decision(frame_id):
    return DrivingState(frame_id, time.time_ns(), 95, 30, None, 100, 'camera', 100, time.monotonic_ns())


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, 'sessions')
        self.history = FrameRingBuffer(20)
        self.active = True
        self.car = SimpleNamespace(frame_history=self.history,
                                   sensor_snapshot=SensorSnapshot(ultrasonic_cm=None))
        self.recorder = SessionRecorder(self.car, self.root, is_driving=lambda: self.active,
                                        commit_grace_s=0.2)
        self.recorder.start()
        self.recorder._stop.set()  # Hintergrundthread anhalten; Tests rufen capture_once direkt auf
        self.recorder._thread.join(1)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        return load_session(self.recorder.path.name, self.root)[1]

    def test_schema_und_manifest(self):
        with (self.recorder.path / 'frames.csv').open() as f:
            self.assertEqual(tuple(next(csv.reader(f))), FIELDS)
        with (self.recorder.path / 'events.csv').open() as f:
            self.assertEqual(tuple(next(csv.reader(f))), EVENT_FIELDS)
        manifest = json.loads((self.recorder.path / 'manifest.json').read_text())
        self.assertEqual(manifest['schema_version'], 2)
        self.assertIn('y = m*x + b', manifest['lane_model'])

    def test_wartet_auf_fahrentscheidung(self):
        self.history.add_camera(camera(1))
        self.recorder.capture_once()
        self.assertEqual(self.recorder.count, 0)          # Entscheidung steht noch aus
        self.history.add_driving(decision(1))
        self.recorder.capture_once()
        row = self.rows()[0]
        self.assertEqual(row['data_status'], 'complete')
        self.assertEqual(row['steering_angle_car'], '100')
        self.assertEqual(row['roi_top_px'], str(int(240 * (1 - 0.65))))
        self.assertEqual(row['ultrasonic_cm'], '')        # fehlender Messwert bleibt leer

    def test_fehlende_entscheidung_nach_frist(self):
        self.history.add_camera(camera(1, age_s=0.5))
        self.recorder.capture_once()
        self.assertEqual(self.rows()[0]['data_status'], 'missing_decision')
        self.assertEqual(self.recorder.missing_decisions, 1)

    def test_ohne_fahrmodus_sofort(self):
        self.active = False
        self.history.add_camera(camera(1))
        self.recorder.capture_once()
        self.assertEqual(self.rows()[0]['data_status'], 'no_driving_state')

    def test_luecken_doppelte_und_leere_frames(self):
        self.active = False
        for frame_id in (1, 2):
            self.history.add_camera(camera(frame_id))
        self.recorder.capture_once()
        self.history.add_camera(camera(2))                 # doppelter Frame
        self.history.add_camera(camera(3, image=False))    # leeres Bild
        self.history.add_camera(camera(6))
        self.recorder.capture_once()
        self.assertEqual([r['frame_id'] for r in self.rows()], ['1', '2', '6'])
        self.assertEqual(self.recorder.skipped_empty, 1)
        self.assertEqual(self.recorder.gap_frames, 2)
        codes = [e['code'] for e in load_events(self.recorder.path.name, self.root)]
        self.assertIn('RECORDER_FRAME_GAP', codes)

    def test_stop_sichert_rest_und_manifest(self):
        self.history.add_camera(camera(1))
        self.recorder._thread = SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None)
        self.recorder.stop()
        manifest = json.loads((self.recorder.path / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'stopped')
        self.assertEqual(manifest['frames'], 1)
        self.assertIsNotNone(manifest['created_at'])

    def test_replay_und_export(self):
        self.active = False
        for frame_id in range(1, 6):
            self.history.add_camera(camera(frame_id))
        self.recorder.capture_once()
        path, rows = load_session(self.recorder.path.name, self.root)
        self.assertIs(load_session(self.recorder.path.name, self.root)[1], rows)  # Cache
        image = draw_hud(np.zeros((240, 320, 3), np.uint8), rows[0])
        self.assertEqual(image.shape, (240, 320, 3))
        target = export_session_range(self.recorder.path.name, 2, 4, self.root,
                                      os.path.join(self.tmp.name, 'exports'))
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            self.assertEqual(len([n for n in names if n.startswith('frames/')]), 3)
            exported = list(csv.DictReader(archive.read('frames.csv').decode().splitlines()))
            self.assertEqual([r['frame_id'] for r in exported], ['2', '3', '4'])

    def test_ringbuffer_export(self):
        self.history.add_camera(camera(1))
        self.history.add_driving(decision(1))
        target = export_live_records(self.history.snapshot(), os.path.join(self.tmp.name, 'exports'))
        with zipfile.ZipFile(target) as archive:
            row = next(csv.DictReader(archive.read('frames.csv').decode().splitlines()))
            self.assertEqual(row['data_status'], 'complete')

    def test_schema_v1_wird_gelesen(self):
        old = os.path.join(self.root, 'session_v1')
        os.makedirs(os.path.join(old, 'frames'))
        with open(os.path.join(old, 'frames.csv'), 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=('frame_id', 'image_filename', 'steering_angle', 'data_status'))
            writer.writeheader()
            writer.writerow({'frame_id': 5, 'image_filename': 'frames/x.jpg', 'steering_angle': '',
                             'data_status': 'no_driving_state'})
        _, rows = load_session('session_v1', self.root)
        self.assertEqual(load_events('session_v1', self.root), [])
        self.assertEqual(draw_hud(np.zeros((10, 10, 3), np.uint8), rows[0]).shape, (10, 10, 3))


if __name__ == '__main__':
    unittest.main()
