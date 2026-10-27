# Camp2Code V5.5: Replay HUD und Ereignisprotokoll

## Datenarchitektur

**Beides:** `frames.csv` ist die tabellarische ML-Grundlage und enthält `lanes_json` mit den vom Kameradatenpaket gelieferten Linienkoeffizienten und `event_codes` für Ereignisse, die beim Schreiben bereits vorlagen. `events.csv` ist die vollständiger gedachte, getrennte Ereignis-Zeitachse mit `timestamp_ns`, `frame_id`, `code` und `detail`. Diese Trennung verhindert mehrfach wiederholte Ereignisbeschreibungen in jeder Frame-Zeile und ermöglicht spätere Ereignisfenster-Exporte.

`events.csv` wird nur während einer laufenden Session aus `DrivingHealthMonitor.events()` übernommen. Der Monitor besitzt eine begrenzte Historie: bei sehr hoher Ereignisrate können Ereignisse vor dem nächsten Recorder-Tick verloren gehen. `event_codes` in `frames.csv` ist daher nur ein Hinweis und **keine** vollständige Eventliste. Spät eintreffende Ereignisse erscheinen in `events.csv`, nicht rückwirkend in der Frame-Zeile.

## Replay HUD

Gespeicherte Rohbilder werden erst beim Anzeigen mit dem HUD überlagert. Es zeigt Frame-ID, Speed-Steuerwert, Stellwinkel, Datenstatus, gespeicherte Linien und ein gelbes Richtungsinstrument. Bei zwei verfügbaren Linien beginnt die gelbe Linie in der Mitte ihrer unteren Bildpunkte. Die Neigung folgt dem gespeicherten Stellwinkel; sie ist **keine geometrisch berechnete Solltrajektorie** und keine physische Servorückmeldung.

**Wichtig:** Die Zeichnung der Linienkoeffizienten setzt `x = m*y + b` voraus. Vor dem Einsatz die Konvention des tatsächlich installierten Linienprozessors prüfen. Bei abweichender Definition muss ausschließlich `replay_hud.py` angepasst werden. Fehlende Linien oder Stellwinkel werden nicht erfunden. Die ursprünglichen JPEGs bleiben unverändert.

## Installation

Im Projektroot entpacken: `unzip -o camp2code_dashboard_v55.zip`. Vorher Git-Checkpoint und Sicherung erstellen. Start: `python Camp2CodeDashboard.py`.

Tests: `python -m compileall -q Camp2CodeDashboard.py session_recorder.py replay_hud.py` und `python -m unittest discover -s tests -p 'test_replay_hud.py'`.

## Grenzen

Bestehende Sessions aus V5.4 haben noch keine `lanes_json`-Spalte und kein `events.csv`; das HUD zeigt dort nur die verfügbaren Felder. Kein Pi-/Dash-Integrationstest durchgeführt. Keine Änderung an Fahrregelung, Speed oder Hardware-Lifecycle.
