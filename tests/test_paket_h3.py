"""Paket H3: getrennte Aufloesungen fuer Aufnahme (gross) und Bildverarbeitung (klein) ueber den Dual-Stream.

Hardwarefrei: Picamera2 und OpenCV-Kamera sind Attrappen, es gibt keine Motoransteuerung. Das ECHTE Dash 4 laeuft ueber den
Flask-Testclient, sofern installiert. Nicht getestet: die echte Kamera (Sensor-Modi, Dual-Stream-Annahme, Bildrate,
Belichtung), Bedienung im Browser, Fahrt am Auto und Performance auf dem Pi - dafuer gibt es scripts/cam_check.py.
"""
import base64
import contextlib
import csv
import importlib.util
import json
import logging
import math
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from models import CameraData, DrivingState  # noqa: E402
from test_paket_f import PARAMS  # noqa: E402  (vor jeder Dash-Attrappe importieren)

ROOT = Path(__file__).resolve().parents[1]
HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
PARAMS_TUPLE = (tuple(PARAMS[0]), tuple(PARAMS[1]), tuple(PARAMS[2]), tuple(PARAMS[3]), PARAMS[4], PARAMS[5],
                tuple(PARAMS[6]), dict(PARAMS[7]))
LEFT_COLOR, RIGHT_COLOR = (50, 230, 100), (20, 200, 255)       # dashboard_overlay.LANE_COLORS


