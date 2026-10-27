"""CameraMode ohne Hardware: Frame-Zuordnung, Schutzfunktionen, Thread-Lifecycle.

Das Fahrzeug ist eine Attrappe, die Fahrbefehle nur protokolliert.
"""
import os
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from models import CameraData  # noqa: E402
from telemetry import FrameRingBuffer  # noqa: E402

TWO_LANES = ((-1.19, 166.5), (1.585, -317.1))


class FakeCameraCar:
    """Speichert Daten wie CameraCar, sendet aber nichts an Hardware."""

    def __init__(self):
        self.camera_data = CameraData(process_image=np.empty(0), raw_image=np.empty(0))
        self.frame_history = FrameRingBuffer(30)
        self.commands = []
        self.states = []
        self.steering_angle = 90
        self.speed = 0
        self._lock = threading.RLock()

    @property
    def driving_state(self):
        return self.states[-1] if self.states else None

    @driving_state.setter
    def driving_state(self, state):
        self.states.append(state)
        self.frame_history.add_driving(state)

    def drive(self, speed=None, steering_angle=None):
        with self._lock:
            self.commands.append((speed, steering_angle))
            self.speed = speed if speed is not None else self.speed
            self.steering_angle = steering_angle if steering_angle is not None else self.steering_angle

    def stop(self):
        self.drive(0, 90)

    def publish(self, frame_id, lanes=TWO_LANES, age_s=0.0):
        camera = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.zeros((240, 320, 3), np.uint8),
                            frame_id=frame_id, monotonic_ns=time.monotonic_ns() - int(age_s * 1e9), lanes=lanes)
        self.camera_data = camera
        self.frame_history.add_camera(camera)
        return camera


class CameraModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                                  cameracar_controller={'lost_line_timeout': 0.2})
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        import ConfigReader
        from driving.camera_mode import CameraMode
        self.car = FakeCameraCar()
        self.mode = CameraMode(car=self.car, cfg=ConfigReader.ConfigReader('cameracar_controller', self.config))
        self.mode._pre_run()
        self.car.commands.clear()

    def tearDown(self):
        self.mode.stop()
        self.stubs.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_speed_steuerwert_bleibt_30(self):
        self.assertEqual(self.mode.SPEED_CONTROL_VALUE, 30)
        self.assertEqual(self.mode.driving_parameter(100), (100, 30))
        self.car.publish(1)
        self.assertTrue(self.mode._run())
        self.assertEqual(self.car.commands[-1][0], 30)

    def test_ein_entscheid_je_frame(self):
        self.car.publish(1)
        for _ in range(20):
            self.assertTrue(self.mode._run())
        self.assertEqual(len(self.car.commands), 1)
        self.assertEqual([s.frame_id for s in self.car.states], [1])
        self.car.publish(2)
        self.mode._run()
        self.assertEqual([s.frame_id for s in self.car.states], [1, 2])

    def test_entscheidung_passt_zur_frame_id(self):
        self.car.publish(4)
        self.mode._run()
        record = self.car.frame_history.find(4)
        self.assertEqual(record.driving.frame_id, 4)
        self.assertEqual(record.driving.driving_mode, 'camera')
        self.assertIsNotNone(record.driving.requested_angle)

    def test_lenkwinkel_wie_bisher(self):
        from LenkwinkelKalkulator import cals_drive_parameter_from_lanes
        self.car.publish(3)
        self.mode._run()
        raw, _ = cals_drive_parameter_from_lanes(np.zeros((96, 320), np.uint8), list(TWO_LANES))
        expected = int(max(45, min(135, 90 + (raw - 90) * 2.0)))
        self.assertEqual(self.car.states[-1].steering_angle, raw)
        self.assertEqual(self.car.commands[-1], (30, expected))

    def test_lane_timeout_bleibt_erhalten(self):
        self.car.publish(1, lanes=((1.0, 2.0),))
        self.assertTrue(self.mode._run())          # Vorwert wird gehalten
        self.assertTrue(self.mode.last_diagnostic['held_previous_angle'])
        time.sleep(0.25)
        self.assertFalse(self.mode._run())         # auch ohne neuen Frame
        self.assertEqual(self.mode.last_stop_reason, 'LANE_TIMEOUT')

    def test_kamera_ausfall_stoppt(self):
        self.car.publish(1, age_s=0.5)
        self.assertTrue(self.mode._run())
        self.assertFalse(self.mode._run())
        self.assertEqual(self.mode.last_stop_reason, 'CAMERA_STALE')

    def test_ohne_kamerabild_wird_nicht_gefahren(self):
        self.assertTrue(self.mode._run())          # Platzhalter frame_id -1
        self.assertEqual(self.car.commands, [])
        self.mode._started_ns -= int(1e9)
        self.assertFalse(self.mode._run())
        self.assertEqual(self.mode.last_stop_reason, 'NO_CAMERA_DATA')

    def test_thread_lifecycle(self):
        self.car.publish(1)
        self.mode.start()
        first_thread = self.mode._thread
        self.mode.start()                          # doppelter Start: kein zweiter Thread
        self.assertIs(first_thread, self.mode._thread)
        time.sleep(0.05)
        self.assertTrue(self.mode.is_running())
        self.assertTrue(self.mode.stop())
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(self.mode.is_running())
        self.assertEqual(self.car.commands[-1], (0, 90))  # Stoppbefehl nach Ende


class StopReasonTests(unittest.TestCase):
    def test_none_ist_kein_tupel(self):
        from driving.stop_reason import StopReason
        self.assertEqual(StopReason.NONE, 0)
        self.assertEqual(str(StopReason(0)), 'No reason')


if __name__ == '__main__':
    unittest.main()
