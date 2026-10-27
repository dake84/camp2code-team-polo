"""Hardwarefreie Tests der Session-Aufzeichnung und des Ladevorgangs."""
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from session_recorder import SessionRecorder, load_session, list_sessions

class SessionTests(unittest.TestCase):
    def test_recording_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            camera=SimpleNamespace(frame_id=7,raw_image=np.zeros((12,16,3),dtype=np.uint8),timestamp=100,monotonic_ns=80,lanes=())
            car=SimpleNamespace(frame_history=SimpleNamespace(snapshot=lambda:(SimpleNamespace(camera=camera,driving=None),)))
            recorder=SessionRecorder(car,folder)
            recorder.start()
            recorder.stop()
            recorder.capture_once()
            recorder.capture_once()
            self.assertEqual(recorder.count,1)
            path,rows=load_session(recorder.path.name,folder)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['data_status'],'no_driving_state')
            self.assertTrue((path/rows[0]['image_filename']).is_file())
            self.assertEqual(len(list_sessions(folder)),1)
            with self.assertRaises(ValueError):load_session('../other',folder)

if __name__=='__main__':unittest.main()
