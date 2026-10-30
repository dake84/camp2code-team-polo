"""Kamerabasierter Fahrmodus mit PID-Regler (PAKET-J1).

``PidMode`` ist ein ``CameraMode`` mit einem PID-Regler in ``driving_parameter`` - dem Haken, den ``CameraMode`` dafuer
vorsieht ("Replace with actual calculation"). Alles andere bleibt unveraendert und wird geerbt: genau eine Auswertung je
Kameraframe, ``LANE_TIMEOUT``, ``CAMERA_STALE``, ``NO_CAMERA_DATA``, ``DrivingState`` mit derselben ``frame_id``,
Geschwindigkeitsstrategie (``SpeedPolicy``), austauschbare Uhr fuer die Simulation, Abtastrate.

Regelgroesse
    Der Rohlenkwinkel aus ``LenkwinkelKalkulator`` (ganzzahlige Grad, 90 = geradeaus) ist die Richtung von der Bildmitte zum
    Mittelpunkt der beiden Fahrbahnlinien am oberen Rand des ROI - ein Vorausschau-Winkel. Der Regler betrachtet
    ``e = Rohwinkel - 90`` als Fehler (Sollwert 0) und liefert den Winkel-Offset ``u`` zu 90 Grad::

        u = Kp * e  +  Ki * Integral(e dt)  +  Kd * d/dt(e, tiefpassgefiltert)

    Danach wirkt wie bisher ``steering_gain`` (Faktor auf den Offset) und die Begrenzung auf 45..135 Grad in
    ``CameraMode._run``. Die wirksame Proportionalverstaerkung ist also ``Kp * steering_gain``.

Standardwerte = bisheriges Verhalten
    Mit ``pid_kp = 1``, ``pid_ki = 0``, ``pid_kd = 0`` (Standard) liefert ``PidMode`` exakt dieselben Fahrbefehle wie
    ``CameraMode`` (per Test gesichert). Ki und Kd sind einzeln zuschaltbar.

Konfiguration (config.json, Namensraum ``cameracar_controller``, wie ``steering_gain``; wird je Frame gelesen, Aenderungen im
Speicher wirken also sofort - es gibt dafuer noch keine Oberflaeche). Die Bereiche sind Annahmen dieses Pakets:

    pid_kp           Standard 1.0   0 .. 10      Proportionalanteil
    pid_ki           Standard 0.0   0 .. 5       Integralanteil (1/s); gleicht einen dauerhaften Versatz aus
    pid_kd           Standard 0.0   0 .. 2       Differentialanteil (s); daempft Pendeln
    pid_i_limit      Standard 15.0  0 .. 45      Obergrenze fuer den Betrag des Integralanteils (Grad)
    pid_d_filter_s   Standard 0.05  0.005 .. 1   Zeitkonstante des Tiefpasses auf der Ableitung (s)
    pid_out_limit    Standard 90.0  5 .. 90      Obergrenze fuer den Betrag von u (Grad); 90 = nicht begrenzt
    pid_max_dt_s     Standard 0.5   0.05 .. 5    laengere Luecken zwischen zwei Frames setzen I und D zurueck (s)

Besonderheiten
    * Zeitbasis ist die Uhr des Modus (``_clock``), nicht die Wanduhr: Live monoton, in der Simulation die aufgezeichneten
      Zeitstempel.
    * Anti-Windup: Der Integralanteil waechst nur, solange der Ausgang nicht in der Saettigung (Begrenzung auf 45..135 Grad nach
      ``steering_gain`` bzw. ``pid_out_limit``) in dieselbe Richtung gedrueckt wird; zusaetzlich begrenzt ``pid_i_limit``.
    * Die Ableitung wirkt auf den Fehler und wird tiefpassgefiltert: der Rohwinkel ist auf ganze Grad quantisiert, eine
      ungefilterte Ableitung rauscht stark. Beim ersten Frame, nach Luecken und nach Neustart gibt es keinen Ableitungsstoss.
    * Gehaltene Frames (keine zwei Linien, Vorwert wird gehalten) aktualisieren den Regler nicht: Der letzte Ausgang wird
      gehalten, Integral und Ableitung bleiben unveraendert. Nach laengerer Luecke (``pid_max_dt_s``) startet der Regler neu.
    * START setzt den Regler zurueck (``_pre_run``).
    * Diagnose: ``last_diagnostic['pid']`` mit Fehler, P-, I-, D-Anteil, Ausgang, dt, Saettigung, Halten, Zuruecksetzen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import ConfigReader

from .camera_mode import CameraMode
from mode_registry import register

if TYPE_CHECKING:
    from CameraCar import CameraCar

FINAL_LIMIT_DEG = 45.0        # CameraMode begrenzt den Winkel auf 90 +- 45 Grad (nach steering_gain)

# Schluessel, Standardwert, Minimum, Maximum
PARAM_SPECS = (
    ('pid_kp', 1.0, 0.0, 10.0),
    ('pid_ki', 0.0, 0.0, 5.0),
    ('pid_kd', 0.0, 0.0, 2.0),
    ('pid_i_limit', 15.0, 0.0, 45.0),
    ('pid_d_filter_s', 0.05, 0.005, 1.0),
    ('pid_out_limit', 90.0, 5.0, 90.0),
    ('pid_max_dt_s', 0.5, 0.05, 5.0),
)
DEFAULTS = {key: default for key, default, _lo, _hi in PARAM_SPECS}


@dataclass(frozen=True)
class PidParams:
    kp: float = 1.0
    ki: float = 0.0
    kd: float = 0.0
    i_limit: float = 15.0
    d_filter_s: float = 0.05
    out_limit: float = 90.0
    max_dt_s: float = 0.5


class PidState:
    """Zustand des Reglers (nur vom Regler-Thread veraendert)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.integral = 0.0        # Grad * s (ohne Ki: Aenderungen von Ki erzeugen keinen Sprung)
        self.last_error = None
        self.last_ns = None
        self.d_filtered = 0.0      # Grad / s
        self.last_output = None


