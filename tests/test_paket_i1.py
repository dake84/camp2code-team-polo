"""Paket I1: persistente Simulation Runs (Berechnung -> Speicherung -> Wiedergabe) mit Fingerprint und Fortschritt.

Hardwarefrei: Hardware-Attrappen, ReplayCar, kleine Test-Sessions (40 Frames, wenige Sekunden). Dash laeuft ueber die
Attrappe (tests/dash_stub.py); mit installiertem Dash 4 zusaetzlich ueber den Flask-Testclient. Nicht getestet:
Bedienung im Browser, Laufzeit auf dem Pi.
"""
import importlib.util
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from test_paket_f import write_session  # noqa: E402  (vor jeder Dash-Attrappe importieren)

import simulation_runs as sr  # noqa: E402

HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components', 'plotly'))
FRAMES = 40
NO_UPDATE = object()


def tree_state(folder):
    """(Pfad, Groesse, mtime) aller Dateien unterhalb von ``folder`` - zum Nachweis, dass nichts veraendert wurde."""
    return sorted((str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in Path(folder).rglob('*') if p.is_file())


def sim_frame(index, frame_id, **kwargs):
    from sim_engine import SimFrame
    values = dict(index=index, frame_id=frame_id, warmup=False, crop=(0.15, 0.55), lanes=((-1.2, 166.0), None),
                  lane_count=1, plausible=False, raw_angle=95.0, requested_angle=100.0, car_angle=99.0, speed=30,
                  held=False, stop_reason=None, process_ms=1.5, then_raw_angle=None, then_car_angle=None,
                  then_speed=None, then_lane_count=None, then_plausible=None, then_status=None,
                  captured_ns=10_000_000_000 + index * 40_000_000, process_size=(320, 240))
    values.update(kwargs)
    return SimFrame(**values)


class Base(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)                       # erwartete Fehler (Fehlerfaelle) nicht auf die Konsole
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                                  cameracar_controller={'lost_line_timeout': 1})
        os.chdir(self.tmp.name)
        self.root = os.path.join(self.tmp.name, 'Data_AI', 'sessions')
        self.runs = os.path.join(self.tmp.name, 'Data_AI', 'simulation_runs')

    def make_session(self, count=FRAMES):
        return write_session(self.root, count=count)

    def engine(self, cls=None):
        import ConfigReader
        from sim_engine import SimEngine
        return (cls or SimEngine)(ConfigReader.ConfigReader('cameracar_controller', self.config), v_min=20, v_max=100)

    def lab(self, application=None):
        import dash_stub
        modules = dash_stub.modules()
        ui = SimpleNamespace(dbc=modules['dash_bootstrap_components'], dcc=modules['dash.dcc'], html=modules['dash.html'],
                             Input=dash_stub.Input, Output=dash_stub.Output, State=dash_stub.State,
                             ctx=dash_stub.ctx, no_update=NO_UPDATE)
        from sim_lab import SimLab
        return SimLab(application or SimpleNamespace(running=lambda: False), ui, lambda image: 'data:x', self.root)

    def run_to_end(self, lab, name, force=False, **kwargs):
        args = dict(processor='copilot', param_mode='recorded', lo=None, hi=None, warmup=5)
        args.update(kwargs)
        message, start, pick = lab.start_job(force, name, args['processor'], args['param_mode'], args['lo'], args['hi'],
                                             args['warmup'])
        self.assertTrue(lab.runner.wait(60), 'Berechnung nicht fertig')
        return message, start, pick


