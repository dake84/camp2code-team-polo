"""Basisklasse fuer einsteckbare Dashboard-Tabs.

Ein Tab ist ein Plug-in: Er liefert sein Layout und registriert seine Callbacks.
``dashboard/app.py`` steckt alle Tabs aus ``tabs/__init__.py`` in die Shell
(Steuerleiste, Kennzahlen, ``dbc.Tabs``). Ein neuer Tab braucht nur eine neue
Klasse und einen Eintrag in ``default_tabs``.

Regeln:
    * ``tab_id`` ist eindeutig und zugleich der Schluessel in ``main-tabs.active_tab``.
    * Alle IDs der Komponenten eines Tabs gehoert diesem Tab; ein Output hat genau einen
      Besitzer. Tabuebergreifende Callbacks stehen im Besitzer-Tab und sind dort markiert.
    * Ansichten rechnen nur, wenn ihr Tab sichtbar ist (``active_tab``). Mit
      ``poll_interval_ms`` bekommt der Tab einen eigenen Takt ``tick-<tab_id>``,
      den die Shell nur fuer den sichtbaren Tab aktiviert.
"""
from abc import ABC, abstractmethod


class DashboardTab(ABC):
    tab_id: str = ''
    label: str = ''
    poll_interval_ms = 700       # None = kein eigener Takt (reiner Formular-Tab)

    def __init__(self, context):
        self.ctx = context

    @abstractmethod
    def layout(self):
        """Inhalt des Tabs (Dash-Komponente)."""

    @abstractmethod
    def register_callbacks(self, app):
        """Registriert alle Callbacks dieses Tabs an ``app``."""

    @property
    def tick_id(self):
        return f'tick-{self.tab_id}'

    def tab_component(self):
        return self.ctx.dbc.Tab(self.layout(), label=self.label, tab_id=self.tab_id)

    def interval_component(self, active=False):
        """Eigener Takt des Tabs. ``active`` = Starttab; danach schaltet die Shell je nach sichtbarem Tab."""
        if self.poll_interval_ms is None:
            return None
        return self.ctx.dcc.Interval(id=self.tick_id, interval=self.poll_interval_ms, n_intervals=0,
                                     disabled=not active)