def sanitize(raw, default, lo, hi):
    """Zahl aus der Konfiguration: Texte/Wahrheitswerte/nicht endliche Zahlen -> Standardwert, sonst auf den Bereich begrenzt."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return float(default)
    return float(min(hi, max(lo, raw)))


def clamp(value, limit):
    return max(-limit, min(limit, value))


class PidController:
    """Diskreter PID-Regler ohne Abhaengigkeit von Hardware, Uhr und Konfiguration (einzeln testbar).

    ``step(error, now_ns, params, gain)`` liefert ``(u, info)``. ``gain`` ist ``steering_gain``; er wird nur fuer die
    Saettigungserkennung (Anti-Windup) gebraucht, nicht fuer ``u``.
    """

    def __init__(self):
        self.state = PidState()

    def reset(self):
        self.state.reset()

    def hold(self):
        """Gehaltener Frame: letzter Ausgang, Zustand unveraendert."""
        return self.state.last_output if self.state.last_output is not None else 0.0

    def step(self, error, now_ns, params, gain=1.0):
        st = self.state
        reset = False
        dt = None
        if st.last_ns is not None:
            seconds = (now_ns - st.last_ns) / 1e9
            if seconds > params.max_dt_s:
                st.reset()                       # veraltete Zustaende nicht weiterverwenden
                reset = True
            elif seconds > 0:
                dt = seconds
        limit = min(params.out_limit, FINAL_LIMIT_DEG / gain) if gain > 0 else params.out_limit
        i_cap = params.i_limit / params.ki if params.ki > 0 else 0.0
        integral = clamp(st.integral, i_cap)     # Grenzen koennen sich zur Laufzeit aendern
        saturated = False
        if dt is None:
            d_filtered = st.d_filtered if not reset else 0.0
            u_unsat = params.kp * error + params.ki * integral + params.kd * d_filtered
        else:
            d_raw = (error - st.last_error) / dt
            alpha = dt / (params.d_filter_s + dt)
            d_filtered = st.d_filtered + alpha * (d_raw - st.d_filtered)
            candidate = clamp(integral + error * dt, i_cap)
            u_unsat = params.kp * error + params.ki * candidate + params.kd * d_filtered
            saturated = abs(u_unsat) > limit
            # Anti-Windup (bedingte Integration): nicht weiter aufintegrieren, wenn der Ausgang in der Saettigung steht
            # und der Fehler ihn weiter in dieselbe Richtung drueckt.
            integral = integral if (saturated and error * u_unsat > 0) else candidate
        u = clamp(u_unsat, params.out_limit)
        st.integral, st.last_error, st.last_ns = integral, error, now_ns
        st.d_filtered, st.last_output = d_filtered, u
        info = {'error': error, 'p': params.kp * error, 'i': params.ki * integral, 'd': params.kd * d_filtered,
                'output': u, 'dt_ms': None if dt is None else dt * 1000.0, 'saturated': saturated,
                'held': False, 'reset': reset}
        return u, info


@register('pid', 'PID-Mode (autonom)', order=11, kind='auto', short='PID')
class PidMode(CameraMode):
    # Wie CameraMode: Einstellungen des CameraMode (Geschwindigkeitsstrategie, Lenkverstaerkung, Linien-Timeout,
    # Regler-Abtastung) gelten auch hier - CarApplication/runtime_settings wenden sie auf alle Modi mit diesem Merkmal an.
    follows_camera_settings = True

    def __init__(self, name='PidModus', car: Optional[CameraCar] = None,
                 cfg: Optional[ConfigReader.ConfigReader] = None):
        self._pid = PidController()
        self._warned = set()
        super().__init__(name=name, car=car, cfg=cfg)
        self.mode_key = 'pid'                       # Driving-Mode-Kennung fuer DrivingState/Recorder

    # ------------------------------------------------------------------ Konfiguration
    def pid_params(self) -> PidParams:
        """Aktuelle Reglerparameter aus der Konfiguration (je Frame gelesen, ungueltige Werte -> Standard bzw. begrenzt)."""
        values = {}
        for key, default, lo, hi in PARAM_SPECS:
            raw = self._cfg.get(key, default)
            value = sanitize(raw, default, lo, hi)
            if value != raw and (key, repr(raw)) not in self._warned:
                self._warned.add((key, repr(raw)))
                self._log.warning('cameracar_controller.%s=%r ungueltig bzw. ausserhalb %s..%s - verwende %s',
                                  key, raw, lo, hi, value)
            values[key] = value
        return PidParams(kp=values['pid_kp'], ki=values['pid_ki'], kd=values['pid_kd'], i_limit=values['pid_i_limit'],
                         d_filter_s=values['pid_d_filter_s'], out_limit=values['pid_out_limit'],
                         max_dt_s=values['pid_max_dt_s'])

    # ------------------------------------------------------------------ Lebenszyklus
    def _pre_run(self) -> bool:
        self._pid.reset()
        ok = super()._pre_run()
        p = self.pid_params()
        self._log.info('PidMode startet: Kp=%g Ki=%g Kd=%g (I-Grenze %g, D-Filter %g s, Ausgangsgrenze %g)',
                       p.kp, p.ki, p.kd, p.i_limit, p.d_filter_s, p.out_limit)
        return ok

    # ------------------------------------------------------------------ Regler (Haken von CameraMode)
    def driving_parameter(self, steering_angle_raw) -> tuple[float, int]:
        diagnostic = self.last_diagnostic if isinstance(self.last_diagnostic, dict) else {}
        now = diagnostic.get('decided_monotonic_ns') or self._clock()
        held = bool(diagnostic.get('held_previous_angle'))
        error = float(steering_angle_raw) - 90.0
        if held:
            u = self._pid.hold()
            info = {'error': error, 'p': None, 'i': None, 'd': None, 'output': u, 'dt_ms': None,
                    'saturated': False, 'held': True, 'reset': False}
        else:
            gain = self._cfg.get_float('steering_gain', 2.0)
            u, info = self._pid.step(error, now, self.pid_params(), gain)
        if isinstance(self.last_diagnostic, dict):
            self.last_diagnostic['pid'] = info
        # Bei u == error (Standardwerte) ist 90 + u exakt der Rohwinkel: Die Subtraktion zweier Zahlen im Bereich 45..180 ist in
        # Gleitkomma exakt, deshalb entsteht keine Rundungsabweichung gegenueber CameraMode.
        return 90.0 + u, self.SPEED_CONTROL_VALUE
