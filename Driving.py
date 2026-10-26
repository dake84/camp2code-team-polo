"""Kompatibilitaet mit bisherigen Aufrufern: import Driving.

Neue Implementierungen sollten direkt aus dem Package driving importieren.
Diese Datei enthaelt absichtlich keine eigene Fahrlogik mehr.
"""
from driving import *  # noqa: F401,F403
