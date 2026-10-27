"""Prüft das Replay-HUD und das Ereignisprotokoll ohne Hardware."""
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from replay_hud import draw_hud, encode_lanes
from session_recorder import SessionRecorder

class ReplayHUDTests(unittest.TestCase):
    def test_hud_uses_copy(self):
        raw=np.zeros((240,320,3),dtype=np.uint8)
        result=draw_hud(raw,{'frame_id':'4','steering_angle_car':'110','speed':'30',
            'lanes_json':'[[0.1,10],[-0.1,290]]','event_codes':'LANE_PARTIAL'})
        self.assertFalse(np.array_equal(raw,result))
        self.assertFalse(raw.any())

    def test_events_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            event=SimpleNamespace(timestamp_ns=200,frame_id=4,code='LANE_LOST',detail='Keine Linien')
            cam=SimpleNamespace(frame_id=4,raw_image=np.zeros((12,16,3),dtype=np.uint8),
                timestamp=100,monotonic_ns=80,lanes=((.1,2),None))
            car=SimpleNamespace(frame_history=SimpleNamespace(snapshot=lambda:(SimpleNamespace(camera=cam,driving=None),)))
            recorder=SessionRecorder(car,folder,event_source=lambda:(event,))
            recorder.start();recorder.stop();recorder.capture_once();recorder.capture_once()
            with (recorder.path/'events.csv').open(newline='') as f:events=list(csv.DictReader(f))
            with (recorder.path/'frames.csv').open(newline='') as f:frames=list(csv.DictReader(f))
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]['code'],'LANE_LOST')
            self.assertEqual(frames[0]['event_codes'],'LANE_LOST')
            self.assertIn('null',frames[0]['lanes_json'])

if __name__=='__main__':unittest.main()
