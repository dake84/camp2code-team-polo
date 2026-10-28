"""Datenmodelle, Ringbuffer, Ereignisprotokoll, Pipeline-Parameter (ohne Hardware)."""
import dataclasses
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from driving_health import DrivingHealthMonitor  # noqa: E402
from models import CameraData, DrivingState  # noqa: E402
from models.sensor_snapshot import SensorSnapshot  # noqa: E402
from telemetry import DurationStats, FrameRingBuffer  # noqa: E402


class ModelTests(unittest.TestCase):
    def test_fehlende_werte_bleiben_none(self):
        state = DrivingState(1, 2, 90, 30)
        self.assertIsNone(state.steering_angle_car)
        self.assertIsNone(state.requested_angle)
        self.assertIsNone(state.monotonic_ns)
        self.assertEqual(SensorSnapshot().infrared, (None,) * 5)
        self.assertIsNone(SensorSnapshot().infrared_age_ns(10))

    def test_fahrentscheidung_unveraenderlich(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            DrivingState(1, 2, 90, 30).speed = 20

    def test_roi_wie_im_prozessor(self):
        camera = CameraData(np.empty(0), np.zeros((240, 320, 3), np.uint8), crop=(0.15, 0.55))
        self.assertEqual(camera.roi_rows(), (int(240 * (1 - 0.55)), int(240 * (1 - 0.15))))


class RingBufferTests(unittest.TestCase):
    def test_unterschiedliche_frequenzen_nur_ueber_frame_id(self):
        ring = FrameRingBuffer(maxlen=8)
        for frame_id in range(1, 11):          # Kamera schneller als Regler
            ring.add_camera(CameraData(np.empty(0), np.empty(0), frame_id=frame_id))
        for frame_id in (2, 5, 9):              # 2 ist bereits verdraengt
            ring.add_driving(DrivingState(frame_id, 0, 90, 30))
        joined = {r.camera.frame_id: (r.driving.frame_id if r.driving else None) for r in ring.snapshot()}
        self.assertEqual(joined[5], 5)
        self.assertEqual(joined[9], 9)
        self.assertNotIn(2, joined)
        self.assertTrue(all(v in (None, k) for k, v in joined.items()))
        self.assertEqual(ring.latest_complete().camera.frame_id, 9)
        self.assertIsNone(ring.find(1))

    def test_snapshot_kopiert_keine_bilder(self):
        ring = FrameRingBuffer()
        image = np.zeros((4, 4), np.uint8)
        ring.add_camera(CameraData(image, image, frame_id=1))
        self.assertIs(ring.snapshot()[0].camera.raw_image, image)


class HealthTests(unittest.TestCase):
    def test_gleiche_stoppursache_in_zweiter_fahrt(self):
        monitor = DrivingHealthMonitor()
        for _ in range(2):
            monitor.mark_start('camera')
            monitor.observe(None, None, True)
            monitor.observe(None, None, False, stop_reason='LANE_TIMEOUT')
        codes = [e.code for e in monitor.events()]
        self.assertEqual(codes.count('STOP_REASON'), 2)
        self.assertEqual(codes.count('CONTROLLER_STARTED'), 2)
        self.assertEqual(codes.count('CONTROLLER_STOPPED'), 2)

    def test_kurze_fahrt_wird_erfasst(self):
        monitor = DrivingHealthMonitor()
        monitor.mark_start('camera')
        monitor.observe(None, None, False, stop_reason='NO_CAMERA_DATA')  # Sampler sah nie "laeuft"
        self.assertIn('CONTROLLER_STOPPED', [e.code for e in monitor.events()])

    def test_puffer_begrenzt(self):
        monitor = DrivingHealthMonitor(max_events=5)
        for i in range(20):
            monitor.record('X', str(i), i)
        self.assertEqual(len(monitor.events()), 5)


class TimingTests(unittest.TestCase):
    def test_messung(self):
        stats = DurationStats()
        with stats.measure('a'):
            pass
        self.assertEqual(stats.summary()['a']['count'], 1)


class PipelineTests(unittest.TestCase):
    PARAMS = ((100, 130), (80, 255), (40, 255), (0.15, 0.55), 3, 5, (50, 100),
              {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True})

    def setUp(self):
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        import pipeline_processors
        self.module = pipeline_processors

    def tearDown(self):
        self.stubs.__exit__(None, None, None)

    def test_stufen_ohne_kopien_und_rohbild_unveraendert(self):
        raw = hardware_stubs.test_image()
        before = raw.copy()
        result = self.module.CopilotLaneProcessor().process(raw, self.PARAMS)
        self.assertEqual(set(result.stages), {'roi', 'hsv', 'mask', 'blur', 'binary', 'morph', 'edges'})
        self.assertTrue(np.shares_memory(result.stages['roi'], raw))
        self.assertIs(result.stages['edges'], result.processed_image)
        np.testing.assert_array_equal(raw, before)
        self.assertEqual(len(result.lanes), 2)

    def test_legacy_und_copilot_gleiche_linien(self):
        raw = hardware_stubs.test_image()
        a = self.module.LaneProcessor().process(raw, self.PARAMS)
        b = self.module.CopilotLaneProcessor().process(raw, self.PARAMS)
        self.assertEqual(a.lanes, b.lanes)
        self.assertEqual(a.stages, {})

    def test_leeres_bild(self):
        with self.assertRaises(ValueError):
            self.module.LaneProcessor().process(np.empty(0), self.PARAMS)

    def test_presets(self):
        import pipeline_config
        with tempfile.TemporaryDirectory() as folder:
            data = dict(pipeline_config.DEFAULT, blur=7)
            pipeline_config.save_preset('halle_hell', data, folder)
            self.assertEqual(pipeline_config.list_presets(folder), ['halle_hell'])
            self.assertEqual(pipeline_config.load_preset('halle_hell', folder)['blur'], 7)
            with self.assertRaises(ValueError):
                pipeline_config.save_preset('../boese', data, folder)


class CameraSensorTests(unittest.TestCase):
    def test_leere_bilder_und_zeitstempel(self):
        frames = [np.empty(0), hardware_stubs.test_image(), hardware_stubs.test_image()]
        with tempfile.TemporaryDirectory() as folder, hardware_stubs.installed(frames=frames):
            import threading
            import ConfigReader
            from CameraSensor import CameraSensor
            from telemetry import FrameRingBuffer as Ring
            config = hardware_stubs.write_config(os.path.join(folder, 'config.json'))
            published = []

            class Car:
                frame_history = Ring()

                @property
                def camera_data(self):
                    return published[-1]

                @camera_data.setter
                def camera_data(self, value):
                    published.append(value)

            sensor = CameraSensor(Car(), ConfigReader.ConfigReader('cam_sensor', config))
            with self.assertRaises(RuntimeError):     # Testbilder erschoepft = Kamera getrennt
                sensor.read_loop(threading.Event())
            self.assertEqual([c.frame_id for c in published], [1, 2])
            self.assertEqual(sensor.released, 1)       # Kamera wird freigegeben
            for camera in published:
                self.assertGreater(camera.monotonic_ns, 0)
                self.assertGreaterEqual(camera.processing_ns, 0)
                self.assertEqual(len(camera.lanes), 2)


class BaseCarLockTests(unittest.TestCase):
    def test_hardwarebefehle_werden_nicht_verschraenkt(self):
        with tempfile.TemporaryDirectory() as folder, hardware_stubs.installed() as log:
            import threading
            import ConfigReader
            import basisklassen
            from BaseCar import BaseCar
            config = hardware_stubs.write_config(os.path.join(folder, 'config.json'))
            car = BaseCar(ConfigReader.ConfigReader('car', config))
            basisklassen.FrontWheels.hold = basisklassen.BackWheels.hold = 0.002
            threads = [threading.Thread(target=lambda a=a: [car.drive(30, a) for _ in range(10)]) for a in (60, 120)]
            threads.append(threading.Thread(target=lambda: [car.stop() for _ in range(10)]))
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(log.max_parallel, 1)
            self.assertIn(car.steering_angle, (60, 90, 120))


if __name__ == '__main__':
    unittest.main()

