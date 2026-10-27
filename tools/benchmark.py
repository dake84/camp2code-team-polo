"""Hardwarefreier Mikro-Benchmark fuer Camp2Code (ohne Motoren, ohne Kamera).

Misst mit synthetischen 320x240-Bildern:
  1. Bildverarbeitung je Frame (LaneProcessor / CopilotLaneProcessor)
  2. Speicherbedarf eines Ringbuffer-Eintrags
  3. Reglerrechnung je Entscheidung (CameraMode._run mit Attrappen-Fahrzeug)
  4. Fahrbefehle je Kameraframe (Regler-Abtastung 500 Hz, Kamera ca. 15 Hz)
  5. JPEG-Kodierung fuer das Dashboard

Aufruf im Repository-Root (auf dem Pi und auf dem Entwicklungsrechner):
    python tools/benchmark.py
Vorher/Nachher-Vergleich: auf altem und neuem Stand ausfuehren (git stash/checkout).
Die Ergebnisse sind Richtwerte fuer den jeweiligen Rechner, keine Fahrzeugmessung.
"""
import os
import statistics
import sys
import time
import types

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
for name in ('evdev', 'curses'):  # nur fuer alte Staende mit Modulimporten noetig
    if name not in sys.modules:
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.InputDevice = object
            stub.ecodes = types.SimpleNamespace()
            sys.modules[name] = stub

import hardware_stubs  # noqa: E402

PARAMS = ((100, 130), (80, 255), (40, 255), (0.15, 0.55), 3, 5, (50, 100),
          {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True})


def timeit(func, repeat=300):
    values = []
    for _ in range(repeat):
        start = time.perf_counter()
        func()
        values.append((time.perf_counter() - start) * 1000)
    return statistics.median(values), statistics.mean(values)


def main():
    with hardware_stubs.installed():
        import pipeline_processors
        from models import CameraData
        raw = hardware_stubs.test_image()
        print(f'Python {sys.version.split()[0]} · OpenCV {cv2.__version__} · NumPy {np.__version__}')
        for name in ('LaneProcessor', 'CopilotLaneProcessor'):
            processor = getattr(pipeline_processors, name)()
            med, avg = timeit(lambda: processor.process(raw, PARAMS))
            print(f'1. {name:22s} Median {med:6.2f} ms · Mittel {avg:6.2f} ms je Frame')
        result = pipeline_processors.CopilotLaneProcessor().process(raw, PARAMS)
        own = [result.processed_image] + [a for a in result.stages.values() if a.base is None or not np.shares_memory(a, raw)]
        unique = {id(a): a for a in own}
        total = raw.nbytes + sum(a.nbytes for a in unique.values())
        print(f'2. Ringbuffer-Eintrag (Copilot) ca. {total / 1024:.0f} KiB · 60 Frames ca. {60 * total / 2**20:.1f} MiB')

        import ConfigReader
        import tempfile
        from driving.camera_mode import CameraMode
        from telemetry import FrameRingBuffer
        with tempfile.TemporaryDirectory() as folder:
            config = hardware_stubs.write_config(os.path.join(folder, 'config.json'))

            class Car:
                def __init__(self):
                    self.frame_history = FrameRingBuffer(60)
                    self.camera_data = None
                    self.drives = 0
                    self.steering_angle, self.speed = 90, 0
                    self._camera_data_processed = None

                def drive(self, speed=None, angle=None):
                    self.drives += 1
                    self.speed, self.steering_angle = speed, angle

                def stop(self):
                    self.drive(0, 90)

                driving_state = property(lambda self: None, lambda self, s: self.frame_history.add_driving(s))
                camera_data_processed = property(lambda self: None, lambda self, v: None)

            car = Car()
            mode = CameraMode(car=car, cfg=ConfigReader.ConfigReader('cameracar_controller', config))
            frame = 0

            def publish():
                nonlocal frame
                frame += 1
                data = CameraData(process_image=result.processed_image, raw_image=raw, frame_id=frame,
                                  timestamp=time.time_ns(), monotonic_ns=time.monotonic_ns(), lanes=result.lanes,
                                  crop=(0.15, 0.55))
                car.camera_data = data
                car.frame_history.add_camera(data)

            publish()
            mode._pre_run()

            def decide():
                publish()
                mode._run()
            med, avg = timeit(decide, 500)
            print(f'3. Reglerrechnung je neuem Frame       Median {med:6.3f} ms · Mittel {avg:6.3f} ms')
            car.drives = 0
            mode.start()
            stop = time.monotonic() + 2.0
            frames = 0
            while time.monotonic() < stop:
                publish()
                frames += 1
                time.sleep(1 / 15)
            mode.stop()
            print(f'4. Fahrbefehle je Kameraframe          {car.drives / max(1, frames):6.1f} '
                  f'({car.drives} Befehle bei {frames} Frames, 500 Hz Abtastung)')
        from dashboard_overlay import render_calibration_overlay
        camera = CameraData(process_image=result.processed_image, raw_image=raw, frame_id=1,
                            lanes=result.lanes, crop=(0.15, 0.55))
        overlay = render_calibration_overlay(camera)
        med, _ = timeit(lambda: cv2.imencode('.jpg', overlay, [cv2.IMWRITE_JPEG_QUALITY, 70]))
        print(f'5. JPEG-Kodierung 320x240 q70          Median {med:6.2f} ms je Bild')


if __name__ == '__main__':
    main()
