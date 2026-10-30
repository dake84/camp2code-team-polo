"""Paket I2: schnellere Wiedergabe der Simulation (Anzeigebilder: ein Dekodieren, HUD auf Anzeigegroesse, Cache,
Vorausberechnung).

Hardwarefrei und kurz (wenige Sekunden). Gemessen wird in ``scripts/sim_render_bench.py`` auf dem Pi, nicht hier.
"""
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from test_paket_f import TempConfig, write_session  # noqa: E402

import hud_style  # noqa: E402
from replay_frames import FrameRenderCache  # noqa: E402

FRAMES = 30


def wait_for(condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


# ------------------------------------------------------------------ Cache
class CacheTests(unittest.TestCase):
    def tearDown(self):
        for cache in getattr(self, 'caches', ()):
            cache.close()

    def cache(self, capacity=4, workers=1):
        self.caches = getattr(self, 'caches', [])
        cache = FrameRenderCache(capacity, workers)
        self.caches.append(cache)
        return cache

    def test_treffer_und_lru_begrenzung(self):
        cache, calls = self.cache(capacity=3), []

        def factory(key):
            return lambda: calls.append(key) or f'wert{key}'
        for key in (1, 2, 3):
            self.assertEqual(cache.render(key, factory(key)), f'wert{key}')
        self.assertEqual(cache.render(1, factory(1)), 'wert1')            # Treffer
        self.assertEqual(calls, [1, 2, 3])
        cache.render(4, factory(4))                                       # 2 ist am laengsten ungenutzt
        self.assertIsNone(cache.get(2))
        self.assertEqual(len(cache), 3)
        self.assertEqual(cache.stats()['hits'], 1)

    def test_gleichzeitige_anfragen_rechnen_einmal(self):
        cache, calls, gate = self.cache(), [], threading.Event()

        def factory():
            calls.append(1)
            gate.wait(10)
            return 'x'
        results = []
        threads = [threading.Thread(target=lambda: results.append(cache.render('k', factory))) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.assertTrue(wait_for(lambda: calls))
        gate.set()
        for thread in threads:
            thread.join(10)
        self.assertEqual((len(calls), results), (1, ['x'] * 4))

    def test_vorausberechnung_im_hintergrund(self):
        cache, calls = self.cache(capacity=10), []
        cache.prefetch([(k, lambda k=k: calls.append(k) or k * 10) for k in (1, 2, 3)])
        self.assertTrue(wait_for(lambda: len(cache) == 3))
        self.assertEqual(sorted(calls), [1, 2, 3])
        self.assertEqual(cache.render(2, lambda: self.fail('sollte aus dem Cache kommen')), 20)
        self.assertEqual(cache.stats()['prefetched'], 3)

    def test_mehrere_threads_rechnen_gleichzeitig_ohne_doppelte_arbeit(self):
        cache, calls = self.cache(capacity=10, workers=2), []
        barrier = threading.Barrier(2, timeout=10)        # beide Auftraege muessen GLEICHZEITIG laufen

        def factory(key):
            def run():
                barrier.wait()
                calls.append(key)
                return key
            return run
        cache.prefetch([(1, factory(1)), (2, factory(2))])
        self.assertTrue(wait_for(lambda: len(cache) == 2))
        self.assertEqual(sorted(calls), [1, 2])
        self.assertEqual(len([t for t in cache._threads if t.is_alive()]), 2)

    def test_neue_vorausberechnung_ersetzt_die_alte(self):
        cache, calls, gate, started = self.cache(capacity=10), [], threading.Event(), threading.Event()

        def first():
            started.set()
            gate.wait(10)
            calls.append('a')
            return 'a'
        cache.prefetch([('a', first), ('b', lambda: calls.append('b') or 'b'), ('c', lambda: calls.append('c') or 'c')])
        self.assertTrue(started.wait(10))                                  # 'a' laeuft, 'b' und 'c' warten
        cache.prefetch([('d', lambda: calls.append('d') or 'd')])         # Wiedergabe sprang: alte Auftraege entfallen
        gate.set()
        self.assertTrue(wait_for(lambda: cache.get('d') == 'd'))
        self.assertEqual(sorted(calls), ['a', 'd'])

    def test_fehler_im_vordergrund_und_im_hintergrund(self):
        cache = self.cache()
        with self.assertRaises(ValueError):
            cache.render('k', lambda: (_ for _ in ()).throw(ValueError('kaputt')))
        self.assertIsNone(cache.get('k'))                                  # Fehler werden nicht gespeichert
        self.assertEqual(cache.render('k', lambda: 'ok'), 'ok')            # erneuter Versuch moeglich
        cache.prefetch([('x', lambda: (_ for _ in ()).throw(OSError('weg'))), ('y', lambda: 'y')])
        self.assertTrue(wait_for(lambda: cache.get('y') == 'y'))           # Hintergrund-Thread lebt weiter
        self.assertIsNone(cache.get('x'))
        with self.assertRaises(OSError):
            cache.render('x', lambda: (_ for _ in ()).throw(OSError('weg')))

    def test_clear_verwirft_laufende_berechnungen(self):
        cache, gate, started = self.cache(), threading.Event(), threading.Event()

        def slow():
            started.set()
            gate.wait(10)
            return 'alt'
        cache.prefetch([('k', slow)])
        self.assertTrue(started.wait(10))
        cache.clear()                                                      # anderer Run: das Ergebnis ist wertlos
        gate.set()
        self.assertTrue(wait_for(lambda: cache.stats()['queued'] == 0))
        time.sleep(0.1)
        self.assertEqual(len(cache), 0)


# ------------------------------------------------------------------ SimLab
def hd_session(root, name='session_hd', count=FRAMES, size=(1280, 720)):
    """Wie ``write_session``, aber mit grossen Bildern (HD-Aufnahme)."""
    write_session(root, name, count)
    session = os.path.join(root, name)
    for frame_id in range(1, count + 1):
        path = os.path.join(session, f'frames/frame_{frame_id:010d}.jpg')
        small = cv2.imread(path)
        big = cv2.resize(small, size, interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(path, big, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return name


class LabCase(TempConfig):
    def setUp(self):
        super().setUp()
        self.addCleanup(hud_style.set_current, hud_style.current())
        hud_style.set_current('classic')

    def lab(self, name, uri=lambda image: image):
        import dash_stub
        modules = dash_stub.modules()
        ui = SimpleNamespace(dbc=modules['dash_bootstrap_components'], dcc=modules['dash.dcc'],
                             html=modules['dash.html'], Input=dash_stub.Input, Output=dash_stub.Output,
                             State=dash_stub.State, ctx=dash_stub.ctx, no_update='NO_UPDATE')
        from frame_source import SessionSource
        from sim_lab import SimLab
        lab = SimLab(SimpleNamespace(running=lambda: False), ui, uri, self.root)
        source = SessionSource(name, self.root)
        result = self.engine().run(source, 'copilot', None, None, None, 0)
        self.addCleanup(lab._frames.close)
        return lab, source, result


class RenderTests(LabCase):
    def test_ohne_verkleinerung_identisch_zum_bisherigen_weg(self):
        """Bild kleiner als die Anzeigebreite: Ergebnis pixelgleich zu ``render_frame`` (alle HUD-Stile)."""
        name = write_session(self.root, count=FRAMES)
        lab, source, result = self.lab(name)
        stops = [i for i, f in enumerate(result.frames) if f.stop_reason]
        positions = [0, 10, 25] + stops[:1]
        for style in ('classic', 'jetpilot', 'arcade'):
            hud_style.set_current(style)
            for position in positions:
                old_then, old_now = lab.render_frame(result, source, position)
                new_then, new_now = lab.render_uris('t', result, source, position, 640)
                self.assertTrue(np.array_equal(old_then, new_then), (style, position, 'damals'))
                self.assertTrue(np.array_equal(old_now, new_now), (style, position, 'jetzt'))

    def test_verkleinert_auf_die_gewaehlte_breite_und_aehnlich_zum_vollbild(self):
        name = hd_session(self.root)
        lab, source, result = self.lab(name)
        for width in (640, 960, 1280):
            then, now = lab.render_uris('t', result, source, 12, width)
            self.assertEqual((then.shape[1], now.shape[1]), (min(width, 1280),) * 2)
            self.assertEqual(then.shape, now.shape)
        then, now = lab.render_uris('t', result, source, 12, 640)
        self.assertEqual(now.shape[:2], (360, 640))
        old_then, old_now = lab.render_frame(result, source, 12)
        original = cv2.resize(cv2.imread(os.path.join(self.root, name, 'frames/frame_0000000013.jpg')), (640, 360),
                              interpolation=cv2.INTER_AREA).astype(np.int16)
        for new, old in ((then, old_then), (now, old_now)):
            reference = cv2.resize(old, (640, 360), interpolation=cv2.INTER_AREA).astype(np.int16)
            difference = np.abs(new.astype(np.int16) - reference).mean(axis=2)
            # Geometrie (ROI, Linien, Pfeil) stimmt ueberein; nur die Schrift der Kopfzeile ist auf dem kleinen Bild
            # (ganzzahlig gerundete Strichstaerke) etwas kraeftiger.
            self.assertLess(difference[60:].mean(), 4.5)
            self.assertLess(difference.mean(), 7.0)
            self.assertGreater(np.abs(new.astype(np.int16) - original).mean(), 0.5)   # HUD ist wirklich gezeichnet

    def test_ein_dekodieren_statt_zwei_und_cache_treffer(self):
        name = hd_session(self.root)
        lab, source, result = self.lab(name)
        calls = []
        real = source.image
        source.image = lambda index: calls.append(index) or real(index)
        lab.render_frame(result, source, 5)
        self.assertEqual(len(calls), 2)                                    # bisher: Rohbild + then_image
        calls.clear()
        lab.render_uris('t', result, source, 5, 960)
        self.assertEqual(len(calls), 1)                                    # jetzt: einmal
        lab.render_uris('t', result, source, 5, 960)                       # Cache-Treffer
        self.assertEqual(len(calls), 1)
        lab.render_uris('t', result, source, 5, 640)                       # andere Groesse = anderer Eintrag
        self.assertEqual(len(calls), 2)
        hud_style.set_current('jetpilot')                                  # anderer HUD-Stil = anderer Eintrag
        lab.render_uris('t', result, source, 5, 640)
        self.assertEqual(len(calls), 3)
        lab.discard()                                                      # anderer Run / Einstellungen: Cache leer
        self.assertEqual(len(lab._frames), 0)

    def test_eingabebild_wird_nicht_veraendert(self):
        """Beide HUDs zeichnen auf Kopien desselben verkleinerten Bildes; es darf nicht beschriftet werden."""
        name = hd_session(self.root)
        lab, source, result = self.lab(name)
        seen = []
        import replay_hud
        real = replay_hud.draw_hud

        def spy(image, *args, **kwargs):
            seen.append((image, image.copy()))
            return real(image, *args, **kwargs)
        for style in ('classic', 'jetpilot', 'arcade'):
            with mock.patch.object(replay_hud, 'draw_hud', spy):
                lab._render_pair(result, source, 7, 640, style)
        self.assertEqual(len(seen), 6)
        for image, before in seen:
            self.assertTrue(np.array_equal(image, before))
        for first, second in zip(seen[0::2], seen[1::2]):
            self.assertIs(first[0], second[0])                             # beide HUDs erhalten dasselbe Bild

    def test_hud_zeilen_wie_render_frame(self):
        """Die duplizierte Zeilenbildung (``_hud_row``) darf nicht von ``render_frame`` abweichen."""
        name = write_session(self.root, count=FRAMES)
        lab, source, result = self.lab(name)
        import replay_hud
        rows = []
        real = replay_hud.draw_hud

        def spy(image, row, **kwargs):
            rows.append((dict(row), kwargs.get('footer')))
            return real(image, row, **kwargs)
        stops = [i for i, f in enumerate(result.frames) if f.stop_reason]
        warm = [i for i, f in enumerate(result.frames) if f.warmup]
        for position in [0, 8, 20] + stops[:1] + warm[:1]:
            rows.clear()
            with mock.patch.object(replay_hud, 'draw_hud', spy):
                lab.render_frame(result, source, position)
                old = rows[-1]                                             # die Jetzt-Zeile ist die zweite
                rows.clear()
                lab._render_pair(result, source, position, 640, 'classic')
                new = rows[-1]
            self.assertEqual(old, new, position)

    def test_damals_hud_skaliert_die_roi_zeilen_mit(self):
        name = hd_session(self.root)
        lab, source, result = self.lab(name)
        source._rows[4]['roi_top_px'], source._rows[4]['roi_bottom_px'] = '324', '612'
        import replay_hud
        rows = []
        with mock.patch.object(replay_hud, 'draw_hud', lambda image, row, **kw: rows.append(dict(row)) or image):
            source.then_hud(np.zeros((360, 640, 3), np.uint8), 4, 0.5)
            source.then_hud(np.zeros((720, 1280, 3), np.uint8), 4, 1.0)
        self.assertEqual((rows[0]['roi_top_px'], rows[0]['roi_bottom_px']), (162, 306))
        self.assertEqual((rows[1]['roi_top_px'], rows[1]['roi_bottom_px']), ('324', '612'))   # unveraendert
        self.assertEqual(source._rows[4]['roi_top_px'], '324')             # die Session-Zeile bleibt unberuehrt

    def test_ungueltige_groesse_und_quelle_geaendert(self):
        name = write_session(self.root, count=FRAMES)
        lab, source, result = self.lab(name)
        for bad in (None, 'x', 0, 777, -1, 1.5e9):
            self.assertEqual(lab.display_width(bad), 960)
        self.assertEqual([lab.display_width(w) for w in (640, '960', 1280.0)], [640, 960, 1280])
        result.frames[3].frame_id += 1000                                  # Run passt nicht zur Session
        with self.assertRaises(ValueError):
            lab.render_uris('t', result, source, 3, 640)
        os.remove(os.path.join(self.root, name, 'frames/frame_0000000005.jpg'))
        position = next(i for i, f in enumerate(result.frames) if f.frame_id == 5)
        with self.assertRaises(ValueError):
            lab.render_uris('t', result, source, position, 640)
        self.assertEqual(len(lab._frames), 0)                              # Fehler werden nicht gecacht


class PrefetchTests(LabCase):
    def test_vorausberechnung_nur_waehrend_der_wiedergabe(self):
        name = write_session(self.root, count=FRAMES)
        lab, source, result = self.lab(name)
        lab.render_uris('t', result, source, 3, 960, playing=False)
        time.sleep(0.3)
        self.assertEqual(len(lab._frames), 1)                              # Pause/Scrubben: keine Hintergrundarbeit
        lab.render_uris('t', result, source, 3, 960, playing=True)
        from sim_lab import PREFETCH_AHEAD
        self.assertTrue(wait_for(lambda: len(lab._frames) == 1 + PREFETCH_AHEAD))
        for position in range(4, 4 + PREFETCH_AHEAD):
            self.assertIsNotNone(lab._frames.get(('t', position, 960, 'classic')), position)
        calls = []
        real = source.image
        source.image = lambda index: calls.append(index) or real(index)
        lab.render_uris('t', result, source, 4, 960, playing=True)       # naechster Takt: Treffer, nichts dekodiert
        self.assertNotIn(result.frames[4].index, calls)

    def test_pause_verwirft_offene_vorausberechnungen(self):
        name = write_session(self.root, count=FRAMES)
        lab, source, result = self.lab(name)
        gate, started = threading.Event(), threading.Event()
        real = source.image

        def slow(index):
            started.set()
            gate.wait(10)
            return real(index)
        lab.render_uris('t', result, source, 0, 960, playing=False)       # Vordergrund: schnell
        source.image = slow
        lab._frames.prefetch([(('t', p, 960, 'classic'), lambda p=p: lab._render_pair(result, source, p, 960, 'classic'))
                              for p in range(10, 20)])
        self.assertTrue(started.wait(10))
        source.image = real
        lab.render_uris('t', result, source, 2, 960, playing=False)       # Pause
        self.assertEqual(lab._frames.stats()['queued'], 0)
        gate.set()
        self.assertTrue(wait_for(lambda: lab._frames.stats()['queued'] == 0))
        time.sleep(0.2)
        self.assertLessEqual(len(lab._frames), 1 + 1 + 2)                 # hoechstens die beiden laufenden Frames fertig

    def test_schrittweite_folgt_dem_letzten_sprung_und_endet_am_letzten_frame(self):
        name = write_session(self.root, count=FRAMES)
        lab, source, result = self.lab(name)
        lab.render_uris('t', result, source, 2, 960, playing=True)
        lab.render_uris('t', result, source, 4, 960, playing=True)        # Sprung 2 (z. B. Echtzeit bei 2x)
        self.assertTrue(wait_for(lambda: lab._frames.get(('t', 6 + 2 * 5, 960, 'classic')) is not None))
        self.assertIsNotNone(lab._frames.get(('t', 8, 960, 'classic')))
        last = len(result.frames) - 1
        lab.render_uris('t', result, source, last - 1, 960, playing=True)  # am Ende nicht ueber den letzten Frame hinaus
        self.assertTrue(wait_for(lambda: lab._frames.get(('t', last, 960, 'classic')) is not None))
        self.assertIsNone(lab._frames.get(('t', last + 1, 960, 'classic')))

    def test_speicher_bleibt_begrenzt(self):
        name = write_session(self.root, count=FRAMES)
        lab, source, result = self.lab(name)
        lab._frames.close()
        lab._frames = FrameRenderCache(capacity=8)
        self.addCleanup(lab._frames.close)
        for position in range(len(result.frames)):
            lab.render_uris('t', result, source, position, 640)
        self.assertLessEqual(len(lab._frames), 8)


# ------------------------------------------------------------------ Dash-Callback (Attrappe)
class CallbackTests(TempConfig):
    def setUp(self):
        super().setUp()
        self.name = write_session(self.root, count=FRAMES)
        from test_dashboard_callbacks import FakeApplication, load_dashboard
        self.module, self.context, self.ctx = load_dashboard()
        self.app = self.module.create_app(FakeApplication())
        self.cb = self.app.callbacks
        self.lab = self.app.c2c_tabs[0].ctx.sim
        self.addCleanup(self.lab._frames.close)

    def tearDown(self):
        self.context.__exit__(None, None, None)
        super().tearDown()

    def result(self):
        from frame_source import SessionSource
        source = SessionSource(self.name, self.root)
        result = self.engine().run(source, 'copilot', None, None, None, 0)
        self.lab._results = {7: (result, source)}
        return result

    def test_callback_signatur_und_layout(self):
        from test_dashboard_callbacks import layout_of
        ids = {c.id for c in layout_of(self.app).walk() if isinstance(c.id, str)}
        self.assertIn('sim-size', ids)
        callback = self.cb['sim_render']
        self.assertIn(('sim-size', 'value'), [(i.component_id, i.component_property) for i in callback.inputs])
        self.assertEqual([(i.component_id, i.component_property) for i in callback.states],
                         [('sim-play-tick', 'disabled')])

    def test_sim_render_liefert_data_uris_in_der_gewaehlten_groesse(self):
        result = self.result()
        self.ctx.set('sim-index', 'value')
        for size in (None, 640, 1280):                       # None = bisheriger Aufruf mit drei Argumenten
            args = (5, 7, True) if size is None else (5, 7, True, size)
            then, now, table, label, rate = self.cb['sim_render'](*args)
            self.assertTrue(then.startswith('data:image/jpeg;base64,'))
            self.assertTrue(now.startswith('data:image/jpeg;base64,'))
        # Groessenwechsel bei Pause rendert neu (Eingang sim-size); ohne Ergebnis: leere Anzeige
        self.ctx.set('sim-size', 'value')
        self.assertEqual(self.cb['sim_render'](5, 7, True, 640)[3].children, f'Frame #{result.frames[5].frame_id}')
        self.assertEqual(self.cb['sim_render'](5, None, True, 640)[:3], (None, None, 'Noch keine Simulation berechnet'))

    def test_fehlendes_bild_zeigt_meldung_statt_abbruch(self):
        result = self.result()
        os.remove(os.path.join(self.root, self.name, f'frames/frame_{result.frames[5].frame_id:010d}.jpg'))
        out = self.cb['sim_render'](5, 7, True, 960)
        self.assertEqual(out[:2], (None, None))
        self.assertIn('Bild nicht verfügbar', out[2])

    def test_wiedergabe_mit_vorausberechnung_ueber_viele_takte(self):
        result = self.result()
        self.ctx.set('sim-index', 'value')
        for index in range(0, 12):
            out = self.cb['sim_render'](index, 7, False, 960)      # paused=False: Wiedergabe laeuft
            self.assertTrue(out[1].startswith('data:image/jpeg'))
        self.assertTrue(wait_for(lambda: self.lab._frames.stats()['hits'] > 0))
        self.assertLessEqual(len(self.lab._frames), 48)
        self.assertEqual(len(result.frames), FRAMES)


if __name__ == '__main__':
    unittest.main()
