"""Paket J1: PidMode (Fahrmodus mit PID-Regler), Registrierung und gemeinsame Einstellungen mit dem CameraMode.

Hardwarefrei und kurz (wenige Sekunden): Das Fahrzeug ist eine Attrappe, die Fahrbefehle nur protokolliert, die Uhr des
Modus ist steuerbar (wie in der Simulation). Geprueft werden:

* der Regler (``PidController``) einzeln: P, I, D, Begrenzungen, Anti-Windup, Zuruecksetzen, Halten, Zeitbasis;
* der Fahrmodus: mit den Standardwerten exakt dieselben Fahrbefehle wie ``CameraMode`` (Gleichheitstest ueber viele Frames
  und Verstaerkungen), geerbte Schutzfunktionen, ``DrivingState``, Diagnose, Konfigurationspruefung, Aenderungen im Betrieb;
* Registrierung (Dropdown, Art, Kurzname) und Einbindung in ``CarApplication`` (Start/Stopp, Geschwindigkeitsstrategie,
  Einstellungen aus dem Settings-Tab gelten auch fuer den PID-Modus);
* die Simulation (``SimEngine``) kann den Modus rechnen.

Nicht getestet: Regelverhalten am echten Fahrzeug (Reglerparameter sind NICHT eingestellt, siehe REPORT.md).
"""
import math
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import hardware_stubs  # noqa: E402
from models import CameraData  # noqa: E402
from test_camera_mode import FakeCameraCar  # noqa: E402

FRAME_NS = 40_000_000
START_NS = 5_000_000_000
LEFT, RIGHT = (-1.19, 166.5), (1.585, -317.1)


def lanes_for(shift, skew=0.0):
    """Zwei Linien (m, b); ``shift`` verschiebt beide, ``skew`` nur die rechte: ergibt verschiedene Rohwinkel."""
    return ((LEFT[0], LEFT[1] + shift), (RIGHT[0], RIGHT[1] + shift + skew))


def raw_angle(lanes):
    from LenkwinkelKalkulator import steering_angle_from_lanes
    return steering_angle_from_lanes(list(lanes), 320, 96)


class Params:
    """Kurzschreibweise fuer ``PidParams`` mit Standardwerten."""

    @staticmethod
    def make(**kw):
        from driving.pid_mode import PidParams
        return PidParams(**kw)


