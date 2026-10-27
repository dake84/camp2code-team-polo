# Cleanup-Report Version 6.0

Grundlage: statische Analyse aller Projektdateien (AST-Importgraph, Aufrufer, Konfiguration,
Startskripte, Tests). Werkzeug: `tests/test_static_checks.py`. Hardware- und Browsertests wurden **nicht**
durchgeführt.

Nicht im Export enthalten und daher **nicht geprüft**: `scripts/pack-files.sh`, `scripts/unpack-files.sh`,
`todo`, `.gitmodules`, `config.json.example`. Das Cleanup-Skript prüft vor dem Löschen zusätzlich, ob ein
Dateiname noch in `.py`, `.sh` oder `.json` erwähnt wird.

## 1. Technische Schulden – Befunde

Legende: **N** = nachgewiesen (Code/Test/Screenshot) · **V** = begründeter Verdacht · **O** = Optimierungsvorschlag

| # | Befund | Art | Status 6.0 |
|---|---|---|---|
| 1 | Replay-HUD zeichnete Linien als `x = m·y + b` im Vollbild; LaneDetector liefert `y = m·x + b` im ROI | N (Test) | behoben, gemeinsame Zeichenfunktion |
| 2 | `CameraMode` wertete denselben Frame bei 500 Hz bis zu ~32-mal aus und sendete jedes Mal Fahrbefehle | N (Messung) | behoben, 1 Entscheidung je Frame |
| 3 | Kamera ausgefallen → Fahrzeug fuhr mit letztem Winkel weiter | N (Code) | `CAMERA_STALE` |
| 4 | Ohne Kamerabild (`frame_id = -1`) fuhr CameraMode mit 90°/Speed 30 los | N (Code) | `NO_CAMERA_DATA`, kein Fahrbefehl |
| 5 | `BaseCar` schrieb Servo/PWM außerhalb des Locks; parallele Befehle verschränkten sich | N (Test: 3 parallele Zugriffe) | Lock um alle Hardwarebefehle |
| 6 | Recorder schrieb Frames teils vor der Fahrentscheidung → dauerhaft „fehlend“ | N (Code) | Wartefrist + `missing_decision` |
| 7 | Zwei Recorder (`SaveData_4_AI` in `CarApplication`, `SessionRecorder` im Dashboard) | N | ein Recorder in `CarApplication` |
| 8 | Gleiche Stoppursache in späterer Fahrt nicht protokolliert (z. B. zweiter `LANE_TIMEOUT`) | N (Code) | `mark_start()` |
| 9 | Kamera-Ist 15 Hz statt Soll 25 Hz: Wartezeit ohne Abzug der Verarbeitungszeit | N (Code + Screenshot) | Periode kompensiert – **Fahrverhalten prüfen** |
| 10 | Kamerazeitstempel nach der Verarbeitung gesetzt | N (Code) | Zeitstempel nach Aufnahme + `processing_ns` |
| 11 | Kalibrier-Overlay zeigte erfundene Werte „Winkel 90 Speed 0“ | N (Screenshot) | entfernt |
| 12 | Cockpit zeigte während der Fahrt oft „KALIBRIERUNG“, weil der neueste Frame noch keine Entscheidung hatte | N (Code) | neuester vollständiger Frame |
| 13 | 8 Dashboard-Callbacks pro Takt unabhängig vom Tab (+ Bergungspuls 8/s dauerhaft) | N (Code) | 1 + aktive Ansicht, Puls nur bei Bergung |
| 14 | Replay rendert JPEG und Diagramm alle 700 ms auch im verborgenen Tab; Session-CSV bis 4× je Takt gelesen | N (Code) | Tab-gesteuert, CSV-Cache |
| 15 | Browser-Neustart-Problem im regulären Profil | V | Polling reduziert, verborgene Tabs pausieren; siehe PERFORMANCE_REPORT |
| 16 | Statusmeldung „Fahrmodus gestartet“ blieb nach `LANE_TIMEOUT` stehen | N (Screenshot) | Status aus `CarApplication` |
| 17 | Dropdowns/Zahlenfelder im Dark Theme unlesbar (weiß auf weiß) | N (Screenshot) | CSS – **auf Pi prüfen** |
| 18 | Bergung sendete Fahrbefehle direkt aus dem Dashboard-Callback | N (Code) | Watchdog-Thread sendet |
| 19 | Wettlauf Bergung ↔ Start (getrennte Locks) | V (Code) | gemeinsamer Lock |
| 20 | SIGTERM beendete Prozess ohne Motorstopp | N (Code) | Signal-Handler |
| 21 | `StopReason.NONE` war Tupel `(0,)` | N (Test) | behoben |
| 22 | Alle `driving/*.py` importierten `evdev`, `curses`, `cv2`, `CameraCar` … (Copy-Paste-Kopf) | N (Analyse) | nur benötigte Importe, Hardware lazy |
| 23 | `room_explorer.py`: f-String nur ab Python 3.12 gültig | N | behoben |
| 24 | Tests verschmutzten `sys.modules` dauerhaft (Mocks von `pipeline_config`, `Driving`, `lane_detector`) | N | Tests isoliert |
| 25 | `CameraSensor` ignorierte gespeicherten Prozessor beim Start; Debug-`print` | N | behoben |
| 26 | Unnötige Kopien aller Zwischenbilder (5 Kopien + HSV-Rückkonvertierung je Frame) | N | entfernt, 558 → 407 KiB je Ringbuffer-Eintrag |
| 27 | V5.3-Bereichsauswahl im Replay ging durch V5.4/5.5 verloren (`replay_selection.py` ungenutzt) | N | wieder integriert |
| 28 | Kein automatischer Neustart/Retry des Kamerathreads nach Kamerafehler | N | nur angezeigt („KAMERA AUSGEFALLEN“), kein Retry |
| 29 | `RoomExplorer._explore`: `counter = 0` statt `self._counter = 0` | N | **nicht geändert** (Fahralgorithmus) |
| 30 | Zwei Klassen `KeyboardMode` (`AdvancedKeyboardMode.py`, `driving/keyboard_mode.py`) | N | dokumentiert |
| 31 | IR/US-Threads im Dashboard nicht gestartet → Sensorspalten leer | N | bewusst, dokumentiert |

