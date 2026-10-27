# Performance-Report Version 6.0

## Messbasis – bitte genau lesen

* **Gemessen** wurde ausschließlich in einer x86-Sandbox (Python 3.12, OpenCV 4.11, NumPy 1.26,
  Dash 4.4) mit synthetischen 320×240-Bildern und Hardware-Attrappen.
  **Keine Messung auf dem Raspberry Pi, keine Fahrzeugmessung.**
* Absolute Zeiten sind auf dem Pi deutlich höher. Aussagekräftig sind die **Verhältnisse** und die
  **Zählwerte** (Befehle, Callbacks, Bytes), die nicht von der CPU abhängen.
* Reproduzierbar mit `python tools/benchmark.py` (alter und neuer Stand, z. B. per `git stash`).
* Werte aus den Screenshots sind als solche gekennzeichnet.

Legende: **N** nachgewiesen · **V** begründeter Verdacht · **O** Optimierungsvorschlag

## 1. Ergebnisse (Sandbox, Median)

| Messgröße | V5.5 | 6.0 | Art |
|---|---|---|---|
| Fahrbefehle je Kameraframe (Regler 500 Hz, Kamera 15 Hz, 2 s) | **31,7** (951 bei 30 Frames) | **1,1** (34 bei 30 Frames, inkl. Start/Stopp) | N |
| Reglerrechnung je ausgewertetem Frame | 0,198 ms | 0,035 ms | N |
| Ringbuffer-Eintrag (Copilot-Prozessor) | 558 KiB | 407 KiB | N |
| Ringbuffer 60 Frames | 32,7 MiB | 23,8 MiB | N |
| Bildverarbeitung je Frame (Copilot) | 1,13 ms | 1,10 ms | kein messbarer Unterschied |
| Dashboard-Callbacks je 700-ms-Takt (Tab Cockpit) | 8 | 2 | N (Code) |
| Serverzeit je Takt, Tab Cockpit (inkl. Plotly-Figuren) | 8,3 ms | 1,2 ms | N |
| Bilddaten je Takt, Tab Cockpit | 28 KiB | 15 KiB | N |
| HTTP-Anfragen je offenem Browser-Tab | ≈ 19,8 /s (8 Callbacks/0,7 s + Bergungspuls 8,3/s) | ≈ 2,9 /s (Replay 4,3 /s); Puls nur bei Bergung | N (Code) |
| `frames.csv`-Lesezugriffe je Replay-Takt (Session) | bis 4 | 0 (Cache bis Dateiänderung) | N (Code) |

Hardwarebefehle: Jeder `drive()`-Aufruf erzeugt mehrere I2C-Transaktionen (PWM beider Motoren,
Richtung, Servo). Die Reduktion um Faktor ~29 betrifft daher direkt die I2C-Last des Pi.
Die tatsächliche CPU-Entlastung auf dem Pi ist **nicht gemessen**.

## 2. Kamera

| Befund | Art | Maßnahme |
|---|---|---|
| Ist 13–16 Hz bei Soll 25 Hz (Screenshot Telemetrie) | N (Screenshot) | – |
| Ursache: nach Aufnahme + Verarbeitung wurde zusätzlich die volle Periode gewartet | N (Code) | Periode wird jetzt um die Zykluszeit verkürzt |
| Zeitstempel erst nach der Verarbeitung gesetzt → Datenalter/Latenz zu klein angezeigt | N (Code) | Zeitstempel direkt nach Aufnahme, `processing_ns` separat |
| Zwischenbilder kopiert (roi, mask, blur, morph, edges) + HSV→BGR-Rückkonvertierung | N (Code) | entfernt; HSV als Falschfarbenbild |
| JPEG-Kodierung 320×240 q70 ≈ 0,18 ms (Sandbox) | N | unverändert |

**Wichtig:** Die korrigierte Taktung kann die Kamera- und Entscheidungsrate erhöhen (Ziel 25 Hz).
Das ändert die Reaktionszeit des Reglers und erhöht die CPU-Last. Falls sich das Fahrverhalten
verschlechtert, `cam_sensor.scan_frequency` in `config.json` auf 15 setzen (entspricht dem bisherigen
Ist-Wert). Ob Picamera2 25 Hz liefert, muss auf dem Pi gemessen werden (Telemetrie-Ansicht).

## 3. Threading