# ------------------------------------------------------------------ Regler einzeln
class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        self.addCleanup(self.stubs.__exit__, None, None, None)
        from driving.pid_mode import PidController
        self.pid = PidController()
        self.t = START_NS

    def step(self, error, params, dt_s=0.04, gain=1.0):
        self.t += int(dt_s * 1e9)
        return self.pid.step(error, self.t, params, gain)

    def test_nur_p(self):
        params = Params.make(kp=2.5)
        for error in (0.0, 10.0, -7.5, 30.0):
            u, info = self.step(error, params)
            self.assertAlmostEqual(u, 2.5 * error)
            self.assertEqual((info['i'], info['d']), (0.0, 0.0))

    def test_integral_waechst_mit_fehler_mal_zeit(self):
        params = Params.make(kp=0.0, ki=1.0, i_limit=45.0)
        self.step(10.0, params)                                  # erster Frame: keine Zeitbasis, kein Integral
        for _ in range(25):                                      # 25 x 40 ms = 1 s
            u, info = self.step(10.0, params)
        self.assertAlmostEqual(info['i'], 10.0, places=6)        # Ki * e * t = 1 * 10 * 1 s
        self.assertAlmostEqual(u, 10.0, places=6)

    def test_integral_begrenzt_und_ki_aenderung_erzeugt_keinen_sprung(self):
        params = Params.make(kp=0.0, ki=2.0, i_limit=6.0)
        self.step(20.0, params)
        for _ in range(100):
            u, info = self.step(20.0, params)
        self.assertAlmostEqual(u, 6.0, places=6)                 # |Ki * Integral| <= i_limit
        before = u
        u, _ = self.step(20.0, Params.make(kp=0.0, ki=1.0, i_limit=6.0))   # Ki halbiert: Ausgang aendert sich stetig
        self.assertLessEqual(abs(u - before), 3.0 + 1e-6)
        u, _ = self.step(0.0, Params.make(kp=0.0, ki=0.0, i_limit=6.0))   # Ki = 0: kein Integralanteil mehr
        self.assertEqual(u, 0.0)

    def test_integral_baut_sich_bei_fehlerumkehr_wieder_ab(self):
        params = Params.make(kp=0.0, ki=1.0, i_limit=45.0)
        self.step(10.0, params)
        for _ in range(25):
            self.step(10.0, params)
        for _ in range(26):
            u, _ = self.step(-10.0, params)
        self.assertLess(abs(u), 0.5)

    def test_anti_windup_bei_saettigung(self):
        """Sitzt der Ausgang in der Saettigung (45 Grad nach steering_gain), waechst das Integral nicht weiter."""
        params = Params.make(kp=1.0, ki=1.0, i_limit=45.0)
        self.step(30.0, params, gain=2.0)                        # 30 * 2 = 60 > 45: gesaettigt
        for _ in range(200):
            _, info = self.step(30.0, params, gain=2.0)
        self.assertTrue(info['saturated'])
        self.assertLess(self.pid.state.integral, 1.0)            # nicht aufgewickelt (ohne Schutz: 30 * 8 s = 240)
        # Fehler kehrt um: der Ausgang folgt sofort (kein Nachlaufen durch ein aufgewickeltes Integral)
        u, _ = self.step(-5.0, params, gain=2.0)
        self.assertLess(u, 0.0)

    def test_ableitung_ohne_stoss_und_tiefpass(self):
        params = Params.make(kp=0.0, kd=1.0, d_filter_s=0.1)
        u, info = self.step(20.0, params)                        # erster Frame: kein Ableitungsstoss
        self.assertEqual((u, info['d']), (0.0, 0.0))
        u, info = self.step(20.0, params)                        # konstanter Fehler: Ableitung 0
        self.assertEqual(u, 0.0)
        u1, _ = self.step(22.0, params)                          # Sprung um 2 Grad in 40 ms = 50 Grad/s (roh)
        self.assertGreater(u1, 5.0)
        self.assertLess(u1, 25.0)                                # tiefpassgefiltert: dt / (tau + dt) = 0,29 des Rohwerts
        self.assertAlmostEqual(u1, 50.0 * 0.04 / (0.1 + 0.04), places=6)
        us = [self.step(22.0, params)[0] for _ in range(60)]
        self.assertLess(abs(us[-1]), 1e-3)                       # klingt ab, sobald der Fehler konstant ist

    def test_ableitung_ist_bei_gleichmaessigem_anstieg_konstant(self):
        params = Params.make(kp=0.0, kd=1.0, d_filter_s=0.01)
        error = 0.0
        for _ in range(80):
            error += 1.0                                         # 1 Grad je 40 ms = 25 Grad/s
            u, _ = self.step(error, params)
        self.assertAlmostEqual(u, 25.0, delta=0.5)

    def test_ausgangsbegrenzung(self):
        params = Params.make(kp=3.0, out_limit=20.0)
        self.assertEqual(self.step(30.0, params)[0], 20.0)
        self.assertEqual(self.step(-30.0, params)[0], -20.0)

    def test_luecke_setzt_zurueck_zeitrueckgang_und_stillstand_nicht(self):
        params = Params.make(kp=1.0, ki=1.0, kd=1.0, max_dt_s=0.5)
        self.step(10.0, params)
        for _ in range(10):
            self.step(10.0, params)
        self.assertGreater(self.pid.state.integral, 0)
        u, info = self.step(10.0, params, dt_s=0.8)              # Luecke > pid_max_dt_s
        self.assertTrue(info['reset'])
        self.assertEqual((info['i'], info['d']), (0.0, 0.0))
        self.assertAlmostEqual(u, 10.0)                          # nur P
        for dt in (0.0, -0.1):                                   # gleiche/zurueckgehende Zeit: kein Teilen durch 0, kein Integral
            before = self.pid.state.integral
            u, info = self.pid.step(12.0, self.pid.state.last_ns + int(dt * 1e9), params, 1.0)
            self.assertTrue(math.isfinite(u))
            self.assertEqual(self.pid.state.integral, before)

    def test_halten_gibt_den_letzten_ausgang_und_laesst_den_zustand_unveraendert(self):
        params = Params.make(kp=1.0, ki=1.0)
        self.assertEqual(self.pid.hold(), 0.0)                   # noch nie gerechnet
        self.step(10.0, params)
        u, _ = self.step(10.0, params)
        state = dict(vars(self.pid.state))
        self.assertEqual(self.pid.hold(), u)
        self.assertEqual(dict(vars(self.pid.state)), state)

    def test_reset(self):
        params = Params.make(kp=1.0, ki=1.0, kd=1.0)
        for _ in range(5):
            self.step(10.0, params)
        self.pid.reset()
        s = self.pid.state
        self.assertEqual((s.integral, s.last_error, s.last_ns, s.d_filtered, s.last_output), (0.0, None, None, 0.0, None))

    def test_deterministisch(self):
        params = Params.make(kp=1.2, ki=0.3, kd=0.1)
        errors = [math.sin(i / 7.0) * 20 for i in range(100)]
        runs = []
        for _ in range(2):
            from driving.pid_mode import PidController
            pid, t, out = PidController(), START_NS, []
            for e in errors:
                t += FRAME_NS
                out.append(pid.step(e, t, params, 2.0)[0])
            runs.append(out)
        self.assertEqual(runs[0], runs[1])

    def test_sanitize(self):
        from driving.pid_mode import sanitize
        self.assertEqual(sanitize(5, 1.0, 0.0, 10.0), 5.0)
        self.assertEqual(sanitize(50, 1.0, 0.0, 10.0), 10.0)            # zu gross: begrenzt
        self.assertEqual(sanitize(-3, 1.0, 0.0, 10.0), 0.0)
        for bad in ('abc', None, True, [1], float('nan'), float('inf')):
            self.assertEqual(sanitize(bad, 1.0, 0.0, 10.0), 1.0, bad)   # ungueltig: Standardwert


