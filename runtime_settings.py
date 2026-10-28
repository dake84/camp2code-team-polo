"""Einstellungen fuer CameraSensor, CameraMode (Regler) und Fahrzeug (Paket H1b).

Die Schluessel stammen aus den ConfigReader-Aufrufen im Code (nicht aus Annahmen):

    cam_sensor.scan_frequency             CameraSensor.scan_frequency      Standard 25 Hz
    cameracar_controller.scan_frequency   DrivingMode / CameraMode         Standard 100 Hz (DEFAULT_POLL_HZ)
    cameracar_controller.lost_line_timeout  CameraMode.__init__            Standard 1 s
    cameracar_controller.steering_gain    CameraMode._run (je Frame)       Standard 2.0
    car.v_min / car.v_max                 BaseCar.speed (je Fahrbefehl)    Standard 20 / 100

Nicht enthalten (gehoeren zu H3): Hardware-Kalibrierung (turning_offset, forward_A, forward_B),
Aufloesung und colormode.

Regeln:
    * Aenderungen nur im Stillstand (CarApplication.set_runtime_settings).
    * Wertebereiche werden validiert (die Bereiche sind Annahmen dieses Pakets, nirgends im Code vorgegeben).
    * Speichern ueber config_store.update (atomar, Sicherung, nur nach erfolgreichem Lesen).
    * Fehlerhafte gespeicherte Werte ergeben die Standardwerte; Laden wirft nie eine Ausnahme.
"""
import logging
import math
from dataclasses import dataclass

import config_store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Setting:
    key: str            # eindeutiger Schluessel (UI, Tests)
    namespace: str      # Namensraum in config.json
    name: str           # Schluessel im Namensraum
    label: str
    unit: str
    kind: type          # int oder float
    default: object     # Standardwert des Codes, wenn der Schluessel fehlt
    minimum: object
    maximum: object
    step: object
    help_key: str       # Schluessel in help_texts.HELP
    group: str          # Kamera | Regler | Fahrzeug
    behavior: bool = False   # veraendert das Fahrverhalten (im UI gekennzeichnet)


SETTINGS = (
    Setting('cam_scan', 'cam_sensor', 'scan_frequency', 'Kamera-Abtastung', 'Hz', int, 25, 5, 200, 1,
            'camfreq', 'Kamera'),
    Setting('ctl_scan', 'cameracar_controller', 'scan_frequency', 'Regler-Abtastung', 'Hz', int, 100, 10, 500, 10,
            'ctlfreq', 'Regler'),
    Setting('lost_timeout', 'cameracar_controller', 'lost_line_timeout', 'Linien-Timeout', 's', float, 1.0, 0.3, 5.0,
            0.1, 'losttimeout', 'Regler', True),
    Setting('steer_gain', 'cameracar_controller', 'steering_gain', 'Lenkverstärkung', '', float, 2.0, 0.5, 4.0, 0.1,
            'steergain', 'Regler', True),
    Setting('v_min', 'car', 'v_min', 'Minimaler Steuerwert', '', int, 20, 10, 60, 1, 'vmin', 'Fahrzeug', True),
    Setting('v_max', 'car', 'v_max', 'Maximaler Steuerwert', '', int, 100, 30, 100, 1, 'vmax', 'Fahrzeug', True),
)
BY_KEY = {s.key: s for s in SETTINGS}


def number_text(value):
    """Zahl ohne ueberfluessige Nullen; fehlende Werte als Gedankenstrich."""
    if value is None:
        return '–'
    return f'{value:g}' if isinstance(value, float) else str(value)


def defaults():
    return {s.key: s.default for s in SETTINGS}


