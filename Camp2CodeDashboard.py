"""Camp2Code Control Center: Startpunkt der Dash-Anwendung (Version 6.7).

Der Aufbau steckt im Paket ``dashboard/``: eine Shell (Steuerleiste, Kennzahlen, Bergung) und je Reiter
eine Tab-Klasse (``dashboard/tabs/``: Driving, Pipeline, Telemetry, Recording, Replay, Settings).
Diese Datei liefert nur die Dash-Bausteine, erzeugt die Anwendung und startet den Server.

Grundsaetze (unveraendert seit 6.0):
    * Aktionen (Start, STOP, Bergung, Aufzeichnung) laufen ueber genau einen Callback und die
      oeffentlichen Methoden von ``CarApplication``.
    * Alle Anzeigen werden aus dem tatsaechlichen Anwendungszustand abgeleitet.
    * Jede Ansicht aktualisiert sich nur, wenn ihr Tab sichtbar ist.
    * Das Dashboard erkennt keine Linien und rechnet keine Fahrentscheidungen; es zeichnet nur
      vorhandene Daten auf Bildkopien.

Start: ``python Camp2CodeDashboard.py [--offline] [--port N]``
"""
import atexit
import logging
import os
import signal
import sys
from types import SimpleNamespace

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update

try:  # Teilaktualisierung des Diagramms (Dash >= 2.9)
    from dash import Patch
except ImportError:  # Dash-Attrappe in den Tests
    Patch = None
try:  # PAKET-H1B: clientseitige Callbacks (Frame-Balken)
    from dash import ClientsideFunction
except ImportError:  # Dash-Attrappe ohne diese Klasse
    ClientsideFunction = None

try:  # ohne Raspberry-Pi-Bibliotheken (Laptop) nur mit --offline
    from car_application import CarApplication
    _HARDWARE_ERROR = None
except (ImportError, RuntimeError, OSError) as _exc:
    CarApplication = None
    _HARDWARE_ERROR = _exc

from dashboard.app import build_app
from dashboard.helpers import parse_jump  # noqa: F401  (Kompatibilitaet: Tests und Skripte)

log = logging.getLogger(__name__)

# Dash-Bausteine fuer die Tabs. Das Paket ``dashboard`` importiert Dash nie selbst; so laeuft es mit echtem
# Dash und mit der Test-Attrappe (tests/dash_stub.py).
COMPONENTS = SimpleNamespace(Dash=Dash, dbc=dbc, dcc=dcc, html=html, go=go, Input=Input, Output=Output,
                             State=State, ALL=ALL, ctx=ctx, no_update=no_update, Patch=Patch,
                             ClientsideFunction=ClientsideFunction)


def create_app(application, tabs=None):
    """Erzeugt das Control Center fuer ``application`` (``tabs``: optional eigene Tab-Klassen)."""
    return build_app(application, COMPONENTS, tabs=tabs, name=__name__)


def _port_from_argv(default):
    """``--port N`` (z. B. Laptop-Offline parallel zu einem anderen Dienst)."""
    if '--port' in sys.argv:
        index = sys.argv.index('--port')
        try:
            return int(sys.argv[index + 1])
        except (IndexError, ValueError):
            raise SystemExit('--port erwartet eine Zahl')
    return default


def main():
    logging.basicConfig(level=logging.INFO)
    # Jede Dash-Anfrage wuerde sonst eine Logzeile erzeugen (ca. 10 pro Sekunde).
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    # Fahrbefehls-Meldungen ("Changed speed ...") nur mit C2C_LOG=INFO oder DEBUG
    _level = getattr(logging, os.environ.get('C2C_LOG', 'WARNING').upper(), logging.WARNING)
    for _name in ('BaseCar', 'SonicCar', 'SensorCar', 'CameraCar', 'picamera2'):
        logging.getLogger(_name).setLevel(_level)
    offline = '--offline' in sys.argv
    if offline:
        from offline_application import OfflineApplication
        application = OfflineApplication()
        log.warning('OFFLINE-Modus: keine Kamera, keine Motoren')
    elif CarApplication is None:
        raise SystemExit(f'Hardware-Module nicht ladbar ({_HARDWARE_ERROR}). Ohne Hardware starten: --offline')
    else:
        application = CarApplication()
    atexit.register(application.shutdown)

    def _terminate(signum, _frame):
        # SIGTERM (z. B. systemd, kill) beendet sonst den Prozess ohne Motorstopp.
        log.warning('Signal %s empfangen, beende Anwendung', signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _terminate)
    try:
        create_app(application).run(host='127.0.0.1' if offline else '0.0.0.0',
                                    port=_port_from_argv(8050), debug=False, use_reloader=False)
    finally:
        application.shutdown()


if __name__ == '__main__':
    main()
