"""CarApplication mit echten Projektklassen und Hardware-Attrappen.

Prueft Start/Stop, doppelte Starts, gegenseitigen Ausschluss von autonomem
Fahrmodus und Bergung, Recorder-Lebenszyklus und Shutdown. Die Attrappen
protokollieren Befehle nur; es wird kein Motor angesteuert.
"""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                    cameracar_controller={'lost_line_timeout': 5})
        os.chdir(self.tmp.name)
        self.stubs = hardware_stubs.installed()
        self.log = self.stubs.__enter__()
        from car_application import CarApplication
        self.app = CarApplication(start_camera=False)

    def tearDown(self):
        self.app.shutdown()
        self.stubs.__exit__(None, None, None)
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_threads_und_ein_recorder(self):
        names = {t.name for t in threading.enumerate()}
        self.assertIn('telemetry-sampler', names)
        self.assertIn('recovery-watchdog', names)
        self.assertEqual(type(self.app.recorder).__name__, 'SessionRecorder')

    def test_start_stop_und_doppelstart(self):
        self.app.start_mode('camera')
        self.assertTrue(self.app.running())
        with self.assertRaises(RuntimeError):
            self.app.start_mode('camera')
        with self.assertRaises(RuntimeError):
            self.app.enable_recovery()          # Bergung waehrend autonomer Fahrt verboten
        self.app.stop_all()
        self.assertFalse(self.app.running())
        self.assertEqual(self.app.stop_reason(), 'Benutzer-STOP')

    def test_start_beendet_bergung(self):
        self.app.enable_recovery()
        self.assertTrue(self.app.recovery.enabled)
        self.app.start_mode('camera')
        self.assertFalse(self.app.recovery.enabled)
        self.app.stop_all()

    def test_unbekannter_modus(self):
        with self.assertRaises(ValueError):
            self.app.start_mode('turbo')

    def test_kalibrierung_gesperrt_waehrend_fahrt(self):
        self.app.start_mode('camera')
        with self.assertRaises(RuntimeError):
            self.app.set_pipeline({'crop': (0.1, 0.9)})
        self.app.stop_all()

    def test_aufzeichnung(self):
        self.assertTrue(self.app.toggle_recording())
        path = self.app.recorder.path
        self.assertFalse(self.app.toggle_recording())
        self.assertTrue((path / 'manifest.json').is_file())
        codes = [e.code for e in self.app.health.events()]
        self.assertIn('RECORDING_STARTED', codes)

    def test_shutdown_idempotent_und_spaeter_kein_start(self):
        self.app.shutdown()
        self.app.shutdown()
        with self.assertRaises(RuntimeError):
            self.app.start_mode('camera')
        self.assertFalse(any(t.name == 'telemetry-sampler' and t.is_alive() for t in threading.enumerate()))

    def test_bergung_watchdog_stoppt(self):
        self.app.enable_recovery()
        self.app.recovery.command(forward=True, deadman=True)
        deadline = time.monotonic() + 1
        while self.app.recovery.applied_command != (30, 90) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.app.recovery.applied_command, (30, 90))
        time.sleep(0.5)                          # keine weiteren Befehle -> Watchdog stoppt
        self.assertEqual(self.app.recovery.applied_command, (0, 90))
        self.assertEqual(self.app.car.speed, 0)


if __name__ == '__main__':
    unittest.main()
