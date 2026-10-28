"""Zentrales Verzeichnis aller Bildprozessoren (Punkt 1 aus Paket F).

Ein neuer Prozessor wird nur noch hier eingetragen (oder per ``register``).
Alle Verbraucher - CameraSensor, Pipeline Studio, A/B-Labor, Replay, Simulation
und ``scripts/sim_report.py`` - lesen aus diesem Verzeichnis.

``live=True`` bedeutet: Der Prozessor darf im Fahrbetrieb verwendet werden.
Prozessoren mit ``live=False`` (z. B. der Mock) erscheinen nur in der
Simulation und werden von ``CameraSensor.set_processor`` abgewiesen.

Die Klassen werden erst bei ``create_processor`` importiert. Dadurch bleibt
das Verzeichnis leichtgewichtig und Tests koennen Module austauschen.
"""
import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessorSpec:
    key: str
    label: str
    module: str
    class_name: str
    live: bool = True


_SPECS = {}


def register(key, label, module, class_name, live=False):
    """Traegt einen Prozessor ein. Neue Prozessoren sind standardmaessig nur fuer die Simulation."""
    if not key or not isinstance(key, str):
        raise ValueError('Prozessor-Schluessel fehlt')
    _SPECS[key] = ProcessorSpec(key, label, module, class_name, bool(live))
    return _SPECS[key]


register('copilot', 'Mit Diagnosebildern (Copilot)', 'pipeline_processors', 'CopilotLaneProcessor', live=True)
register('legacy', 'Ohne Diagnosebilder (Legacy)', 'pipeline_processors', 'LaneProcessor', live=True)
register('opus', 'Modus Opus (dunkle Linie)', 'pipeline_processors', 'OpusLaneProcessor', live=True)
register('mock', 'MOCK · Testprozessor (nur Simulation)', 'mock_processor', 'MockProcessor', live=False)


def specs(live_only=False):
    return [spec for spec in _SPECS.values() if spec.live or not live_only]


def processor_options(live_only=False):
    """Optionen fuer Dash-Dropdowns (``label``/``value``)."""
    return [{'label': spec.label, 'value': spec.key} for spec in specs(live_only)]


def is_live_processor(key):
    spec = _SPECS.get(key)
    return spec is not None and spec.live


def create_processor(key):
    """Erzeugt eine neue Instanz. Unbekannte Schluessel fuehren zu ``ValueError``."""
    spec = _SPECS.get(key)
    if spec is None:
        raise ValueError(f'Unbekannter Prozessor: {key}')
    return getattr(importlib.import_module(spec.module), spec.class_name)()


def key_for_class(name):
    """Klassenname (z. B. aus frames.csv) oder Schluessel -> Schluessel.

    Unbekannte Namen ergeben wie bisher ``'copilot'`` (Verhalten von
    ``replay_studio.processor_key`` vor Paket F).
    """
    if name in _SPECS:
        return name
    for spec in _SPECS.values():
        if spec.class_name == name:
            return spec.key
    return 'copilot'


def label_for(key):
    spec = _SPECS.get(key)
    return spec.label if spec is not None else str(key)



def processor_class(key):
    """Klasse eines Prozessors (ohne Instanz). Unbekannte Schluessel fuehren zu ``ValueError``."""
    spec = _SPECS.get(key)
    if spec is None:
        raise ValueError(f'Unbekannter Prozessor: {key}')
    return getattr(importlib.import_module(spec.module), spec.class_name)


def processor_stages(key):
    """Stufenbeschreibung (``PipelineStage``) eines Prozessors fuer das Pipeline Studio.

    Das Dashboard erzeugt Karten und Regler ausschliesslich aus dieser Beschreibung.
    Prozessoren ohne ``dashboard_stages()`` zeigen nur Rohbild und Linien.
    """
    cls = processor_class(key)
    describe = getattr(cls, 'dashboard_stages', None)
    if describe is not None:
        return tuple(describe())
    from pipeline_processors import PipelineStage, STAGE_OVERLAY, STAGE_RAW
    return (PipelineStage(STAGE_RAW, 'Rohbild'), PipelineStage(STAGE_OVERLAY, 'Linien', 'lanes'))
