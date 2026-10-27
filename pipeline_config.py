"""Persistente Einstellungen fuer das Pipeline Studio.

``pipeline_studio.json`` ist die aktive Konfiguration, die der CameraSensor
beim Start laedt. Benannte Presets liegen zusaetzlich in ``pipeline_presets/``.
"""
import json
import re
from pathlib import Path

PATH = Path(__file__).with_name('pipeline_studio.json')
PRESET_DIR = Path(__file__).with_name('pipeline_presets')
DEFAULT = {
    'processor': 'copilot', 'crop': [0.1, 0.9],
    'hue': [110, 179], 'sat': [0, 255], 'val': [0, 255],
    'blur': 3, 'morph': 5, 'canny': [50, 100],
    'enabled': {key: True for key in ('roi', 'mask', 'blur', 'morph', 'canny', 'lanes')},
}
_NAME = re.compile(r'^[A-Za-z0-9_\-]{1,40}$')


def _merge(data):
    result = {**DEFAULT, **data}
    result['enabled'] = {**DEFAULT['enabled'], **data.get('enabled', {})}
    return result


def load(path=None):
    """Fehlende Felder werden mit Standardwerten ergaenzt."""
    path = Path(path) if path is not None else PATH
    if not path.exists():
        return {**DEFAULT, 'enabled': dict(DEFAULT['enabled'])}
    return _merge(json.loads(path.read_text(encoding='utf-8')))


def save(data, path=None):
    """Schreibt erst eine temporaere Datei und ersetzt dann atomar."""
    path = Path(path) if path is not None else PATH
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(path)


def validate_preset_name(name):
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError('Preset-Name: 1-40 Zeichen, nur Buchstaben, Ziffern, _ und -')
    return name


def list_presets(directory=None):
    directory = Path(directory) if directory is not None else PRESET_DIR
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob('*.json'))


def save_preset(name, data, directory=None):
    directory = Path(directory) if directory is not None else PRESET_DIR
    directory.mkdir(parents=True, exist_ok=True)
    save(data, directory / f'{validate_preset_name(name)}.json')


def load_preset(name, directory=None):
    directory = Path(directory) if directory is not None else PRESET_DIR
    path = directory / f'{validate_preset_name(name)}.json'
    if not path.is_file():
        raise ValueError(f'Preset {name} nicht gefunden')
    return load(path)
