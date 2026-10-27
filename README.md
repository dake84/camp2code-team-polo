# Paket A: Dashboard-Konsolidierung

## Ziel

Eine Dash-Anwendung auf Port 8050 mit vier Ansichten: Driving Cockpit, Pipeline Studio, Telemetrie & Performance, Recording & Events.

## Installation

Im Repository-Root entpacken. Bestehende Dashboards bleiben unverändert und als Legacy erhalten. Start mit `python Camp2CodeDashboard.py`. Nicht parallel mit den Legacy-Dashboards starten: Kamera und Port 8050 dürfen nur von einer Anwendung verwendet werden.

## Architektur

`CarApplication` besitzt genau eine `CameraCar`-, eine `CameraSensor`- und eine `SaveData_4_AI`-Instanz sowie die bestehenden Fahrmodi. Der Kamerathread wird einmal gestartet; die Bildverarbeitung findet ausschließlich im bestehenden `CameraSensor` statt. Die Dash-Callbacks konsumieren `CameraData`, `DrivingState`, Ringbuffer und Diagnosebilder. Overlays werden nur auf Kopien gezeichnet.

Die Historie wird in einem eigenen leichten Telemetrie-Sampler mit begrenzten Deques aufgenommen. Sie bleibt beim Wechsel zwischen den Ansichten erhalten und wird nicht durch einen Browser-Callback erzeugt. Sie ist keine persistente Session; die Session-Funktion ist Paket C.

## Bedienung

- Driving Cockpit: CameraMode oder Gamepad wählen, Start und STOP. Ein Wechsel erfolgt durch Stoppen und erneutes Starten.
- Pipeline Studio: acht Bildstufen, Slider und Aktivierungsschalter. Änderungen werden nur im Stillstand übernommen. Speichern schreibt die bestehende `pipeline_studio.json`.
- Telemetrie: Frequenzen, Verlauf, Fahrzustand und Datenalter.
- Recording & Events: bestehender Recorder, Status und nicht-persistente Linienverlust-Beobachtungen.

## Test

`python -m compileall -q Camp2CodeDashboard.py car_application.py`

`python -m unittest discover -s tests -p 'test_dashboard_a.py'`

Die Tests ersetzen Hardwaremodule vor dem Import durch Mocks. Keine Motoransteuerung. Das tatsächliche Dash-Rendering, die Kamera und Fahrmodi müssen auf dem Pi geprüft werden.

## Bekannte Einschränkungen

- Die vier Ansichten sind Tabs innerhalb einer Anwendung, keine getrennten Server.
- Die Frequenzhistorie ist eine begrenzte Stichprobe (0,25 s) und nicht dauerhaft gespeichert.
- Die Bilddiagnose hängt vom ausgewählten bestehenden Pipeline-Prozessor ab. Der Legacy-Prozessor liefert nicht alle Diagnosebilder.
- Die bestehenden CameraSensor-/CameraMode-Implementierungen und ihre eventuellen Laufzeitprobleme werden nicht in Paket A geändert.
- Es werden keine zusätzlichen IR-/Ultraschallsensoren initialisiert. Die zuvor vorhandene SensorSnapshot-Unterstützung wird nicht zurückgebaut.
- Die Recorder-Datensatzanzahl ist im bisherigen Recorder nicht als öffentliche Eigenschaft verfügbar und wird daher nicht erfunden.
- Die Ereignisanzeige zeigt nur Beobachtungen über fehlende Linien, kein neues Event-Modell.
- Die Stop-Funktion stoppt Fahrmodi und Motoren, nicht die laufende Kamera oder Recorderaufnahme. Der vollständige Shutdown beendet zusätzlich Kamera und Recorder beim Prozessende.

## Ausbau: Pipeline Inspector und Replay Lab

