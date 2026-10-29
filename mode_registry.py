"""Register der Fahrmodi: ein Eintrag je Modus statt Dict in CarApplication und Liste im Dashboard."""
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class ModeSpec:
    key: str
    label: str
    factory: Callable      # factory(car=car) -> DrivingMode
    order: int = 100       # kleiner = weiter oben im Dropdown
    kind : str = 'manual'
    short: str = ''

KINDS = ('auto', 'manual')
_MODES: dict[str, ModeSpec] = {}


def _identity(factory):
    return getattr(factory, '__module__', None), getattr(factory, '__qualname__', None)


def register(key, label, order=100, kind='manual', short=''):
    if kind not in KINDS:
        raise ValueError(f'Fahrmodus {key!r}: kind muss {KINDS} sein, nicht {kind!r}')
    def decorator(factory):
        existing = _MODES.get(key)
        if existing is not None and _identity(existing.factory) != _identity(factory):
            raise ValueError(f'Fahrmodus {key!r} ist bereits registriert')
        _MODES[key] = ModeSpec(key, label, factory, order, kind, short or label)
        return factory
    return decorator

def specs():
    return tuple(sorted(_MODES.values(), key=lambda s: (s.order, s.key)))

def kind_of(key):
    spec = _MODES.get(key)
    return spec.kind if spec is not None else 'manual'

def short_label(key):
    spec = _MODES.get(key)
    return spec.short if spec is not None else (str(key) if key else None)

def unregister(key):   # nur für Tests
    _MODES.pop(key, None)


def dropdown_options():
    return [{'label': spec.label, 'value': spec.key} for spec in specs()]


def create_all(car):
    return {spec.key: spec.factory(car=car) for spec in specs()}