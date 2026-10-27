
"""Tests für Zustandsbeobachtung und sichere Bergung ohne Hardware."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from driving_health import DrivingHealthMonitor
from recovery_control import RecoveryControl

class HealthTests(unittest.TestCase):
    def test_transitionen(self):
        monitor = DrivingHealthMonitor()
        monitor.observe(SimpleNamespace(frame_id=1, lanes=()), None, True)
        monitor.observe(SimpleNamespace(frame_id=2, lanes=((1,2),)), None, True)
        monitor.observe(SimpleNamespace(frame_id=3, lanes=((1,2),(3,4))), None, False)
        self.assertEqual([e.code for e in monitor.events()],
            ['LANE_LOST', 'LANE_PARTIAL', 'LANES_DETECTED', 'CONTROLLER_STOPPED'])

def wait_for(condition, timeout=1.0):
    """Wartet auf den Watchdog-Thread (Befehle werden dort ausgefuehrt)."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)
    return condition()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.car = Mock()
        self.running = False
        self.recovery = RecoveryControl(self.car, lambda: self.running, timeout=0.15)

    def tearDown(self):
        self.recovery.shutdown()

    def test_keine_autonome_ueberlagerung(self):
        self.running = True
        with self.assertRaises(RuntimeError):
            self.recovery.enable()
        self.car.drive.assert_not_called()

    def test_deadman_und_watchdog(self):
        self.recovery.enable()
        self.recovery.command(forward=True, deadman=False)
        self.car.drive.assert_not_called()
        self.recovery.command(forward=True, deadman=True)
        self.assertTrue(wait_for(lambda: self.car.drive.called))
        self.car.drive.assert_called_once_with(30,90)
        self.assertTrue(wait_for(lambda: self.car.stop.call_count >= 2))

    def test_rueckwaerts_und_stop(self):
        self.recovery.enable()
        self.recovery.command(reverse=True, left=True, deadman=True)
        self.assertTrue(wait_for(lambda: self.car.drive.called))
        self.car.drive.assert_called_once_with(-30,75)
        self.recovery.disable()
        self.assertFalse(self.recovery.enabled)

if __name__ == '__main__':
    unittest.main()
