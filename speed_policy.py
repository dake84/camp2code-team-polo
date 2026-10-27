"""Geschwindigkeitsstrategie fuer den CameraMode (Paket G).

* ``fixed``     immer derselbe Speed-Steuerwert (Standard 30 = bisheriges Verhalten).
* ``adaptive``  schneller auf Geraden, langsamer in Kurven:
                v = v_max - (v_max - v_min) * min(1, |Sollwinkel - 90| / 45)
                Wird der Vorwert gehalten (Linie fehlt), faehrt das Auto mit v_min.

Unter 30 faehrt das Fahrzeug nicht an; deshalb ist v_min mindestens 30.
Obergrenze 60 als Sicherheitsdeckel. Werte sind Steuerwerte (PWM), keine Geschwindigkeit.
"""
from dataclasses import dataclass

MIN_MOVING = 30
MAX_ALLOWED = 60
MODES = ('fixed', 'adaptive')


@dataclass(frozen=True)
class SpeedPolicy:
    mode: str = 'fixed'
    fixed_speed: int = 30
    v_min: int = 30
    v_max: int = 45

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f'Unbekannter Geschwindigkeitsmodus: {self.mode}')
        for name in ('fixed_speed', 'v_min', 'v_max'):
            value = getattr(self, name)
            if not isinstance(value, int) or not MIN_MOVING <= value <= MAX_ALLOWED:
                raise ValueError(f'{name} muss eine ganze Zahl von {MIN_MOVING} bis {MAX_ALLOWED} sein')
        if self.v_min > self.v_max:
            raise ValueError('v_min darf nicht groesser als v_max sein')

    @classmethod
    def from_values(cls, mode, fixed_speed, v_min, v_max):
        """Aus Formularwerten (Zahlen oder Text); ungueltige Werte -> ValueError."""
        try:
            return cls(str(mode), int(fixed_speed), int(v_min), int(v_max))
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc) or 'Ungueltige Zahl') from exc

    def speed_for(self, requested_angle, held=False):
        """Speed-Steuerwert fuer einen Sollwinkel (45..135 Grad, 90 = geradeaus)."""
        if self.mode == 'fixed':
            return self.fixed_speed
        if held:
            return self.v_min
        deviation = min(1.0, abs(float(requested_angle) - 90.0) / 45.0)
        return int(round(self.v_max - (self.v_max - self.v_min) * deviation))

    def describe(self):
        if self.mode == 'fixed':
            return f'fest {self.fixed_speed}'
        return f'adaptiv {self.v_min}–{self.v_max}'