# ------------------------------------------------------------------ Fingerprint
class FingerprintTests(Base):
    def test_normalisierung_ist_stabil(self):
        a = {'a': [1, 2.0], 'b': {'y': 1, 'x': (3, 4)}, 'c': 2.0}
        b = {'c': 2, 'b': {'x': [3, 4], 'y': 1}, 'a': (1, 2)}
        self.assertEqual(sr.fingerprint(a), sr.fingerprint(b))               # Reihenfolge, Tupel/Liste, 2.0 == 2
        self.assertNotEqual(sr.fingerprint(a), sr.fingerprint({**a, 'c': 2.5}))
        self.assertEqual(len(sr.fingerprint(a)), 64)
        self.assertEqual(sr.normalize(float('nan')), None)
        self.assertEqual(sr.canonical_json({'x': float('inf')}), '{"x":null}')

    def request(self, engine=None, **kwargs):
        from frame_source import SessionSource
        args = dict(processor_key='copilot', param_mode='recorded', studio_params=None, lo=None, hi=None, warmup=5,
                    speed_policy=None)
        args.update(kwargs)
        engine = engine or self.engine()
        return sr.prepare_request(engine, SessionSource(self.name, self.root), **args)

    def setUp(self):
        super().setUp()
        self.name = self.make_session()

    def test_gleiche_parameter_gleicher_fingerprint_ohne_fluechtige_werte(self):
        first = self.request()
        time.sleep(0.01)
        second = self.request(engine=self.engine())
        self.assertEqual(first.fingerprint, second.fingerprint)
        text = sr.canonical_json(first.snapshot)
        self.assertNotIn(self.tmp.name, text)                               # keine Pfade
        self.assertNotIn(str(time.localtime().tm_year), text.split('"recorded_parameters"')[0])  # keine Zeitangaben
        for key in ('source', 'range', 'processor', 'param_mode', 'driving_mode', 'controller', 'speed_policy'):
            self.assertIn(key, first.snapshot)
        self.assertEqual(first.snapshot['controller']['steering_gain'], 2)
        self.assertEqual(first.total_frames, FRAMES)

    def test_jede_ergebnisrelevante_aenderung_aendert_den_fingerprint(self):
        from speed_policy import SpeedPolicy
        base = self.request().fingerprint
        studio = (tuple([100, 130]), (80, 255), (40, 255), (0.15, 0.55), 3, 5, (50, 100),
                  {'roi': True, 'mask': True, 'blur': True, 'morph': True, 'canny': False, 'lanes': True})
        crop = (studio[0], studio[1], studio[2], (0.2, 0.55)) + studio[4:]
        engine = self.engine()
        engine._cfg.set_value('steering_gain', 1.5)
        variants = {
            'Prozessor': self.request(processor_key='legacy'),
            'Studio': self.request(param_mode='studio', studio_params=studio),
            'Studio-Crop': self.request(param_mode='studio', studio_params=crop),
            'Bereich': self.request(lo=10, hi=30),
            'Vorlauf': self.request(lo=10, warmup=3),
            'Speed adaptiv': self.request(speed_policy=SpeedPolicy('adaptive', 30, 30, 45)),
            'Speed fest 35': self.request(speed_policy=SpeedPolicy('fixed', 35, 30, 45)),
            'Lenkverstaerkung': self.request(engine=engine),
        }
        fingerprints = {name: req.fingerprint for name, req in variants.items()}
        self.assertNotIn(base, fingerprints.values(), fingerprints)
        self.assertEqual(len(set(fingerprints.values())), len(fingerprints), fingerprints)
        self.assertEqual(variants['Studio'].snapshot['processing_parameters'][3], [0.15, 0.55])  # Studio = Snapshot-Wert

    def test_ungueltige_eingaben(self):
        with self.assertRaises(ValueError):
            self.request(processor_key='gibt_es_nicht')
        with self.assertRaises(ValueError):
            self.request(param_mode='studio', studio_params=None)
        with self.assertRaises(ValueError):
            self.request(lo=900, hi=950)                                    # Bereich ausserhalb der Session
        with self.assertRaises(ValueError):
            sr.prepare_request(self.engine(), SimpleNamespace(kind='ringbuffer', frames=[], name='x'), 'copilot',
                               'recorded')                                  # Ringbuffer ist fluechtig


