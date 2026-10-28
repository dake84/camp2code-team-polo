"""Camp2Code Control Center als Plug-in-Architektur.

    shell.py    Steuerleiste, Kennzahlen, Bergung, Einstecken der Tabs
    tabs/       je Reiter eine Klasse (Layout + Callbacks): Driving, Pipeline, Telemetry,
                Recording, Replay, Settings
    context.py  gemeinsame Abhaengigkeiten (Anwendung, Dash-Bausteine, Widget-Helfer)
    helpers.py  reine Hilfsfunktionen

Der Start erfolgt wie bisher ueber ``python Camp2CodeDashboard.py``.
"""
