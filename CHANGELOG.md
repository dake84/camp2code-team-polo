# Changelog Paket A+B

- Paket A: `SensorSnapshot`, Zeitstempel für IR und Ultraschall, gemeinsames `CameraCar`-Sensormodell.
- Paket B: Ringbuffer-basierter Recorder mit Frame-ID-Verknüpfung, Rohbildern, Linien, Prozessparametern und Status für fehlende Fahrentscheidungen.
- `BaseCar`: negative Geschwindigkeit als Betrag intern speichern und Richtung getrennt halten.
- `ConfigReader`: boolesche Zeichenketten explizit auswerten.
- `FollowLine`: PID-Zeit und Zustandsfortschreibung korrigiert.
- `DrivingMode`: Start-/Stop-Thread-Lifecycle verbessert.
- `CameraSensor`: leere Frames und Ressourcenfreigabe behandelt; zyklisches Debug-JPEG entfernt.
- `CameraDataProcessed` als Kompatibilitätsbrücke beibehalten.