# ------------------------------------------------------------------ Fahrmodus
class ModeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        self.addCleanup(self.stubs.__exit__, None, None, None)
        self.counter = 0

    def make(self, cls_name='pid', **controller):
        """(Modus, Fahrzeug, Uhr) mit eigener Konfiguration; die Uhr des Modus ist steuerbar."""
        import ConfigReader
        from driving.camera_mode import CameraMode
        from driving.pid_mode import PidMode
        self.counter += 1
        controller.setdefault('lost_line_timeout', 1.0)
        config = hardware_stubs.write_config(os.path.join(self.tmp.name, f'config{self.counter}.json'),
                                             cameracar_controller=controller)
        car = FakeCameraCar()
        mode = (PidMode if cls_name == 'pid' else CameraMode)(car=car, cfg=ConfigReader.ConfigReader('cameracar_controller', config))
        clock = [START_NS]
        mode._clock = lambda: clock[0]
        mode._pre_run()
        car.commands.clear()
        return mode, car, clock

    @staticmethod
    def feed(mode, car, clock, frame_id, lanes, advance=FRAME_NS):
        clock[0] += advance
        camera = CameraData(process_image=np.zeros((96, 320), np.uint8), raw_image=np.zeros((240, 320, 3), np.uint8),
                            frame_id=frame_id, monotonic_ns=clock[0], lanes=lanes)
        car.camera_data = camera
        car.frame_history.add_camera(camera)
        return mode._run()