def coerce(setting, value):
    """Prueft einen Wert streng (keine Texte, keine Wahrheitswerte, Bereich) und liefert ihn im Zieltyp."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{setting.label}: Zahl erwartet')
    if not math.isfinite(value):
        raise ValueError(f'{setting.label}: keine endliche Zahl')
    if setting.kind is int:
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(f'{setting.label}: ganze Zahl erwartet')
            value = int(value)
    else:
        value = round(float(value), 3)
    if not setting.minimum <= value <= setting.maximum:
        raise ValueError(f'{setting.label}: {number_text(setting.minimum)} bis {number_text(setting.maximum)}'
                         f'{" " + setting.unit if setting.unit else ""} erwartet')
    return value


def validate(values):
    """Alle Einstellungen pruefen (alle Schluessel muessen vorhanden sein); ValueError mit allen Meldungen."""
    result, errors = {}, []
    for setting in SETTINGS:
        if setting.key not in values:
            errors.append(f'{setting.label}: Wert fehlt')
            continue
        try:
            result[setting.key] = coerce(setting, values[setting.key])
        except ValueError as exc:
            errors.append(str(exc))
    if not errors and result['v_min'] > result['v_max']:
        errors.append('Minimaler Steuerwert darf nicht größer als der maximale sein')
    if errors:
        raise ValueError('; '.join(errors))
    return result


# ------------------------------------------------------------------ gespeicherte Werte
def stored_values(path=None):
    """Je Einstellung der gespeicherte Wert: {'value', 'state': ok|absent|invalid, 'raw'}. Wirft nie."""
    try:
        data = config_store.read(path)
    except config_store.ConfigStoreError as exc:
        log.info('Kamera-/Reglereinstellungen: keine gespeicherte Konfiguration (%s) - Standardwerte', exc)
        data = {}
    result = {}
    for setting in SETTINGS:
        section = data.get(setting.namespace, {})
        if not isinstance(section, dict) or setting.name not in section:
            result[setting.key] = {'value': None, 'state': 'absent', 'raw': None}
            continue
        raw = section[setting.name]
        try:
            result[setting.key] = {'value': coerce(setting, raw), 'state': 'ok', 'raw': raw}
        except ValueError as exc:
            log.warning('config.json %s.%s ungueltig (%s) - Standardwert %s', setting.namespace, setting.name, exc,
                        number_text(setting.default))
            result[setting.key] = {'value': None, 'state': 'invalid', 'raw': raw}
    low, high = result['v_min'], result['v_max']
    if low['state'] == high['state'] == 'ok' and low['value'] > high['value']:
        log.warning('config.json car.v_min > car.v_max - Standardwerte')
        for entry in (low, high):
            entry.update(value=None, state='invalid')
    return result


def startup_values(path=None):
    """Werte, die beim Start angewendet werden: gueltige gespeicherte; ungueltige -> Standard; fehlende entfallen."""
    result = {}
    for key, entry in stored_values(path).items():
        if entry['state'] == 'ok':
            result[key] = entry['value']
        elif entry['state'] == 'invalid':
            result[key] = BY_KEY[key].default
    return result


def save(values, path=None, backup_dir=None):
    """Validiert und schreibt alle Werte ueber config_store (ConfigStoreError, wenn nichts geschrieben wurde)."""
    values = validate(values)
    changes = {}
    for setting in SETTINGS:
        changes.setdefault(setting.namespace, {})[setting.name] = values[setting.key]
    return config_store.update(changes, path, backup_dir)


# ------------------------------------------------------------------ laufende Objekte
def _camera_mode(application):
    modes = getattr(application, 'modes', None)
    return modes.get('camera') if isinstance(modes, dict) else None


def _set(reader, name, value):
    setter = getattr(reader, 'set_value', None)
    if setter is None:
        return False
    setter(name, value)
    return True


def _apply_one(setting, value, application):
    sensor, mode, car = getattr(application, 'sensor', None), _camera_mode(application), getattr(application, 'car', None)
    if setting.key == 'cam_scan':
        return _set(getattr(sensor, '_cfg', None), setting.name, value)
    if mode is None and setting.group == 'Regler':
        return False
    if setting.key == 'ctl_scan':
        ok = _set(mode._cfg, setting.name, value)
        mode._frequency = int(value)             # die Schleife liest die Abtastung beim naechsten START
        return ok
    if setting.key == 'lost_timeout':
        ok = _set(mode._cfg, setting.name, value)
        mode._lost_timeout = float(value)        # CameraMode liest den Wert nur einmal im Konstruktor
        return ok
    if setting.key == 'steer_gain':
        return _set(mode._cfg, setting.name, value)   # CameraMode liest ihn je Frame aus der Konfiguration
    if setting.key in ('v_min', 'v_max'):
        return _set(getattr(car, '_cfg', None), setting.name, value)  # BaseCar liest ihn je Fahrbefehl
    return False


def apply_live(application, values):
    """Setzt Werte in den laufenden Objekten (kein Hardwarezugriff). Liefert {key: live uebernommen?}."""
    applied = {}
    for key, value in values.items():
        try:
            applied[key] = bool(_apply_one(BY_KEY[key], value, application))
        except Exception:
            log.exception('Einstellung %s nicht uebernommen', key)
            applied[key] = False
    return applied


def active_values(application):
    """Aktuell wirksame Werte aus den laufenden Objekten (None = nicht ermittelbar)."""
    sensor, mode, car = getattr(application, 'sensor', None), _camera_mode(application), getattr(application, 'car', None)
    readers = {
        'cam_scan': lambda: int(sensor.scan_frequency),
        'ctl_scan': lambda: int(mode.poll_frequency),
        'lost_timeout': lambda: float(mode._lost_timeout),
        'steer_gain': lambda: float(mode._cfg.get_float('steering_gain', 2.0)),
        'v_min': lambda: int(car.v_min),
        'v_max': lambda: int(car.v_max),
    }
    result = {}
    for key, read in readers.items():
        try:
            result[key] = read()
        except Exception:
            result[key] = None
    return result


def snapshot(application, path=None):
    """Je Einstellung aktiver und gespeicherter Wert fuer die Anzeige."""
    active, stored = active_values(application), stored_values(path)
    return {s.key: {'active': active.get(s.key), 'stored': stored[s.key]['value'], 'state': stored[s.key]['state'],
                    'default': s.default} for s in SETTINGS}


def describe(setting, entry):
    """Statuszeile (Text, Stufe ok|warn|off) je Einstellung: aktiv, gespeichert, gilt ab naechstem Start."""
    unit = f' {setting.unit}' if setting.unit else ''
    active, stored = entry.get('active'), entry.get('stored')
    if active is None:
        return (f'nicht aktiv (kein Fahrbetrieb) · gespeichert {number_text(stored)}{unit}'
                if stored is not None else 'nicht aktiv (kein Fahrbetrieb)'), 'off'
    if entry.get('state') == 'invalid':
        return f'aktiv {number_text(active)}{unit} · gespeicherter Wert ungültig (Standard)', 'warn'
    if stored is None:
        return f'aktiv {number_text(active)}{unit} · nicht gespeichert (Standard {number_text(setting.default)})', 'ok'
    if stored == active:
        return f'aktiv {number_text(active)}{unit} · gespeichert', 'ok'
    return (f'aktiv {number_text(active)}{unit} · gespeichert {number_text(stored)}{unit} '
            f'(gilt ab nächstem Start)'), 'warn'
