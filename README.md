# Camp2Code Team Polo – Control Center 6.0

Autonom fahrendes Modellauto auf Raspberry Pi: Kamera → Linienerkennung → CameraMode → Lenkung/Antrieb,
bedient über **eine** Dash-Anwendung mit fünf Ansichten.

* Architektur: [ARCHITECTURE.md](ARCHITECTURE.md)
* Änderungen: [CHANGELOG.md](CHANGELOG.md)
* Aufräumen: [CLEANUP_REPORT.md](CLEANUP_REPORT.md), [DELETE_MANIFEST.txt](DELETE_MANIFEST.txt)
* Messwerte: [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md)

## Installation des Release-Pakets

```bash
cd ~/camp2code-team-polo
git add -A && git commit -m "Stand vor 6.0"          # Sicherungspunkt
unzip -o ~/camp2code_architecture_cleanup.zip -d .    # überschreibt nur enthaltene Dateien
python -m unittest discover -s tests -p 'test_*.py'   # hardwarefrei, ohne Motoransteuerung
scripts/cleanup_obsolete.sh                           # Vorschau obsoleter Dateien
scripts/cleanup_obsolete.sh --apply                   # Backup + Entfernen (optional)
git rm -r --cached Data_AI                            # Aufzeichnungen nicht mehr versionieren (optional)
```

Das Entpacken löscht keine Dateien. Gelöscht wird ausschließlich über `scripts/cleanup_obsolete.sh`.

## Start

```bash
python Camp2CodeDashboard.py      # http://<pi>:8050
```

Nur **eine** Instanz starten. Legacy-Dashboards (`legacy/dashboards/`) und `Dashboard_LiveData.py`
öffnen eigene Hardware und denselben Port – nie parallel betreiben. Legacy-Start nur aus dem Root:
`python -m legacy.dashboards.CameraCar_DashLive`.

## Bedienung

| Bereich | Funktion |
|---|---|
| Steuerleiste (immer sichtbar) | Kamera-/Fahr-/REC-Status, Frame-ID, Fahrmodus, **START**, **STOP**, Bergung, REC |
| Driving Cockpit | Livebild mit Linien und Reglerrichtung, Stellwert, Soll-/Rohwinkel, Speed-Steuerwert, Diagnose |
| Pipeline Studio | 8 Stufen mit Reglern, Freeze, A/B-Vergleich, Presets (nur im Stillstand änderbar) |
| Telemetrie & Performance | Soll/Ist-Frequenzen, Verläufe, Linienanzahl, Datenalter, Latenzen, CPU/RAM |
| Recording & Events | Aufzeichnungsstatus, Zähler, Ereignisprotokoll |
| Replay Lab | Session/Ringbuffer, Timeline, Klick-Navigation, Play/Pause, Ereignissprung mit Vor-/Nachlauf, Export |

Bergung: „Bergung aktivieren“, dann **Leertaste halten** + W/S/A/D. Ohne Tastensignal stoppt der
Watchdog nach 0,35 s. Autonomer Fahrmodus und Bergung schließen sich gegenseitig aus.

## Tests

```bash
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py'
python tools/benchmark.py        # Richtwerte für den jeweiligen Rechner
```

Alle Tests laufen ohne Hardware: `tests/hardware_stubs.py` ersetzt `basisklassen`/`basisklassen_cam`
durch Attrappen, die nur protokollieren. `tests/test_dashboard_real_dash.py` wird ohne installiertes Dash
übersprungen.

## Pflichtprüfung auf dem Pi (nicht automatisiert, bisher nicht durchgeführt)

**Fahrzeug aufbocken.** Erst danach im abgesperrten Bereich fahren.

1. Dashboard starten, Telemetrie: Kamera-Ist-Frequenz notieren (Ziel 25 Hz, vorher ~15 Hz).
2. START mit abgedeckter Kamera → Räder drehen bis zu `lost_line_timeout` (1 s) geradeaus, dann Stopp
   `LANE_TIMEOUT` (unverändertes Schutzverhalten).
3. START mit Linien: Räder drehen mit Speed-Steuerwert 30; Lenkung folgt; STOP stoppt sofort.
4. Kameraausfall (`CAMERA_STALE`) und fehlendes erstes Bild (`NO_CAMERA_DATA`) sind nur automatisiert
   getestet. Das Kamerakabel nie im Betrieb trennen.
5. Bergung: Deadman loslassen → Stopp ≤ 0,35 s; Browser-Tab schließen → Stopp.
6. REC während der Fahrt → `frames.csv`: überwiegend `complete`, `missing_decision` selten; Replay-HUD-Linien liegen auf der Fahrbahn.
7. Dropdowns und Zahlenfelder im Dark Theme lesbar; Browser-Neustart im regulären Profil mit mehreren alten Tabs.
8. Bei verändertem Fahrverhalten durch die höhere Kamerarate: `cam_sensor.scan_frequency` auf 15 setzen.

## Sicherheit

Das Browser-Dashboard ist kein zertifizierter Not-Aus. Ein Verbindungsverlust beendet einen **autonomen**
Fahrmodus nicht automatisch (nur die Bergung hat einen Watchdog). Physischen Abschalter bereithalten.

## Bekannte Grenzen

* IR- und Ultraschallsensoren werden im Dashboard-Betrieb nicht gestartet; Sensorspalten bleiben leer.
* Kamerathread wird nach einem Kamerafehler nicht automatisch neu gestartet (Neustart der Anwendung).
* Der Live-Ringbuffer (60 Frames) ist gleitend; für lange Analysen Sessions aufzeichnen.
* Bergung und A/B-Referenz sind nicht für mehrere gleichzeitige Browser ausgelegt.
* Dash-Version auf dem Pi ist nicht in `requirements.txt` erfasst; getestet mit Dash 4.4 (Sandbox).
