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
