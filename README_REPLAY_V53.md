# V5.3: Replay-Bereichsauswahl

Das Replay-Lab erhält eine Bereichsauswahl über Plotly-Zoom (X-Achse = Frame-ID). Die Auswahl filtert Wiedergabe, Einzelbildnavigation und ZIP-Export auf die tatsächlich noch vorhandenen FrameRecords. Doppelklick oder "Bereich zurücksetzen" entfernt den Filter. Ein Klick auf einen Messpunkt springt weiterhin zum Frame.

## Installation

Im Repository-Root `unzip -o camp2code_replay_v53.zip`, anschließend `python -m compileall -q Camp2CodeDashboard.py replay_selection.py` und `python -m unittest discover -s tests -p 'test_replay_selection.py'`.

## Einschränkungen

- Nur der flüchtige Ringbuffer (standardmäßig 60 Frames), keine ganze Fahrt.
- Wird ein Frame aus dem Ringbuffer verdrängt, kann er nicht wiederhergestellt werden. Der Export enthält ausschließlich vorhandene Frames.
- Das Diagramm wird beim Refresh neu aufgebaut. Ob die Plotly-Zoomauswahl im laufenden Browser stabil erhalten bleibt, muss auf dem Raspberry Pi getestet werden.
- Keine Änderung der Fahrregelung oder Geschwindigkeitsstrategie. Die Geschwindigkeit in CameraMode ist weiterhin ein konstanter Steuerwert von 30.
