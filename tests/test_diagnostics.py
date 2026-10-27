"""Prüft die Zustandsübergänge der Fahrdiagnose ohne Hardware."""
import unittest
from types import SimpleNamespace
from driving_health import DrivingHealthMonitor

class DiagnosticsTests(unittest.TestCase):
    def test_held_and_stop_reason(self):
        monitor = DrivingHealthMonitor()
        camera = SimpleNamespace(frame_id=8, lanes=((1, 2),))
        monitor.observe(camera, None, True, diagnostic={
            'frame_id': 8, 'raw_angle': None, 'held_previous_angle': True,
            'previous_angle': 102, 'requested_angle': 102, 'actual_angle': 101})
        monitor.observe(camera, None, False, stop_reason='LANE_TIMEOUT')
        codes = [event.code for event in monitor.events()]
        self.assertIn('STEERING_HELD', codes)
        self.assertIn('STOP_REASON', codes)
        self.assertIn('CONTROLLER_STOPPED', codes)
        self.assertEqual(len([x for x in monitor.events() if x.code == 'STOP_REASON']), 1)

if __name__ == '__main__':
    unittest.main()
