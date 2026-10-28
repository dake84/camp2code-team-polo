"""Baut das Control Center aus Shell und Tabs zusammen."""
import logging

from pipeline_ab import PipelineAB
from sim_lab import SimLab
from telemetry.thread_stats import ThreadCpuSampler

from .context import DashboardContext
from .helpers import uri
from .shell import Shell
from .tabs import default_tabs

log = logging.getLogger(__name__)


def build_app(application, components, tabs=None, name=None):
    """Erzeugt die Dash-Anwendung.

    ``components``: Dash-Bausteine (siehe context.py). ``tabs``: optionale Liste von Tab-Klassen;
    Standard sind die sechs Tabs aus ``tabs/__init__.py``. ``name``: Modulname fuer Dash (bestimmt den
    Ordner ``assets/``; Camp2CodeDashboard.py uebergibt seinen eigenen Namen, die Assets liegen im Projektroot).
    """
    sim = SimLab(application, components, uri)  # Cockpit-Simulation (Live | Simulation)
    context = DashboardContext(application, components, PipelineAB(), ThreadCpuSampler(), sim)
    tab_instances = [tab_class(context) for tab_class in tabs] if tabs else default_tabs(context)
    shell = Shell(context, tab_instances)
    app = components.Dash(name or 'Camp2CodeDashboard', external_stylesheets=[components.dbc.themes.DARKLY],
                          suppress_callback_exceptions=False, title='Camp2Code | Control Center',
                          update_title=None)
    # Funktion statt fertigem Baum: jeder Seitenaufruf baut das Layout neu und zeigt damit die aktuell
    # wirksamen Studio-Einstellungen (ein Neuladen im Browser darf keine alten Reglerwerte anwenden).
    app.layout = shell.layout
    app.c2c_layout = shell.layout
    shell.register_callbacks(app)
    for tab in tab_instances:
        tab.register_callbacks(app)
    app.c2c_tabs = tab_instances  # fuer Tests und Diagnose
    return app
