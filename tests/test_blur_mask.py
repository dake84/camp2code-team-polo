"""Regressionstest für das Binärisieren nach aktiviertem Blur."""
import sys
import types
import unittest
from unittest.mock import patch
import cv2
import numpy as np

# Für den Test der Pipeline ist kein echter Linien-Detektor nötig.
module = types.ModuleType('lane_detector')
class Detector:
    def detect(self, image):
        return ()
module.LaneDetector = Detector
sys.modules['lane_detector'] = module
from pipeline_processors import CopilotLaneProcessor


class TestBlurMaske(unittest.TestCase):
    def test_blur_verbreitert_binäre_linie_nicht_durch_geringste_grauwerte(self):
        image = np.zeros((90, 120, 3), dtype=np.uint8)
        image[20:70, 56:64] = (255, 255, 255)
        base = ([0, 179], [0, 255], [200, 255], [0, 1], 15, 1, [50, 100])
        enabled = dict(roi=False, mask=True, blur=True, morph=False,
                       canny=False, lanes=False)
        processor = CopilotLaneProcessor()
        on = processor.process(image, (*base, enabled))
        off = processor.process(image, (*base, {**enabled, 'blur': False}))
        self.assertLessEqual(np.count_nonzero(on.stages['morph']),
                             np.count_nonzero(off.stages['morph']) * 1.2)
        self.assertTrue(np.any((on.stages['blur'] > 0) & (on.stages['blur'] < 127)))

    def test_blur_off_bleibt_unveraendert(self):
        image = np.zeros((32, 32, 3), dtype=np.uint8)
        image[:, 12:20] = 255
        enabled = dict(roi=False, mask=True, blur=False, morph=False,
                       canny=False, lanes=False)
        result = CopilotLaneProcessor().process(image,
            ([0,179], [0,255], [200,255], [0,1], 15, 1, [50,100], enabled))
        np.testing.assert_array_equal(result.stages['mask'], result.stages['blur'])
        np.testing.assert_array_equal(result.stages['mask'], result.stages['morph'])

if __name__ == '__main__':
    unittest.main()
