# Camp2Code Dashboard V5.4: Sessions und Replay

## Änderungen

- Behebt die Callback-Signatur des Replay-Seeks. Die Fehlermeldung `seek() missing ... bounds` stammt aus einem abweichenden installierten Stand; diese vollständige Dashboard-Datei überschreibt die alte Callback-Definition.
- Neue `session_recorder.py`: Ein Thread liest die vorhandene Ringbuffer-Historie, schreibt JPEG-Rohbilder und CSV mit Frame-ID, Zeitstempeln, Fahrdaten oder `no_driving_state`.
- Persistente Sessions unter `Data_AI/sessions/session_.../`. Die Aufzeichnung läuft unabhängig vom Browser. Die gespeicherten Daten bleiben nach Neustart erhalten.
- Replay Lab kann gespeicherte Sessions laden und Frame-by-Frame abspielen; die Chart-Navigation verwendet die Frame-ID.
- Mini-Preview zeigt das vorhandene Kalibrier-Overlay statt des nackten Rohbildes. Keine zweite Linienerkennung.
- Neue CSS-Datei für responsive Tabs, Cards und Diagramme.

## Installation

Im Repository-Root: `unzip -o camp2code_dashboard_v54.zip`. Vorher Git-Checkpoint erstellen. Danach `python -m compileall -q Camp2CodeDashboard.py session_recorder.py` und `python -m unittest discover -s tests -p 'test_session_recorder.py'`.

## Grenzen

- Der Recorder schreibt bereits verfügbare Frames. Fahrentscheidungen, die erst nach dem Speichern eintreffen, werden nicht nachgetragen. Fehlende Werte bleiben leer.
- Frameverluste sind möglich, wenn die SD-Karte zu langsam ist oder Frames vor dem Recorder-Tick aus dem Ringbuffer fallen.
- Sessions werden auf der SD-Karte gespeichert; freien Speicher kontrollieren. Es gibt noch keine Quota und keine automatische Löschung.
- Persistenter Replay verwendet gespeicherte Rohbilder. Linien-Overlays sind für alte Sessions nicht rekonstruiert, weil das CSV noch keine Liniengeometrie speichert.
- Der bisherige Ringbuffer-Export bleibt nur für den Live-Ringbuffer sinnvoll.
- Keine Änderung an der Motorsteuerung, am DrivingMode oder am Speed-Wert. Die 30 ist eine vom Nutzer vorgenommene lokale Anpassung und wird nicht überschrieben.
- Kein Hardware-/Dash-Browser-Integrationstest durchgeführt. Vor der nächsten autonomen Fahrt zuerst mit stehendem Fahrzeug prüfen.