class IdentityTests(ModeBase):
    """Mit den Standardwerten (Kp=1, Ki=0, Kd=0) fahren PidMode und CameraMode exakt gleich."""

    def sequence(self):
        frames = [lanes_for(s, k) for s in range(-90, 91, 3) for k in (0.0, 25.0, -30.0, 60.0)]   # viele Rohwinkel
        frames[40:44] = [((1.0, 2.0),)] * 4                                               # eine Linie: Vorwert halten
        frames[60:62] = [(None, RIGHT)] * 2                                               # eine Linie ist None: 90 Grad
        frames[80:83] = [()] * 3                                                          # keine Linien
        return frames

    def drive(self, cls_name, **controller):
        mode, car, clock = self.make(cls_name, **controller)
        results = []
        for frame_id, lanes in enumerate(self.sequence(), 1):
            ok = self.feed(mode, car, clock, frame_id, lanes)
            results.append((ok, mode.last_stop_reason, len(car.commands)))
        return car.commands, [(s.frame_id, s.steering_angle, s.requested_angle, s.steering_angle_car) for s in car.states], results

    def test_gleiche_fahrbefehle_fuer_verschiedene_lenkverstaerkungen(self):
        angles = {raw_angle(lanes) for lanes in self.sequence() if len(lanes) == 2 and None not in lanes}
        self.assertGreater(len(angles), 20)                                                 # die Folge ist abwechslungsreich
        for gain in (0.5, 0.7, 1.0, 2.0, 3.3, 4.0):
            with self.subTest(steering_gain=gain):
                camera = self.drive('camera', steering_gain=gain)
                pid = self.drive('pid', steering_gain=gain)
                self.assertEqual(pid[0], camera[0])
                self.assertEqual(pid[1], camera[1])
                self.assertEqual(pid[2], camera[2])
                self.assertGreater(len(set(c[1] for c in camera[0])), 10)

    def test_gleiche_fahrbefehle_mit_adaptiver_geschwindigkeit(self):
        from speed_policy import SpeedPolicy
        outputs = []
        for cls_name in ('camera', 'pid'):
            mode, car, clock = self.make(cls_name)
            mode.speed_policy = SpeedPolicy('adaptive', 30, 30, 45)
            for frame_id, lanes in enumerate(self.sequence(), 1):
                self.feed(mode, car, clock, frame_id, lanes)
            outputs.append(car.commands)
        self.assertEqual(outputs[0], outputs[1])
        self.assertGreater(len({speed for speed, _ in outputs[0]}), 1)

    def test_ki_und_kd_null_aendern_nichts_auch_mit_gesetzten_grenzen(self):
        camera = self.drive('camera')
        pid = self.drive('pid', pid_kp=1, pid_ki=0, pid_kd=0, pid_i_limit=3, pid_d_filter_s=0.5, pid_out_limit=90)
        self.assertEqual(pid[0], camera[0])


