# Changelog
## 6.9 – Paket H2 (PAKET-H2)
* **Fünf Reiter:** „Recording & Events“ entfällt (`dashboard/tabs/recording.py` gelöscht, Callback `recording_view` und alle
  IDs `record-*`, `session-stats`, `event-list`, `tick-record` entfernt). Aufnahmezustand und Zähler (Frames, Lücken, fehlende
  Entscheidungen, Session, Fehler): Tooltip am REC-Chip der Steuerleiste und eine Zeile im Cockpit. Der wirkungslose Button
  „Letzte Session im Replay öffnen“ entfällt; im Replay Lab öffnet „Neueste Session öffnen“ die neueste Session aus
  `list_sessions()` auch ohne laufende Aufnahme. `replay_to_studio` bleibt einziger Besitzer von `main-tabs.active_tab`.
* **Ereignisprotokoll** (`event_log.py`) im Cockpit und im Replay Lab: Zeit, Frame, Ereignistyp, Beschreibung, neueste zuerst,
  Filter „Wichtig“ / „Alle“. Die Frame-Nummer ist ein Link `#jump/<session>/<frame>/<nonce>` (nur anspringbar, wenn der Frame in der
  aufgezeichneten Session – neu `SessionRecorder.has_frame` – oder im Ringbuffer liegt, sonst grau mit Tooltip; Frame -1/None
  ohne Link). Replay Lab: `events.csv` der gewählten Session bzw. Live-Ringbuffer; die Ereignismarker im Replay-Diagramm waren
  schon vorhanden und bleiben.
* **Auslastung** (`system_stats.py`, `SystemSampler`): CPU-Last (Mittel über alle Kerne) prominent, RAM und Temperatur klein, in
  Kennzahlenleiste und Cockpit; Ampel < 60 % grün, < 85 % gelb, darüber rot, ab 80 °C immer rot. Genau ein Aufrufer (Telemetrie-
  Sampler-Thread, ca. 4 Hz); Dash liest nur den letzten Wert; fehlende Werte sind `None` (Anzeige „–“).
* **Telemetrie & Performance:** Speicherplatz der SD-Karte mit Schätzung „reicht für ca. X min Aufnahme“ (alle ca. 30 s);
  EIN gekoppeltes Diagramm (`telemetry_chart.py`) statt drei: Frequenzen, Latenzen, Fahren, System auf gemeinsamer Zeitachse
  (60 s), echte Messwerte mit Lücken im Stillstand, dict-Figur, nur jeden 3. Takt, max. 240 Punkte je Kurve, `uirevision`.
  Das Textblock „Datenalter, Latenzen, System“ ist einklappbar. Info-Text zu „Entscheidungen“.
* **Cockpit:** Button „Frame im Pipeline Studio öffnen“ (gleiche Übergabe wie im Replay Lab); Fehlermeldung im Cockpit, wenn kein
  Frame vorliegt. Das Frequenzhistorie-Tupel enthält veraltete Werte jetzt als `None` statt 0.

## 6.8 – Paket H1b (PAKET-H1B)
* **Frame-Balken** in Replay Lab („Zeitliche Navigation“) und Simulations-Diagramm: senkrechter, ziehbarer Balken am
  angezeigten Frame (`assets/framebar.js`). Der Balken ist ein eigenes Element über dem Plotly-Diagramm (keine Plotly-Shape:
  deren Ziehen meldet erst beim Loslassen, verschiebt frei in y und machte auch Ereignislinien ziehbar). Ziehen baut das
  Diagramm nicht neu (Zoom, Legende und Bereichsauswahl bleiben), meldet gedrosselt (150 ms) an `replay-bar`/`sim-bar` und
  setzt Frame und Bild; bei Wiedergabe läuft er clientseitig mit (Dash-Callback `c2c_framebar.move`). Programmatisches
  Setzen meldet nichts zurück. Die Kurve „Aktueller Frame“ (Patch) im Replay-Diagramm entfällt.
* **Kamera & Regler** im Settings-Reiter (`runtime_settings.py`): Kamera-Abtastung (`cam_sensor.scan_frequency`),
  Regler-Abtastung, Linien-Timeout, Lenkverstärkung (`cameracar_controller.*`), minimaler/maximaler Steuerwert
  (`car.v_min/v_max`). Nur im Stillstand, Bereiche validiert, sofort wirksam, dauerhaft über `config_store`; je Wert
  Anzeige „aktiv“ / „gespeichert (gilt ab nächstem Start)“; Standardwerte unverändert; fehlerhafte gespeicherte Werte
  ergeben die Standardwerte (`ConfigReader.get_int/get_float` brechen bei ungültigen Werten nicht mehr ab).
  Verhaltensrelevante Werte sind gekennzeichnet; Hardware-Kalibrierung, Auflösung und colormode bleiben außen vor (H3).
* Ereignis `CONFIG_SAVED` bei erfolgreichem dauerhaften Speichern; `ConfigReader.set_value`; Simulation liest nach
  Änderungen der Settings die Regler-Konfiguration neu.

