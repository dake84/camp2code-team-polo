"""Hardwarefreie Tests der Lenkwinkelanzeige."""
import unittest
import numpy as np
from dashboard_steering import draw_steering_overlay

class TestSteeringOverlay(unittest.TestCase):
    def test_kein_bild(self):
        self.assertIsNone(draw_steering_overlay(None, 90))

    def test_original_bleibt_unveraendert(self):
        original = np.zeros((180, 320, 3), dtype=np.uint8)
        result = draw_steering_overlay(original, 105)
        self.assertTrue(np.all(original == 0))
        self.assertTrue(np.any(result != 0))

    def test_fehlender_winkel(self):
        result = draw_steering_overlay(np.zeros((180, 320, 3), dtype=np.uint8), None)
        self.assertEqual(result.shape, (180, 320, 3))