## 2. Cleanup-Matrix

| Datei | Verantwortung | Aktuelle Verbraucher (statisch/dynamisch) | Bewertung | Maßnahme | Risiko |
|---|---|---|---|---|---|
| `Camp2CodeDashboard.py` | Control Center | Start `python Camp2CodeDashboard.py` | Kern | **REFACTOR** (6.0) | mittel – Browser-/Pi-Test nötig |
| `car_application.py` | Lebenszyklus | Dashboard, Tests | Kern | **REFACTOR** | niedrig |
| `CameraSensor.py` | Aufnahme + Verarbeitung | CarApplication, Startskripte, Legacy | Kern; tote Hilfsfunktionen `blur_img`, `kanten_erkennung`, `morph_kernel_filter` ohne Verbraucher | **REFACTOR** | niedrig |
| `CameraCar.py` | Datenspeicher | alle Kamerapfade | Kern | **KEEP** (bereinigt) | niedrig |
| `BaseCar.py`, `SonicCar.py`, `SensorCar.py` | Fahrzeug | alle Fahrmodi | Kern; BaseCar ohne Lock bei Hardware | **REFACTOR** (BaseCar), KEEP | mittel – Hardwaretest |
| `driving/camera_mode.py` | Regler | CarApplication, Startskripte | Kern | **REFACTOR** | mittel – Fahrtest |
| `driving/*.py` (übrige) | Fahrmodi | run.py, run_room_explorer.py, Dashboard_LiveData.py | Logik unverändert | **REFACTOR** (nur Importe) | niedrig |
| `driving/stop_reason.py` | Stoppursachen IR/US | base.py, approach_obstacle.py | Bugfix NONE | **REFACTOR** | niedrig |
| `Driving.py` | Kompatibilitäts-Router `import Driving` | 9 Module | bewusst schlank | **KEEP** | – |
| `LenkwinkelKalkulator.py` | Linien + Winkel | LaneDetector, CameraMode | neue zeichnungsfreie Funktion; `lenkwinkel_from_image` ohne Verbraucher | **KEEP** (+ Erweiterung) | niedrig |
| `lane_detector.py`, `pipeline_processors.py` | Bildverarbeitung | CameraSensor, PipelineAB | „Legacy“-Prozessor ist nur Variante ohne Diagnosebilder | **KEEP** / REFACTOR (Kopien) | niedrig |
| `models/camera_data.py` – `CameraDataProcessed` | altes Ergebnismodell | **kein Erzeuger, kein Leser**; nur Import in `legacy/dashboards/CameraCar_DashLive.py` | veraltet | **DEPRECATE** (entfernen mit Legacy-Dashboards) | niedrig |
| `Save_Data_AI.py` | alter Recorder | nur Legacy-Dashboards | durch SessionRecorder ersetzt | **DEPRECATE** | niedrig |
| `session_recorder.py` | Recorder + Laden + Export | CarApplication, Dashboard | Schema v2 | **REFACTOR** | niedrig |
| `replay_hud.py` | HUD | Dashboard, Recorder (`encode_lanes`) | Geometriefehler | **REFACTOR** | niedrig |
| `dashboard_overlay.py` | Overlay | Dashboard, Replay, Legacy | doppelte Zeichenlogik mit HUD | **MERGE** (gemeinsame Funktionen) | niedrig |
| `dashboard_steering.py` | Lenkinstrument | Dashboard | – | **KEEP** | – |
| `replay_selection.py` | Bereichsauswahl | vorher nur Test | wieder genutzt | **KEEP** (+ Erweiterung) | – |
| `driving_health.py`, `recovery_control.py` | Ereignisse, Bergung | CarApplication | Fehlerkorrekturen | **REFACTOR** | niedrig |
| `pipeline_ab.py`, `pipeline_config.py` | A/B, Konfiguration | Dashboard, CameraSensor | Presets ergänzt | **KEEP** | – |
| `telemetry/*` | Ringbuffer, Frequenz, Laufzeiten | CameraCar, Dashboard | Hilfsmethoden | **KEEP** | – |
| `legacy/dashboards/CameraCar_DashLive.py` | altes Dashboard | keiner (manueller Start) | ersetzt; startet eigene Hardware; Port 8050 | **DEPRECATE** (nicht parallel starten) | – |
| `legacy/dashboards/CoPilotPipelineDashboard.py` | altes Pipeline-Dashboard | keiner | ersetzt; `sensor.close()` existiert nicht | **DEPRECATE** | – |
| `legacy/dashboards/CoPilotDrivingDashboard.py` | altes Cockpit | keiner | ersetzt; `sensor.stop()` existiert nicht | **DEPRECATE** | – |
| `Dashboard_LiveData.py` | IR/US-Dashboard Phase 1 | keiner (manuell) | noch funktionsfähig für SensorCar | **KEEP** (Kandidat für `legacy/`) | – |
| `run.py`, `run_room_explorer.py`, `AdvancedKeyboardMode.py`, `utils/ir_sensor_utils.py` | Phase-1-Werkzeuge | manuell | Referenzen gültig (Test) | **KEEP** | – |
| `run_camera_sensor_car.py` | CameraMode ohne Dashboard | manuell | gültig | **KEEP** | – |
| `PS_controller.py`, `stadia_controller.py` | Direktsteuerung über basisklassen | manuell | umgehen BaseCar/v_min; PS durch `PSControllerMode` ersetzt | **DEPRECATE** | – |
| `run_mock_sensor_car.py` | Mock-Start | keiner | **nicht lauffähig** (`Driving.DriveController` fehlt) | **DELETE** | sehr niedrig |
| `dash_live.py` | eigenes Dashboard | keiner | eigene 2. Bildverarbeitung, startet CameraMode **ohne Benutzeraktion**, zweite Klasse `LaneProcessor` | **DELETE** | niedrig |
| `sourcecode.txt`, `src-paket-a.txt`, `scripts/src-paket-b.txt`, `src-dashboard-v52.txt`, `camp2code-full-source.txt`, `camp2code_*.zip` | Übertragungsartefakte | keiner | aus Code erzeugbar | **DELETE** | sehr niedrig |
| `Dash-Frame-Processed.jpg`, `camera_test_processed.jpg` | Debugbilder | keiner (Erzeuger entfernt) | obsolet | **DELETE** | sehr niedrig |
| `README_REPLAY_V53.md`, `README_V54.md`, `README_V55.md` | Versionsnotizen | – | Inhalt in `CHANGELOG.md` | **DELETE** | sehr niedrig |
| `Data_AI/2026-10-27_08-57-39-499098/data.csv` | Aufzeichnung (versioniert) | – | Nutzdaten, nicht obsolet | **KEEP**, aus Git nehmen: `git rm -r --cached Data_AI` | – |
| `camp2code/` (Submodul) | Kursmaterial | – | fremd | **KEEP**, nicht verändert | – |

