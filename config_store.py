"""Dauerhafte Einstellungen in config.json (Paket H1).

Gespeichert werden (Namensraeume wie bei ``ConfigReader``):

    cameracar_controller: speed_mode, speed_fixed, speed_vmin, speed_vmax
    session_recorder:     jpeg_quality

Sicherheitsregeln:
    * Geschrieben wird nur, wenn ``config.json`` existiert UND erfolgreich gelesen
      werden konnte. Die Datei wird nie neu angelegt (sie liegt nicht im Git).
    * Vor jeder Aenderung entsteht eine Sicherungskopie in ``Data_AI/config_backups``
      (die letzten ``KEEP_BACKUPS`` bleiben erhalten).
    * Atomares Schreiben: temporaere Datei im selben Ordner, ``fsync``,
      ``os.replace``, danach ``fsync`` des Ordners.
    * Laden darf nie scheitern: fehlerhafte oder fehlende Werte ergeben die
      Standardwerte (Geschwindigkeit fest 30, JPEG-Qualitaet 95).

Hinweis: ``ConfigReader._save_config`` schreibt seinen eigenen Speicherstand. Ein anderer
Leser, der danach speichert (z. B. die IR-Kalibrierung), wuerde diese Schluessel verlieren.
Im Dashboard-Betrieb speichert kein ConfigReader.
"""
import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from speed_policy import MAX_ALLOWED, MIN_MOVING, MODES, SpeedPolicy

log = logging.getLogger(__name__)

CONFIG_FILE = 'config.json'
BACKUP_DIR = 'Data_AI/config_backups'
KEEP_BACKUPS = 5
SPEED_NAMESPACE = 'cameracar_controller'
RECORDER_NAMESPACE = 'session_recorder'
DEFAULT_JPEG_QUALITY = 95
JPEG_RANGE = (50, 100)

_LOCK = threading.Lock()


class ConfigStoreError(Exception):
    """Konfiguration nicht lesbar oder nicht schreibbar (es wurde nichts veraendert)."""