class ModeTests(ModeBase):
    def test_klasse_und_kennung(self):
        from driving import CameraMode, PidMode
        self.assertTrue(issubclass(PidMode, CameraMode))
        mode, car, clock = self.make()
        self.assertEqual((mode.mode_key, mode.SPEED_CONTROL_VALUE), ('pid', 30))
        self.feed(mode, car, clock, 1, lanes_for(0))
        self.assertEqual(car.states[-1].driving_mode, 'pid')                                # DrivingState traegt 'pid'
        self.assertEqual(car.frame_history.find(1).driving.frame_id, 1)
        self.assertEqual(mode.driving_parameter(100)[1], 30)

    def test_geerbte_schutzfunktionen(self):
        mode, car, clock = self.make(lost_line_timeout=0.2)
        self.assertTrue(self.feed(mode, car, clock, 1, ((1.0, 2.0),)))                      # Vorwert gehalten
        self.assertTrue(mode.last_diagnostic['held_previous_angle'])
        clock[0] += int(0.3e9)
        self.assertFalse(mode._run())                                                       # auch ohne neuen Frame
        self.assertEqual(mode.last_stop_reason, 'LANE_TIMEOUT')
        mode, car, clock = self.make()
        self.assertTrue(mode._run())                                                        # Platzhalter: nicht fahren
        self.assertEqual(car.commands, [])
        clock[0] += int(1.1e9)
        self.assertFalse(mode._run())
        self.assertEqual(mode.last_stop_reason, 'NO_CAMERA_DATA')
        mode, car, clock = self.make()
        self.assertTrue(self.feed(mode, car, clock, 1, lanes_for(0)))
        clock[0] += int(1.5e9)
        self.assertFalse(mode._run())
        self.assertEqual(mode.last_stop_reason, 'CAMERA_STALE')

    def test_ein_entscheid_je_frame(self):
        mode, car, clock = self.make()
        self.feed(mode, car, clock, 1, lanes_for(0))
        for _ in range(10):
            self.assertTrue(mode._run())
        self.assertEqual(len(car.commands), 1)
        self.assertEqual(mode.last_diagnostic['pid']['dt_ms'], None)                        # erster Frame

    def test_diagnose_enthaelt_die_reglerterme(self):
        mode, car, clock = self.make(pid_kp=1.0, pid_ki=0.5, pid_kd=0.1)
        for frame_id in range(1, 6):
            self.feed(mode, car, clock, frame_id, lanes_for(40))
        pid = mode.last_diagnostic['pid']
        self.assertEqual(set(pid), {'error', 'p', 'i', 'd', 'output', 'dt_ms', 'saturated', 'held', 'reset'})
        self.assertAlmostEqual(pid['error'], raw_angle(lanes_for(40)) - 90.0)
        self.assertAlmostEqual(pid['p'], pid['error'])
        self.assertGreater(pid['i'], 0 if pid['error'] > 0 else -1e9) if pid['error'] > 0 else self.assertLess(pid['i'], 0)
        self.assertAlmostEqual(pid['dt_ms'], 40.0, places=3)
        self.assertAlmostEqual(pid['output'], pid['p'] + pid['i'] + pid['d'], places=6)
        self.assertFalse(pid['held'])

    def test_integral_gleicht_dauerhaften_versatz_aus(self):
        """Bei konstantem Fehler wird der Lenkwinkel mit Ki nach und nach groesser als mit reinem P."""
        error = raw_angle(lanes_for(30)) - 90
        self.assertNotEqual(error, 0)
        p_mode, p_car, p_clock = self.make(steering_gain=1.0)
        i_mode, i_car, i_clock = self.make(steering_gain=1.0, pid_ki=0.8, pid_i_limit=20)
        for frame_id in range(1, 80):
            self.feed(p_mode, p_car, p_clock, frame_id, lanes_for(30))
            self.feed(i_mode, i_car, i_clock, frame_id, lanes_for(30))
        p_offset = p_car.commands[-1][1] - 90
        i_offset = i_car.commands[-1][1] - 90
        self.assertAlmostEqual(p_offset, error, delta=1)
        self.assertGreater(abs(i_offset), abs(p_offset) + 5)                                # Integral hat aufgeschaltet
        self.assertEqual(math.copysign(1, i_offset), math.copysign(1, error))
        self.assertLessEqual(abs(i_offset - p_offset), 20 + 1)                              # ... aber durch pid_i_limit begrenzt

    def test_ausgang_bleibt_im_gueltigen_bereich(self):
        mode, car, clock = self.make(steering_gain=4.0, pid_kp=10, pid_ki=5, pid_kd=2)
        for frame_id in range(1, 120):
            self.feed(mode, car, clock, frame_id, lanes_for(((-1) ** frame_id) * 90))
        self.assertTrue(all(45 <= angle <= 135 for _speed, angle in car.commands))

    def test_gehaltene_frames_halten_den_ausgang_und_den_zustand(self):
        mode, car, clock = self.make(pid_kp=1.5, pid_ki=0.5, pid_kd=0.2)
        for frame_id in range(1, 12):
            self.feed(mode, car, clock, frame_id, lanes_for(35))
        before_angle = car.commands[-1][1]
        before_state = dict(vars(mode._pid.state))
        for frame_id in (12, 13, 14):
            self.assertTrue(self.feed(mode, car, clock, frame_id, ((1.0, 2.0),)))            # keine zwei Linien
            self.assertTrue(mode.last_diagnostic['pid']['held'])
            self.assertEqual(car.commands[-1][1], before_angle)
        self.assertEqual(dict(vars(mode._pid.state)), before_state)

    def test_nach_langer_luecke_startet_der_regler_neu(self):
        mode, car, clock = self.make(pid_ki=1.0, pid_kd=0.5, pid_max_dt_s=0.2, lost_line_timeout=5.0)
        for frame_id in range(1, 15):
            self.feed(mode, car, clock, frame_id, lanes_for(35))
        self.assertNotEqual(mode.last_diagnostic['pid']['i'], 0.0)
        self.feed(mode, car, clock, 15, ((1.0, 2.0),), advance=int(0.4e9))                  # 0,4 s Luecke, gehalten
        self.feed(mode, car, clock, 16, lanes_for(35))
        pid = mode.last_diagnostic['pid']
        self.assertTrue(pid['reset'])
        self.assertEqual((pid['i'], pid['d']), (0.0, 0.0))

    def test_start_setzt_den_regler_zurueck(self):
        mode, car, clock = self.make(pid_ki=1.0)
        for frame_id in range(1, 20):
            self.feed(mode, car, clock, frame_id, lanes_for(35))
        self.assertGreater(abs(mode._pid.state.integral), 0)
        mode._pre_run()
        self.assertEqual((mode._pid.state.integral, mode._pid.state.last_ns), (0.0, None))

    def test_parameter_werden_gepruft_und_aenderungen_wirken_sofort(self):
        mode, car, clock = self.make(pid_kp='abc', pid_ki=99, pid_kd=True, pid_out_limit=0)
        p = mode.pid_params()
        self.assertEqual((p.kp, p.ki, p.kd, p.out_limit), (1.0, 5.0, 0.0, 5.0))             # ungueltig: Standard; zu gross/klein: begrenzt
        with self.assertLogs(mode._log, level='WARNING') as logs:
            mode.pid_params()
            mode._warned.clear()
            mode.pid_params()
        self.assertTrue(logs.output)
        count = len(logs.output)
        mode.pid_params()
        self.assertEqual(len(logs.output), count)                                           # nicht bei jedem Frame erneut melden
        mode._cfg.set_value('pid_kp', 3.5)                                                  # Aenderung im Betrieb (ohne Neustart)
        mode._cfg.set_value('pid_kd', float('nan'))
        p = mode.pid_params()
        self.assertEqual((p.kp, p.kd), (3.5, 0.0))

    def test_aenderung_von_kp_im_betrieb_wirkt_im_naechsten_frame(self):
        mode, car, clock = self.make(steering_gain=1.0)
        self.feed(mode, car, clock, 1, lanes_for(35))
        first = car.commands[-1][1]
        mode._cfg.set_value('pid_kp', 2.0)
        self.feed(mode, car, clock, 2, lanes_for(35))
        error = raw_angle(lanes_for(35)) - 90
        self.assertEqual(car.commands[-1][1], int(max(45, min(135, 90 + 2.0 * error))))
        self.assertNotEqual(car.commands[-1][1], first)

    def test_jede_stufe_nur_mit_aktivem_regler_unterscheidet_sich(self):
        """P allein, P+I und P+D liefern verschiedene Winkel (der Regler ist wirklich im Pfad)."""
        results = {}
        for name, cfg in (('p', {}), ('pi', {'pid_ki': 1.0}), ('pd', {'pid_kd': 0.3})):
            mode, car, clock = self.make(steering_gain=1.0, **cfg)
            for frame_id, shift in enumerate([0, 20, 40, 60, 60, 60, 20, 0, -20, -40], 1):
                self.feed(mode, car, clock, frame_id, lanes_for(shift))
            results[name] = [angle for _speed, angle in car.commands]
        self.assertNotEqual(results['p'], results['pi'])
        self.assertNotEqual(results['p'], results['pd'])
        self.assertNotEqual(results['pi'], results['pd'])