## 3. DELETE – Nachweise

* `run_mock_sensor_car.py`: Zeile 13 `Driving.DriveController(...)` – Name existiert in `driving.__all__` nicht
  (`test_static_checks.test_fahrmodus_referenzen` schlägt ohne Manifest fehl).
* `dash_live.py`: kein statischer Import; startet `Driving.CameraMode(...).start()` direkt in `main()`;
  zweite HSV/Canny/Hough-Verarbeitung neben `CameraSensor`; kollidiert mit Port 8050 und Kamera.
* Artefakte/Debugbilder/Versions-READMEs: weder importiert noch in Konfiguration referenziert
  (Referenzprüfung im Cleanup-Skript).

**Ausführung:** `scripts/cleanup_obsolete.sh` (Vorschau) → `scripts/cleanup_obsolete.sh --apply`
(Backup unter `~/camp2code_backups/`, `git rm` für versionierte Dateien, Abbruch bei lokalen Änderungen
oder verbleibenden Referenzen).

## 4. Bewusst NICHT geändert

* Fahralgorithmen (`FollowLine`, `RoomExplorer`, `ApproachObstacle`, Lenkberechnung, `steering_gain`).
* Speed-Steuerwert 30 im CameraMode (jetzt als Klassenkonstante, testgesichert).
* `LANE_TIMEOUT`-Schutzfunktion (Schwelle und Uhr unverändert).
* `basisklassen*.py`, `config.json`, `pipeline_studio.json`, Submodul `camp2code/`.
* Legacy-Dashboards (nur dokumentiert).