- Acht Vorschaukarten mit jeweils eigener Auswahl für einen großen Inspector.
- Freeze hält die Frame-ID im vorhandenen Ringbuffer fest; sobald sie verdrängt wurde, zeigt das Studio wieder den aktuellen Frame. Es wird kein zusätzlicher Bildspeicher angelegt.
- Replay Lab erlaubt Chart-Klick, Frame vor/zurück, Play/Pause und Export des **momentan verfügbaren Ringbuffers** als ZIP mit JPEGs und CSV. Das ist ausdrücklich noch kein Session-Recorder und kein LLM-Trainingspaket.
- Das Replay verwendet einen Index in einem gleitenden Ringbuffer. Bei Verdrängung alter Frames kann sich die Position während der Live-Aufnahme verschieben. Für stabiles Rückspulen über längere Zeiträume benötigen wir eine persistente Session und einen Frame-ID-basierten Cursor (Paket C).
- Das Export-ZIP wird für den Download im Arbeitsspeicher aufgebaut; deshalb nur für den kurzen Ringbuffer verwenden.
- Es wird keine erneute Linienerkennung und keine Bildvorverarbeitung im Dashboard ausgeführt.

## V3: Pipeline und Cockpit

- Pipeline-Slider stehen direkt an den zugehörigen Bildstufen und lösen die Konfigurationsübernahme ohne separaten Klick aus. Die tatsächliche Bildvorschau aktualisiert sich mit dem nächsten Kameraframe. Während der Fahrt sind Änderungen weiterhin gesperrt. `Speichern` persistiert die Konfiguration.
- Das Cockpit zeigt nur ein großes Overlay mit dem tatsächlich angesteuerten Lenkwinkel als kleinem Richtungsinstrument. Die Anzeige arbeitet auf einer Kopie und ändert keine Fahrentscheidung.
- Die ursprünglichen Legacy-Dashboards bleiben erhalten. Die Replay-Funktionen aus V2 bleiben unverändert.
- Ein Dash-/Raspberry-Pi-Integrationstest ist noch erforderlich; Syntax- und Mock-Tests ersetzen ihn nicht.

## V4: Performance, Browser-Neustart und A/B

- Der Browser erhält nur noch JPEG-Daten der sichtbaren Hauptansicht. Bei Tabwechsel werden die Bilder erneut geladen. Das reduziert insbesondere die gleichzeitige Übertragung von acht Pipelinebildern während der Fahrt.
- Replay-Play und Freeze sind bewusst flüchtiger Browserzustand (`dcc.Store` mit `storage_type=memory`); ein Browser-Reload beginnt ohne alte Replay- oder Freeze-Selektion. Die laufende Fahrzeuganwendung wird dadurch **nicht** neu gestartet und ein bereits fahrendes Fahrzeug wird nicht automatisch gestoppt.
- Ein eingefrorener Frame, der aus dem Ringbuffer verdrängt wurde, wird nicht mehr als verfügbar ausgegeben. Die Vorschau fällt auf LIVE zurück. Dies ist eine defensive Maßnahme; die konkrete Ursache des gemeldeten Browser-Neustart-Bugs wurde ohne Browser-Fehlerprotokoll nicht reproduziert.
- A/B verwendet `PipelineAB`: Beim Einfrieren wird **ein** Rohbild kopiert und die Referenzparameter A gespeichert. `A/B berechnen` führt den vorhandenen `LaneProcessor` oder `CopilotLaneProcessor` offline auf separaten Kopien dieses Rohbilds aus. B verwendet die aktuellen Slider. Kein neuer Kamerazugriff, keine Motorsteuerung, kein neuer Thread.
- A/B ist ein Diagnosewerkzeug und **keine** zweite Verarbeitung in der laufenden Fahrpipeline. Eine manuelle A/B-Berechnung benötigt zusätzliche CPU-Zeit. Sie sollte vorzugsweise bei stehendem Fahrzeug erfolgen.
- Die A/B-Referenz liegt im Prozessspeicher und wird bei Neustart der Anwendung verworfen. Bei mehreren Browsern teilen sich Benutzer diese Referenz; Multi-User-Isolation ist noch nicht implementiert.
- Kein Performance-Benchmark auf Raspberry-Pi-Hardware durchgeführt. Der Browser-Neustart-Fix ist defensiv und muss im realen Browser verifiziert werden.

## V5: Fahrzustände, globale Steuerleiste und Bergung

