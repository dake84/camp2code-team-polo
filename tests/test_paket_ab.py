"""Hardwarefreie Kernprüfungen der Datenmodelle und des Ringbuffers."""
import unittest
import numpy as np
from models import CameraData, DrivingState
from models.sensor_snapshot import SensorSnapshot
from telemetry.ring_buffer import FrameRingBuffer

class TestPaketAB(unittest.TestCase):
    def test_ringbuffer_zuordnung(self):
        ring = FrameRingBuffer(maxlen=2)
        for i in (1, 2):
            ring.add_camera(CameraData(np.zeros((2,2)), np.zeros((2,2)), frame_id=i))
        self.assertTrue(ring.add_driving(DrivingState(1, 1, 90, 30)))
        self.assertFalse(ring.add_driving(DrivingState(3, 1, 90, 30)))
        self.assertEqual(ring.snapshot()[0].driving.frame_id, 1)
        ring.add_camera(CameraData(np.zeros((2,2)), np.zeros((2,2)), frame_id=3))
        self.assertEqual([r.camera.frame_id for r in ring.snapshot()], [2,3])
    def test_sensor_alter(self):
        s=SensorSnapshot(infrared_monotonic_ns=10,ultrasonic_monotonic_ns=20)
        self.assertEqual(s.infrared_age_ns(30),20)
        self.assertEqual(s.ultrasonic_age_ns(30),10)
    def test_fehlende_entscheidung(self):
        r=FrameRingBuffer();r.add_camera(CameraData(np.zeros((1,1)),np.zeros((1,1)),frame_id=1))
        self.assertIsNone(r.snapshot()[0].driving)

if __name__ == '__main__': unittest.main()
