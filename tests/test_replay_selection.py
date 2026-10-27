"""Hardwarefreie Tests für Framebereich und Plotly-Selektion."""
import unittest
from types import SimpleNamespace
from replay_selection import selected_bounds, select_records

class ReplaySelectionTests(unittest.TestCase):
    def test_bereich(self):
        self.assertEqual(selected_bounds({'xaxis.range[0]': 30, 'xaxis.range[1]': 10}), (10, 30))
        self.assertIsNone(selected_bounds({'xaxis.autorange': True}))
    def test_frames_ohne_erfindung(self):
        records = [SimpleNamespace(camera=SimpleNamespace(frame_id=i)) for i in (3, 7, 11, 20)]
        self.assertEqual([r.camera.frame_id for r in select_records(records, (5, 15))], [7, 11])
        self.assertEqual(select_records(records, (30, 40)), ())
