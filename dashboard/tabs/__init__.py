"""Die Tabs des Control Centers.

Ein neuer Tab: Klasse von ``DashboardTab`` ableiten (Layout + Callbacks) und hier eintragen.
Die Reihenfolge entspricht der Reihenfolge der Reiter; der erste Tab ist der Starttab.
"""
from .base import DashboardTab
from .driving import DrivingTab
from .pipeline import PipelineTab
from .recording import RecordingTab
from .replay import ReplayTab
from .settings import SettingsTab
from .telemetry import TelemetryTab

DEFAULT_TABS = (DrivingTab, PipelineTab, TelemetryTab, RecordingTab, ReplayTab, SettingsTab)


def default_tabs(context):
    """Erzeugt die Standard-Tabs mit dem gemeinsamen Kontext."""
    return [tab_class(context) for tab_class in DEFAULT_TABS]


__all__ = ['DashboardTab', 'DEFAULT_TABS', 'default_tabs', 'DrivingTab', 'PipelineTab', 'TelemetryTab',
           'RecordingTab', 'ReplayTab', 'SettingsTab']
