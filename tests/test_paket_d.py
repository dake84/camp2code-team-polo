"""Paket D: Replay-Frame erscheint in allen Pipeline-Stufen (ohne Hardware)."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

PARAMS = [[100, 130], [80, 255], [40, 255], [0.15, 0.55], 3, 5, [50, 100],
          {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True}]


class ReplayStudioViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        from replay_studio import parameters_from_record
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
        import pipeline_config
        with patch.object(cls.module, 'load', return_value=pipeline_config.DEFAULT):
            cls.app = cls.module.create_app(cls.fake)
        cls.cb = cls.app.callbacks
        for frame_id in range(40, 44):
            camera = cls.fake.publish(frame_id, with_decision=True)
            camera.processing_parameters = parameters_from_record(json.dumps(PARAMS))
            camera.processor_name = 'CopilotLaneProcessor'

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def test_replay_frame_in_allen_stufen(self):
        no = self.module.no_update
        tab, _msg, _src, token = self.cb['replay_to_studio'](1, '__live__', None, 0)
        self.assertEqual(tab, 'pipeline')
        self.ctx.set('replay-studio-token', 'data')
        frozen = self.cb['freeze'](None, token, None)
        self.assertEqual(frozen['frame_id'], 40)
        self.fake.publish(99)                                  # Live laeuft weiter
        result = self.cb['pipeline_view'](1, 'roi', frozen, 'pipeline', None)
        self.assertTrue(result[18].startswith('REPLAY · Frame 40'), result[18])
        self.assertTrue(all(isinstance(s, str) and s.startswith('data:image') for s in result[:6]))
        again = self.cb['pipeline_view'](2, 'roi', frozen, 'pipeline', result[-1])
        self.assertIs(again[0], no)                            # gleicher Frame: keine Neukodierung
        self.ctx.set('pipeline-freeze')
        self.assertIsNone(self.cb['freeze'](1, token, frozen))  # Freeze/Live -> zurueck zu Live
        live = self.cb['pipeline_view'](3, 'roi', None, 'pipeline', None)
        self.assertTrue(live[18].startswith('LIVE · Frame 99'), live[18])


if __name__ == '__main__':
    unittest.main()
