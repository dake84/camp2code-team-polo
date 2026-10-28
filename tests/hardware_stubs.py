"""Hardwarefreie Ersatzmodule fuer Tests.

Ersetzt ``basisklassen`` (Motoren, Servo, Sensoren) und ``basisklassen_cam``
(Kamera) durch Attrappen, die nur Aufrufe protokollieren. Es wird keine
GPIO-, I2C- oder Kamerahardware angesprochen und kein Motor angesteuert.

Verwendung::

    with hardware_stubs.installed() as hw:
        import CameraCar  # nutzt die Attrappen
"""
import contextlib
import importlib
import sys
import threading
import types

import numpy as np

# Projektmodule, die Hardwaremodule importieren und daher pro Test frisch geladen werden.
PROJECT_MODULES = ('BaseCar', 'SonicCar', 'SensorCar', 'CameraCar', 'CameraSensor', 'InfraredSensor',
                   'UltrasonicSensor', 'Save_Data_AI', 'car_application', 'Driving', 'driving',
                   'pipeline_processors', 'lane_detector', 'pipeline_ab', 'session_recorder',
                   'replay_hud', 'dashboard_overlay', 'Camp2CodeDashboard')


def _fresh(name):
    """Paketmodule, die pro Test frisch importiert werden (driving/*, dashboard/* mit den Tabs)."""
    return name.startswith(('driving.', 'dashboard.')) or name == 'dashboard'


class CallLog:
    """Gemeinsames Protokoll aller Attrappen (thread-sicher)."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_parallel = 0

    def record(self, name, *args):
        with self.lock:
            self.calls.append((name, args))

    @contextlib.contextmanager
    def section(self, name, *args, hold=0.0):
        """Erfasst parallele Hardwarezugriffe (fuer den Lock-Test in BaseCar)."""
        with self.lock:
            self.active += 1
            self.max_parallel = max(self.max_parallel, self.active)
            self.calls.append((name, args))
        try:
            if hold:
                threading.Event().wait(hold)
            yield
        finally:
            with self.lock:
                self.active -= 1


def build(log=None, frames=None):
    log = log or CallLog()
    hw = types.ModuleType('basisklassen')

    class FrontWheels:
        hold = 0.0

        def __init__(self, turning_offset=0):
            self.turning_offset = turning_offset

        def turn(self, angle):
            with log.section('turn', angle, hold=FrontWheels.hold):
                return angle

        def test(self):
            log.record('fw.test')

    class BackWheels:
        hold = 0.0

        def __init__(self, forward_A=0, forward_B=0):
            self._speed = 0

        @property
        def speed(self):
            return self._speed

        @speed.setter
        def speed(self, value):
            with log.section('speed', value, hold=BackWheels.hold):
                self._speed = value

        def forward(self):
            log.record('forward')

        def backward(self):
            log.record('backward')

        def stop(self):
            self.speed = 0

        def test(self):
            log.record('bw.test')

    class Infrared:
        def __init__(self, references=None):
            pass

        def read_analog(self):
            return [0, 0, 0, 0, 0]

    class Ultrasonic:
        def __init__(self, *args, **kwargs):
            pass

        def distance(self):
            return 100

    hw.FrontWheels, hw.BackWheels, hw.Infrared, hw.Ultrasonic = FrontWheels, BackWheels, Infrared, Ultrasonic

    cam = types.ModuleType('basisklassen_cam')

    class Camera:
        """Liefert Bilder aus ``frames`` (Liste); danach RuntimeError wie eine getrennte Kamera."""

        def __init__(self, *args, **kwargs):
            self._frames = list(frames or [])
            self.released = 0

        def get_frame(self):
            if not self._frames:
                raise RuntimeError('Keine weiteren Testbilder')
            return self._frames.pop(0)

        def release(self):
            self.released += 1

        def set_camera_controls(self, controls):
            log.record('camera_controls', controls)

    cam.Camera = Camera
    return {'basisklassen': hw, 'basisklassen_cam': cam}, log


def test_image(width=320, height=240, lanes=True):
    """Synthetisches BGR-Bild mit zwei blauen Linien (HSV-Hue ca. 120)."""
    import cv2
    image = np.full((height, width, 3), (180, 160, 200), dtype=np.uint8)
    if lanes:
        cv2.line(image, (int(width * .2), height - 1), (int(width * .42), 0), (200, 40, 20), 10)
        cv2.line(image, (int(width * .8), height - 1), (int(width * .58), 0), (200, 40, 20), 10)
    return image


@contextlib.contextmanager
def installed(frames=None, extra=None):
    """Installiert die Attrappen und laedt Projektmodule frisch; stellt danach alles wieder her."""
    modules, log = build(frames=frames)
    if extra:
        modules.update(extra)
    saved = {name: sys.modules.get(name) for name in list(modules) + list(PROJECT_MODULES)}
    saved.update({name: mod for name, mod in sys.modules.items() if _fresh(name)})
    for name in list(sys.modules):
        if name in PROJECT_MODULES or _fresh(name):
            del sys.modules[name]
    sys.modules.update(modules)
    try:
        yield log
    finally:
        for name in list(sys.modules):
            if name in modules or name in PROJECT_MODULES or _fresh(name):
                del sys.modules[name]
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


def write_config(path, **sections):
    """Schreibt eine minimale config.json fuer ConfigReader-Tests."""
    import json
    data = {'car': {'v_min': 20, 'v_max': 80, 'turning_offset': 0},
            'cameracar_controller': {}, 'cam_sensor': {'scan_frequency': 200, 'colormode': 'BGR'}}
    for key, value in sections.items():
        data.setdefault(key, {}).update(value)
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(data, file)
    return str(path)


def pin_pipeline_config():
    """Fixiert ``pipeline_config.load`` auf die Standardwerte und startet den Patch.

    Dashboard-Tests sollen nicht von der ``pipeline_studio.json`` im Projekt abhaengen. Aufrufer muessen
    ``.stop()`` aufrufen.
    """
    import copy
    from unittest import mock
    import pipeline_config
    real_load = pipeline_config.load

    def pinned(path=None):
        # Nur die aktive Konfiguration (ohne Pfad) wird fixiert; Presets (load_preset -> load(pfad)) bleiben echt.
        return copy.deepcopy(pipeline_config.DEFAULT) if path is None else real_load(path)

    patcher = mock.patch.object(pipeline_config, 'load', side_effect=pinned)
    patcher.start()
    return patcher


def fresh_import(name):
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)

