"""Paket H3b: HUD-Stile Jetpilot und Arcade (hardwarefrei).

Geprueft werden die Zeichenfunktionen (``hud_render``), die Auswahl (``hud_style``), die Weiche ``replay_hud.draw_hud``
und - wenn Dash installiert ist - dass ``dbc.Select`` im echten Dash den Wert als Zeichenkette an den Callback gibt.
Nicht geprueft sind das Aussehen im Browser und die Rechenzeit auf dem Pi.
"""
import importlib.util
import json
import logging
import os
import re
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
ROOT = Path(__file__).resolve().parents[1]
HAS_DASH = all(importlib.util.find_spec(m) for m in ('dash', 'dash_bootstrap_components'))

import hud_render  # noqa: E402
import hud_style  # noqa: E402
import replay_hud  # noqa: E402

GREEN = hud_render.GREEN
SIZES = ((1296, 972), (1920, 1080), (640, 480), (320, 240), (200, 100))
CROP = (0.15, 0.55)


def lane_image(width, height):
    image = np.full((height, width, 3), (150, 140, 135), dtype=np.uint8)
    thick = max(8, width // 40)
    cv2.line(image, (int(width * .2), height - 1), (int(width * .42), 0), (200, 40, 20), thick)
    cv2.line(image, (int(width * .8), height - 1), (int(width * .58), 0), (200, 40, 20), thick)
    return image


def lane_geometry(width, height, count=2):
    """(top, bottom, Linien (m, b) im ROI) fuer die zwei Linien aus ``lane_image``."""
    top, bottom = int(height * (1 - CROP[1])), int(height * (1 - CROP[0]))

    def line(p0, p1):
        m = (p1[1] - p0[1]) / (p1[0] - p0[0])
        return [m, p0[1] - m * p0[0] - top]
    lanes = [line((width * .2, height - 1), (width * .42, 0)), line((width * .8, height - 1), (width * .58, 0))]
    return top, bottom, lanes[:count]


def make_row(width, height, count=2, **extra):
    top, bottom, lanes = lane_geometry(width, height, count)
    row = {'frame_id': '3545', 'roi_top_px': top, 'roi_bottom_px': bottom, 'lanes_json': json.dumps(lanes),
           'steering_angle_car': 97, 'requested_angle': 99, 'steering_angle': 104, 'speed': 30,
           'data_status': 'complete', 'vehicle_mode': 'auto', 'vehicle_mode_key': 'camera',
           'exposure_us': 8000, 'gain': 2.0, 'lux': 120.5, 'frame_duration_us': 40000}
    row.update(extra)
    return row


def make_info(width, height, count=2, **extra):
    top, bottom, lanes = lane_geometry(width, height, count)
    info = {'frame_id': '7', 'lanes': [tuple(lane) for lane in lanes], 'geometry': True, 'top': top, 'bottom': bottom,
            'stell': 97.0, 'soll': 99.0, 'roh': 104.0, 'speed': 30.0, 'label': 'AUTONOM', 'kind': 'auto',
            'color': (120, 230, 120), 'mode_name': 'CAMERAMODE', 'events': '', 'footer': '',
            'exposure_us': 8000.0, 'gain': 2.0, 'lux': 120.0, 'frame_duration_us': 40000.0}
    info.update(extra)
    return info


def make_trail(lane_count_now=2):
    trail = hud_render.HudTrail()
    now = time.monotonic()
    for i in range(40):
        trail.add(100 + i, 90 + 10 * np.sin(i / 4), 92 + 10 * np.sin(i / 4 + .4), 2 if i > 8 else 1, now=now - 6 + i * .15)
    return trail


def color_near(image, point, color, radius=8, tolerance=60):
    x, y = point
    window = image[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1].reshape(-1, 3).astype(int)
    return bool((np.abs(window - np.array(color)).sum(axis=1) < tolerance).any())


class StyleTests(unittest.TestCase):
    def tearDown(self):
        hud_style.set_current('off')

    def test_normalisieren_auch_der_alte_schalter(self):
        n = hud_style.normalize
        self.assertEqual([n(v) for v in ([], ['on'], None, False, True, 'off', 'classic', 'JetPilot', ' arcade ', 'on')],
                         ['off', 'classic', 'off', 'off', 'classic', 'off', 'classic', 'jetpilot', 'arcade', 'classic'])
        self.assertEqual([n(v) for v in ('x', 5, {}, 'hud')], ['off'] * 4)
        self.assertEqual(n('x', 'classic'), 'classic')
        self.assertEqual([o['value'] for o in hud_style.OPTIONS], list(hud_style.STYLES))
        self.assertTrue(all(o['label'] for o in hud_style.OPTIONS))

    def test_gemeinsamer_stil_und_weiche_off_wird_classic(self):
        self.assertEqual(hud_style.current(), 'off')
        self.assertEqual(hud_style.resolve(None), 'classic')            # HUD-Ansichten zeigen immer ein HUD
        self.assertEqual(hud_style.set_current(['on']), 'classic')
        self.assertEqual(hud_style.set_current('jetpilot'), 'jetpilot')
        self.assertEqual((hud_style.current(), hud_style.resolve(None)), ('jetpilot', 'jetpilot'))
        self.assertEqual(hud_style.resolve('arcade'), 'arcade')         # ausdrueckliche Angabe gewinnt
        self.assertEqual((hud_style.resolve('off'), hud_style.resolve('kaputt')), ('classic', 'classic'))
        self.assertEqual(hud_style.set_current(None), 'off')


class TrailTests(unittest.TestCase):
    def test_verlauf_dedupliziert_und_begrenzt(self):
        trail = hud_render.HudTrail(window_s=10)
        self.assertTrue(trail.add(1, 90, 90, 2, now=100.0))
        self.assertFalse(trail.add(1, 91, 91, 2, now=100.1))               # derselbe Frame nur einmal
        self.assertFalse(trail.add('x', 90, 90, 2, now=100.2))             # ohne Frame-ID nichts
        self.assertTrue(trail.add('2', 90, 90, 2, now=105.0))              # Zeichenketten sind erlaubt
        self.assertTrue(trail.add(3, 90, 90, 2, now=112.0))                # 100.0 ist nun aelter als 10 s
        self.assertEqual([i[0] for i in trail.snapshot()], [2, 3])
        trail.add(1, 90, 90, 2, now=113.0)                                 # sinkende Frame-ID = Neustart: leeren
        self.assertEqual([i[0] for i in trail.snapshot()], [1])
        trail.clear()
        self.assertEqual(trail.snapshot(), [])

    def test_streak_sterne_und_lenkspur_fenster(self):
        s = hud_render.streak_seconds
        self.assertIsNone(s([]))
        self.assertIsNone(s([(1, 0.0, 90, 90, 2)]))
        items = [(1, 0.0, 90, 90, 2), (2, 4.0, 90, 90, 1), (3, 6.0, 90, 90, 2), (4, 9.5, 90, 90, 2)]
        self.assertAlmostEqual(s(items), 5.5)                              # seit dem Verlust bei t=4
        self.assertAlmostEqual(s(items[:1] + [(2, 3.0, 90, 90, 2)]), 3.0)  # nie verloren: seit dem ersten Eintrag
        self.assertAlmostEqual(s([(1, 0.0, 90, 90, 2), (2, 2.0, 90, 90, 0)]), 0.0)
        self.assertEqual([hud_render.stars_for(v) for v in (None, 0, 4.9, 5, 14.9, 15, 30, 99)], [0, 0, 0, 1, 1, 2, 3, 3])
        self.assertEqual(len(hud_render.recent(items, 5.0)), 2)            # relativ zum neuesten Eintrag
        self.assertEqual(hud_render.recent([], 5.0), [])


class RenderTests(unittest.TestCase):
    def test_alle_stile_groessen_und_varianten_ohne_fehler(self):
        variants = {
            'voll': {},
            'leer': dict(stell=None, soll=None, roh=None, speed=None, lanes=[], exposure_us=None, gain=None, lux=None,
                         frame_duration_us=None, mode_name='', label=''),
            'ohne ROI': dict(geometry=False, top=None, bottom=None),
            'eine Linie': dict(lanes=[(-1.2, 90.0), None]),
            'schlechte Linien': dict(lanes=[(float('nan'), 1.0), ('x', 2), (0.0, 5.0), (1e-12, 3.0), (1e9, 1e9)]),
            'rueckwaerts': dict(speed=-20.0),
            'gestoppt': dict(kind='stopped', label='GESTOPPT: LANE_TIMEOUT_MIT_SEHR_LANGER_URSACHE',
                             color=(80, 80, 255), events='STOP_REASON|LANE_LOST', footer='JETZT (Simulation) - COPILOT'),
            'Extremwerte': dict(stell=-500.0, soll=900.0, roh=1e6, speed=1e5),
        }
        for style in hud_render.STYLES:
            for width, height in SIZES:
                image = lane_image(width, height)
                for name, extra in (variants.items() if width <= 1296 else [('voll', {})]):    # Full-HD nur einmal (Laufzeit)
                    for trail in (None, make_trail()):
                        original = image.copy()
                        result = hud_render.draw(style, image, make_info(width, height, **extra), trail)
                        label = (style, width, height, name, trail is not None)
                        self.assertEqual(result.shape, image.shape, label)
                        self.assertEqual(result.dtype, np.uint8, label)
                        np.testing.assert_array_equal(image, original)          # Eingabe bleibt unveraendert
                        self.assertFalse(np.array_equal(result, image), label)  # es wurde etwas gezeichnet

    def test_graubild_und_bgra_und_leere_bilder(self):
        for style in hud_render.STYLES:
            gray = cv2.cvtColor(lane_image(640, 480), cv2.COLOR_BGR2GRAY)
            self.assertEqual(hud_render.draw(style, gray, make_info(640, 480)).shape, (480, 640, 3))
            bgra = cv2.cvtColor(lane_image(640, 480), cv2.COLOR_BGR2BGRA)
            self.assertEqual(hud_render.draw(style, bgra, make_info(640, 480)).shape, (480, 640, 3))
            self.assertIsNone(hud_render.draw(style, None, {}))
            empty = np.zeros((0, 0, 3), np.uint8)
            self.assertIs(hud_render.draw(style, empty, {}), empty)
        with self.assertRaises(ValueError):
            hud_render.draw('classic', lane_image(64, 48), make_info(64, 48))

    def test_jetpilot_linien_an_der_richtigen_stelle(self):
        for width, height in ((1296, 972), (640, 480)):
            image = np.full((height, width, 3), 60, np.uint8)                   # neutral: keine Fremdfarben
            result = hud_render.draw('jetpilot', image, make_info(width, height))
            top, bottom, _ = lane_geometry(width, height)
            y = (top + bottom) // 2
            for side_x in (width * .2 + (width * .42 - width * .2) * (height - 1 - y) / (height - 1),
                           width * .8 + (width * .58 - width * .8) * (height - 1 - y) / (height - 1)):
                found = any(color_near(result, (int(side_x), y + dy), GREEN, 5) for dy in range(-12, 13, 3))
                self.assertTrue(found, (width, height, side_x))              # gestrichelt: dicht daneben suchen

    def test_arcade_teppich_und_linien(self):
        width, height = 1296, 972
        image = np.full((height, width, 3), 60, np.uint8)
        result = hud_render.draw('arcade', image, make_info(width, height), make_trail())
        top, bottom, _ = lane_geometry(width, height)
        y = (top + bottom) // 2 - 40
        x_left = width * .2 + (width * .42 - width * .2) * (height - 1 - y) / (height - 1)
        x_right = width * .8 + (width * .58 - width * .8) * (height - 1 - y) / (height - 1)
        middle = result[y, int((x_left + x_right) / 2) - 120]                    # im Teppich, neben den Chevrons
        self.assertGreater(int(middle[1]), int(middle[0]) + 20)                  # gruen getoent
        self.assertGreater(int(middle[1]), int(middle[2]) + 20)
        outside = result[y, int(x_left) - 120]
        self.assertTrue(np.array_equal(outside, [60, 60, 60]))                    # ausserhalb der Linien unveraendert
        self.assertTrue(color_near(result, (int(x_left), y), (80, 255, 120), 6, 80))
        # keine Linie bei Fahrt: roter Warnton im ROI, ohne Fahrt keiner
        red = hud_render.draw('arcade', image, make_info(width, height, count=0, lanes=[]), make_trail())
        self.assertGreater(int(red[y, 400][2]), int(red[y, 400][1]) + 20)
        idle = hud_render.draw('arcade', image, make_info(width, height, lanes=[], kind='idle', label='STEHT'),
                               make_trail())
        self.assertTrue(np.array_equal(idle[y, 400], [60, 60, 60]))

    def test_jetpilot_spur_lock_je_linienzahl(self):
        # Text nicht lesbar pruefbar: die Farbe des Lock-Felds (gruen / gelb / rot) unterscheidet sich je Zustand
        width, height = 1296, 972
        base = np.full((height, width, 3), 60, np.uint8)
        region = (slice(302, 345), slice(width - 250, width - 40))      # Zeile 'LOCK 2/2' (ohne die Beschriftung 'SPUR')

        def lock_pixels(**extra):
            out = hud_render.draw('jetpilot', base, make_info(width, height, **extra))[region].reshape(-1, 3)
            return out[(out.astype(int).sum(axis=1) > 400)]
        two, one = lock_pixels(), lock_pixels(lanes=[(-1.0, 10.0)])
        none_driving = lock_pixels(lanes=[], kind='auto')
        none_idle = lock_pixels(lanes=[], kind='idle')
        self.assertTrue(len(two) and len(one) and len(none_driving) and len(none_idle))
        self.assertGreater(two[:, 1].mean(), two[:, 2].mean())                   # gruen: G > R
        self.assertTrue(abs(one[:, 2].mean() - 255) < 40 or one[:, 2].mean() > two[:, 2].mean())   # gelb/amber: R hoch
        self.assertGreater(none_driving[:, 2].mean(), none_driving[:, 1].mean())  # rot: R > G
        self.assertGreater(none_idle[:, 1].mean(), none_driving[:, 1].mean())     # ohne Fahrt nur amber, nicht rot

    def test_kompakt_zeigt_weniger(self):
        image = lane_image(320, 240)
        small = hud_render.draw('arcade', image, make_info(320, 240), make_trail())
        self.assertEqual(small.shape, image.shape)
        # kompakt: keine Tafeln links/rechts oben (Streak, Lenkspur) und kein Tacho
        self.assertTrue(np.array_equal(small[:12, :12], image[:12, :12]))
        self.assertTrue(np.array_equal(small[:12, -12:], image[:12, -12:]))

    def test_footer_und_ereignisleiste(self):
        for style in hud_render.STYLES:
            image = np.full((720, 960, 3), 60, np.uint8)
            result = hud_render.draw(style, image, make_info(960, 720, events='STOP_REASON', footer='DAMALS (Ringbuffer)'))
            bottom = result[-24:].reshape(-1, 3)
            self.assertTrue((np.abs(bottom.astype(int) - [40, 28, 12]).sum(axis=1) < 6).any(), style)   # Kennzeichnungsleiste
            event_band = result[-70:-40].reshape(-1, 3)
            self.assertTrue((np.abs(event_band.astype(int) - [15, 30, 100]).sum(axis=1) < 6).any(), style)

    def test_ascii_absicherung(self):
        image = lane_image(640, 480)
        result = hud_render.draw('jetpilot', image, make_info(640, 480, label='GESTOPPT: Ähre ñ', mode_name='Gänge'))
        self.assertEqual(result.shape, image.shape)


class SwitchTests(unittest.TestCase):
    def tearDown(self):
        hud_style.set_current('off')

    def test_klassisch_unveraendert_und_weiche(self):
        image = lane_image(640, 480)
        row = make_row(640, 480)
        classic = replay_hud.draw_hud_classic(image, row)
        np.testing.assert_array_equal(replay_hud.draw_hud(image, row, style='classic'), classic)
        np.testing.assert_array_equal(replay_hud.draw_hud(image, row), classic)          # Standard (off) = classic
        np.testing.assert_array_equal(replay_hud.draw_hud(image, row, style='off'), classic)
        np.testing.assert_array_equal(replay_hud.draw_hud(image, row, style='kaputt'), classic)
        jet = replay_hud.draw_hud(image, row, style='jetpilot')
        arc = replay_hud.draw_hud(image, row, style='arcade')
        for result in (jet, arc):
            self.assertEqual(result.shape, image.shape)
            self.assertFalse(np.array_equal(result, classic))
        self.assertFalse(np.array_equal(jet, arc))
        hud_style.set_current('arcade')                                                  # gemeinsamer Stil
        np.testing.assert_array_equal(replay_hud.draw_hud(image, row), arc)
        np.testing.assert_array_equal(replay_hud.draw_hud(image, row, style='classic'), classic)
        self.assertIsNone(replay_hud.draw_hud(None, row))
        self.assertEqual(replay_hud.draw_hud(np.zeros((10, 10, 3), np.uint8), {}, style='jetpilot').shape, (10, 10, 3))

    def test_werte_aus_csv_zeilen_und_alte_sessions(self):
        image = lane_image(640, 480)
        csv_row = {k: str(v) for k, v in make_row(640, 480).items()}                    # alles Zeichenketten wie in frames.csv
        self.assertEqual(replay_hud.draw_hud(image, csv_row, style='jetpilot').shape, image.shape)
        old = {k: v for k, v in csv_row.items() if k not in ('exposure_us', 'gain', 'lux', 'frame_duration_us',
                                                              'vehicle_mode_key')}
        for style in hud_render.STYLES:
            self.assertEqual(replay_hud.draw_hud(image, old, style=style).shape, image.shape)
            self.assertEqual(replay_hud.draw_hud(image, {'frame_id': '1'}, style=style).shape, image.shape)
        info = replay_hud._hud_info(image, csv_row, None, None)
        self.assertEqual((info['exposure_us'], info['gain'], info['lux'], info['frame_duration_us']),
                         (8000.0, 2.0, 120.5, 40000.0))
        info = replay_hud._hud_info(image, old, None, None)
        self.assertEqual((info['exposure_us'], info['gain'], info['mode_name']), (None, None, ''))   # nie geraten

    def test_linien_vom_verarbeitungsbild_werden_auf_das_grosse_bild_umgerechnet(self):
        width, height = 1296, 972
        top, bottom, lanes = lane_geometry(320, 240)                                    # im kleinen Bild gemessen
        row = make_row(320, 240, process_width=320, process_height=240, roi_high=CROP[1], roi_top_px=int(height * .45),
                       roi_bottom_px=int(height * .85))
        row['lanes_json'] = json.dumps(lanes)
        info = replay_hud._hud_info(np.zeros((height, width, 3), np.uint8), row, None, None)
        expected = replay_hud.convert_lanes(lanes, (320, 240), (width, height), int(240 * (1 - CROP[1])), int(height * .45))
        np.testing.assert_allclose(np.array(info['lanes'], float), np.array(expected, float))

    def test_fehler_im_neuen_stil_faellt_auf_klassisch_zurueck(self):
        image = lane_image(640, 480)
        row = make_row(640, 480)
        classic = replay_hud.draw_hud_classic(image, row)
        replay_hud._STYLE_FAILED.clear()
        with mock.patch.object(hud_render, 'draw', side_effect=RuntimeError('kaputt')):
            with self.assertLogs(replay_hud.__name__, 'ERROR') as logs:
                first = replay_hud.draw_hud(image, row, style='jetpilot')
            second = replay_hud.draw_hud(image, row, style='jetpilot')                  # keine zweite Meldung
        np.testing.assert_array_equal(first, classic)
        np.testing.assert_array_equal(second, classic)
        self.assertEqual(len(logs.records), 1)

    def test_verlauf_nur_wenn_uebergeben_und_je_frame_einmal(self):
        image = lane_image(640, 480)
        row = make_row(640, 480)
        trail = hud_render.HudTrail()
        replay_hud.draw_hud(image, row, style='arcade')
        self.assertEqual(trail.snapshot(), [])
        replay_hud.draw_hud(image, row, style='arcade', trail=trail)
        replay_hud.draw_hud(image, row, style='arcade', trail=trail)                    # gleicher Frame: nicht doppelt
        replay_hud.draw_hud(image, dict(row, frame_id='3546'), style='arcade', trail=trail)
        items = trail.snapshot()
        self.assertEqual([i[0] for i in items], [3545, 3546])
        self.assertEqual((items[0][2], items[0][3], items[0][4]), (97.0, 99.0, 2))
        replay_hud.draw_hud(image, dict(row, frame_id='3547'), style='classic', trail=trail)   # classic sammelt nicht
        self.assertEqual(len(trail.snapshot()), 2)

    def test_legende_hat_die_neuen_zeilen(self):
        names = [entry[0] for entry in replay_hud.LEGEND]
        for needed in ('JETPILOT', 'SPUR LOCK', 'ARCADE', 'SPUR-STREAK', 'LENKSPUR', 'EXP / GAIN / LUX / CAM', 'MODUS'):
            self.assertIn(needed, names)
        self.assertTrue(all(len(entry) == 3 and all(isinstance(x, str) and x for x in entry) for entry in replay_hud.LEGEND))

    def test_row_from_camera_liefert_kamerawerte(self):
        camera = SimpleNamespace(frame_id=5, lanes=[(-1.0, 100.0), (1.0, -50.0)], crop=CROP, process_size=(320, 240),
                                 vehicle_mode='auto', vehicle_angle=90, vehicle_speed=30, vehicle_mode_key='camera',
                                 exposure_us=8000.0, gain=2.0, lux=55.0, frame_duration_us=33333.0,
                                 roi_rows=lambda height=None: (400, 800))
        row = replay_hud.row_from_camera(camera)
        self.assertEqual((row['exposure_us'], row['gain'], row['lux'], row['frame_duration_us']),
                         (8000.0, 2.0, 55.0, 33333.0))
        self.assertEqual((row['process_width'], row['process_height'], row['roi_high']), (320, 240, CROP[1]))
        plain = SimpleNamespace(frame_id=1, lanes=[], crop=CROP, roi_rows=lambda height=None: (0, 10))
        row = replay_hud.row_from_camera(plain)                                          # alte Kameradaten ohne neue Felder
        self.assertEqual([row[k] for k in ('exposure_us', 'gain', 'lux', 'frame_duration_us')], [None] * 4)
        self.assertEqual(replay_hud.draw_hud(np.zeros((480, 640, 3), np.uint8), row, style='jetpilot').shape, (480, 640, 3))


class PatchTests(unittest.TestCase):
    def test_cockpit_auswahl_im_quelltext(self):
        source = (ROOT / 'dashboard' / 'tabs' / 'driving.py').read_text(encoding='utf-8')
        self.assertIn("dbc.Select(id='cockpit-hud'", source)
        self.assertNotIn("dbc.Checklist(id='cockpit-hud'", source)
        self.assertEqual(source.count("id='cockpit-hud'"), 1)                            # ID eindeutig
        self.assertEqual(source.count("State('cockpit-hud', 'value')"), 1)               # Callback-Signatur unveraendert
        self.assertNotIn("(':hud' if hud_on else '')", source)
        self.assertEqual(source.count("f':hud-{hud_style_name}'"), 2)
        self.assertIn('trail=hud_render.TRAIL', source)

    def test_replay_schluessel_mit_stil_bleibt_lesbar(self):
        try:
            from dashboard.tabs.replay import parse_drive_key
        except ImportError as exc:                                                       # Test ohne die uebrigen Module
            self.skipTest(f'replay-Tab nicht importierbar: {exc}')
        self.assertEqual([parse_drive_key(k) for k in ('drive:5:hud-jetpilot', 'cal:7:hud-arcade', 'drive:9')], [5, 7, 9])

    def test_marker_exakt(self):
        spec = importlib.util.spec_from_file_location('h3b_patch', ROOT / 'scripts' / 'paket_h3b_patch.py')
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        self.assertTrue(patch.has_marker('# PAKET-H3B: HUD'))
        self.assertFalse(patch.has_marker('# PAKET-H3: Dual-Stream'))                    # PAKET-H3 ist kein PAKET-H3B
        self.assertFalse(patch.has_marker('PAKET-H3BX'))

    def test_keine_html_reste(self):
        pattern = re.compile(r'&lt;|&gt;|&amp;|&quot;|<br\s*/?>')
        for name in ('hud_render.py', 'hud_style.py', 'replay_hud.py', 'scripts/paket_h3b_patch.py',
                     'dashboard/tabs/driving.py'):
            self.assertIsNone(pattern.search((ROOT / name).read_text(encoding='utf-8')), name)

    def test_fahrregelung_unveraendert(self):
        source = (ROOT / 'driving' / 'camera_mode.py').read_text(encoding='utf-8')
        self.assertIn('SPEED_CONTROL_VALUE = 30', source)
        self.assertNotIn('PAKET-H3B', source)


@unittest.skipUnless(HAS_DASH, 'Dash nicht installiert')
class RealDashSelectTests(unittest.TestCase):
    """Echtes Dash (Flask-Testclient): ``dbc.Select`` liefert eine Zeichenkette, der alte Schalter eine Liste."""

    def test_select_wert_kommt_als_zeichenkette_an(self):
        import dash
        import dash_bootstrap_components as dbc
        from dash import Input, Output, State, html
        app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])
        app.layout = html.Div([
            dbc.Select(id='cockpit-hud', options=hud_style.OPTIONS, value='off', size='sm',
                       className='d-inline-block ms-3', style={'width': 'auto', 'minWidth': '9.5rem'}),
            html.Div(id='out'), dash.dcc.Interval(id='tick', interval=1000)])

        @app.callback(Output('out', 'children'), Input('tick', 'n_intervals'), State('cockpit-hud', 'value'))
        def view(_, hud=None):
            return hud_style.normalize(hud)

        client = app.server.test_client()
        layout = client.get('/_dash-layout').get_json()
        select = layout['props']['children'][0]
        self.assertEqual((select['type'], select['props']['value']), ('Select', 'off'))
        self.assertEqual([o['value'] for o in select['props']['options']], list(hud_style.STYLES))
        for value, expected in (('jetpilot', 'jetpilot'), ('arcade', 'arcade'), ('off', 'off'), (['on'], 'classic'),
                                ([], 'off'), (None, 'off')):
            body = {'output': 'out.children', 'outputs': {'id': 'out', 'property': 'children'},
                    'inputs': [{'id': 'tick', 'property': 'n_intervals', 'value': 1}],
                    'state': [{'id': 'cockpit-hud', 'property': 'value', 'value': value}],
                    'changedPropIds': ['tick.n_intervals']}
            response = client.post('/_dash-update-component', json=body)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
            self.assertEqual(response.get_json()['response']['out']['children'], expected, value)


if __name__ == '__main__':
    unittest.main()
