"""Prueft die tatsaechliche Definition der Linienkoeffizienten und ihre Darstellung."""
import json
import unittest

import cv2
import numpy as np

from dashboard_overlay import LANE_COLORS, render_calibration_overlay
from LenkwinkelKalkulator import (cals_drive_parameter_from_lanes, detect_lanes_from_areas,
                                  steering_angle_from_lanes)
from models import CameraData
from replay_hud import draw_hud


def mask_with_lines(points, shape=(96, 320)):
    mask = np.zeros(shape, np.uint8)
    for p0, p1 in points:
        cv2.line(mask, p0, p1, 255, 6)
    return mask


class LaneModelTests(unittest.TestCase):
    def test_koeffizienten_sind_y_gleich_mx_plus_b(self):
        mask = mask_with_lines([((60, 95), (140, 0)), ((260, 95), (200, 0))])
        lanes, _ = detect_lanes_from_areas(mask)
        self.assertEqual(len(lanes), 2)
        (m0, b0), (m1, b1) = lanes
        # Linke Linie: y = m*x + b trifft (60, 95) und (140, 0) auf wenige Pixel genau.
        self.assertAlmostEqual(m0 * 60 + b0, 95, delta=2)
        self.assertAlmostEqual(m0 * 140 + b0, 0, delta=2)
        self.assertAlmostEqual(m1 * 260 + b1, 95, delta=2)
        # Die frueher im HUD angenommene Form x = m*y + b passt nicht.
        self.assertGreater(abs(m1 * 0 + b1 - 200), 100)

    def test_reine_winkelfunktion_identisch_zum_kalkulator(self):
        rng = np.random.default_rng(7)
        for _ in range(3000):
            w, h = int(rng.integers(40, 700)), int(rng.integers(10, 300))
            lanes = [(float(rng.normal(0, 3)), float(rng.normal(0, 300))) for _ in range(int(rng.integers(0, 4)))]
            expected, _ = cals_drive_parameter_from_lanes(np.zeros((h, w), np.uint8), lanes)
            self.assertEqual(steering_angle_from_lanes(lanes, w, h), expected)
        self.assertEqual(steering_angle_from_lanes([(1.0, 2.0), None], 320, 96), 90.0)
        self.assertIsNone(steering_angle_from_lanes([(1.0, 2.0)], 320, 96))


class DrawingTests(unittest.TestCase):
    def setUp(self):
        self.top, self.bottom = 86, 182  # ROI bei 240 px Hoehe und crop (0.25, 0.64)
        self.lane = (-1.19, 166.5)       # y_roi = m*x + b

    def expected_pixel(self, x):
        return x, int(round(self.lane[0] * x + self.lane[1])) + self.top

    def assert_lane_color_near(self, image, point, color):
        x, y = point
        window = image[max(0, y - 3):y + 4, max(0, x - 3):x + 4].reshape(-1, 3)
        self.assertTrue(any(np.abs(window.astype(int) - color).sum(axis=1) < 60), (point, window[:3]))

    def test_hud_zeichnet_im_roi_mit_versatz(self):
        raw = np.zeros((240, 320, 3), np.uint8)
        row = {'frame_id': '9', 'lanes_json': json.dumps([list(self.lane)]),
               'roi_top_px': str(self.top), 'roi_bottom_px': str(self.bottom), 'data_status': 'complete'}
        result = draw_hud(raw, row)
        self.assertFalse(raw.any())                        # Rohbild unveraendert
        self.assertGreater(result[self.top:self.bottom].any(axis=2).sum(), 0)
        self.assert_lane_color_near(result, self.expected_pixel(100), LANE_COLORS[0])
        # Ausserhalb des ROI wird keine Linie gezeichnet.
        self.assertFalse((np.abs(result[self.bottom + 5:230].astype(int) - LANE_COLORS[0]).sum(axis=2) < 30).any())

    def test_hud_ohne_roi_zeichnet_keine_linien(self):
        raw = np.zeros((240, 320, 3), np.uint8)
        result = draw_hud(raw, {'frame_id': '1', 'lanes_json': '[[-1.19, 166.5]]'})
        green = (np.abs(result.astype(int) - LANE_COLORS[0]).sum(axis=2) < 30)
        self.assertFalse(green.any())

    def test_live_overlay_und_hud_identisch(self):
        raw = np.zeros((240, 320, 3), np.uint8)
        camera = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=raw, frame_id=3,
                            crop=(0.2416, 0.6416), lanes=(self.lane,))
        top, bottom = camera.roi_rows(240)
        live = render_calibration_overlay(camera)
        self.assertFalse(raw.any())
        self.assert_lane_color_near(live, (100, int(round(self.lane[0] * 100 + self.lane[1])) + top), LANE_COLORS[0])


if __name__ == '__main__':
    unittest.main()
