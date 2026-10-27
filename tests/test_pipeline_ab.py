"""Hardwarefreie A/B-Tests mit deterministischem Mock-Prozessor."""
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

# Der Test verwendet nur die veröffentlichte Prozessorschnittstelle.
module = types.ModuleType('pipeline_processors')
class FakeProcessor:
    def process(self, raw, params):
        return SimpleNamespace(processed_image=raw.copy() + int(params[0]), lanes=((1,2),))
module.CopilotLaneProcessor = FakeProcessor
module.LaneProcessor = FakeProcessor
with patch.dict(sys.modules, {'pipeline_processors': module}):
    from pipeline_ab import PipelineAB

class TestPipelineAB(unittest.TestCase):
    def test_identischer_frame_unterschiedliche_parameter(self):
        lab = PipelineAB()
        raw = np.zeros((5, 5), dtype=np.uint8)
        lab.freeze(SimpleNamespace(raw_image=raw, frame_id=42), (1,))
        raw[:] = 99
        comparison = lab.compare((2,))
        self.assertEqual(comparison.frame_id, 42)
        self.assertTrue(np.all(comparison.image_a == 1))
        self.assertTrue(np.all(comparison.image_b == 2))

    def test_reset(self):
        lab = PipelineAB()
        lab.clear()
        with self.assertRaises(ValueError):
            lab.compare((1,))

if __name__ == '__main__':
    unittest.main()