# ------------------------------------------------------------------ Registrierung und Anwendung
class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        self.addCleanup(self.stubs.__exit__, None, None, None)

    def test_registrierung(self):
        import driving
        import mode_registry
        self.assertTrue(hasattr(driving, 'PidMode'))                                         # Import registriert die Modi
        keys = [spec.key for spec in mode_registry.specs()]
        self.assertIn('pid', keys)
        self.assertIn('camera', keys)
        spec = {s.key: s for s in mode_registry.specs()}['pid']
        self.assertEqual((spec.label, spec.kind, spec.short, spec.order), ('PID-Mode (autonom)', 'auto', 'PID', 11))
        self.assertEqual(mode_registry.kind_of('pid'), 'auto')                               # wie CameraMode: autonom
        self.assertEqual(mode_registry.short_label('pid'), 'PID')
        self.assertLess(keys.index('camera'), keys.index('pid'))                             # im Dropdown nach dem CameraMode
        self.assertIn({'label': 'PID-Mode (autonom)', 'value': 'pid'}, mode_registry.dropdown_options())

    def test_driving_exportiert_den_modus(self):
        import driving
        self.assertIn('PidMode', vars(driving)['__all__'])          # (kein Attributzugriff: test_static_checks prueft ``driving.<Name>``)
        self.assertIs(driving.PidMode, sys.modules['driving.pid_mode'].PidMode)


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                    cameracar_controller={'lost_line_timeout': 1.0})
        os.chdir(self.tmp.name)
        self.stubs = hardware_stubs.installed()
        self.stubs.__enter__()
        from car_application import CarApplication
        self.app = CarApplication(start_camera=False)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.shutdown()
        self.stubs.__exit__(None, None, None)
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_modus_ist_im_dropdown_und_in_der_anwendung(self):
        import mode_registry
        self.assertIn('pid', self.app.modes)
        self.assertIn('pid', [o['value'] for o in mode_registry.dropdown_options()])
        self.assertTrue(self.app.modes['pid'].follows_camera_settings)
        self.assertFalse(getattr(self.app.modes['camera'], 'follows_camera_settings', False))

    def test_start_stopp_ueber_die_anwendung(self):
        self.app.start_mode('pid')
        self.assertEqual(self.app.active_mode, 'pid')
        self.assertTrue(self.app.running())
        self.assertEqual(self.app.vehicle_state()[0], 'auto')                                # Art aus dem Register
        self.assertEqual(self.app.vehicle_state()[3], 'pid')
        with self.assertRaises(RuntimeError):
            self.app.start_mode('camera')                                                    # immer nur ein Modus
        self.app.stop_all()
        self.assertFalse(self.app.running())
        self.assertIsNone(self.app.active_mode)
        self.assertEqual(self.app.stop_reason(), 'Benutzer-STOP')

    def test_geschwindigkeitsstrategie_gilt_auch_fuer_den_pid_modus(self):
        from speed_policy import SpeedPolicy
        policy = SpeedPolicy('adaptive', 30, 30, 45)
        self.assertTrue(self.app.set_speed_policy(policy))
        self.assertIs(self.app.modes['camera'].speed_policy, policy)
        self.assertIs(self.app.modes['pid'].speed_policy, policy)
        self.assertIs(self.app.speed_policy(), policy)
        other = SpeedPolicy('fixed', 35, 30, 45)
        self.app.set_speed_policy(other)
        self.assertIs(self.app.modes['pid'].speed_policy, other)

    def test_gespeicherte_strategie_wird_beim_start_uebernommen(self):
        from speed_policy import SpeedPolicy
        self.app.set_speed_policy(SpeedPolicy('fixed', 35, 30, 45))
        self.app.shutdown()
        from car_application import CarApplication
        self.app = CarApplication(start_camera=False)
        self.assertEqual(self.app.modes['camera'].speed_policy.fixed_speed, 35)
        self.assertEqual(self.app.modes['pid'].speed_policy.fixed_speed, 35)

    def test_einstellungen_aus_dem_settings_tab_gelten_auch_fuer_den_pid_modus(self):
        import runtime_settings
        values = {**runtime_settings.defaults(), 'ctl_scan': 50, 'lost_timeout': 0.5, 'steer_gain': 1.5}
        result = self.app.set_runtime_settings(values)
        self.assertTrue(result['applied']['steer_gain'])
        for key in ('camera', 'pid'):
            mode = self.app.modes[key]
            self.assertEqual((mode.poll_frequency, mode._lost_timeout, mode._cfg.get_float('steering_gain', 0)),
                             (50, 0.5, 1.5), key)

    def test_andere_modi_werden_nicht_beruehrt(self):
        import runtime_settings
        from speed_policy import SpeedPolicy
        marker = types.SimpleNamespace(is_running=lambda: False, follows_camera_settings='ja', speed_policy='unveraendert')
        self.app.modes['dummy'] = marker                                                     # Merkmal nur bei ``is True``
        self.assertEqual(runtime_settings.followers(self.app), [self.app.modes['pid']])
        self.app.set_speed_policy(SpeedPolicy('fixed', 33, 30, 45))
        self.assertEqual(marker.speed_policy, 'unveraendert')
        del self.app.modes['dummy']
        self.assertEqual(runtime_settings.followers(type('NoModes', (), {})()), [])