# ------------------------------------------------------------------ Store
class StoreTests(Base):
    def store(self):
        return sr.SimulationRunStore(self.runs)

    def make_run(self, store, run_id='r1', status='completed', frames=2, source='s', fingerprint='f' * 64):
        manifest = sr.SimulationRunManifest(
            run_id=run_id, display_name=f'name {run_id}', created_at='2026-10-29T10:00:00', status='queued',
            source_session=source, parameters={'processor': 'copilot', 'param_mode': 'recorded'},
            fingerprint=fingerprint, total_frames=frames)
        store.create(manifest)
        writer = store.writer(run_id)
        for i in range(frames):
            writer.write(sim_frame(i, 100 + i))
        writer.close()
        manifest.status, manifest.frames_done, manifest.results_bytes = status, frames, writer.bytes
        store.write_manifest(manifest)
        return manifest

    def test_speichern_und_laden_eines_minimalen_runs(self):
        store = self.store()
        self.assertFalse(Path(self.runs).exists())                          # fehlt: wird bei Bedarf angelegt
        self.assertEqual(store.list_runs(), [])                             # Leerzustand ohne Ausnahme
        self.make_run(store)
        self.assertTrue(Path(self.runs, 'r1', 'manifest.json').is_file())
        self.assertEqual([p.name for p in Path(self.runs, 'r1').iterdir() if p.name.endswith('.tmp')], [])
        entry = store.list_runs()[0]
        self.assertTrue(entry.usable)
        self.assertEqual(entry.manifest.format_version, sr.RESULT_FORMAT_VERSION)
        reader = store.open_reader('r1')
        self.assertEqual(len(reader), 2)
        self.assertEqual(reader.position_of(101), 1)
        first = reader.frames[0]
        self.assertEqual(first.lanes, ((-1.2, 166.0), None))                # Linien als Tupel, None bleibt None
        self.assertEqual((first.crop, first.process_size, first.car_angle), ((0.15, 0.55), (320, 240), 99.0))
        result = reader.as_result()
        self.assertEqual([f.frame_id for f in result.frames], [100, 101])
        self.assertEqual(result.processor_key, 'copilot')

    def test_unbekannte_felder_werden_toleriert_und_bleiben_erhalten(self):
        store = self.store()
        self.make_run(store)
        path = Path(self.runs, 'r1', 'manifest.json')
        data = json.loads(path.read_text())
        data['zukunftsfeld'] = {'a': 1}
        path.write_text(json.dumps(data))
        results = Path(self.runs, 'r1', 'results.jsonl')
        line = json.loads(results.read_text().splitlines()[0])
        line['neues_feld'] = 5
        results.write_text(json.dumps(line) + '\n' + results.read_text().splitlines()[1] + '\n')
        entry = store.get_entry('r1')
        self.assertEqual(entry.manifest.extra, {'zukunftsfeld': {'a': 1}})
        self.assertTrue(entry.status in ('completed', 'corrupt'))           # Groesse geaendert -> nicht blind vertrauen
        manifest = entry.manifest
        manifest.results_bytes = results.stat().st_size
        store.write_manifest(manifest)
        self.assertEqual(len(store.open_reader('r1')), 2)
        self.assertEqual(json.loads(path.read_text())['zukunftsfeld'], {'a': 1})

    def test_defekte_abgebrochene_und_fehlgeschlagene_runs(self):
        store = self.store()
        self.make_run(store, 'a_gut')
        self.make_run(store, 'b_laeuft', status='running')                  # kein aktiver Job: abgebrochen
        self.make_run(store, 'c_fehler', status='failed')
        Path(self.runs, 'd_kaputt').mkdir()
        Path(self.runs, 'd_kaputt', 'manifest.json').write_text('{kaputt')
        Path(self.runs, 'e_ohne_manifest').mkdir()
        self.make_run(store, 'f_ergebnisse_weg')
        Path(self.runs, 'f_ergebnisse_weg', 'results.jsonl').write_text('')
        status = {e.run_id: e.status for e in store.list_runs()}
        self.assertEqual(status, {'a_gut': 'completed', 'b_laeuft': 'incomplete', 'c_fehler': 'failed',
                                  'd_kaputt': 'corrupt', 'e_ohne_manifest': 'corrupt', 'f_ergebnisse_weg': 'corrupt'})
        active = {e.run_id: e.status for e in store.list_runs(active_run_id='b_laeuft')}
        self.assertEqual(active['b_laeuft'], 'running')                     # aktiver Job: nicht "unvollstaendig"
        for bad in ('b_laeuft', 'c_fehler', 'd_kaputt', 'f_ergebnisse_weg'):
            with self.assertRaises(sr.RunStoreError, msg=bad):
                store.open_reader(bad)
        self.assertEqual(store.find_matching('s', 'f' * 64).run_id, 'a_gut')   # nur der erfolgreiche Run
        labels = [e.label() for e in store.list_runs()]
        self.assertTrue(any('DEFEKT' in label for label in labels) and any('FEHLGESCHLAGEN' in label for label in labels))

    def test_kompatible_formatversion_und_quelle(self):
        store = self.store()
        manifest = self.make_run(store)
        self.assertIsNone(store.find_matching('andere_session', 'f' * 64))
        self.assertIsNone(store.find_matching('s', 'e' * 64))
        manifest.format_version = 99
        store.write_manifest(manifest)
        self.assertIsNone(store.find_matching('s', 'f' * 64))               # unbekannte Formatversion
        self.assertEqual(store.get_entry('r1').status, 'corrupt')

    def test_reader_cache_ist_begrenzt_und_liest_nicht_erneut(self):
        store = self.store()
        for run_id in ('r1', 'r2', 'r3'):
            self.make_run(store, run_id)
        created = []
        real = sr.SimulationResultReader
        with mock.patch.object(sr, 'SimulationResultReader', lambda *a, **k: created.append(1) or real(*a, **k)):
            first = store.open_reader('r1')
            self.assertIs(store.open_reader('r1'), first)
            self.assertEqual(len(created), 1)
            store.open_reader('r2')
            store.open_reader('r3')
        self.assertLessEqual(len(store._readers), sr.MAX_CACHED_READERS)

    def test_run_id_pfadschutz_und_kein_ueberschreiben(self):
        store = self.store()
        for bad in ('../x', 'a/b', '', None, 'x' * 70):
            with self.assertRaises(sr.RunStoreError, msg=bad):
                store.run_dir(bad)
        self.make_run(store)
        before = tree_state(self.runs)
        with self.assertRaises(sr.RunStoreError):
            self.make_run(store)                                            # gleiche ID: nie ueberschreiben
        self.assertEqual(tree_state(self.runs), before)


# ------------------------------------------------------------------ Runner
class GatedEngine:
    """Echte Engine, die vor dem ersten Frame wartet (kontrollierbarer Hintergrund-Job)."""

    @staticmethod
    def make(base_cls, test, **behavior):
        class Engine(base_cls):
            entered = threading.Event()
            gate = threading.Event()

            def run(self, *args, **kwargs):
                Engine.entered.set()
                Engine.gate.wait(30)
                if behavior.get('fail_after') is not None:
                    seen = []
                    original = kwargs['on_frame']

                    def on_frame(frame):
                        original(frame)
                        seen.append(frame)
                        if len(seen) >= behavior['fail_after']:
                            raise OSError('Platte voll')
                    kwargs['on_frame'] = on_frame
                if behavior.get('raise_text'):
                    raise RuntimeError(behavior['raise_text'])
                return super().run(*args, **kwargs)
        return Engine