def lane_image(width, height, shift=0):
    """BGR-Bild mit zwei dicken blauen Linien (HSV-Hue ca. 118); Linienbreite waechst mit der Bildgroesse."""
    image = np.full((height, width, 3), (180, 160, 200), dtype=np.uint8)
    thick = max(10, width // 32)
    cv2.line(image, (int(width * .2) + shift, height - 1), (int(width * .42) + shift, 0), (200, 40, 20), thick)
    cv2.line(image, (int(width * .8) + shift, height - 1), (int(width * .58) + shift, 0), (200, 40, 20), thick)
    return image


def scene(width, height):
    """Glattes Testbild (Farbverlauf) fuer Vergleiche zwischen grossem und kleinem Bild."""
    ys, xs = np.mgrid[0:height, 0:width]
    image = np.dstack([xs * 255 // max(1, width - 1), ys * 255 // max(1, height - 1),
                       (xs + ys) * 255 // max(1, width + height - 2)]).astype(np.uint8)
    cv2.rectangle(image, (width // 8, height // 8), (width // 2, height // 2), (40, 200, 220), -1)
    return image


def color_near(image, point, color, radius=8, tolerance=40):
    x, y = point
    window = image[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1].reshape(-1, 3).astype(int)
    return bool((np.abs(window - np.array(color)).sum(axis=1) < tolerance).any())


def x_on_line(p0, p1, y):
    """x der Geraden durch p0 und p1 bei der Bildzeile y."""
    return p0[0] + (y - p0[1]) / (p1[1] - p0[1]) * (p1[0] - p0[0])


# ------------------------------------------------------------------ Attrappen fuer Picamera2 und OpenCV
class FakePicamera2:
    """Attrappe: lehnt auf Wunsch den lores-Stream ab; liefert beide Bilder aus DERSELBEN Szene (mit Zeilen-Stride)."""
    reject_lores = False
    bad_yuv = False
    instances = []
    STRIDE_PAD = 16
    META = {'ExposureTime': 8000, 'AnalogueGain': 2.0, 'DigitalGain': 1.0, 'FrameDuration': 33333, 'Lux': 120.5,
            'SensorTimestamp': 1000}

    def __init__(self, number=0):
        self.number = number
        self.configs = []
        self.active = None
        self.controls = []
        self.started = self.closed = False
        self.camera_controls = {'FrameDurationLimits': (33333, 120000, 33333), 'ExposureValue': (-8.0, 8.0, 0.0)}
        self.sensor_modes = [{'size': (1296, 972), 'fps': 43.25, 'bit_depth': 10, 'format': 'SGBRG10',
                              'crop_limits': (0, 0, 2592, 1944)},
                             {'size': (2592, 1944), 'fps': 15.63, 'bit_depth': 10, 'format': 'SGBRG10',
                              'crop_limits': (0, 0, 2592, 1944)}]
        self.captures = 0
        FakePicamera2.instances.append(self)

    @staticmethod
    def global_camera_info():
        return [{'Id': '/base/soc/i2c0mux/i2c@1/ov5647@36', 'Model': 'ov5647'}]

    def create_preview_configuration(self, main=None, lores=None, buffer_count=4, queue=True, controls=None):
        config = {'main': dict(main or {}), 'lores': dict(lores) if lores else None,
                  'buffer_count': buffer_count, 'queue': queue}
        config['main'].setdefault('size', (640, 480))
        return config

    def configure(self, config):
        self.configs.append(config)
        if config.get('lores') and FakePicamera2.reject_lores:
            raise RuntimeError('Configuration rejected: lores stream')
        active = {'main': dict(config['main']), 'lores': None}
        if config.get('lores'):
            width, height = config['lores']['size']
            active['lores'] = {'size': (width, height), 'format': 'YUV420', 'stride': width + self.STRIDE_PAD}
        self.active = active

    def camera_configuration(self):
        return self.active

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def set_controls(self, controls):
        self.controls.append(dict(controls))

    def _main(self):
        width, height = self.active['main']['size']
        return scene(width, height)

    def capture_array(self, name='main'):
        self.captures += 1
        return self._main()

    def capture_arrays(self, names):
        self.captures += 1
        main = self._main()
        arrays = []
        for name in names:
            if name == 'main':
                arrays.append(main)
                continue
            width, height = self.active['lores']['size']
            stride = self.active['lores']['stride']
            small = cv2.resize(main, (width, height), interpolation=cv2.INTER_AREA)   # dieselbe Szene
            padded = np.zeros((height, stride, 3), np.uint8)                        # Zeilen mit Stride aufgefuellt
            padded[:, :width] = small
            yuv = cv2.cvtColor(padded, cv2.COLOR_BGR2YUV_I420)
            arrays.append(yuv[:-2] if FakePicamera2.bad_yuv else yuv)
        return arrays, dict(self.META)


class FakeCapture:
    """Attrappe fuer cv2.VideoCapture (USB-/OpenCV-Backend)."""
    def __init__(self, device):
        self.props = {cv2.CAP_PROP_FRAME_WIDTH: 640, cv2.CAP_PROP_FRAME_HEIGHT: 480}
        self.released = False

    def isOpened(self):
        return not self.released

    def set(self, key, value):
        self.props[key] = value
        return True

    def get(self, key):
        return self.props.get(key, 0)

    def grab(self):
        return True

    def read(self):
        return True, scene(int(self.props[cv2.CAP_PROP_FRAME_WIDTH]), int(self.props[cv2.CAP_PROP_FRAME_HEIGHT]))

    def release(self):
        self.released = True


@contextlib.contextmanager
def real_camera_module():
    """Die ECHTE basisklassen_cam mit Picamera2-Attrappe (nicht die Attrappe aus hardware_stubs)."""
    fake = type(sys)('picamera2')
    fake.Picamera2 = FakePicamera2
    saved = sys.modules.get('picamera2')
    sys.modules['picamera2'] = fake
    FakePicamera2.instances = []
    FakePicamera2.reject_lores = FakePicamera2.bad_yuv = False
    spec = importlib.util.spec_from_file_location('c2c_basisklassen_cam_h3', ROOT / 'basisklassen_cam.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        if saved is None:
            sys.modules.pop('picamera2', None)
        else:
            sys.modules['picamera2'] = saved


# ------------------------------------------------------------------ Geometrie (rein)
class GeometryTests(unittest.TestCase):
    def setUp(self):
        import camera_geometry
        self.g = camera_geometry

    def test_verarbeitungsgroesse_seitenverhaeltnis_und_nie_groesser(self):
        g = self.g
        capture = (1280, 960)
        self.assertEqual(g.resolve_process_size(capture, 320, 240), ((320, 240), None))
        self.assertEqual(g.resolve_process_size(capture, 320, 0)[0], (320, 240))        # Hoehe folgt aus der Breite
        self.assertEqual(g.resolve_process_size(capture, 0, 240)[0], (320, 240))        # Breite folgt aus der Hoehe
        self.assertEqual(g.resolve_process_size((1920, 1080), 640, 0)[0], (640, 360))
        self.assertEqual(g.resolve_process_size((1920, 1080), 321, 0)[0], (321, 182))   # gerade Hoehe
        self.assertEqual(g.resolve_process_size(capture, 0, 0), (None, None))           # keine Angabe: wie bisher
        self.assertEqual(g.resolve_process_size(capture, None, None), (None, None))
        self.assertEqual(g.resolve_process_size(capture, 1280, 960), (None, None))      # gleiche Groesse: kein Verkleinern
        for width, height in ((1600, 1200), (320, 1200), (2000, 0), (320, 100), (16, 12), (-4, 3), (320, 320)):
            size, hint = g.resolve_process_size(capture, width, height)
            self.assertIsNone(size, (width, height))
            self.assertTrue(hint, (width, height))
        self.assertEqual(g.resolve_process_size(None, 320, 240)[0], None)               # Aufnahmegroesse unbekannt
        self.assertEqual(g.resolve_process_size(capture, 322, 241)[0], (322, 241))      # Toleranz 2 % fuer Rundung

    def test_linien_umrechnung_trifft_dieselben_punkte(self):
        g = self.g
        capture, process, crop = (1280, 960), (320, 240), (0.15, 0.55)
        top_big, top_small = g.roi_top_px(960, crop), g.roi_top_px(240, crop)
        self.assertEqual((top_big, top_small), (int(960 * (1 - .55)), int(240 * (1 - .55))))   # (431, 107) wegen Gleitkomma
        # Gerade im grossen Vollbild: von (256, 959) nach (537, 0); im kleinen Vollbild: durch die Skalierung
        big0, big1 = (256, 959), (537, 0)
        small0, small1 = (big0[0] / 4, big0[1] / 4), (big1[0] / 4, big1[1] / 4)
        m = (small1[1] - small0[1]) / (small1[0] - small0[0])
        b = small0[1] - m * small0[0] - top_small                                      # ROI-Koordinaten (klein)
        (lane,) = g.convert_lanes([(m, b)], process, capture, top_small, top_big)
        for x in (100, 256, 400, 537):
            y_roi = lane[0] * x + lane[1]                                               # ROI-Koordinaten (gross)
            expected = big0[1] + (x - big0[0]) / (big1[0] - big0[0]) * (big1[1] - big0[1])
            self.assertAlmostEqual(y_roi + top_big, expected, delta=0.6)

    def test_umrechnung_laesst_gespeicherte_werte_unveraendert(self):
        g = self.g
        lanes = ((-1.5, 160.0), None, (1.2, -300.0))
        before = tuple(lanes)
        result = g.convert_lanes(lanes, (320, 240), (1280, 960), 108, 432)
        self.assertEqual(lanes, before)
        self.assertIsNot(result, lanes)
        self.assertIsNone(result[1])
        self.assertEqual(g.convert_lanes(lanes, (320, 240), (320, 240), 108, 108), before)  # gleiche Groesse: gleich
        bad = g.convert_lanes([(float('nan'), 1.0), ('x', 2), (1.0,)], (320, 240), (640, 480))
        self.assertEqual(bad, (None, None, None))
        self.assertEqual(g.convert_lanes(None, (320, 240), (640, 480)), ())
        self.assertEqual(g.convert_lanes(lanes, None, (640, 480)), before)                  # unbekannte Groesse

    def test_ringbuffer_laenge_je_bildgroesse(self):
        g = self.g
        self.assertEqual(g.ring_length((320, 240)), 60)                          # wie bisher
        self.assertEqual(g.ring_length((320, 240), (320, 240)), 60)
        self.assertEqual(g.ring_length((160, 120)), 60)                          # nie mehr als der Standard
        lengths = [g.ring_length(size, (320, 240)) for size in ((640, 480), (1280, 720), (1280, 960), (1920, 1080))]
        self.assertEqual(lengths, sorted(lengths, reverse=True))                 # groesser = hoechstens gleich lang
        self.assertTrue(all(g.RING_MIN <= n <= 60 for n in lengths), lengths)
        self.assertEqual(g.ring_length((4000, 3000), (320, 240)), g.RING_MIN)    # nie unter dem Minimum
        self.assertLess(g.ring_length((1280, 960), (320, 240)), 15)
        # Budget: die geschaetzten Bytes bleiben (bis auf das Minimum) unter dem bisherigen Budget
        budget = 60 * g.frame_bytes_estimate((320, 240), (320, 240))
        for size in ((640, 480), (1280, 720), (1280, 960)):
            self.assertLessEqual(g.ring_length(size, (320, 240)) * g.frame_bytes_estimate(size, (320, 240)), budget)

    def test_ui_skalierung_und_software_verkleinern(self):
        g = self.g
        self.assertEqual([g.ui_scale(h) for h in (120, 240)], [1.0, 1.0])
        self.assertEqual(g.ui_scale(960), 4.0)
        self.assertEqual(g.ui_scale('x'), 1.0)
        image = scene(640, 480)
        small = g.downscale_image(image, (320, 240))
        self.assertEqual(small.shape, (240, 320, 3))
        self.assertIs(g.downscale_image(image, (640, 480)), image)               # gleiche Groesse: dasselbe Bild
        self.assertIs(g.downscale_image(image, None), image)
        self.assertIs(g.downscale_image(image, (1280, 960)), image)              # nie hochskalieren
        self.assertEqual(g.downscale_image(image[:, :, 0], (320, 240)).shape, (240, 320))


# ------------------------------------------------------------------ YUV420 und Metadaten
class Yuv420Tests(unittest.TestCase):
    def test_stride_wird_beachtet(self):
        with real_camera_module() as cam:
            bgr = scene(320, 240)
            for pad in (0, 16, 48):
                stride = 320 + pad
                padded = np.zeros((240, stride, 3), np.uint8)
                padded[:, :320] = bgr
                yuv = cv2.cvtColor(padded, cv2.COLOR_BGR2YUV_I420)
                self.assertEqual(yuv.shape, (360, stride))
                result = cam.yuv420_to_bgr(yuv, 320, 240)
                self.assertEqual(result.shape, (240, 320, 3))
                self.assertTrue(result.flags['C_CONTIGUOUS'])
                self.assertLess(np.abs(result.astype(int) - bgr.astype(int)).mean(), 6, pad)   # Rundung/Chroma

    def test_ungueltige_formen(self):
        with real_camera_module() as cam:
            good = cv2.cvtColor(np.zeros((240, 320, 3), np.uint8), cv2.COLOR_BGR2YUV_I420)
            for yuv, width, height in ((None, 320, 240), (good[:-2], 320, 240), (good, 336, 240), (good, 320, 238),
                                       (np.zeros((360, 320, 1), np.uint8), 320, 240)):
                with self.assertRaises(ValueError, msg=(None if yuv is None else yuv.shape, width, height)):
                    cam.yuv420_to_bgr(yuv, width, height)

    def test_metadaten_normalisiert(self):
        with real_camera_module() as cam:
            meta = cam.normalize_metadata({'ExposureTime': 8000, 'AnalogueGain': 2.5, 'FrameDuration': 33333,
                                           'Lux': 12.0, 'DigitalGain': True, 'SensorTimestamp': float('nan')})
            self.assertEqual((meta['exposure_us'], meta['gain'], meta['frame_duration_us'], meta['lux']),
                             (8000, 2.5, 33333, 12.0))
            self.assertIsNone(meta['digital_gain'])                # Wahrheitswert ist keine Messung
            self.assertIsNone(meta['sensor_timestamp_ns'])         # nicht endlich
            for bad in (None, {}, [], 'x'):
                self.assertEqual(set(cam.normalize_metadata(bad).values()), {None})


# ------------------------------------------------------------------ Kameraklasse (Picamera2- und OpenCV-Attrappe)
class CameraClassTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_altverhalten_ohne_neue_argumente(self):
        logging.disable(logging.CRITICAL)
        with real_camera_module() as module:
            camera = module.Camera(width=320, height=240)
            fake = FakePicamera2.instances[-1]
            self.assertEqual(len(fake.configs), 1)                                   # genau eine Konfiguration
            self.assertEqual(fake.configs[0], {'main': {'format': 'RGB888', 'size': (320, 240)},
                                               'lores': None, 'buffer_count': 2, 'queue': False})
            self.assertEqual(fake.controls, [])                                      # keine Steuerung gesetzt
            self.assertTrue(fake.started)
            self.assertEqual(camera.get_size(), (320, 240))
            self.assertIsNone(camera.get_small_size())
            self.assertIsNone(camera.small_source)
            frame = camera.get_frame()                                               # wie bisher: gedrehtes Hauptbild
            np.testing.assert_array_equal(frame, cv2.flip(scene(320, 240), -1))
            main, small = camera.get_frames()                                        # neue Methode ohne kleines Bild
            self.assertIsNone(small)
            np.testing.assert_array_equal(main, frame)
            camera.release()
            self.assertTrue(fake.closed)
            with self.assertRaises(RuntimeError):
                camera.get_frame()

    def test_konstruktor_validierung_bleibt_und_neue_argumente(self):
        with real_camera_module() as module:
            for kwargs in ({'backend': 'x'}, {'colorspace': 'x'}, {'width': 0}, {'height': -1}, {'small_mode': 'x'}):
                with self.assertRaises(ValueError, msg=kwargs):
                    module.Camera(**kwargs)

    def test_dual_stream_ok(self):
        with real_camera_module() as module:
            camera = module.Camera(width=1280, height=960, small_size=(320, 240))
            fake = FakePicamera2.instances[-1]
            self.assertEqual(len(fake.configs), 1)
            self.assertEqual(fake.configs[0]['lores'], {'format': 'YUV420', 'size': (320, 240)})
            self.assertEqual(fake.configs[0]['main']['format'], 'RGB888')
            self.assertEqual((camera.get_size(), camera.get_small_size(), camera.small_source),
                             ((1280, 960), (320, 240), 'camera'))
            self.assertEqual(camera.get_sizes(), {'main': (1280, 960), 'small': (320, 240), 'small_source': 'camera'})
            info = camera.get_stream_info()
            self.assertEqual((info['backend'], info['lores_stride'], info['dual_note']), ('picamera2', 336, None))
            main, small = camera.get_frames()
            self.assertEqual((main.shape, small.shape), ((960, 1280, 3), (240, 320, 3)))
            self.assertEqual(fake.captures, 1)                                       # EINE Aufnahme fuer beide Bilder
            reference = cv2.resize(main, (320, 240), interpolation=cv2.INTER_AREA)   # gleiche Szene, gleich gedreht
            self.assertLess(np.abs(reference.astype(int) - small.astype(int)).mean(), 8)
            meta = camera.get_metadata()
            self.assertEqual((meta['exposure_us'], meta['gain'], meta['frame_duration_us'], meta['lux']),
                             (8000, 2.0, 33333, 120.5))
            self.assertIsNot(camera.get_metadata(), meta)                            # Kopie
            camera.release()

    def test_dual_stream_gedreht_und_im_farbraum(self):
        with real_camera_module() as module:
            gray = module.Camera(width=640, height=480, small_size=(320, 240), colorspace='gray', flip=False)
            main, small = gray.get_frames()
            self.assertEqual((main.ndim, small.ndim, small.shape), (2, 2, (240, 320)))
            plain = module.Camera(width=640, height=480, small_size=(320, 240), flip=False)
            main, small = plain.get_frames()
            flipped = module.Camera(width=640, height=480, small_size=(320, 240), flip=True)
            main_f, small_f = flipped.get_frames()
            np.testing.assert_array_equal(main_f, cv2.flip(main, -1))
            np.testing.assert_array_equal(small_f, cv2.flip(small, -1))               # beide gleich gedreht

    def test_dual_stream_abgelehnt_faellt_auf_software_zurueck(self):
        with real_camera_module() as module:
            FakePicamera2.reject_lores = True
            with self.assertLogs('Camera', 'WARNING') as logs:
                camera = module.Camera(width=1280, height=960, small_size=(320, 240))   # Start scheitert nicht
            fake = FakePicamera2.instances[-1]
            self.assertEqual(len(fake.configs), 2)                                   # lores abgelehnt, dann ohne lores
            self.assertIsNone(fake.configs[1]['lores'])
            self.assertTrue(any('Dual-Stream' in line for line in logs.output), logs.output)
            self.assertEqual((camera.get_small_size(), camera.small_source), ((320, 240), 'software'))
            self.assertIn('abgelehnt', camera.get_stream_info()['dual_note'])
            main, small = camera.get_frames()
            self.assertEqual((main.shape, small.shape), ((960, 1280, 3), (240, 320, 3)))
            np.testing.assert_array_equal(small, cv2.resize(main, (320, 240), interpolation=cv2.INTER_AREA))

    def test_modus_software_versucht_keinen_zweiten_stream(self):
        with real_camera_module() as module:
            camera = module.Camera(width=1280, height=960, small_size=(320, 240), small_mode='software')
            fake = FakePicamera2.instances[-1]
            self.assertEqual(len(fake.configs), 1)
            self.assertIsNone(fake.configs[0]['lores'])
            self.assertEqual(camera.small_source, 'software')
            self.assertEqual(camera.get_frames()[1].shape, (240, 320, 3))

    def test_opencv_backend_nutzt_software_verkleinern_mit_hinweis(self):
        with real_camera_module() as module, mock.patch.object(module.cv2, 'VideoCapture', FakeCapture):
            with self.assertLogs('Camera', 'WARNING') as logs:
                camera = module.Camera(backend='opencv', width=1280, height=960, small_size=(320, 240))
            self.assertTrue(any('OpenCV' in line for line in logs.output), logs.output)
            self.assertEqual((camera.get_size(), camera.get_small_size(), camera.small_source),
                             ((1280, 960), (320, 240), 'software'))
            main, small = camera.get_frames()
            self.assertEqual((main.shape, small.shape), ((960, 1280, 3), (240, 320, 3)))
            self.assertEqual(set(camera.get_metadata().values()), {None})            # OpenCV liefert keine Metadaten
            self.assertEqual(camera.sensor_modes(), [])
            self.assertFalse(camera.set_frame_rate(30))                              # kein FrameDurationLimits

    def test_kleines_bild_nicht_groesser_oder_gleich_gross(self):
        logging.disable(logging.CRITICAL)
        with real_camera_module() as module:
            same = module.Camera(width=320, height=240, small_size=(320, 240))
            self.assertIsNone(same.get_small_size())
            self.assertIsNone(same.get_frames()[1])
            bigger = module.Camera(width=320, height=240, small_size=(640, 480))
            self.assertIsNone(bigger.get_small_size())
            self.assertIsNone(FakePicamera2.instances[-1].configs[0]['lores'])
            for invalid in ((0, 10), (-3, 5), 'x', (None, None)):
                camera = module.Camera(width=320, height=240, small_size=invalid)
                self.assertIsNone(camera.get_small_size(), invalid)

    def test_tatsaechliche_groesse_wird_uebernommen(self):
        with real_camera_module() as module:
            original = FakePicamera2.configure
            def aligning(self, config):
                if config.get('lores'):
                    config = dict(config, lores=dict(config['lores'], size=(318, 238)))   # Kamera passt die Groesse an
                original(self, config)
            with mock.patch.object(FakePicamera2, 'configure', aligning):
                camera = module.Camera(width=1280, height=960, small_size=(320, 240))
            self.assertEqual(camera.get_small_size(), (318, 238))
            self.assertEqual(camera.get_frames()[1].shape, (238, 318, 3))

    def test_lores_umwandlung_schlaegt_fehl_software_fuer_diesen_frame(self):
        with real_camera_module() as module:
            camera = module.Camera(width=640, height=480, small_size=(320, 240))
            FakePicamera2.bad_yuv = True
            with self.assertLogs('Camera', 'WARNING') as logs:
                main, small = camera.get_frames()
                main, small = camera.get_frames()                                    # Warnung nur einmal
            self.assertEqual(sum('lores-Bild nicht umwandelbar' in line for line in logs.output), 1)
            self.assertEqual(small.shape, (240, 320, 3))

    def test_aufnahmefehler(self):
        with real_camera_module() as module:
            camera = module.Camera(width=640, height=480, small_size=(320, 240))
            with mock.patch.object(FakePicamera2, 'capture_arrays', side_effect=OSError('weg')):
                with self.assertRaises(RuntimeError):
                    camera.get_frames()
            camera.release()
            with self.assertRaises(RuntimeError):
                camera.get_frames()

    def test_bildrate_fest_und_automatik_belichtungskorrektur(self):
        logging.disable(logging.CRITICAL)
        with real_camera_module() as module:
            camera = module.Camera(width=640, height=480, frame_rate=30)
            fake = FakePicamera2.instances[-1]
            self.assertEqual(fake.controls, [{'FrameDurationLimits': (33333, 33333)}])
            self.assertEqual(camera.get_frame_rate_setting(), 30.0)
            self.assertTrue(camera.set_frame_rate(0))                                # Automatik: voller Bereich
            self.assertEqual(fake.controls[-1], {'FrameDurationLimits': (33333, 120000)})
            self.assertIsNone(camera.get_frame_rate_setting())
            self.assertTrue(camera.set_frame_rate(60))
            self.assertEqual(fake.controls[-1], {'FrameDurationLimits': (16667, 16667)})
            for bad in (-5, float('nan'), 'x'):
                self.assertFalse(camera.set_frame_rate(bad), bad)
            self.assertEqual(fake.controls[-1], {'FrameDurationLimits': (16667, 16667)})   # nichts gesetzt
            self.assertTrue(camera.set_exposure_value(-1.5))
            self.assertEqual(fake.controls[-1], {'ExposureValue': -1.5})
            self.assertFalse(camera.set_exposure_value('x'))
            fake.camera_controls = {}                                                # Kamera meldet keine Grenzen
            self.assertFalse(camera.set_frame_rate(0))

    def test_sensor_modi_werden_gelesen_nicht_festgeschrieben(self):
        with real_camera_module() as module:
            camera = module.Camera(width=640, height=480)
            modes = camera.sensor_modes()
            self.assertEqual([m['size'] for m in modes], [(1296, 972), (2592, 1944)])
            self.assertEqual(modes[0]['fps'], 43.25)
            fake = FakePicamera2.instances[-1]
            self.assertNotIn('sensor', fake.configs[0])                              # kein Sensor-Modus festgelegt
            self.assertNotIn('raw', fake.configs[0])
            type(fake).sensor_modes = mock.PropertyMock(side_effect=RuntimeError('x'))
            try:
                with self.assertLogs('Camera', 'WARNING'):
                    self.assertEqual(camera.sensor_modes(), [])
            finally:
                del type(fake).sensor_modes

    def test_hauptprogramm_bleibt_lauffaehig(self):
        source = (ROOT / 'basisklassen_cam.py').read_text(encoding='utf-8')
        self.assertIn('if __name__ == "__main__":', source)
        self.assertIn('with Camera() as cam:', source)
        self.assertIn('cam.get_frame()', source)
        compile(source, 'basisklassen_cam.py', 'exec')


# ------------------------------------------------------------------ Einstellungen aus config.json
class SettingsTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def read(self, **cam_sensor):
        import ConfigReader
        from camera_settings import read_camera_settings
        path = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'), cam_sensor=cam_sensor)
        return read_camera_settings(ConfigReader.ConfigReader('cam_sensor', path))

    def test_ohne_neue_eintraege_wie_bisher(self):
        s = self.read(scan_frequency=25, colormode='BGR')
        self.assertEqual((s.width, s.height, s.process_width, s.process_height, s.downscale, s.frame_rate,
                          s.exposure_value, s.notes), (640, 480, 0, 0, 'auto', 0.0, 1.0, ()))

    def test_gueltige_werte(self):
        s = self.read(width=1280, height=960, process_width=320, process_height=240, downscale='Camera',
                      frame_rate=30, exposure_value=-0.5)
        self.assertEqual((s.width, s.height, s.process_width, s.process_height, s.downscale, s.frame_rate,
                          s.exposure_value), (1280, 960, 320, 240, 'camera', 30.0, -0.5))
        self.assertEqual(s.notes, ())

    def test_fehlerhafte_werte_ergeben_standardwerte(self):
        s = self.read(width='abc', height=-5, process_width='x', process_height=None, downscale='turbo',
                      frame_rate='schnell', exposure_value=99)
        self.assertEqual((s.width, s.height, s.process_width, s.process_height, s.downscale, s.frame_rate,
                          s.exposure_value), (640, 480, 0, 0, 'auto', 0.0, 1.0))
        self.assertGreaterEqual(len(s.notes), 3)
        for bad in (dict(frame_rate=-3), dict(frame_rate=500), dict(frame_rate=float('inf')),
                    dict(exposure_value=float('nan')), dict(exposure_value=-9), dict(downscale=None),
                    dict(process_width=-4), dict(width=0)):
            s = self.read(**bad)
            self.assertEqual((s.frame_rate, s.exposure_value, s.downscale, s.process_width, s.width),
                             (0.0, 1.0, 'auto', 0, 640), bad)
            self.assertTrue(s.notes, bad)

    def test_unlesbare_konfiguration_wirft_nie(self):
        from camera_settings import CameraSettings, safe_camera_settings
        broken = SimpleNamespace(get_int=lambda *a: 1 / 0, get_float=lambda *a: 1 / 0, get_str=lambda *a: 1 / 0)
        s = safe_camera_settings(broken)
        self.assertEqual(s.width, 640)                                              # Standardwerte
        self.assertEqual((s.frame_rate, s.exposure_value, s.downscale), (0.0, 1.0, 'auto'))
        none = safe_camera_settings(None)                                           # kein Leser vorhanden
        self.assertEqual((none.width, none.height, none.frame_rate), (640, 480, 0.0))
        self.assertTrue(none.notes)
        self.assertIsInstance(none, CameraSettings)


# ------------------------------------------------------------------ CameraSensor mit Kamera-Attrappe
class SensorCar:
    def __init__(self):
        from telemetry import FrameRingBuffer
        self.frame_history = FrameRingBuffer()
        self.published = []

    @property
    def camera_data(self):
        return self.published[-1]

    @camera_data.setter
    def camera_data(self, value):
        self.published.append(value)


class CameraSensorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        os.chdir(self.tmp.name)                                                      # keine pipeline_studio.json aus dem Projekt
        logging.disable(logging.CRITICAL)                                            # erwartete Fehler der Kameraschleife
        self.addCleanup(logging.disable, logging.NOTSET)
        import pipeline_config
        studio = {'processor': 'copilot', 'crop': list(PARAMS[3]), 'hue': PARAMS[0], 'sat': PARAMS[1], 'val': PARAMS[2],
                  'blur': PARAMS[4], 'morph': PARAMS[5], 'canny': PARAMS[6], 'enabled': dict(PARAMS[7])}
        self.pin = mock.patch.object(pipeline_config, 'load', return_value=studio)   # Linien ohne Canny, wie in test_paket_f
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def sensor(self, frames, metas=None, dual_ok=True, **cam_sensor):
        import ConfigReader
        stubs = hardware_stubs.installed(frames=frames)
        log = stubs.__enter__()
        self.addCleanup(stubs.__exit__, None, None, None)
        sys.modules['basisklassen_cam'].Camera.dual_ok = dual_ok                      # False: Konfiguration abgelehnt
        from CameraSensor import CameraSensor
        path = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'), cam_sensor=dict(
            {'scan_frequency': 200, 'colormode': 'BGR'}, **cam_sensor))
        car = SensorCar()
        sensor = CameraSensor(car, ConfigReader.ConfigReader('cam_sensor', path), logger=logging.getLogger('t'))
        if metas is not None:
            iterator = iter(metas)
            sensor.get_metadata = lambda: next(iterator)
        return sensor, car, log

    def run_loop(self, sensor):
        with self.assertRaises(RuntimeError):                                        # Testbilder erschoepft
            sensor.read_loop(threading.Event())

    def test_altverhalten_kamera_wird_wie_bisher_erzeugt(self):
        sensor, car, log = self.sensor([hardware_stubs.test_image(), hardware_stubs.test_image()])
        self.assertEqual(sensor.init_kwargs, {'colorspace': 'BGR', 'height': 480, 'width': 640})   # keine neuen Argumente
        self.assertIn(('camera_controls', ({'AeEnable': True, 'ExposureValue': 1.0},)), log.calls)
        self.run_loop(sensor)
        self.assertEqual([c.frame_id for c in car.published], [1, 2])
        camera = car.published[0]
        self.assertEqual(camera.raw_image.shape, (240, 320, 3))
        self.assertEqual(camera.process_size, (320, 240))                           # Verarbeitung = Aufnahme
        self.assertEqual(camera.process_source, 'none')
        self.assertEqual(len(camera.lanes), 2)
        self.assertIsNone(camera.exposure_us)                                        # Attrappe ohne Metadaten
        info = sensor.camera_info()
        self.assertEqual((info['capture_size'], info['process_size'], info['frame_rate_set']), ((640, 480), None, None))

    def test_dual_stream_verarbeitung_klein_aufnahme_gross(self):
        frames = [lane_image(1280, 960, shift=s) for s in (0, 3, -3)]
        sensor, car, log = self.sensor(frames, width=1280, height=960, process_width=320, process_height=240,
                                       frame_rate=30, exposure_value=0.5)
        self.assertEqual(sensor.init_kwargs['small_size'], (320, 240))
        self.assertEqual((sensor.init_kwargs['small_mode'], sensor.init_kwargs['frame_rate']), ('auto', 30.0))
        self.assertIn(('camera_controls', ({'AeEnable': True, 'ExposureValue': 0.5},)), log.calls)
        sensor.get_metadata = lambda: {'exposure_us': 8000, 'gain': 2.0, 'digital_gain': 1.0, 'frame_duration_us': 33333,
                                       'lux': 120.5, 'sensor_timestamp_ns': time.monotonic_ns()}
        self.run_loop(sensor)
        self.assertEqual(len(car.published), 3)
        for camera in car.published:
            self.assertEqual(camera.raw_image.shape, (960, 1280, 3))                # Aufnahme/Anzeige: gross
            self.assertEqual(camera.process_size, (320, 240))
            self.assertEqual(camera.process_source, 'camera')
            self.assertEqual(len(camera.lanes), 2)
            self.assertEqual(camera.process_image.shape[1], 320)                    # Linienbild stammt vom kleinen Bild
            self.assertLessEqual(camera.process_image.shape[0], 240)
            self.assertEqual((camera.exposure_us, camera.gain, camera.frame_duration_us, camera.lux),
                             (8000, 2.0, 33333, 120.5))
        info = sensor.camera_info()
        self.assertEqual((info['capture_size'], info['process_size'], info['process_source'], info['frame_rate_set'],
                          info['exposure_value']), ((1280, 960), (320, 240), 'camera', 30.0, 0.5))
        # Linien gelten im kleinen Bild: gleiches Ergebnis wie direkt auf dem verkleinerten Bild
        small = cv2.resize(frames[0], (320, 240), interpolation=cv2.INTER_AREA)
        from pipeline_processors import LaneProcessor
        expected = LaneProcessor().process(small, car.published[0].processing_parameters).lanes
        self.assertEqual(car.published[0].lanes, expected)

    def test_software_verkleinern_wenn_dual_stream_nicht_verfuegbar(self):
        sensor, car, _ = self.sensor([lane_image(1280, 960)], dual_ok=False, width=1280, height=960,
                                     process_width=320, process_height=240)
        self.assertEqual(sensor.small_source, 'software')
        self.run_loop(sensor)
        self.assertEqual((car.published[0].process_source, car.published[0].process_size), ('software', (320, 240)))
        self.assertEqual(car.published[0].raw_image.shape, (960, 1280, 3))
        self.assertEqual(sensor.camera_info()['process_source'], 'software')

    def test_dieselbe_aufnahme_wird_nicht_zweimal_verarbeitet(self):
        frames = [hardware_stubs.test_image() for _ in range(4)]
        stamps = iter([100, 100, 200, 200, 300])
        sensor, car, _ = self.sensor(frames)
        sensor.get_metadata = lambda: {'exposure_us': None, 'gain': None, 'digital_gain': None,
                                       'frame_duration_us': None, 'lux': None, 'sensor_timestamp_ns': next(stamps)}
        self.run_loop(sensor)
        self.assertEqual([c.frame_id for c in car.published], [1, 2])                # Wiederholungen uebersprungen

    def test_schleife_nie_schneller_als_die_kamera(self):
        frames = [hardware_stubs.test_image() for _ in range(3)]
        sensor, car, _ = self.sensor(frames)                                         # scan_frequency 200 Hz = 5 ms
        sensor.get_metadata = lambda: {'exposure_us': None, 'gain': None, 'digital_gain': None,
                                       'frame_duration_us': 50_000, 'lux': None, 'sensor_timestamp_ns': None}
        waits = []
        class Event(threading.Event):
            def wait(self, timeout=None):
                waits.append(timeout)
                return False
        with self.assertRaises(RuntimeError):
            sensor.read_loop(Event())
        periods = [w for w in waits if w and w > 0.01]
        self.assertTrue(periods and all(abs(p - 0.05) < 0.02 for p in periods), waits)   # ~50 ms statt 5 ms

    def test_fehlerhafte_konfiguration_ergibt_standardwerte_start_scheitert_nie(self):
        frames = [hardware_stubs.test_image()]
        sensor, car, log = self.sensor(frames, width='abc', height=-5, process_width='x', process_height=-1,
                                       downscale='turbo', frame_rate='schnell', exposure_value=99)
        self.assertEqual(sensor.init_kwargs, {'colorspace': 'BGR', 'height': 480, 'width': 640})
        self.assertIn(('camera_controls', ({'AeEnable': True, 'ExposureValue': 1.0},)), log.calls)
        self.run_loop(sensor)
        self.assertEqual(len(car.published), 1)
        # zu grosse Verarbeitungsgroesse: ignoriert (Verarbeitung auf dem Aufnahmebild)
        sensor, car, _ = self.sensor([hardware_stubs.test_image()], width=320, height=240, process_width=640,
                                     process_height=480)
        self.assertNotIn('small_size', sensor.init_kwargs)


# ------------------------------------------------------------------ Darstellung: Linien auf dem grossen Bild
def hd_camera(width=1280, height=960, process=(320, 240), crop=(0.15, 0.55), frame_id=1):
    """CameraData mit grossem Aufnahmebild und Linien, die auf dem kleinen Bild ermittelt wurden."""
    from pipeline_processors import CopilotLaneProcessor
    big = lane_image(width, height)
    small = cv2.resize(big, process, interpolation=cv2.INTER_AREA) if process else big
    result = CopilotLaneProcessor().process(small, PARAMS_TUPLE)
    return CameraData(process_image=result.processed_image, raw_image=big, frame_id=frame_id, crop=crop,
                      lanes=result.lanes, process_size=tuple(process) if process else None, process_source='camera')


def expected_points(width=1280, height=960, crop=(0.15, 0.55)):
    """Je Linie ein Punkt im Vollbild: mitten im ROI auf den bekannten Linienmitten des Testbilds."""
    top = int(height * (1 - crop[1]))
    bottom = int(height * (1 - crop[0]))
    y = (top + bottom) // 2
    left = (int(round(x_on_line((int(width * .2), height - 1), (int(width * .42), 0), y))), y)
    right = (int(round(x_on_line((int(width * .8), height - 1), (int(width * .58), 0), y))), y)
    return left, right


def lane_drawn(image, point, radius=12):
    return color_near(image, point, LEFT_COLOR, radius) or color_near(image, point, RIGHT_COLOR, radius)


class DrawingTests(unittest.TestCase):
    def setUp(self):
        stubs = hardware_stubs.installed()
        stubs.__enter__()
        self.addCleanup(stubs.__exit__, None, None, None)

    def assert_on_lines(self, image, expected=True):
        left, right = expected_points()
        self.assertEqual(lane_drawn(image, left) and lane_drawn(image, right), expected,
                         'Linien an den erwarteten Stellen' if expected else 'Linien duerfen dort nicht liegen')

    def test_linien_auf_hd_bild_an_der_richtigen_stelle(self):
        from dashboard_overlay import render_calibration_overlay
        camera = hd_camera()
        self.assertEqual(len(camera.lanes), 2)
        raw_before, lanes_before = camera.raw_image.copy(), tuple(camera.lanes)
        overlay = render_calibration_overlay(camera)
        self.assertEqual(overlay.shape, (960, 1280, 3))
        self.assert_on_lines(overlay)
        np.testing.assert_array_equal(camera.raw_image, raw_before)                  # Rohbild unveraendert
        self.assertEqual(camera.lanes, lanes_before)                                 # gespeicherte Werte unveraendert
        # Gegenprobe: ohne process_size wuerden die Linien im kleinen Massstab oben links gezeichnet
        wrong = hd_camera()
        wrong.process_size = None
        self.assert_on_lines(render_calibration_overlay(wrong), expected=False)

    def test_live_overlay_mit_fahrentscheidung_und_hud(self):
        from dashboard_overlay import render_overlay
        from replay_hud import draw_hud, row_from_camera
        camera = hd_camera()
        state = DrivingState(1, 0, 95, 30, camera.lanes, 99, 'camera', 99, 0)
        overlay = render_overlay(camera, state)
        self.assert_on_lines(overlay)
        row = row_from_camera(camera, state)
        self.assertEqual((row['process_width'], row['process_height']), (320, 240))
        hud = draw_hud(camera.raw_image, row)
        self.assertEqual(hud.shape, (960, 1280, 3))
        self.assert_on_lines(hud)
        self.assertFalse(np.array_equal(hud, camera.raw_image))
        self.assertEqual(camera.raw_image.shape, (960, 1280, 3))                      # Rohbild bleibt gross
        processed = render_overlay(camera, state, processed=True)                    # ROI-Bild des Verarbeitungsbilds
        self.assertEqual(processed.shape[1], 320)

    def test_hud_aus_gespeicherter_zeile_umrechnung_ueber_spalten(self):
        from replay_hud import draw_hud, encode_lanes
        camera = hd_camera()
        top = int(960 * (1 - .55))
        row = {'frame_id': '3', 'roi_top_px': str(top), 'roi_bottom_px': str(int(960 * .85)), 'roi_low': '0.15',
               'roi_high': '0.55', 'lanes_json': encode_lanes(camera.lanes), 'process_width': '320',
               'process_height': '240', 'data_status': 'complete'}
        self.assert_on_lines(draw_hud(camera.raw_image, row))
        old = {k: v for k, v in row.items() if not k.startswith('process_')}
        small_scale = draw_hud(camera.raw_image, old)                                # Revision 2/3: unveraendert gezeichnet
        self.assert_on_lines(small_scale, expected=False)
        no_high = {k: v for k, v in row.items() if k != 'roi_high'}                  # ohne roi_high: ROI-Versatz geschaetzt
        self.assert_on_lines(draw_hud(camera.raw_image, no_high))

    def test_schrift_und_linienstaerke_wachsen_mit_der_bildgroesse(self):
        from dashboard_overlay import put_text
        small, big = np.zeros((240, 320, 3), np.uint8), np.zeros((960, 1280, 3), np.uint8)
        put_text(small, 'FRAME 12', (8, 22))
        put_text(big, 'FRAME 12', (32, 88))
        def extent(image):
            ys, xs = np.nonzero(image.any(axis=2))
            return xs.max() - xs.min(), ys.max() - ys.min()
        (sw, sh), (bw, bh) = extent(small), extent(big)
        self.assertGreater(bw, 3.5 * sw)
        self.assertGreater(bh, 3.5 * sh)

    def test_text_bis_240_pixel_unveraendert(self):
        """put_text auf Bildern bis 240 Pixel Hoehe zeichnet exakt wie vor Paket H3."""
        from dashboard_overlay import TEXT_COLOR, put_text
        offsets = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
        old = np.full((240, 320, 3), 90, np.uint8)
        for dx, dy in offsets:
            cv2.putText(old, 'Frame 7  Linien 2', (8 + dx, 22 + dy), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 0, 0), 1,
                        cv2.LINE_AA)
        cv2.putText(old, 'Frame 7  Linien 2', (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.48, TEXT_COLOR, 1, cv2.LINE_AA)
        new = np.full((240, 320, 3), 90, np.uint8)
        put_text(new, 'Frame 7  Linien 2', (8, 22))
        np.testing.assert_array_equal(new, old)

    def test_spurqualitaet_nutzt_das_verarbeitungsbild(self):
        from dashboard.helpers import lane_quality
        camera = hd_camera()
        record = SimpleNamespace(camera=camera, driving=None)
        self.assertEqual(lane_quality([record]), 1.0)                                # plausibel im Verarbeitungsbild
        legacy = SimpleNamespace(camera=hd_camera(process=None), driving=None)
        self.assertEqual(lane_quality([legacy]), 1.0)
        calls = []                                                                   # welche Groesse wird geprueft?
        with mock.patch('sim_engine.lanes_plausible', side_effect=lambda lanes, w, h: calls.append((w, h)) or True):
            lane_quality([record])
            lane_quality([legacy])
        top, bottom = camera.roi_rows(240)
        self.assertEqual(calls, [(320, bottom - top), (1280, legacy.camera.roi_rows(960)[1] - legacy.camera.roi_rows(960)[0])])


# ------------------------------------------------------------------ Aufnahme, Replay, Simulation
CSV_NEW = ('process_width', 'process_height', 'process_source', 'exposure_us', 'gain')


def write_hd_session(root, name='session_hd', count=12, with_process=True, revision=4, image_size=(1280, 960)):
    """Session mit grossen Bildern; ``with_process=False`` entspricht Revision 2/3 (Spalten fehlen)."""
    path = Path(root) / name
    (path / 'frames').mkdir(parents=True)
    width, height = image_size
    top, bottom = int(height * (1 - .55)), int(height * (1 - .15))
    rows = []
    for frame_id in range(1, count + 1):
        filename = f'frames/frame_{frame_id:010d}.jpg'
        cv2.imwrite(str(path / filename), lane_image(width, height, shift=(frame_id % 5) - 2), [cv2.IMWRITE_JPEG_QUALITY, 95])
        row = {'frame_id': frame_id, 'image_filename': filename, 'data_status': 'complete',
               'camera_timestamp_ns': 1_700_000_000_000_000_000 + frame_id * 40_000_000,
               'camera_monotonic_ns': 10_000_000_000 + frame_id * 40_000_000, 'processing_ns': 4_000_000,
               'steering_angle': 90, 'requested_angle': 90, 'steering_angle_car': 90, 'speed': 30,
               'image_width': width, 'image_height': height, 'roi_low': 0.15, 'roi_high': 0.55,
               'roi_top_px': top, 'roi_bottom_px': bottom, 'processor_name': 'CopilotLaneProcessor',
               'processing_parameters_json': json.dumps(PARAMS), 'lane_count': 2, 'lanes_json': '[]'}
        if revision >= 3:
            row['vehicle_mode_key'] = 'camera'
        if with_process:
            row.update(process_width=320, process_height=240, process_source='camera', exposure_us=8000, gain=2.0)
        rows.append(row)
    with (path / 'frames.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (path / 'manifest.json').write_text(json.dumps({'schema_version': 2, 'schema_revision': revision}), encoding='utf-8')
    return name


class RecordingTests(unittest.TestCase):
    def setUp(self):
        stubs = hardware_stubs.installed()
        stubs.__enter__()
        self.addCleanup(stubs.__exit__, None, None, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, 'sessions')

    def record(self, cameras, provider=None):
        from session_recorder import SessionRecorder
        from telemetry import FrameRingBuffer
        history = FrameRingBuffer(30)
        for camera in cameras:
            history.add_camera(camera)
        recorder = SessionRecorder(SimpleNamespace(frame_history=history), self.root, is_driving=lambda: False)
        recorder.camera_info = provider
        recorder.start()
        recorder._stop.set()
        recorder._thread.join(2)
        recorder.capture_once()
        recorder._thread = SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None)
        recorder.stop()
        return recorder

    def hd_cameras(self, count=6, process=(320, 240)):
        base = time.monotonic_ns()
        cameras = []
        for i in range(1, count + 1):
            camera = hd_camera(frame_id=i, process=process)
            camera.monotonic_ns = base + i * 40_000_000
            camera.timestamp = time.time_ns()
            camera.exposure_us, camera.gain = 8000.0, 2.0
            cameras.append(camera)
        return cameras

    def test_neue_spalten_und_schema_revision_4(self):
        from session_recorder import FIELDS, REVISION_NOTE, SCHEMA_REVISION, load_manifest, load_session
        self.assertEqual(SCHEMA_REVISION, 4)
        self.assertEqual(FIELDS[-5:], CSV_NEW)
        self.assertIn('Revision 4', REVISION_NOTE)
        self.assertIn('lesbar', REVISION_NOTE)
        recorder = self.record(self.hd_cameras())
        _, rows = load_session(recorder.path.name, self.root)
        self.assertEqual(len(rows), 6)
        self.assertEqual({k: rows[0][k] for k in CSV_NEW},
                         {'process_width': '320', 'process_height': '240', 'process_source': 'camera',
                          'exposure_us': '8000.0', 'gain': '2.0'})
        self.assertEqual((rows[0]['image_width'], rows[0]['image_height']), ('1280', '960'))   # grosses Bild gespeichert
        manifest = load_manifest(recorder.path.name, self.root)
        self.assertEqual((manifest['schema_version'], manifest['schema_revision']), (2, 4))
        self.assertIn('Revision 4', manifest['revision_note'])
        for needed in CSV_NEW:
            self.assertIn(needed, manifest['frames_fields'])
        self.assertIn('Verarbeitungsbild', manifest['lane_model'])
        self.assertIn('y = m*x + b', manifest['lane_model'])

    def test_manifest_aufloesungen_und_bildrate(self):
        from session_recorder import load_manifest
        provider = lambda: {'capture_size': (1280, 960), 'process_size': (320, 240), 'frame_rate_set': 30.0,  # noqa: E731
                            'downscale': 'auto', 'exposure_value': 0.5}
        recorder = self.record(self.hd_cameras(), provider)
        camera = load_manifest(recorder.path.name, self.root)['camera']
        self.assertEqual((camera['capture_size'], camera['process_size'], camera['process_source']),
                         ([1280, 960], [320, 240], 'camera'))
        self.assertEqual((camera['frame_rate_set_hz'], camera['frame_rate_mode']), (30.0, 'fest'))
        self.assertAlmostEqual(camera['frame_rate_measured_median_hz'], 25.0, delta=0.5)   # 40 ms Abstand
        self.assertEqual((camera['downscale'], camera['exposure_value']), ('auto', 0.5))
        self.assertIn('Verarbeitungsbild', camera['note'])
        auto = self.record(self.hd_cameras(), lambda: {'frame_rate_set': None})
        self.assertEqual(load_manifest(auto.path.name, self.root)['camera']['frame_rate_mode'], 'automatisch')
        for provider in (None, lambda: 1 / 0):                                       # unbekannt, nie raten, nie scheitern
            unknown = self.record(self.hd_cameras(), provider)
            camera = load_manifest(unknown.path.name, self.root)['camera']
            self.assertEqual((camera['frame_rate_mode'], camera['frame_rate_set_hz']), ('unbekannt', None))
            self.assertEqual(camera['capture_size'], [1280, 960])                    # aus den geschriebenen Frames

    def test_ohne_verarbeitungsgroesse_bleiben_zellen_leer(self):
        from session_recorder import load_session
        camera = self.hd_cameras(1)[0]
        camera.process_size = camera.process_source = camera.exposure_us = camera.gain = None
        recorder = self.record([camera])
        _, rows = load_session(recorder.path.name, self.root)
        self.assertEqual({k: rows[0][k] for k in CSV_NEW}, {k: '' for k in CSV_NEW})      # unbekannt, nie geraten

    def test_alte_sessions_revision_2_und_3_sind_lesbar(self):
        from frame_source import SessionSource
        from replay_hud import draw_hud
        from session_recorder import load_manifest, load_session
        for revision in (2, 3):
            name = write_hd_session(self.root, f'session_rev{revision}', count=4, with_process=False, revision=revision,
                                    image_size=(320, 240))
            path, rows = load_session(name, self.root)
            self.assertEqual(len(rows), 4)
            for column in CSV_NEW:
                self.assertNotIn(column, rows[0])
            self.assertEqual(load_manifest(name, self.root)['schema_revision'], revision)
            source = SessionSource(name, self.root)
            self.assertEqual([f.process_size for f in source.frames], [None] * 4)    # fehlende Spalten = unbekannt
            image = source.image(0)
            self.assertEqual(draw_hud(image, rows[0]).shape, image.shape)
            self.assertEqual(source.then_image(0).shape, image.shape)

    def test_ringbuffer_quelle_liefert_verarbeitungsgroesse(self):
        from frame_source import RingbufferSource
        from replay_studio import parameters_from_record
        cameras = self.hd_cameras(3)
        for camera in cameras:
            camera.processing_parameters = parameters_from_record(PARAMS)
        records = [SimpleNamespace(camera=camera, driving=None) for camera in cameras]
        self.assertEqual([f.process_size for f in RingbufferSource(records).frames], [(320, 240)] * 3)


class SimulationHDTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        os.chdir(self.tmp.name)
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                                  cameracar_controller={'lost_line_timeout': 1})
        self.root = os.path.join(self.tmp.name, 'Data_AI', 'sessions')
        stubs = hardware_stubs.installed()
        stubs.__enter__()
        self.addCleanup(stubs.__exit__, None, None, None)

    def engine(self):
        import ConfigReader
        from sim_engine import SimEngine
        return SimEngine(ConfigReader.ConfigReader('cameracar_controller', self.config), v_min=20, v_max=100)

    def test_simulation_einer_hd_session_rechnet_auf_der_verarbeitungsgroesse(self):
        from frame_source import SessionSource
        from pipeline_processors import CopilotLaneProcessor
        name = write_hd_session(self.root)
        source = SessionSource(name, self.root)
        self.assertEqual({f.process_size for f in source.frames}, {(320, 240)})
        result = self.engine().run(source, 'copilot', warmup=0)
        self.assertEqual(result.errors, 0)
        self.assertEqual(len(result.frames), 12)
        self.assertEqual({f.process_size for f in result.frames}, {(320, 240)})
        self.assertEqual({f.lane_count for f in result.frames}, {2})
        self.assertTrue(all(f.plausible for f in result.frames))                       # plausibel im Verarbeitungsbild
        # exakt dasselbe Ergebnis wie das Software-Verkleinern des gespeicherten Bildes
        small = cv2.resize(source.image(0), (320, 240), interpolation=cv2.INTER_AREA)
        expected = CopilotLaneProcessor().process(small, source.frames[0].params).lanes
        self.assertEqual(result.frames[0].lanes, tuple(expected))

    def test_alte_session_ohne_spalten_rechnet_wie_bisher_auf_dem_gespeicherten_bild(self):
        from frame_source import SessionSource
        name = write_hd_session(self.root, 'session_alt', count=4, with_process=False, revision=3)
        source = SessionSource(name, self.root)
        self.assertEqual({f.process_size for f in source.frames}, {None})
        result = self.engine().run(source, 'copilot', warmup=0)
        self.assertEqual({f.process_size for f in result.frames}, {(1280, 960)})      # keine Verkleinerung
        self.assertEqual({f.lane_count for f in result.frames}, {2})

    def test_darstellung_damals_und_jetzt_auf_dem_grossen_bild(self):
        from frame_source import SessionSource
        from sim_lab import SimLab
        name = write_hd_session(self.root, count=6)
        source = SessionSource(name, self.root)
        result = self.engine().run(source, 'copilot', warmup=0)
        lab = SimLab(SimpleNamespace(running=lambda: False), SimpleNamespace(no_update=None), lambda image: 'x', self.root)
        then, now = lab.render_frame(result, source, 2)
        self.assertEqual((then.shape, now.shape), ((960, 1280, 3), (960, 1280, 3)))
        left, right = expected_points()
        self.assertTrue(lane_drawn(now, left, 14) and lane_drawn(now, right, 14))

    def test_ab_labor_rechnet_auf_der_damaligen_groesse(self):
        from pipeline_ab import PipelineAB
        from replay_studio import parameters_from_record
        camera = hd_camera()
        camera.processing_parameters = parameters_from_record(PARAMS)
        lab = PipelineAB()
        lab.freeze(camera, parameters_from_record(PARAMS), 'copilot')
        self.assertEqual(lab.process_size(), (320, 240))
        self.assertEqual(lab.downscale(camera.raw_image).shape, (240, 320, 3))
        comparison = lab.compare(parameters_from_record(PARAMS), 'copilot')
        self.assertEqual(comparison.lanes_a, comparison.lanes_b)
        self.assertEqual(len(comparison.lanes_a), 2)
        self.assertEqual(comparison.lanes_a, tuple(camera.lanes))                     # wie live auf dem kleinen Bild
        self.assertEqual(comparison.image_a.shape[1], 320)
        lab.freeze(SimpleNamespace(raw_image=camera.raw_image, frame_id=2), parameters_from_record(PARAMS))
        self.assertIsNone(lab.process_size())                                         # ohne Angabe wie bisher
        lab.clear()
        self.assertIsNone(lab.process_size())


# ------------------------------------------------------------------ Ringbuffer und Anwendung
class RingBufferTests(unittest.TestCase):
    def test_resize_behaelt_die_neuesten_frames(self):
        from telemetry import FrameRingBuffer
        ring = FrameRingBuffer(maxlen=10)
        for frame_id in range(1, 9):
            ring.add_camera(CameraData(np.empty(0), np.empty(0), frame_id=frame_id))
        ring.resize(5)
        self.assertEqual((ring.maxlen, [r.camera.frame_id for r in ring.snapshot()]), (5, [4, 5, 6, 7, 8]))
        ring.add_camera(CameraData(np.empty(0), np.empty(0), frame_id=9))
        self.assertEqual([r.camera.frame_id for r in ring.snapshot()], [5, 6, 7, 8, 9])
        ring.resize(20)
        self.assertEqual((ring.maxlen, len(ring.snapshot())), (20, 5))
        with self.assertRaises(ValueError):
            ring.resize(0)

    def test_anwendung_passt_die_laenge_an_die_bildgroesse_an(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        import camera_geometry
        expected = {}
        for label, cam_sensor in (('klein', {'width': 320, 'height': 240}),
                                  ('hd', {'width': 1280, 'height': 960, 'process_width': 320, 'process_height': 240})):
            hardware_stubs.write_config(os.path.join(tmp.name, 'config.json'),
                                        cam_sensor=dict({'scan_frequency': 200, 'colormode': 'BGR'}, **cam_sensor))
            os.chdir(tmp.name)
            with hardware_stubs.installed():
                try:
                    from car_application import CarApplication
                except ImportError as exc:
                    self.skipTest(f'Anwendung nicht importierbar: {exc}')
                app = CarApplication(start_camera=False)
                try:
                    expected[label] = (app.car.frame_history.maxlen, app.recorder.camera_info == app.sensor.camera_info)
                finally:
                    app.shutdown()
        self.assertEqual(expected['klein'], (60, True))                                # wie bisher
        self.assertEqual(expected['hd'][0], camera_geometry.ring_length((1280, 960), (320, 240)))
        self.assertLess(expected['hd'][0], 60)


# ------------------------------------------------------------------ echtes Dash
@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication
        cls.stubs = hardware_stubs.installed()
        cls.stubs.__enter__()
        cls.pin = hardware_stubs.pin_pipeline_config()
        module = hardware_stubs.fresh_import('Camp2CodeDashboard')
        cls.fake = FakeApplication()
        cls.app = module.create_app(cls.fake)
        cls.client = cls.app.server.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.pin.stop()
        cls.stubs.__exit__(None, None, None)

    def post(self, outputs, inputs, state, changed):
        output = ('..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..' if len(outputs) > 1
                  else f'{outputs[0][0]}.{outputs[0][1]}')
        specs = [{'id': i, 'property': p} for i, p in outputs]
        body = {'output': output, 'outputs': specs if len(specs) > 1 else specs[0],
                'inputs': [{'id': i, 'property': p, 'value': v} for i, p, v in inputs],
                'state': [{'id': i, 'property': p, 'value': v} for i, p, v in state], 'changedPropIds': changed}
        return self.client.post('/_dash-update-component', json=body)

    def test_callback_registrierung_unveraendert(self):
        deps = json.loads(self.client.get('/_dash-dependencies').get_data(as_text=True))
        self.assertEqual(len(deps), 39)  # PAKET-I1: SimLab 4 -> 9 Callbacks (+5)  # vorher 34                                              # H3 aendert die Anzahl nicht
        outputs = []
        for dep in deps:
            outputs.extend(dep['output'].strip('.').split('...') if dep['output'].startswith('..') else [dep['output']])
        self.assertEqual(len(outputs), len(set(outputs)))                            # keine doppelten Outputs
        self.assertEqual(self.client.get('/').status_code, 200)

    def cockpit(self, width, height):
        camera = hd_camera(width, height, process=(320, 240), frame_id=77)
        camera.monotonic_ns = time.monotonic_ns()
        self.fake.car.camera_data = camera
        self.fake.car.frame_history.add_camera(camera)
        outputs = [('drive-overlay', 'src'), ('drive-rendered', 'data'), ('drive-angle', 'children'),
                   ('drive-speed', 'children'), ('drive-requested', 'children'), ('drive-raw', 'children'),
                   ('drive-mode-status', 'children'), ('drive-diagnostic', 'children'),
                   ('drive-frame-badge', 'children'), ('drive-age', 'children'), ('drive-cpu', 'children'),
                   ('drive-ram', 'children'), ('drive-temp', 'children'), ('drive-rec-line', 'children')]
        response = self.post(outputs, [('tick-drive', 'n_intervals', 1), ('main-tabs', 'active_tab', 'drive')],
                             [('drive-rendered', 'data', None), ('cockpit-hud', 'value', [])], ['tick-drive.n_intervals'])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        data = response.get_json()['response']['drive-overlay']['src']
        decoded = cv2.imdecode(np.frombuffer(base64.b64decode(data.split(',', 1)[1]), np.uint8), cv2.IMREAD_COLOR)
        return camera, decoded

    def test_cockpit_hd_bild_ueber_http_und_verkleinerung_nur_fuer_den_browser(self):
        camera, decoded = self.cockpit(1280, 960)
        self.assertEqual(decoded.shape[:2], (960, 1280))                             # bis 1280 Pixel: unveraendert
        camera, decoded = self.cockpit(1920, 1440)
        self.assertEqual(decoded.shape[:2], (960, 1280))                             # nur fuer die Uebertragung verkleinert
        self.assertEqual(camera.raw_image.shape, (1440, 1920, 3))                    # Original bleibt gross
        self.assertEqual(self.fake.car.camera_data.raw_image.shape, (1440, 1920, 3))

    def test_uri_verkleinert_nur_zu_breite_bilder(self):
        import dashboard.helpers as helpers
        for width, expected in ((640, 640), (1280, 1280), (1281, 1280), (1920, 1280)):
            image = scene(width, width * 3 // 4)
            encoded = helpers.uri(image)
            decoded = cv2.imdecode(np.frombuffer(base64.b64decode(encoded.split(',', 1)[1]), np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(decoded.shape[1], expected, width)
            self.assertEqual(image.shape[1], width)                                  # Eingabe unveraendert
        self.assertIsNone(helpers.uri(None))
        self.assertEqual(cv2.imdecode(np.frombuffer(base64.b64decode(helpers.uri(scene(1920, 1080), max_width=0)
                                                                      .split(',', 1)[1]), np.uint8),
                                      cv2.IMREAD_COLOR).shape[1], 1920)                  # max_width=0: nicht verkleinern


# ------------------------------------------------------------------ Paket, Patch und Skripte
class PackageTests(unittest.TestCase):
    def test_marker_exakt_und_pruefsumme_tolerant(self):
        spec = importlib.util.spec_from_file_location('h3_patch', ROOT / 'scripts' / 'paket_h3_patch.py')
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        self.assertTrue(patch.has_marker('# PAKET-H3: Dual-Stream'))
        self.assertTrue(patch.has_marker('PAKET-H3'))
        self.assertFalse(patch.has_marker('# PAKET-H3B: Settings'))                  # PAKET-H3 ist Teilstring von PAKET-H3B
        self.assertFalse(patch.has_marker('# PAKET-H2B'))
        text = 'import cv2\n\nclass A:\n    x = 1\n'
        self.assertEqual(patch.cam_fingerprint(text), patch.cam_fingerprint(text.replace('\n', '\r\n')))
        self.assertEqual(patch.cam_fingerprint(text), patch.cam_fingerprint(text.replace('\n', '\n\n')))   # Leerzeilen egal
        self.assertNotEqual(patch.cam_fingerprint(text), patch.cam_fingerprint(text.replace('1', '2')))

    def test_cam_check_ist_syntaktisch_in_ordnung_und_beruehrt_keine_motoren(self):
        source = (ROOT / 'scripts' / 'cam_check.py').read_text(encoding='utf-8')
        compile(source, 'cam_check.py', 'exec')
        for forbidden in ('BaseCar', 'basisklassen import', 'FrontWheels', 'BackWheels', 'CameraCar'):
            self.assertNotIn(forbidden, source)
        self.assertIn('Picamera2', source)

    def test_keine_html_reste_in_neuen_dateien(self):
        names = ('camera_geometry.py', 'camera_settings.py', 'basisklassen_cam.py', 'scripts/cam_check.py',
                 'scripts/paket_h3_patch.py', 'CameraSensor.py', 'dashboard_overlay.py', 'replay_hud.py')
        pattern = re.compile(r'&lt;|&gt;|&amp;|&quot;|<br\s*/?>')
        for name in names:
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIsNone(pattern.search(text), name)

    def test_fahrregelung_unveraendert(self):
        source = (ROOT / 'driving' / 'camera_mode.py').read_text(encoding='utf-8')
        self.assertIn('SPEED_CONTROL_VALUE = 30', source)
        self.assertNotIn('PAKET-H3', source)                                         # Fahrregelung nicht angefasst


if __name__ == '__main__':
    unittest.main()