# ------------------------------------------------------------------ Anzeige im Cockpit
class DiagnosticTextTests(ModeBase):
    def text(self, diagnostic):
        from dashboard.helpers import diagnostic_text
        return diagnostic_text(diagnostic, None, True)

    def test_reglerterme_erscheinen_nur_beim_pid_modus(self):
        camera, car, clock = self.make('camera')
        self.feed(camera, car, clock, 1, lanes_for(35))
        plain = self.text(camera.last_diagnostic)
        self.assertNotIn('PID', plain)
        self.assertTrue(plain.startswith('Aktuell · Frame 1'))
        mode, car, clock = self.make('pid', pid_ki=0.5)
        for frame_id in range(1, 6):
            self.feed(mode, car, clock, frame_id, lanes_for(35))
        text = self.text(mode.last_diagnostic)
        pid = mode.last_diagnostic['pid']
        self.assertIn(f"PID: Fehler {pid['error']:.1f}° = P {pid['p']:.1f} + I {pid['i']:.1f} + D {pid['d']:.1f} → {pid['output']:.1f}°", text)
        self.assertTrue(text.startswith(plain.split(' · Frame')[0]))
        self.assertNotIn('gehalten)', text)
        self.feed(mode, car, clock, 6, ((1.0, 2.0),))
        self.assertIn('(gehalten)', self.text(mode.last_diagnostic))
        self.assertEqual(self.text(None), 'Noch keine Controllerdiagnose')
        self.assertNotIn('PID', self.text({'frame_id': 3, 'lanes': (), 'pid': 'kaputt'}))      # unerwartetes Format: ignoriert


