# Changelog
## 6.1 – Paket C (PAKET-C)

* **Replay-Bugfix:** Beim Abspielen gespeicherter Sessions lief der Slider, das Bild stand.
  Ursache: Takt und Index loesen im selben Zyklus aus; `ctx.triggered_id` meldete nur den Takt.
  Bild und Diagramm sind getrennt; das Diagramm wird nur bei Session-/Bereichswechsel neu
  gebaut, beim Blaettern wandert nur der Cursor (`Patch`).
* **missing_decision korrigiert:** Ringbuffer-Vorlauf vor dem Fahrmodusstart wird als
  `no_driving_state` gespeichert (`driving_since`). Session 15:07: alle 51 „fehlenden“
  Entscheidungen waren Frames 96–146 vor `CONTROLLER_STARTED` (Frame 147).
* **Replay → Pipeline Studio:** Rohbild + damalige Parameter als Referenz A; B = aktuelle Slider.
* **Thread-CPU** je Thread in der Telemetrie (Messgrundlage fuer Threading-Entscheidungen).
* `requirements-c2c.txt` mit den direkten Abhaengigkeiten.

## 6.0 – Architektur-Konsolidierung (27.10.2026)

Kein Feature-Sprint. Ziel: Korrektheit, eine Verantwortung je Modul, weniger Last, testbare Lebenszyklen.

### Korrekturen (nachgewiesene Fehler)
- **Replay-HUD-Geometrie:** Linien wurden als `x = m·y + b` im Vollbild gezeichnet. Tatsächlich gilt
  `y = m·x + b` im ROI (Test `test_lane_geometry`). HUD und Live-Overlay nutzen jetzt dieselbe
  Zeichenfunktion mit ROI-Versatz. Alte Sessions ohne ROI-Angabe: keine Linien statt falscher Linien.
- **CameraMode:** wertet jeden Kameraframe genau einmal aus (vorher bis ~32 Fahrbefehle je Frame).
  `LANE_TIMEOUT` unverändert. Neu `CAMERA_STALE` (kein neuer Frame innerhalb `lost_line_timeout`) und
  `NO_CAMERA_DATA` (kein Losfahren ohne Kamerabild). Speed-Steuerwert **30** unverändert, jetzt
  `CameraMode.SPEED_CONTROL_VALUE` mit Test.
- **BaseCar:** Servo-/PWM-Befehle laufen unter dem Fahrzeug-Lock (vorher verschränkt bei parallelem
  Regler, STOP und Bergung).
- **Recorder:** wartet bis 0,25 s auf die Fahrentscheidung eines Frames; neuer Status `missing_decision`;
  Frame-Lücken als Ereignis `RECORDER_FRAME_GAP`; Manifest behält `created_at`.
- **Ereignisprotokoll:** gleiche Stoppursache in einer späteren Fahrt wird wieder protokolliert
  (`DrivingHealthMonitor.mark_start`); neue Ereignisse `CONTROLLER_STARTED`, `RECOVERY_*`, `RECORDING_*`.
- **CameraSensor:** Taktung berücksichtigt die Verarbeitungszeit (Soll 25 Hz statt Ist ~15 Hz);
  Zeitstempel direkt nach der Aufnahme; gespeicherter Prozessor wird beim Start verwendet;
  Debug-`print` und ungenutzte Hilfsfunktionen entfernt.
- **Kalibrier-Overlay** zeigt keine erfundenen Werte („Winkel 90 Speed 0“) mehr.
- **Cockpit** zeigt während der Fahrt den neuesten vollständigen Frame statt „KALIBRIERUNG“.
- **StopReason.NONE** war ein Tupel `(0,)`.
- **room_explorer.py** war vor Python 3.12 ein SyntaxError (verschachtelte Anführungszeichen im f-String).
- **SIGTERM** stoppt jetzt Motoren und Recorder (vorher Prozessende ohne Shutdown).

### Architektur
- `CarApplication` besitzt genau einen Recorder (`SessionRecorder`); `SaveData_4_AI` nur noch Legacy.
- Bergung: `CarApplication.enable_recovery()` unter demselben Lock wie `start_mode()`;
  Fahrbefehle der Bergung sendet ausschließlich der Watchdog-Thread.
- `driving/*`: nur noch benötigte Importe; Hardware- und Plattformmodule (`evdev`, `curses`, Fahrzeuge)
  werden erst bei Bedarf geladen. Das Paket ist ohne Raspberry-Pi-Bibliotheken importierbar.
- `CameraDataProcessed` wird nicht mehr erzeugt (keine Leser) – **DEPRECATED**.
- `LenkwinkelKalkulator.steering_angle_from_lanes()` (zeichnungsfrei, identische Rechnung) und
  `steering_line_from_lanes()` (Visualisierung der Reglerrichtung).
- Datenmodelle: `DrivingState.requested_angle`, `.monotonic_ns`; `CameraData.processing_ns`, `roi_rows()`.
- `FrameRingBuffer.find()`, `.latest_complete()`; `telemetry.DurationStats`.

### Dashboard
- Globale Steuerleiste mit Fahrmodus-Auswahl, START/STOP, Bergung, REC; alle Zustände aus der Anwendung.
- Eigener Takt je Ansicht; nur der sichtbare Tab pollt; verborgene Browser-Tabs pausieren.
- Bergungspuls nur bei aktiver Bergung.
- Cockpit: Stellwert, Sollwinkel, Rohwinkel, Speed-Steuerwert, Reglerrichtung, Datenalter, Latenz.
- Pipeline Studio: Presets speichern/laden (`pipeline_presets/`), Sperrhinweis während der Fahrt,
  Inspector ohne Doppelkodierung, Freeze ohne Neukodierung.
