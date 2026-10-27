
"""Hardwarefreie Tests für die zentrale Anwendungsverwaltung."""
import sys
import unittest
from unittest.mock import MagicMock, Mock

# Hardwaremodule nur waehrend des Imports ersetzen. Frueher blieben die Mocks
# (u. a. pipeline_config, Driving) fuer alle folgenden Testmodule aktiv.
_names = ("CameraCar", "CameraSensor", "Driving", "car_application")
_saved = {name: sys.modules.pop(name, None) for name in _names}
sys.modules.update({name: MagicMock() for name in _names[:-1]})
try:
    from car_application import CarApplication
finally:
    # Nur die selbst gesetzten Eintraege entfernen (patch.dict wuerde auch cv2 entladen).
    for _name in _names:
        sys.modules.pop(_name, None)
        if _saved[_name] is not None:
            sys.modules[_name] = _saved[_name]

class TestCarApplication(unittest.TestCase):
    def setUp(self):
        self.car = Mock()
        self.car.driving_state = None
        self.car.camera_data = None
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