class RunnerTests(Base):
    def setUp(self):
        super().setUp()
        self.name = self.make_session()

    def use_engine(self, lab, engine):
        lab._engine, lab._engine_revision = engine, 0

    def runs_on_disk(self):
        return sorted(p.name for p in Path(self.runs).iterdir() if p.is_dir()) if Path(self.runs).exists() else []

    def test_berechnung_speicherung_wiedergabe_ohne_neuberechnung(self):
        lab = self.lab()
        message, start, pick = self.run_to_end(lab, self.name)
        self.assertIn('Berechnung gestartet', message)
        self.assertIs(pick, NO_UPDATE)
        (run_id,) = self.runs_on_disk()
        manifest = lab.store.load_manifest(run_id)
        self.assertEqual((manifest.status, manifest.frames_done, manifest.total_frames), ('completed', FRAMES, FRAMES))
        self.assertEqual(manifest.source_session, self.name)
        self.assertEqual(len(manifest.fingerprint), 64)
        lines = Path(self.runs, run_id, 'results.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), FRAMES)
        record = json.loads(lines[10])
        for key in ('frame_id', 'captured_ns', 'wall_ns', 'image_ref', 'lanes', 'raw_angle', 'requested_angle',
                    'car_angle', 'speed', 'driving_mode', 'processor_name', 'held', 'stop_reason', 'diagnostics',
                    'step_ms'):
            self.assertIn(key, record, key)
        self.assertEqual((record['driving_mode'], record['processor_name']), ('camera', 'CopilotLaneProcessor'))
        self.assertTrue(record['image_ref'].startswith('frames/frame_'))
        self.assertTrue(record['wall_ns'] > 0)
        # Ergebnis = direkte Berechnung der Engine
        from frame_source import SessionSource
        direct = self.engine().run(SessionSource(self.name, self.root), 'copilot', warmup=5)
        status, stats, maximum, chart, token, info = lab.load_run(run_id)
        self.assertTrue(token)
        loaded = lab.lookup(token)[0]
        self.assertEqual([f.car_angle for f in loaded.frames], [f.car_angle for f in direct.frames])
        self.assertEqual([f.lanes for f in loaded.frames], [f.lanes for f in direct.frames])
        self.assertEqual(maximum, FRAMES - 1)
        self.assertIn('keine Neuberechnung', status)
        # Wiedergabe: weder Engine noch Prozessor werden aufgerufen, results.jsonl wird nicht erneut gelesen
        opened = []
        real_open = Path.open

        def counting_open(path, *args, **kwargs):
            if path.name == 'results.jsonl':
                opened.append(path)
            return real_open(path, *args, **kwargs)
        with mock.patch('sim_engine.SimEngine.run', side_effect=AssertionError('Neuberechnung')), \
                mock.patch('processor_registry.create_processor', side_effect=AssertionError('Prozessor')), \
                mock.patch.object(Path, 'open', counting_open):
            for position in (0, 5, 6, 7, 30):
                then, now = lab.render_frame(loaded, lab.lookup(token)[1], position)
                self.assertEqual(then.shape, now.shape)
            for index in range(3, 9):
                out = lab.playback('sim-play-tick', token, 1.0, 'every', index, False, now=100 + index)
                self.assertEqual(len(out), 3)
        self.assertEqual(opened, [])

    def test_vorhandener_run_wird_nicht_neu_berechnet_aber_bewusst_neu_moeglich(self):
        lab = self.lab()
        self.run_to_end(lab, self.name)
        (first,) = self.runs_on_disk()
        before = tree_state(Path(self.runs, first))
        message, start, pick = lab.start_job(False, self.name, 'copilot', 'recorded', None, None, 5)
        self.assertIn('Passender Run vorhanden', message)
        self.assertEqual(pick['run_id'], first)
        self.assertIs(start, NO_UPDATE)
        self.assertFalse(lab.runner.is_active())
        self.assertEqual(self.runs_on_disk(), [first])                      # kein neuer Ordner
        time.sleep(0.01)
        message, start, pick = self.run_to_end(lab, self.name, force=True)  # bewusst neu
        self.assertIn('Berechnung gestartet', message)
        second = [r for r in self.runs_on_disk() if r != first]
        self.assertEqual(len(second), 1)
        self.assertEqual(tree_state(Path(self.runs, first)), before)        # alter Run unveraendert
        self.assertEqual(lab.store.load_manifest(first).fingerprint, lab.store.load_manifest(second[0]).fingerprint)
        hint = lab.match_hint(self.name, 'copilot', 'recorded', None, None, 5)
        self.assertIn('Passender Run vorhanden', ''.join(str(c) for c in hint.children))
        other = lab.match_hint(self.name, 'legacy', 'recorded', None, None, 5)
        self.assertIn('Noch kein vollständiger Run', other.children)

    def test_parameteraenderung_erzeugt_neuen_run_alte_bleiben(self):
        lab = self.lab()
        self.run_to_end(lab, self.name)
        (first,) = self.runs_on_disk()
        before = tree_state(Path(self.runs, first))
        self.run_to_end(lab, self.name, processor='legacy')
        self.assertEqual(len(self.runs_on_disk()), 2)
        self.assertEqual(tree_state(Path(self.runs, first)), before)

    def test_start_blockiert_nicht_zweiter_start_wird_abgewiesen_und_abbruch(self):
        engine_cls = GatedEngine.make(type(self.engine()), self)
        lab = self.lab()
        self.use_engine(lab, self.engine(engine_cls))
        started = time.monotonic()
        message, start, pick = lab.start_job(False, self.name, 'copilot', 'recorded', None, None, 5)
        self.assertLess(time.monotonic() - started, 5)                      # kehrt sofort zurueck
        self.assertIn('Berechnung gestartet', message)
        self.assertTrue(engine_cls.entered.wait(10))
        self.assertTrue(lab.runner.is_active())
        self.assertFalse(lab.job_interval_disabled())                       # Takt laeuft
        snap = lab.runner.progress.snapshot()
        self.assertEqual((snap.status, snap.total_frames), ('running', FRAMES))
        text = lab.start_job(True, self.name, 'copilot', 'recorded', None, None, 5)[0]
        self.assertIn('läuft bereits', text)                                # zweiter Start: verstaendliche Meldung
        with self.assertRaises(sr.RunnerBusyError):
            lab.runner.start(sr.prepare_request(self.engine(), lab._session_source(self.name), 'copilot', 'recorded'),
                             self.engine())
        self.assertEqual(len(self.runs_on_disk()), 1)
        self.assertEqual(lab.cancel_job(), 'Abbruch angefordert')
        self.assertTrue(lab.job_view()[6])                                  # Abbrechen-Knopf gesperrt
        engine_cls.gate.set()
        self.assertTrue(lab.runner.wait(30))
        (run_id,) = self.runs_on_disk()
        manifest = lab.store.load_manifest(run_id)
        self.assertEqual(manifest.status, 'cancelled')
        self.assertLess(manifest.frames_done, manifest.total_frames)
        self.assertIsNone(lab.store.find_matching(self.name, manifest.fingerprint))    # nicht als Erfolg behandelt
        self.assertEqual(lab.runner.progress.snapshot().status, 'cancelled')
        self.assertEqual(lab.cancel_job(), 'Keine Berechnung aktiv')
        status = lab.load_run(run_id)
        self.assertIn('nicht abspielbar', status[0])
        self.assertIsNone(status[4])
        self.run_to_end(lab, self.name)                                     # danach ist ein neuer Lauf moeglich

    def test_fahrt_waehrend_der_berechnung_bricht_den_lauf_ab(self):
        driving = [False]
        engine_cls = GatedEngine.make(type(self.engine()), self)
        lab = self.lab(SimpleNamespace(running=lambda: driving[0]))
        self.use_engine(lab, self.engine(engine_cls))
        self.assertIn('Berechnung gestartet', lab.start_job(False, self.name, 'copilot', 'recorded', None, None, 5)[0])
        self.assertTrue(engine_cls.entered.wait(10))
        driving[0] = True                                                   # Fahrt startet: CPU nicht belasten
        engine_cls.gate.set()
        self.assertTrue(lab.runner.wait(30))
        (run_id,) = self.runs_on_disk()
        manifest = lab.store.load_manifest(run_id)
        self.assertEqual((manifest.status, manifest.phase), ('cancelled', 'Abgebrochen (Fahrt gestartet)'))
        self.assertIsNone(lab.store.find_matching(self.name, manifest.fingerprint))

    def test_session_quelle_wird_zwischengespeichert_und_bei_aenderung_neu_geladen(self):
        lab = self.lab()
        first = lab._session_source(self.name)
        self.assertIs(lab._session_source(self.name), first)
        csv_path = Path(self.root, self.name, 'frames.csv')
        csv_path.write_text('\n'.join(csv_path.read_text().splitlines()[:-1]) + '\n')
        second = lab._session_source(self.name)
        self.assertIsNot(second, first)
        self.assertEqual(len(second.frames), FRAMES - 1)

    def test_fehler_werden_erfasst_und_nie_als_erfolg_behandelt(self):
        for label, behavior in (('sofort', dict(raise_text='kaputt')), ('mittendrin', dict(fail_after=7))):
            with self.subTest(label):
                engine_cls = GatedEngine.make(type(self.engine()), self, **behavior)
                lab = self.lab()
                lab.store = sr.SimulationRunStore(os.path.join(self.tmp.name, f'runs_{label}'))
                lab.runner = sr.SimulationRunner(lab.store, lab._session_source)
                self.use_engine(lab, self.engine(engine_cls))
                lab.start_job(False, self.name, 'copilot', 'recorded', None, None, 5)
                engine_cls.gate.set()
                self.assertTrue(lab.runner.wait(30))
                (entry,) = lab.store.list_runs()
                self.assertEqual(entry.status, 'failed')
                self.assertTrue(entry.manifest.error)
                self.assertIn('FEHLGESCHLAGEN', entry.label())
                snap = lab.runner.progress.snapshot()
                self.assertEqual(snap.status, 'failed')
                self.assertTrue(snap.error)
                self.assertIn('Fehler:', lab.job_view()[5])
                if label == 'mittendrin':
                    self.assertEqual(entry.manifest.frames_done, 7)
                self.assertIsNone(lab.store.find_matching(self.name, entry.manifest.fingerprint))
                self.assertIn('nicht abspielbar', lab.load_run(entry.run_id)[0])

    def test_fortschritt_wird_thread_sicher_veroeffentlicht(self):
        base = type(self.engine())
        seen = []

        class Recording(base):
            def run(inner, *args, **kwargs):
                progress = kwargs['progress']

                def spy(done, total, frame_id):
                    progress(done, total, frame_id)
                    seen.append(lab.runner.progress.snapshot())
                kwargs['progress'] = spy
                return super().run(*args, **kwargs)
        lab = self.lab()
        self.use_engine(lab, self.engine(Recording))
        self.run_to_end(lab, self.name)
        self.assertEqual(len(seen), FRAMES + 1)
        self.assertEqual({s.status for s in seen}, {'running'})
        self.assertEqual([s.current_frame for s in seen], list(range(FRAMES + 1)))
        self.assertTrue(all(s.total_frames == FRAMES and s.run_id for s in seen))
        self.assertTrue(any(s.frame_id is not None for s in seen))
        self.assertEqual(seen[FRAMES // 2].progress_percent, 50.0)
        final = lab.runner.progress.snapshot()
        self.assertEqual((final.status, final.progress_percent, final.current_frame), ('completed', 100.0, FRAMES))
        value, label, color, animated, text, error, cancel_disabled, done = lab.job_view()
        self.assertEqual((value, color, animated, cancel_disabled, error), (100.0, 'success', False, True, ''))
        self.assertIn(f'{FRAMES} Frames', text)
        self.assertEqual(done['status'], 'completed')
        self.assertIs(lab.job_view()[7], NO_UPDATE)                         # Fertig-Meldung nur einmal
        self.assertEqual(lab.job_view()[0], 100.0)                          # Bar springt nicht zurueck
        self.assertTrue(lab.job_interval_disabled())

    def test_zwischenstand_im_manifest_und_inkrementelle_ergebnisse(self):
        base = type(self.engine())
        snapshots = []

        class Slow(base):
            def run(inner, *args, **kwargs):
                on_frame = kwargs['on_frame']
                state = {'n': 0}

                def spy(frame):
                    on_frame(frame)
                    state['n'] += 1
                    if state['n'] == 20:
                        run_id = lab.runner.progress.snapshot().run_id
                        path = Path(self.runs, run_id)
                        snapshots.append((len((path / 'results.jsonl').read_text().splitlines()),
                                          json.loads((path / 'manifest.json').read_text())['status']))
                kwargs['on_frame'] = spy
                return super().run(*args, **kwargs)
        lab = self.lab()
        self.use_engine(lab, self.engine(Slow))
        with mock.patch.object(sr, 'MANIFEST_EVERY_S', 0.0):
            self.run_to_end(lab, self.name)
        self.assertEqual(snapshots, [(20, 'running')])                      # Ergebnisse stehen schon waehrend des Laufs

    def test_sessions_und_alte_aufnahmen_bleiben_unveraendert(self):
        before = tree_state(self.root)
        lab = self.lab()
        self.run_to_end(lab, self.name)
        (run_id,) = self.runs_on_disk()
        lab.load_run(run_id)
        self.assertEqual(tree_state(self.root), before)
        self.assertFalse(any(p.name == 'simulation_runs' for p in Path(self.root).iterdir()))

    def test_quell_session_geaendert_fuehrt_zu_fehler(self):
        lab = self.lab()
        request, engine = lab.prepare(self.name, 'copilot', 'recorded', None, None, 5)
        csv_path = Path(self.root, self.name, 'frames.csv')
        lines = csv_path.read_text().splitlines()
        csv_path.write_text('\n'.join(lines[:-3]) + '\n')                   # drei Frames weniger
        os.utime(csv_path, None)
        lab.runner.start(request, engine)
        self.assertTrue(lab.runner.wait(30))
        self.assertEqual(lab.runner.progress.snapshot().status, 'failed')
        self.assertIn('geändert', lab.runner.progress.snapshot().error)

    def test_keine_simulation_waehrend_der_fahrt_und_live_quelle(self):
        lab = self.lab(SimpleNamespace(running=lambda: True))
        self.assertIn('Fahrt', lab.start_job(False, self.name, 'copilot', 'recorded', None, None, 0)[0])
        lab = self.lab()
        self.assertIn('Session', lab.start_job(False, '__live__', 'copilot', 'recorded', None, None, 0)[0])
        self.assertIn('Session', lab.start_job(False, None, 'copilot', 'recorded', None, None, 0)[0])
        self.assertIn('nicht möglich', lab.start_job(False, self.name, 'copilot', 'recorded', 900, 950, 0)[0])
        self.assertEqual(self.runs_on_disk(), [])


# ------------------------------------------------------------------ Dash-Callbacks (Attrappe)
class CallbackFlowTests(Base):
    def setUp(self):
        super().setUp()
        self.name = self.make_session()
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        self.module, self.context, self.ctx = load_dashboard()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.fake = FakeApplication()
        self.app = self.module.create_app(self.fake)
        self.cb = self.app.callbacks
        self.lab = self.app.c2c_tabs[0].ctx.sim

    def wait(self):
        self.assertTrue(self.lab.runner.wait(60))

    def test_callbacks_registriert_ohne_doppelte_outputs(self):
        from sim_lab import SimLab
        self.assertEqual(set(SimLab.CALLBACKS) - set(self.cb), set())
        self.assertNotIn('sim_run', self.cb)
        sim_outputs = [(o.component_id, o.component_property) for name in SimLab.CALLBACKS for o in self.cb[name].outputs]
        self.assertEqual(len(sim_outputs), len(set(sim_outputs)))
        from test_dashboard_callbacks import id_key
        owners = {}
        for name, callback in self.cb.items():
            for o in callback.outputs:
                owners.setdefault((id_key(o.component_id), o.component_property), []).append(name)
        self.assertEqual([k for k, v in owners.items() if len(v) > 1], [])
        # getrennte Callbacks: Start veraendert nie die Fortschrittsanzeige, Polling startet nie etwas
        start_outputs = {o.component_id for o in self.cb['sim_run_start'].outputs}
        poll_outputs = {o.component_id for o in self.cb['sim_job_poll'].outputs}
        self.assertEqual(start_outputs & poll_outputs, set())
        self.assertEqual([(o.component_id, o.component_property) for o in self.cb['sim_job_interval'].outputs],
                         [('sim-job-tick', 'disabled')])

    def test_ablauf_start_fortschritt_liste_laden_wiedergabe(self):
        cb, ctx = self.cb, self.ctx
        ctx.set('cockpit-source', 'value')
        empty = cb['sim_run_list'](None, None, None, 'sim', None, 'copilot', 'recorded', None, None, 0)
        self.assertEqual(empty[0], [])                                      # Leerzustand: keine Runs
        self.assertIs(empty[1], self.module.no_update)
        ctx.set('sim-run')
        message, start, pick = cb['sim_run_start'](1, None, self.name, 'copilot', 'recorded', None, None, 5)
        self.assertIn('Berechnung gestartet', message)
        self.assertEqual(cb['sim_job_interval'](start, None), self.lab.job_interval_disabled())
        self.wait()
        out = cb['sim_job_poll'](1)
        self.assertEqual((out[0], out[2]), (100.0, 'success'))
        done = out[7]
        self.assertEqual(done['status'], 'completed')
        self.assertTrue(cb['sim_job_interval'](start, done))                # nach Abschluss: Takt aus
        ctx.set('sim-job-done', 'data')
        options, value, hint = cb['sim_run_list'](done, None, None, 'sim', self.name, 'copilot', 'recorded', None, None, 5)
        self.assertEqual([o['value'] for o in options], [done['run_id']])
        self.assertEqual(value, done['run_id'])                             # neuer Run wird ausgewaehlt
        ctx.set('sim-run-choice', 'value')
        status, stats, maximum, chart, token, info = cb['sim_load_run'](value)
        self.assertTrue(token)
        self.assertEqual(maximum, FRAMES - 1)
        self.assertGreaterEqual(len(chart['data']), 4)
        ctx.set('sim-result-token', 'data')
        index, paused, label = cb['sim_playback'](None, None, None, None, None, token, 1.0, 'realtime', 0, True)
        self.assertEqual((paused, label), (True, '▶ Play'))
        then, now, table, frame_label, _rate = cb['sim_render'](index, token, True)
        self.assertTrue(then.startswith('data:image') or then == 'data:x')
        # Run-Auswahl aufheben: Anzeige leer, Run bleibt auf der Platte
        out = cb['sim_load_run'](None)
        self.assertIsNone(out[4])
        self.assertEqual(len(list(Path(self.runs).iterdir())), 1)

    def test_parameteraenderung_aendert_weder_run_noch_anzeige(self):
        cb, ctx = self.cb, self.ctx
        self.run_to_end(self.lab, self.name)
        (run_id,) = [e.run_id for e in self.lab.store.list_runs()]
        token = cb['sim_load_run'](run_id)[4]
        before = tree_state(self.runs)
        ctx.set('sim-from', 'value')
        options, value, hint = cb['sim_run_list'](None, None, None, 'sim', self.name, 'copilot', 'recorded', 10, None, 5)
        self.assertIs(value, self.module.no_update)                         # Auswahl bleibt
        self.assertIn('Noch kein vollständiger Run', hint.children)         # anderer Bereich -> anderer Fingerprint
        self.assertIsNotNone(self.lab.lookup(token))                        # geladener Run bleibt angezeigt
        self.assertEqual(tree_state(self.runs), before)
        ctx.set('sim-run-pick', 'data')
        value = cb['sim_run_list'](None, {'run_id': run_id, 'nonce': 1}, None, 'sim', self.name, 'copilot', 'recorded',
                                   None, None, 5)[1]
        self.assertEqual(value, run_id)

    def test_frame_balken_und_play_nur_mit_geladenem_run(self):
        cb, ctx = self.cb, self.ctx
        self.run_to_end(self.lab, self.name, lo=10, hi=30, warmup=5)
        (run_id,) = [e.run_id for e in self.lab.store.list_runs()]
        status, stats, maximum, chart, token, info = cb['sim_load_run'](run_id)
        ids = chart['data'][0]['x']
        self.assertEqual(ids, list(range(5, 31)))                           # x = Frame-IDs (mit Vorlauf) = Balken-Skala
        self.assertEqual(chart['data'][0]['customdata'], list(range(len(ids))))
        keep = self.module.no_update
        ctx.set('sim-bar', 'data')
        out = cb['sim_playback'](None, None, None, None, None, token, 1.0, 'realtime', 0, True, {'frame': 12, 'nonce': 1})
        self.assertEqual(out, (7, keep, keep))
        ctx.set('sim-play', 'n_clicks')
        index, paused, label = cb['sim_playback'](None, None, 1, None, None, token, 1.0, 'realtime', 2, True)
        self.assertEqual((paused, label), (False, '⏸ Pause'))               # Play mit geladenem Run
        out = cb['sim_playback'](None, None, 1, None, None, None, 1.0, 'realtime', 5, True)
        self.assertEqual(out, (0, True, '▶ Play'))                          # ohne Run: kein Play
        self.assertEqual(cb['sim_render'](5, None, True), (None, None, 'Noch keine Simulation berechnet', '', ''))

    def test_fahrt_sperrt_den_start(self):
        self.fake._running = True
        self.ctx.set('sim-run')
        message = self.cb['sim_run_start'](1, None, self.name, 'copilot', 'recorded', None, None, 0)[0]
        self.assertIn('Fahrt', message)
        self.assertEqual(self.lab.store.list_runs(), [])

    def test_cockpit_start_bleibt_im_simulationsmodus_gesperrt(self):
        self.cb['sim_view_mode']('sim')
        self.assertTrue(self.lab.active)
        options = self.cb['sim_view_mode']('sim')[2]
        self.assertEqual([o['value'] for o in options], [self.name])        # nur gespeicherte Sessions
        self.cb['sim_view_mode']('live')


@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashTests(Base):
    def test_layout_abhaengigkeiten_und_start_ueber_http(self):
        name = self.make_session()
        import dash_bootstrap_components as dbc
        from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
        from sim_lab import SimLab
        fake = SimpleNamespace(running=lambda: False, car=SimpleNamespace(frame_history=None), offline=True)
        app = Dash(__name__)
        app.layout = html.Div([SimLab.wrap_cockpit(html.Div('live'), dbc, dcc, html)])
        sim = SimLab(fake, SimpleNamespace(dbc=dbc, dcc=dcc, html=html, Input=Input, Output=Output, State=State,
                                           ctx=ctx, no_update=no_update), lambda image: 'data:x', self.root)
        sim.register(app)
        client = app.server.test_client()
        self.assertEqual(client.get('/_dash-layout').status_code, 200)
        deps = client.get('/_dash-dependencies').get_json()
        self.assertEqual(len(deps), len(SimLab.CALLBACKS))
        outputs = [o for dep in deps for o in (dep['output'].strip('.').split('...') if dep['output'].startswith('..')
                                               else [dep['output']])]
        self.assertEqual(len(outputs), len(set(outputs)))

        def post(outputs, inputs, state, changed):
            output = ('..' + '...'.join(f'{i}.{p}' for i, p in outputs) + '..' if len(outputs) > 1
                      else f'{outputs[0][0]}.{outputs[0][1]}')
            specs = [{'id': i, 'property': p} for i, p in outputs]
            body = {'output': output, 'outputs': specs if len(specs) > 1 else specs[0],
                    'inputs': [{'id': i, 'property': p, 'value': v} for i, p, v in inputs],
                    'state': [{'id': i, 'property': p, 'value': v} for i, p, v in state], 'changedPropIds': changed}
            return client.post('/_dash-update-component', json=body)

        start = post([('sim-job-msg', 'children'), ('sim-job-start', 'data'), ('sim-run-pick', 'data')],
                     [('sim-run', 'n_clicks', 1), ('sim-run-force', 'n_clicks', None)],
                     [('sim-session', 'value', name), ('sim-processor', 'value', 'copilot'),
                      ('sim-params', 'value', 'recorded'), ('sim-from', 'value', None), ('sim-to', 'value', None),
                      ('sim-warmup', 'value', 5)], ['sim-run.n_clicks'])
        self.assertEqual(start.status_code, 200, start.get_data(as_text=True)[:400])
        self.assertIn('Berechnung gestartet', start.get_data(as_text=True))
        self.assertTrue(sim.runner.wait(60))
        poll = post([('sim-job-bar', 'value'), ('sim-job-bar', 'label'), ('sim-job-bar', 'color'),
                     ('sim-job-bar', 'animated'), ('sim-job-text', 'children'), ('sim-job-error', 'children'),
                     ('sim-job-cancel', 'disabled'), ('sim-job-done', 'data')],
                    [('sim-job-tick', 'n_intervals', 3)], [], ['sim-job-tick.n_intervals'])
        self.assertEqual(poll.status_code, 200, poll.get_data(as_text=True)[:400])
        payload = poll.get_json()['response']
        self.assertEqual(payload['sim-job-bar']['value'], 100.0)
        self.assertEqual(payload['sim-job-done']['data']['status'], 'completed')
        done = payload['sim-job-done']['data']
        interval = post([('sim-job-tick', 'disabled')], [('sim-job-start', 'data', None), ('sim-job-done', 'data', done)],
                        [], ['sim-job-done.data'])
        self.assertEqual(interval.status_code, 200, interval.get_data(as_text=True)[:300])
        self.assertTrue(interval.get_json()['response']['sim-job-tick']['disabled'])
        listing = post([('sim-run-choice', 'options'), ('sim-run-choice', 'value'), ('sim-run-match', 'children')],
                       [('sim-job-done', 'data', done), ('sim-run-pick', 'data', None),
                        ('sim-run-refresh', 'n_clicks', None), ('cockpit-source', 'value', 'sim'),
                        ('sim-session', 'value', name), ('sim-processor', 'value', 'copilot'),
                        ('sim-params', 'value', 'recorded'), ('sim-from', 'value', None), ('sim-to', 'value', None),
                        ('sim-warmup', 'value', 5)], [], ['sim-job-done.data'])
        self.assertEqual(listing.status_code, 200, listing.get_data(as_text=True)[:400])
        run_id = listing.get_json()['response']['sim-run-choice']['value']
        self.assertEqual(run_id, done['run_id'])
        self.assertIn('Passender Run vorhanden', listing.get_data(as_text=True))
        load = post([('sim-status', 'children'), ('sim-stats', 'children'), ('sim-index', 'max'), ('sim-chart', 'figure'),
                     ('sim-result-token', 'data'), ('sim-run-info', 'children')],
                    [('sim-run-choice', 'value', run_id)], [], ['sim-run-choice.value'])
        self.assertEqual(load.status_code, 200, load.get_data(as_text=True)[:400])
        loaded = load.get_json()['response']
        self.assertEqual(loaded['sim-index']['max'], FRAMES - 1)
        self.assertTrue(loaded['sim-result-token']['data'])
        self.assertEqual(len(loaded['sim-chart']['figure']['data'][0]['x']), FRAMES)
        self.assertIn('keine Neuberechnung', json.dumps(loaded['sim-status'], ensure_ascii=False))
        render = post([('sim-img-then', 'src'), ('sim-img-now', 'src'), ('sim-compare', 'children'),
                       ('sim-frame-label', 'children'), ('sim-display-rate', 'children')],
                      [('sim-index', 'value', 7), ('sim-result-token', 'data', loaded['sim-result-token']['data'])],
                      [('sim-play-tick', 'disabled', True)], ['sim-index.value'])
        self.assertEqual(render.status_code, 200, render.get_data(as_text=True)[:400])
        self.assertTrue(render.get_json()['response']['sim-img-now']['src'] == 'data:x')


if __name__ == '__main__':
    unittest.main()