## 6.7 – Dashboard als Plug-ins, dynamisches Pipeline Studio, Modus Opus
* **Dashboard in Tab-Klassen zerlegt:** `Camp2CodeDashboard.py` (vorher ca. 1400 Zeilen mit allen Layouts und
  Callbacks) startet nur noch; Aufbau im Paket `dashboard/` (Shell + `tabs/`: Driving, Pipeline, Telemetry,
  Recording, Replay, Settings, je Klasse von `DashboardTab`). Neuer Tab = neue Klasse. Das Paket importiert
  Dash nie selbst (Dash-Bausteine über `DashboardContext`). Callback-Namen, Komponenten-IDs (außer Pipeline
  Studio), Takte und Verhalten der übrigen Tabs unverändert.
* **Pipeline Studio dynamisch:** Prozessoren beschreiben ihre Stufen, Regler und Schalter selbst
  (`dashboard_stages()`, `PipelineStage`, `PipelineControl`); das Studio erzeugt daraus Karten mit
  Pattern-Matching-IDs. Prozessorwechsel und Preset-Laden bauen die Karten neu auf (`rebuild_stage_cards`),
  Reglerwerte bleiben erhalten, nicht angezeigte Regler (HSV bei Opus) behalten ihren Wert. Neue Karte
  „Binarisierung“ (Stufe `binary` des Legacy-/Copilot-Prozessors, bisher nicht sichtbar). Der Prozessor-
  Wähler steht jetzt über den Karten. Der Replay-Frame im Studio wird mit dem gewählten Prozessor verarbeitet
  (vorher immer Copilot).
* **Modus Opus** (`OpusLaneProcessor`, Schlüssel `opus`, fahrfreigegeben): dunkle Linien über Graustufen,
  CLAHE, Gauß-Blur, Blackhat und Otsu statt starrer HSV-Farbe; Stufen Graustufen, CLAHE, Blackhat, Otsu-
  Schwelle, neue abschaltbare Schritte `clahe`, `blackhat`, `threshold`. Blackhat-Kernel fest 15 px.
* **Layout je Seitenaufruf neu gebaut:** ein Neuladen im Browser zeigt die wirksamen Studio-Einstellungen. Das
  Studio wendet unveränderte Werte nicht erneut an (Dash ruft Wildcard-Callbacks beim Laden auf).
* **Fix `assets/visibility.js`:** `pageshow` vor dem Rendern des Layouts löste „Cannot read properties of
  undefined“ aus; gesetzt wird jetzt erst, wenn das Layout steht.
* **Tests:** Strukturtests mit Pattern-Matching-IDs, `tests/test_dashboard_plugins.py` (Stufenbeschreibung,
  Opus, Plug-ins, echtes Dash über HTTP). Konfiguration der Dashboard-Tests unabhängig von
  `pipeline_studio.json` (`hardware_stubs.pin_pipeline_config`). Hinweis: die Patch-Skripte
  `scripts/paket_*_patch.py` zielen auf den alten Monolithen und sind nicht mehr anwendbar.
## 6.6 – Paket H1 (PAKET-H1)
* **Diagramme:** feste Höhe nur als `style.height` am `dcc.Graph` (kein `layout.height`, kein Padding am
  Plotly-Element), Vollbild-Schalter als Geschwister in einer Leiste über dem Diagramm, jedes Diagramm startet
  mit einer dunklen leeren Figur samt Hinweis. Interaktiv: Legende klicken = Kurve ein/aus, Doppelklick =
  nur diese Kurve, Mausrad-Zoom, Werkzeugleiste (Zoom, Verschieben, Reset, PNG), Doppelklick = alles.
  Zoom und Legendenzustand überleben Live-Updates (`uirevision`). Replay: Zoom/Ziehen bleibt die
  Bereichsauswahl (nur X-Achse). Ursache des „wachsenden leeren Diagramms“ nicht am Code verifiziert
  (der frühere Chart-Zusatz ist nicht im Stand 062f360), siehe `chart_kit.py`.
* **HUD** (`replay_hud.py`, Replay, Cockpit, Simulation damals und jetzt): Zustandsanzeige statt N/A
  (GESTOPPT: Ursache > BERGUNG/MANUELL > AUTONOM > AUTONOM/ENTSCHEIDUNG FEHLT > STEHT > KEIN FAHRMODUS),
  Speed standardmäßig sichtbar und vorzeichenbehaftet (Steuerwert), ROH > STELL, SOLL nur bei ≥ 1° Abweichung,
  fehlende Werte ausgeblendet, ASCII-Texte, Kennzeichnung unten im Bild, einklappbare Legende.
  Neue Felder `CameraData.vehicle_mode/vehicle_angle/vehicle_speed` (Aufnahmemoment, über
  `CameraSensor.mode_provider` = `CarApplication.vehicle_mode()`), neue Spalten in `frames.csv`
  (Schema 2, Revision 2; alte Sessions bleiben lesbar → KEIN FAHRMODUS).
