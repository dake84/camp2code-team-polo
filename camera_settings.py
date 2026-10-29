"""Kameraeinstellungen aus ``config.json`` (Namensraum ``cam_sensor``), Paket H3.

Schluessel:
    width, height                  Aufnahmegroesse ("main"), Standard 640x480 wie bisher
    process_width, process_height  Verarbeitungsgroesse; fehlend/0 = Verarbeitung auf dem Aufnahmebild (wie bisher)
    downscale                      auto | camera | software (Standard auto)
    frame_rate                     feste Bildrate der KAMERA in fps (FrameDurationLimits); 0/fehlend = Automatik
    exposure_value                 Belichtungskorrektur (ExposureValue), Standard 1.0 wie bisher
    scan_frequency                 Takt der KAMERASCHLEIFE in Hz (nicht dasselbe wie frame_rate!)

``frame_rate`` bestimmt, wie schnell der Sensor Bilder liefert. ``scan_frequency`` bestimmt, wie oft die
Kameraschleife des CameraSensor hoechstens ein Bild abholt und verarbeitet. Die Schleife wird nie schneller
getaktet, als die Kamera liefert.

Fehlerhafte Werte ergeben die Standardwerte (mit Hinweis im Log); das Lesen wirft nie eine Ausnahme.
"""
import logging
import math
from dataclasses import dataclass, field

DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480
DEFAULT_EXPOSURE_VALUE = 1.0
DOWNSCALE_MODES = ('auto', 'camera', 'software')
FRAME_RATE_RANGE = (1.0, 200.0)
EXPOSURE_RANGE = (-8.0, 8.0)


@dataclass(frozen=True)
class CameraSettings:
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    process_width: int = 0
    process_height: int = 0
    downscale: str = 'auto'
    frame_rate: float = 0.0            # 0 = Automatik
    exposure_value: float = DEFAULT_EXPOSURE_VALUE
    notes: tuple = field(default_factory=tuple)   # Hinweise zu verworfenen Werten (fuer das Log)


def read_camera_settings(cfg, log=None):
    """Liest die Kameraeinstellungen; ``cfg`` ist ein ``ConfigReader`` fuer ``cam_sensor``. Wirft nie."""
    log = log if log is not None else logging.getLogger(__name__)
    notes = []

    def number(kind, name, default):
        try:
            return kind(name, default)
        except Exception as exc:  # defekte Konfiguration darf den Start nie verhindern
            notes.append(f'{name} nicht lesbar ({exc}) - Standardwert {default}')
            return default

    width = number(cfg.get_int, 'width', DEFAULT_WIDTH)
    height = number(cfg.get_int, 'height', DEFAULT_HEIGHT)
    if width <= 0:
        notes.append(f'width {width} ungueltig - Standardwert {DEFAULT_WIDTH}')
        width = DEFAULT_WIDTH
    if height <= 0:
        notes.append(f'height {height} ungueltig - Standardwert {DEFAULT_HEIGHT}')
        height = DEFAULT_HEIGHT

    process_width = number(cfg.get_int, 'process_width', 0)
    process_height = number(cfg.get_int, 'process_height', 0)
    if process_width < 0 or process_height < 0:
        notes.append('process_width/process_height muessen positiv sein - Verarbeitung auf dem Aufnahmebild')
        process_width = process_height = 0

    try:
        downscale = str(cfg.get_str('downscale', 'auto')).strip().lower()
    except Exception as exc:
        notes.append(f'downscale nicht lesbar ({exc}) - Standardwert auto')
        downscale = 'auto'
    if downscale not in DOWNSCALE_MODES:
        notes.append(f'downscale {downscale!r} unbekannt (auto|camera|software) - Standardwert auto')
        downscale = 'auto'

    frame_rate = number(cfg.get_float, 'frame_rate', 0.0)
    if not math.isfinite(frame_rate) or frame_rate < 0:
        notes.append(f'frame_rate {frame_rate!r} ungueltig - Automatik')
        frame_rate = 0.0
    elif frame_rate > 0 and not FRAME_RATE_RANGE[0] <= frame_rate <= FRAME_RATE_RANGE[1]:
        notes.append(f'frame_rate {frame_rate:g} ausserhalb {FRAME_RATE_RANGE[0]:g}..{FRAME_RATE_RANGE[1]:g} fps '
                     '- Automatik')
        frame_rate = 0.0

    exposure = number(cfg.get_float, 'exposure_value', DEFAULT_EXPOSURE_VALUE)
    if not math.isfinite(exposure) or not EXPOSURE_RANGE[0] <= exposure <= EXPOSURE_RANGE[1]:
        notes.append(f'exposure_value {exposure!r} ausserhalb {EXPOSURE_RANGE[0]:g}..{EXPOSURE_RANGE[1]:g} '
                     f'- Standardwert {DEFAULT_EXPOSURE_VALUE:g}')
        exposure = DEFAULT_EXPOSURE_VALUE

    for note in notes:
        log.warning('Kameraeinstellung: %s', note)
    return CameraSettings(width=width, height=height, process_width=process_width, process_height=process_height,
                          downscale=downscale, frame_rate=float(frame_rate), exposure_value=float(exposure),
                          notes=tuple(notes))


def safe_camera_settings(cfg, log=None):
    """Wie ``read_camera_settings``, faellt aber bei unlesbarem ``cfg`` komplett auf die Standardwerte zurueck."""
    try:
        return read_camera_settings(cfg, log)
    except Exception as exc:
        (log or logging.getLogger(__name__)).warning('Kameraeinstellungen nicht lesbar (%s) - Standardwerte', exc)
        return CameraSettings(notes=(f'nicht lesbar: {exc}',))