def read(path=None):
    """Liest die Konfiguration als dict. ``ConfigStoreError`` bei fehlender/defekter Datei."""
    path = Path(path if path is not None else CONFIG_FILE)
    try:
        with path.open('r', encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigStoreError(f'{path} nicht vorhanden') from exc
    except (OSError, ValueError) as exc:
        raise ConfigStoreError(f'{path} nicht lesbar: {exc}') from exc
    if not isinstance(data, dict):
        raise ConfigStoreError(f'{path} enthaelt kein JSON-Objekt')
    return data


def _fsync_directory(directory):
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # z. B. Windows
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _backup(path, backup_dir):
    """Kopiert die gelesene Datei nach ``backup_dir`` und behaelt die letzten KEEP_BACKUPS."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    target = backup_dir / f'config_{stamp}.json'
    shutil.copyfile(path, target)
    old = sorted(p for p in backup_dir.glob('config_*.json') if p.is_file())
    for stale in old[:-KEEP_BACKUPS]:
        try:
            stale.unlink()
        except OSError:
            log.warning('Alte Sicherung nicht entfernt: %s', stale)
    return target


def update(changes, path=None, backup_dir=None):
    """Schreibt ``{namespace: {schluessel: wert}}`` in die bestehende config.json.

    Reihenfolge: lesen (muss gelingen) -> Sicherung -> aendern -> atomar schreiben.
    Bei jedem Fehler bleibt die Originaldatei unveraendert (``ConfigStoreError``).
    """
    path = Path(path if path is not None else CONFIG_FILE)
    backup_dir = backup_dir if backup_dir is not None else BACKUP_DIR
    with _LOCK:
        data = read(path)
        for namespace, values in changes.items():
            section = data.setdefault(namespace, {})
            if not isinstance(section, dict):
                raise ConfigStoreError(f'Abschnitt {namespace} ist kein Objekt')
            section.update(values)
        try:
            _backup(path, backup_dir)
        except OSError as exc:
            raise ConfigStoreError(f'Sicherung nicht moeglich: {exc}') from exc
        temp_name = None
        try:
            fd, temp_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent or '.'))
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                # Gleiches Format wie ConfigReader._save_config (kein Diff-Rauschen).
                json.dump(data, handle, indent=4, ensure_ascii=True, sort_keys=True)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            try:
                shutil.copymode(path, temp_name)
            except OSError:
                pass
            os.replace(temp_name, path)
            temp_name = None
            _fsync_directory(path.parent)
        except (OSError, TypeError, ValueError) as exc:
            raise ConfigStoreError(f'Schreiben fehlgeschlagen: {exc}') from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
    return path


# ------------------------------------------------------------------ Geschwindigkeit
def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def policy_to_values(policy):
    """SpeedPolicy -> Schluessel fuer config.json."""
    return {'speed_mode': policy.mode, 'speed_fixed': policy.fixed_speed,
            'speed_vmin': policy.v_min, 'speed_vmax': policy.v_max}


def policy_from_values(section):
    """Strikt: ungueltige Werte -> ValueError (Aufrufer faellt auf fest 30 zurueck)."""
    mode = section.get('speed_mode', 'fixed')
    fixed = section.get('speed_fixed', 30)
    vmin = section.get('speed_vmin', 30)
    vmax = section.get('speed_vmax', 45)
    if mode not in MODES:
        raise ValueError(f'speed_mode {mode!r} unbekannt')
    for name, value in (('speed_fixed', fixed), ('speed_vmin', vmin), ('speed_vmax', vmax)):
        if not _is_int(value) or not MIN_MOVING <= value <= MAX_ALLOWED:
            raise ValueError(f'{name} {value!r}: ganze Zahl {MIN_MOVING}..{MAX_ALLOWED} erwartet')
    return SpeedPolicy(mode, fixed, vmin, vmax)


def load_speed_policy(path=None):
    """Gespeicherte Strategie oder fest 30. Wirft nie eine Ausnahme."""
    default = SpeedPolicy()
    try:
        section = read(path).get(SPEED_NAMESPACE, {})
        if not isinstance(section, dict) or not any(k.startswith('speed_') for k in section):
            return default
        return policy_from_values(section)
    except ConfigStoreError as exc:
        log.info('Geschwindigkeit: keine gespeicherte Konfiguration (%s) - fest 30', exc)
    except Exception as exc:  # defekte Werte duerfen den Start nie verhindern
        log.warning('Geschwindigkeit: Konfiguration ungueltig (%s) - Rueckfall auf fest 30', exc)
    return default


def save_speed_policy(policy, path=None, backup_dir=None):
    return update({SPEED_NAMESPACE: policy_to_values(policy)}, path, backup_dir)


# ------------------------------------------------------------------ JPEG-Qualitaet
def load_jpeg_quality(path=None):
    """Gespeicherte JPEG-Qualitaet (50..100) oder 95. Wirft nie eine Ausnahme."""
    try:
        section = read(path).get(RECORDER_NAMESPACE, {})
        if isinstance(section, dict) and 'jpeg_quality' in section:
            value = section['jpeg_quality']
            if _is_int(value) and JPEG_RANGE[0] <= value <= JPEG_RANGE[1]:
                return value
            log.warning('JPEG-Qualitaet %r ungueltig - Rueckfall auf %d', value, DEFAULT_JPEG_QUALITY)
    except ConfigStoreError:
        pass
    except Exception as exc:
        log.warning('JPEG-Qualitaet nicht lesbar (%s) - Rueckfall auf %d', exc, DEFAULT_JPEG_QUALITY)
    return DEFAULT_JPEG_QUALITY


def save_jpeg_quality(quality, path=None, backup_dir=None):
    quality = int(quality)
    if not JPEG_RANGE[0] <= quality <= JPEG_RANGE[1]:
        raise ValueError(f'JPEG-Qualitaet {JPEG_RANGE[0]}-{JPEG_RANGE[1]}')
    return update({RECORDER_NAMESPACE: {'jpeg_quality': quality}}, path, backup_dir)