* **Simulation:** Änderung von Quelle, Prozessor, Parameterquelle, Frame-Bereich oder Vorlauf pausiert,
  verwirft das Ergebnis und leert die Anzeigen („Einstellungen geändert - bitte neu berechnen“). Tempo
  0,25x/0,5x/1x/2x/4x nach den aufgezeichneten Zeitstempeln (`SimFrame.captured_ns`), automatische Pause am Ende,
  Schalter Echtzeit (überspringt) / Jeder Frame (überspringt nichts), gemessene Anzeigerate.
  Median-fps der Aufnahme im `manifest.json` (`capture.median_fps`).
* **Einstellungen dauerhaft:** `config_store.py` speichert Geschwindigkeit (`cameracar_controller.speed_*`) und
  JPEG-Qualität (`session_recorder.jpeg_quality`) atomar in `config.json` (Sicherung in
  `Data_AI/config_backups`, letzte 5), lädt beim Start und fällt bei Fehlern auf fest 30 / 95 zurück.
* **Hilfetexte** (`help_texts.py`): jeder Text genau ein Satz mit Herkunft gemessen/berechnet/Befehlswert/Einstellung.
* **Tests:** `test_static_checks` durchsucht Verzeichnisse mit Pruning (rglob durchlief `.venv`).

## 6.5 – Paket G (PAKET-G)

* **Settings-Reiter:** Geschwindigkeit fest/adaptiv (v_min 30 … v_max), JPEG-Qualität der Aufnahme.
* **Adaptive Geschwindigkeit** (`speed_policy.py`): v_max auf Geraden, linear bis v_min bei vollem
  Einschlag, v_min bei gehaltenem Vorwert. Standard bleibt fest 30. Änderung nur im Stillstand.
* **Kennzahlen-Leiste:** Regelrate, Reaktionszeit, Spurqualität (immer sichtbar).
* **Ereignisprotokoll:** Frame-Nummern verlinken ins Replay Lab (Session oder Ringbuffer, ±Vor-/Nachlauf).
* **Cockpit:** HUD-Darstellung schaltbar; Vollbild für Cockpit, Replay und Inspector.
* **Info-Buttons (ⓘ)** an Kennzahlen, Telemetrie-Karten, Pipeline-Slidern und Settings (`help_texts.py`).
* **Fix Frequenzverlauf:** veraltete Werte werden als 0 statt als letzter Wert geschrieben.
## 6.4 – Paket F: Simulation (PAKET-F)

* **Zentrales Prozessor-Verzeichnis** (`processor_registry.py`): CameraSensor, Pipeline Studio,
  A/B-Labor, Replay und Simulation lesen dieselbe Liste. Neue Prozessoren: ein Eintrag/`register()`.
* **MockProcessor** (`mock_processor.py`): verschiebt Linien, laesst jede 7. rechte Linie weg;
  nur Simulation (`live=False`), vom CameraSensor abgewiesen.
* **Bildquellen** (`frame_source.py`): `SessionSource`, `RingbufferSource`.
* **Austauschbare Uhr im CameraMode** (`self._clock`): Live unveraendert `time.monotonic_ns`;
  Simulation nutzt aufgezeichnete Zeitstempel (LANE_TIMEOUT wie damals).
* **ReplayCar** (`replay_car.py`): Fahrzeug-Attrappe mit BaseCar-Begrenzungen, keine Hardware.
* **Cockpit-Umschalter Live | Simulation** (`sim_lab.py`, `sim_engine.py`): Session/Ringbuffer
  mit beliebigem Prozessor und Parametern (damals/Studio) neu rechnen, Vorlauf, damals vs. jetzt
  je Frame und als Diagramm, Kennzahlen. START im Simulationsmodus gesperrt.
* **Batch-Report** `scripts/sim_report.py`: Kennzahlen, CSV und HTML mit auffaelligen Frames.
* **Offline-Start** `python Camp2CodeDashboard.py --offline [--port N]` ohne Hardware (Laptop).
## 6.3 – Paket E (PAKET-E)

* **Telemetrie-Lag:** Diagramme als dicts statt go.Figure, nur jeden 3. Takt, Fenster 60 s,
  max. 240 Punkte je Linie (vorher Lenkwinkel-Achse bis -400 s).
* **Regler:** 100 Hz Abtastung, falls cameracar_controller.scan_frequency fehlt (vorher 500 Hz).
* **Recording → Replay:** Button „Letzte Session im Replay öffnen“.
## 6.2 – Paket D (PAKET-D)

* **Replay → Pipeline Studio:** Alle acht Stufen zeigen jetzt den übergebenen Replay-Frame
  (vorher weiter Live/Freeze). Die Stufen nutzen die aktuellen Slider, A/B die damaligen
  Parameter. „Freeze / Live“ kehrt zum Livebild zurück.
* **Konsole:** `Changed speed/steering_angle` und picamera2-Statusmeldungen nur noch mit
  `C2C_LOG=INFO python Camp2CodeDashboard.py`.
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

