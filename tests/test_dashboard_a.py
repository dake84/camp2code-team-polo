"""Hardwarefreie Tests für die zentrale Anwendungsverwaltung."""
import threading
import sys
from unittest.mock import MagicMock
for name in ("CameraCar", "CameraSensor", "Save_Data_AI", "pipeline_config", "Driving"):
    sys.modules.setdefault(name, MagicMock())
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from car_application import CarApplication

class TestCarApplication(unittest.TestCase):
    def setUp(self):
        self.car = Mock()
        self.car.driving_state = None
        self.car.thread_metrics = {}
        self.sensor = Mock()
        self.sensor.read_loop = Mock(side_effect=lambda event: event.wait())
        self.mode = Mock()
        self.mode.is_running.return_value = False
        self.recorder = Mock()
        self.recorder.is_running.return_value = False
        self.app = CarApplication(self.car, self.sensor, {'camera': self.mode}, self.recorder, start_camera=False)

    def tearDown(self):
        self.app.shutdown()

    def test_keine_zweite_kamera(self):
        self.app.start_camera()
        thread = self.app._camera_thread
        self.app.start_camera()
        self.assertIs(thread, self.app._camera_thread)

    def test_doppelstart_abgewiesen(self):
        self.mode.is_running.return_value = True
        with self.assertRaises(RuntimeError):
            self.app.start_mode('camera')

    def test_kalibrierung_nicht_waehrend_fahrt(self):
        self.mode.is_running.return_value = True
        with self.assertRaises(RuntimeError):
            self.app.set_pipeline({'crop': (0.1,0.9)})

    def test_shutdown_idempotent(self):
        self.app.shutdown()
        self.app.shutdown()
        self.assertTrue(self.app.stop_event.is_set())

if __name__ == '__main__':
    unittest.main()