| Befund | Art | Maßnahme |
|---|---|---|
| Regler wertet gleichen Frame mehrfach aus und sendet jedes Mal Fahrbefehle | N (Messung) | nur neue Frames; Schutzfunktionen weiterhin bei jedem Zyklus |
| „Controller Ist 53 Hz bei Soll 500 Hz“ (Screenshot) war die Zahl der Wiederholungen, kein Leistungsmangel | N | Anzeige jetzt „Entscheidungen“, Soll = Kamerafrequenz |
| Hardwarebefehle aus mehreren Threads ohne gemeinsamen Lock (Regler, STOP, Bergung) | N (Test: 3 parallel) | Lock in `BaseCar` |
| Bergung sendete aus Flask-Threads | N | Watchdog-Thread sendet |
| Kamerathread endet bei Kamerafehler ohne Neustart | N | Anzeige „KAMERA AUSGEFALLEN“, Regler stoppt (`CAMERA_STALE`) |
| Lock-Dauer `BaseCar`: wenige I2C-Transaktionen je Befehl | V | auf Pi beobachten (Telemetrie: Entscheidungslatenz) |

## 4. Dashboard

| Befund | Art | Maßnahme |
|---|---|---|
| Alle Ansichten (inkl. Replay-JPEG und drei Plotly-Figuren) wurden alle 700 ms berechnet, auch unsichtbar | N | eigener Takt je Tab, nur sichtbarer Tab aktiv |
| Inspector-Bild doppelt kodiert | N | wiederverwendet |
| Bilder bei unverändertem Frame (Freeze) neu kodiert | N | Render-Schlüssel je Browser (`dcc.Store`) |
| Bergungspuls 120 ms dauerhaft aktiv | N | nur bei aktiver Bergung |
| Werkzeug-Zugriffslog je Anfrage | N | Log-Level WARNING |
| Diagramme rebuild + Zoomverlust | N | `uirevision` |

### Browser-Neustart (regulär vs. privat)

Status: **V – begründeter Verdacht, nicht abschließend nachgewiesen.**

Beobachtung: Im privaten Fenster lädt das Dashboard sofort; im regulären Profil erst nach Schließen aller
alten Tabs. Ein Browser begrenzt gleichzeitige HTTP/1.1-Verbindungen je Host und **Profil** (Chrome: 6).
Ein privates Fenster hat einen eigenen Verbindungspool. Jeder offene Tab von V5.5 erzeugte ca. 20
Anfragen/s, davon bis zu 9 gleichzeitig je Takt. Nach einem Serverneustart pollen alte Tabs weiter und
können den Pool belegen; neue Tabs warten („Stalled“).

Maßnahmen 6.0: ca. 85 % weniger Anfragen je Tab, verborgene Tabs pausieren (`assets/visibility.js`).

Prüfung auf dem Pi/Laptop:
1. DevTools → Network: Stehen Anfragen auf „Stalled/Queueing“?
2. `chrome://net-internals/#sockets` → „Flush socket pools“ und neu laden.
3. Mit genau einem Tab im regulären Profil testen.

Weitere Kandidaten (nicht geprüft): Browser-Erweiterungen im regulären Profil; das DARKLY-Theme wird
von einem CDN geladen – ohne Internet im Fahrzeug-WLAN kann das Laden hängen (O: CSS lokal unter
`assets/` ablegen).

## 5. Recorder

| Befund | Art | Maßnahme |
|---|---|---|
| CSV je Zeile geöffnet/geschlossen | N | ein Schreibzugriff je Zyklus |
| Frames vor Fahrentscheidung gespeichert | N | Wartefrist 0,25 s, `missing_decision` |
| Frameverluste unsichtbar | N | `RECORDER_FRAME_GAP`, Zähler im Manifest und Dashboard |
| Recorder verzögert keine Fahrentscheidung (eigener Thread, liest nur Referenzen) | N (Code) | unverändert |
| JPEG q95 (wie bisher OpenCV-Standard), ca. 20 KB je Frame bei 320×240 | V | SD-Bedarf ≈ 15 Hz × 20 KB ≈ 18 MB/min; Speicher prüfen |
| Pufferreserve: Ringbuffer 60 Frames ≈ 4 s bei 15 Hz; Recorder-Zyklus 0,12 s | N | bei langsamer SD-Karte Lücken im Event-Log sichtbar |

## 6. Telemetrie (neu sichtbar)

Kameradatenalter seit Aufnahme · Bildverarbeitungszeit · Entscheidungslatenz (Aufnahme → Fahrbefehl) ·
Renderzeiten je Ansicht · Prozess-CPU, RSS, Threads, Load (über `psutil`, falls installiert) ·
Ringbuffer-Füllstand · Regler-Abtastrate.

## 7. Offene Optimierungsvorschläge (O)

* Picamera2 mit fester `FrameRate` konfigurieren, damit `capture_array` nicht wartet.
* Dashboard-Theme lokal ausliefern (offline-fähig).
* `LaneProcessor` während der Fahrt verwenden, falls Pi-Messung einen Vorteil zeigt (in der Sandbox kein Unterschied).
* Produktionsserver (z. B. `waitress`) statt Flask-Entwicklungsserver – erst nach Messung.
