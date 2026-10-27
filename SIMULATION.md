# Simulation, Prozessor-Verzeichnis und Offline-Betrieb (Paket F)

## Cockpit: Live | Simulation

Oben im **Driving Cockpit**: Umschalter **Live** / **Simulation (Session-Replay)**.

1. Quelle wählen: gespeicherte Session oder *Live-Ringbuffer (Momentaufnahme)*.
2. Prozessor wählen (alle aus `processor_registry.py`, inkl. **MOCK**).
3. Parameter: *von damals* (aus `frames.csv`) oder *aktuelle Studio-Slider*.
4. Optional Frame-Bereich und **Vorlauf** (Standard 30 Frames; Regler und Prozessor
   "warmlaufen", geht nicht in die Kennzahlen ein).
5. **Simulation berechnen** → Damals/Jetzt-Bilder, Vergleich je Frame, Kennzahlen, Diagramm.
   ◀ ▶ ▶Play, Slider oder Klick ins Diagramm.

Sicherheit: Simulation fährt nie (Fahrzeug-Attrappe `ReplayCar`). Solange *Simulation*
gewählt ist, ist **START gesperrt**. Während einer Fahrt wird nicht simuliert.

Zeitbasis: Der Regler (`CameraMode`) bekommt in der Simulation die **aufgezeichneten
Zeitstempel** (`self._clock`). `LANE_TIMEOUT` tritt daher am selben Frame auf wie damals –
unabhängig davon, wie schnell gerechnet wird. Nach einem Simulations-Stopp wird der Regler
beim nächsten Frame neu scharf geschaltet (wie ein erneutes START).

## Batch-Report

```bash
python scripts/sim_report.py SESSION --proc copilot --proc mock [--params studio] [--from 200 --to 400]
```

Konsole: Kennzahlen (zum Einfügen in den Chat) · `~/c2c-sim-SESSION.html` (Diagramm,
auffällige Frames) · `~/c2c-sim-SESSION.csv` (je Frame und Prozessor).

## Eigenen Prozessor eintragen

```python
# processor_registry.py (oder beim Import des eigenen Moduls)
register('opus', 'Modus Opus (Suchfenster + Gedächtnis)', 'modus_opus', 'ModusOpusProcessor', live=False)
```

Anforderungen an die Klasse: `process(raw, params) -> PipelineResult` mit `lanes` als
`(m, b)` (`y = m*x + b` im ROI). `live=False` = nur Simulation/A/B; erst nach Tests auf `True`.

## Offline (Laptop, ohne Hardware)

```bash
python Camp2CodeDashboard.py --offline [--port 8051]     # http://127.0.0.1:8051
```

Session-Ordner aus `Data_AI/sessions/` auf den Laptop kopieren. Kamera, Motoren, Bergung
und Aufnahme sind deaktiviert; Replay Lab, Pipeline Studio (Replay → Studio, A/B) und
Simulation funktionieren. Benötigt: Python 3.11+, `opencv-python`, `numpy`, `dash`,
`dash-bootstrap-components`, `plotly` (siehe `requirements-c2c.txt`).
