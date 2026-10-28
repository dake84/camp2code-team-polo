"""Gemeinsame Abhaengigkeiten aller Dashboard-Tabs (Dependency Injection).

Die Dash-Bibliotheken werden NICHT hier importiert, sondern als ``components``
uebergeben (``Camp2CodeDashboard.py`` stellt sie bereit). Dadurch laeuft dieselbe
Tab-Implementierung mit echtem Dash und mit der Test-Attrappe (tests/dash_stub.py).

Benoetigte Attribute von ``components``:
    dbc, dcc, html, Input, Output, State, ALL, ctx, no_update, Patch (oder None), go, Dash
"""
import contextlib

import chart_kit
from help_texts import info as _info, label as _label
from replay_hud import legend_block

from .helpers import IMAGE_STYLE, uri


class DashboardContext:
    """Anwendung, Dash-Bausteine und kleine Widget-Helfer, die jeder Tab nutzt."""

    def __init__(self, application, components, ab, thread_cpu, sim):
        self.application = application
        self.car = application.car
        self.components = components
        self.ab = ab                  # PipelineAB (A/B-Labor, Replay -> Studio)
        self.thread_cpu = thread_cpu  # ThreadCpuSampler (Telemetrie)
        self.sim = sim                # SimLab (Cockpit-Simulation)
        self.uri = uri
        for name in ('dbc', 'dcc', 'html', 'Input', 'Output', 'State', 'ALL', 'ctx', 'no_update', 'Patch', 'go', 'ClientsideFunction'):  # PAKET-H1B
            setattr(self, name, getattr(components, name, None))

    # ------------------------------------------------------------- Callback-Kontext
    def triggered_ids(self):
        """IDs ALLER ausloesenden Komponenten (``ctx.triggered_id`` liefert nur die erste)."""
        return {str(t.get('prop_id', '')).split('.')[0] for t in (self.ctx.triggered or [])}

    # ------------------------------------------------------------- Messung
    def measure(self, name):
        """Misst die Renderzeit einer Ansicht (Telemetrie: ``dashboard.<name>``)."""
        timings = getattr(self.application, 'timings', None)
        return timings.measure(name) if timings is not None else contextlib.nullcontext()

    # ------------------------------------------------------------- Widgets
    def card(self, title, content, **kwargs):
        dbc = self.dbc
        return dbc.Card([dbc.CardHeader(title), dbc.CardBody(content)], className='h-100', **kwargs)

    def kpi(self, text, level):
        return self.html.Span(text, className={'ok': 'text-success', 'warn': 'text-warning',
                                               'bad': 'text-danger'}.get(level, 'text-secondary'))

    def fs_button(self, target):
        """Vollbild-Schalter (assets/fullscreen.js)."""
        return self.html.Button('⛶ Vollbild', type='button',
                                className='c2c-fs btn btn-sm btn-outline-info ms-2',
                                **{'data-target': target})

    def info(self, key, suffix=None):
        """ⓘ-Symbol mit Popover (Liste, in children einsetzen)."""
        return _info(key, self.dbc, self.html, suffix=suffix)

    def help_label(self, text, key, className='c2c-kpi-label', suffix=None):
        return _label(text, key, self.dbc, self.html, className, suffix)

    def legend(self):
        return legend_block(self.html)

    def chart_block(self, graph_id, height=260, hint='Noch keine Daten', note=None, frame_bar=None):
        """Diagramm mit fester Hoehe (style.height), Vollbild-Leiste als Geschwister (chart_kit)."""
        return chart_kit.chart_block(self.dcc, self.html, graph_id, height, hint, note, frame_bar)  # PAKET-H1B

    @staticmethod
    def empty_figure(text='Noch keine Daten'):
        return chart_kit.empty_figure(text)

    image_style = IMAGE_STYLE
