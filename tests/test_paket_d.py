"""Paket D: Replay-Frame erscheint in allen Pipeline-Stufen (ohne Hardware)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_dashboard_callbacks import stage_image_ids  # noqa: E402

PARAMS = [[100, 130], [80, 255], [40, 255], [0.15, 0.55], 3, 5, [50, 100],
          {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True}]


class ReplayStudioViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        from replay_studio import parameters_from_record
        cls.module, cls.context, cls.ctx = load_dashboard()
        cls.fake = FakeApplication()
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
        tab, _msg, _src, token, _s = self.cb['replay_to_studio'](1, None, '__live__', None, 0)  # PAKET-E
        self.assertEqual(tab, 'pipeline')
        self.ctx.set('replay-studio-token', 'data')
        frozen = self.cb['freeze'](None, token, None)
        self.assertEqual(frozen['frame_id'], 40)
        self.fake.publish(99)                                  # Live laeuft weiter
        ids = stage_image_ids()
        names = [i['stage'] for i in ids]
        images, _infos, inspector, title, label, _info, key = self.cb['pipeline_view'](
            1, 'roi', frozen, 'pipeline', 0, ids, 'copilot', None)
        self.assertTrue(label.startswith('REPLAY · Frame 40'), label)
        for stage in ('raw', 'roi', 'hsv', 'mask', 'blur', 'binary', 'morph'):
            self.assertTrue(images[names.index(stage)].startswith('data:image'), stage)
        self.assertEqual(inspector, images[names.index('roi')])
        again = self.cb['pipeline_view'](2, 'roi', frozen, 'pipeline', 0, ids, 'copilot', key)
        self.assertEqual(again[0], [no] * len(ids))            # gleicher Frame: keine Neukodierung
        self.ctx.set('pipeline-freeze')
        self.assertIsNone(self.cb['freeze'](1, token, frozen))  # Freeze/Live -> zurueck zu Live
        live = self.cb['pipeline_view'](3, 'roi', None, 'pipeline', 0, ids, 'copilot', None)
        self.assertTrue(live[4].startswith('LIVE · Frame 99'), live[4])

    def test_replay_frame_mit_dem_gewaehlten_prozessor(self):
        """Das Studio verarbeitet den Replay-Frame mit dem gewaehlten Prozessor (hier Modus Opus)."""
        _tab, _msg, _src, token, _s = self.cb['replay_to_studio'](1, None, '__live__', None, 0)
        self.ctx.set('replay-studio-token', 'data')
        frozen = self.cb['freeze'](None, token, None)
        ids = stage_image_ids('opus')
        names = [i['stage'] for i in ids]
        images, infos, inspector, title, label, *_ = self.cb['pipeline_view'](
            1, 'blackhat', frozen, 'pipeline', 0, ids, 'opus', None)
        self.assertTrue(label.startswith('REPLAY'), label)
        for stage in ('gray', 'clahe', 'blackhat', 'threshold', 'morph'):
            self.assertTrue(images[names.index(stage)].startswith('data:image'), stage)
        self.assertEqual(title, 'Blackhat (dunkle Linie)')
        self.assertEqual(inspector, images[names.index('blackhat')])
        # Legacy liefert keine Diagnosebilder; das Replay nutzt dafuer die Diagnose-Variante
        legacy = self.cb['pipeline_view'](2, 'roi', frozen, 'pipeline', 0, stage_image_ids('legacy'), 'legacy', None)
        self.assertTrue(legacy[0][1].startswith('data:image'))


if __name__ == '__main__':
    unittest.main()