- Globale Start-/Stop-Steuerleiste bleibt beim Wechsel der Tabs sichtbar. Die Anzeige liest den echten Laufzustand und nicht den letzten Buttonklick.
- Telemetrie und Recording erhalten eine auf maximal 320 Pixel Breite verkleinerte Live-Rohbildvorschau. Keine zweite Kamera oder Linienerkennung.
- `DrivingHealthMonitor` beobachtet `LANE_LOST`, `LANE_PARTIAL`, `LANES_DETECTED` und `CONTROLLER_STOPPED` im zentralen Telemetrie-Thread. **Wichtig:** Zwei erkannte Linien sind noch keine geometrisch gültige Fahrspur. Die Ursache eines Controller-Stopps ist nicht über die vorhandene Schnittstelle verfügbar und wird als unbekannt angezeigt. Die bestehende 1-Sekunden-Linienverlustlogik bleibt unverändert.
- `RecoveryControl` bietet manuelle Bergung bei ausgeschaltetem Fahrmodus. Die Taste Leertaste ist Deadman, W/S bewegen vorwärts/rückwärts, A/D lenken. Die Befehle werden auf einen niedrigen Steuerwert begrenzt. Ein serverseitiger Watchdog stoppt bei ausbleibender Aktualisierung. Der Bergungsmodus muss explizit aktiviert werden und kann nicht gleichzeitig mit einem Fahrmodus laufen.
- **Sicherheit:** Nur aufgebockt testen, dann in einem abgesperrten Bereich. Das Browser-Dashboard ist kein zertifizierter Not-Aus. Der Watchdog ersetzt keinen physischen Abschalter. Die Tastatursteuerung ist noch nicht mit realem Browser und Hardware validiert. Mehrere gleichzeitig geöffnete Browserfenster sind für Bergung nicht unterstützt.
- Das Dashboard benutzt `assets/recovery_keys.js`. Diese Datei muss beim Entpacken erhalten bleiben. `dash_clientside.set_props` muss von der eingesetzten Dash-Version unterstützt werden.
- Der Browserprofil-/Cache-Fehler ist weiterhin nicht reproduziert und durch V5 nicht nachweislich behoben.

### Tests

`python -m compileall -q .`

`python -m unittest discover -s tests -p 'test_*.py'`

Die neuen Tests prüfen Health-Übergänge, gegenseitigen Ausschluss von autonomen und manuellen Befehlen, Deadman, Rückwärtsfahrt und Watchdog mit Mock-Fahrzeug. Keine echte Hardware wird in diesen Tests angesteuert.

## V5.1: Controller-Diagnose (vier Prüffragen)

`CameraMode` veröffentlicht jetzt eine Diagnose für den zuletzt bearbeiteten Frame:

1. **Welche Linien?** `lanes` und `frame_id` stammen aus dem Kameradatenpaket.
2. **Warum kein gültiger Winkel?** `raw_angle=None` kennzeichnet, dass die vorhandene Winkelberechnung keinen Wert geliefert hat. Die exakte geometrische Ursache wird vom bisherigen Kalkulator nicht zurückgegeben und kann deshalb nicht weiter aufgeschlüsselt werden.
3. **Welcher Winkel wurde verwendet?** `previous_angle`, `held_previous_angle`, `requested_angle` und `actual_angle` unterscheiden Rohwinkel, Fallback, Stellbefehl und gespeicherten Fahrzeug-Stellwert. Es gibt keine physische Winkelrückmessung.
4. **Warum wurde gestoppt?** `LANE_TIMEOUT`, `NO_CAMERA_DATA` und `CONTROLLER_EXCEPTION` werden explizit erfasst. Andere Stopps bleiben als solche kenntlich, ohne eine Ursache zu erfinden.

`DrivingHealthMonitor` protokolliert die Zustandswechsel im begrenzten RAM-Puffer. Das Cockpit zeigt die letzte Diagnose, die Eventliste die Übergänge. Keine Änderung am Fahralgorithmus, Timeout oder Motorverhalten.

**Achtung:** Die Diagnose gehört zur Controller-Frame-ID und kann älter als das aktuell sichtbare Kamerabild sein. Die Anzeige ist keine physische Rückmeldung vom Servo. Das Modell muss auf dem Pi geprüft werden, bevor autonome Testfahrten fortgesetzt werden.