- Telemetrie: Entscheidungen statt „Controller 500 Hz“, Linienanzahl, Latenzen, CPU/RAM.
- Recording: Ereignisprotokoll mit Zeit, Frame, Typ, Beschreibung.
- Replay: Ereignissprung mit Vor-/Nachlauf, Bereichsauswahl (aus V5.3 wiederhergestellt),
  Ereignismarker, Export des Bereichs als ZIP (Session: Bilder unverändert).
- Dark Theme für Dropdowns, Eingabefelder und Plotly-Diagramme.

### Session-Format v2
Siehe ARCHITECTURE.md, Abschnitt 8. Neue Spalten u. a. `driving_active`, `driving_mode`,
`requested_angle`, `processing_ns`, `roi_top_px`/`roi_bottom_px`, `lane0_m … lane1_b`,
Sensorwerte und -alter. Sessions v1 bleiben lesbar.

### Tests
98 hardwarefreie Tests (vorher 24), u. a. Linienmodell, CameraMode-Lebenszyklus, Recorder v2,
Callback-Registrierung (Attrappe und echte Dash-Bibliothek), statische Prüfungen (Syntax, Zyklen,
Referenzen auf entfernte Klassen, Startskripte). Bestehende Tests isoliert (keine dauerhaften Mocks in
`sys.modules`).

### Breaking Changes
- `RecoveryControl.command()` sendet nicht mehr synchron; der Watchdog führt innerhalb von ≤ 50 ms aus.
- `CameraData.timestamp`/`monotonic_ns` bedeuten jetzt Aufnahmezeitpunkt (vorher Veröffentlichung).
- `events.csv`: `timestamp_ns` ist monotone Zeit (wie bisher), jetzt dokumentiert.
- Dashboard-IDs: Cockpit-Start/Stop entfernt (globale Steuerleiste), `record-toggle` in der Steuerleiste,
  neue Takte `tick-<tab>`. Eigene Erweiterungen auf alte IDs prüfen.
- `CameraSensor` verwendet beim Start den in `pipeline_studio.json` gespeicherten Prozessor.

---

## 5.5 – Replay HUD und Ereignisprotokoll
- `frames.csv` mit `lanes_json` und `event_codes`; getrennte `events.csv` (`timestamp_ns`, `frame_id`,
  `code`, `detail`) aus `DrivingHealthMonitor.events()`.
- Replay-HUD auf gespeicherten Rohbildern (Frame-ID, Speed-Steuerwert, Stellwinkel, Status, Linien,
  Richtungsinstrument). Annahme `x = m·y + b` – in 6.0 als falsch nachgewiesen und korrigiert.
- Grenzen: begrenzte Ereignishistorie; Sessions aus 5.4 ohne `lanes_json`/`events.csv`.

## 5.4 – Sessions und Replay
- Callback-Signatur des Replay-Seeks korrigiert.
- `session_recorder.py`: persistente Sessions unter `Data_AI/sessions/session_…/` (JPEG + CSV, Frame-ID,
  Zeitstempel, Fahrdaten oder `no_driving_state`), unabhängig vom Browser.
- Replay gespeicherter Sessions, Chart-Navigation über Frame-ID; Mini-Preview mit Kalibrier-Overlay;
  responsives CSS.
- Grenzen: verspätete Fahrentscheidungen nicht nachgetragen (in 6.0 behoben), keine Quota.

## 5.3 – Replay-Bereichsauswahl
- Bereichsauswahl über Plotly-Zoom (X-Achse = Frame-ID) für Wiedergabe, Navigation und Export
  (`replay_selection.py`). Ging in 5.4/5.5 verloren; in 6.0 wieder integriert.

## 5.1 – Controller-Diagnose
- `CameraMode.last_diagnostic`: Linien, Rohwinkel, gehaltener Vorwert, Soll- und Stellwinkel,
  Stoppursachen `LANE_TIMEOUT`, `NO_CAMERA_DATA`, `CONTROLLER_EXCEPTION`.

## 5.0 – Fahrzustände, globale Steuerleiste, Bergung
- `DrivingHealthMonitor`, `RecoveryControl` (Deadman, Watchdog), globale Start/Stop-Leiste.

## 4.0 / 3.0 / 2.0 – Dashboard-Ausbau
- Nur sichtbare Hauptansicht überträgt Bilder; `PipelineAB`; Slider an den Bildstufen; Pipeline Inspector;
  Replay Lab für den Ringbuffer.

## Paket A+B
- Paket A: `SensorSnapshot`, Zeitstempel für IR und Ultraschall, gemeinsames `CameraCar`-Sensormodell;
  Dashboard-Konsolidierung in einer Anwendung.
- Paket B: Ringbuffer-basierter Recorder mit Frame-ID-Verknüpfung, Rohbildern, Linien,
  Prozessparametern und Status für fehlende Fahrentscheidungen.
- `BaseCar`: negative Geschwindigkeit als Betrag gespeichert, Richtung getrennt.
- `ConfigReader`: boolesche Zeichenketten explizit ausgewertet.
- `FollowLine`: PID-Zeit und Zustandsfortschreibung korrigiert.
- `DrivingMode`: Start-/Stop-Thread-Lifecycle verbessert.
- `CameraSensor`: leere Frames und Ressourcenfreigabe behandelt; zyklisches Debug-JPEG entfernt.