# ------------------------------------------------------------------ Simulation
class SimulationTests(unittest.TestCase):
    def setUp(self):
        from test_paket_f import write_session
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        self.config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'config.json'),
                                                  cameracar_controller={'lost_line_timeout': 1})
        os.chdir(self.tmp.name)
        self.root = os.path.join(self.tmp.name, 'Data_AI', 'sessions')
        self.name = write_session(self.root, count=50)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def run_engine(self, mode_cls=None, **controller):
        import ConfigReader
        from frame_source import SessionSource
        from sim_engine import SimEngine
        if controller:
            config = hardware_stubs.write_config(os.path.join(self.tmp.name, 'sim.json'),
                                                 cameracar_controller={'lost_line_timeout': 1, **controller})
        else:
            config = self.config
        engine = SimEngine(ConfigReader.ConfigReader('cameracar_controller', config), v_min=20, v_max=100)
        source = SessionSource(self.name, self.root)
        if mode_cls is None:
            return engine.run(source, 'copilot', warmup=5)
        with mock.patch('driving.camera_mode.CameraMode', mode_cls):       # SimEngine._controller importiert die Klasse beim Aufruf
            return engine.run(source, 'copilot', warmup=5)

    def test_die_simulation_rechnet_den_pid_modus_und_stimmt_mit_dem_standard_ueberein(self):
        from driving.pid_mode import PidMode
        baseline = self.run_engine()
        pid = self.run_engine(PidMode)
        self.assertEqual(len(pid.frames), len(baseline.frames))
        self.assertEqual([f.car_angle for f in pid.frames], [f.car_angle for f in baseline.frames])
        self.assertEqual([f.stop_reason for f in pid.frames], [f.stop_reason for f in baseline.frames])

    def test_ki_kd_veraendern_das_simulationsergebnis_und_bleiben_gueltig(self):
        from driving.pid_mode import PidMode
        baseline = self.run_engine(PidMode)
        tuned = self.run_engine(PidMode, pid_kp=1.2, pid_ki=0.6, pid_kd=0.2, pid_i_limit=10)
        self.assertEqual(len(tuned.frames), len(baseline.frames))
        self.assertNotEqual([f.car_angle for f in tuned.frames], [f.car_angle for f in baseline.frames])
        self.assertTrue(all(f.car_angle is None or 45 <= f.car_angle <= 135 for f in tuned.frames))
        self.assertEqual(tuned.errors, 0)


if __name__ == '__main__':
    unittest.main()
