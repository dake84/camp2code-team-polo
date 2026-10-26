"""Persistente Einstellungen fuer das experimentelle Pipeline Studio."""
import json
from pathlib import Path

PATH = Path(__file__).with_name('pipeline_studio.json')
DEFAULT = {
    'processor': 'copilot', 'crop': [0.1, 0.9],
    'hue': [110, 179], 'sat': [0, 255], 'val': [0, 255],
    'blur': 3, 'morph': 5, 'canny': [50, 100],
    'enabled': {key: True for key in ('roi', 'mask', 'blur', 'morph', 'canny', 'lanes')},
}

def load():
    """Fehlende Felder werden mit Standardwerten ergaenzt."""
    if not PATH.exists():
        return {**DEFAULT, 'enabled': dict(DEFAULT['enabled'])}
    data = json.loads(PATH.read_text(encoding='utf-8'))
    result = {**DEFAULT, **data}
    result['enabled'] = {**DEFAULT['enabled'], **data.get('enabled', {})}
    return result

def save(data):
    """Schreibt erst eine temporaere Datei und ersetzt dann atomar."""
    temp = PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(PATH)
