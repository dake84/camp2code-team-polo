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
register('mein_prozessor', 'Mein Prozessor', 'mein_modul', 'MeinProzessor', live=False)
```

Anforderungen an die Klasse: `process(raw, params) -> PipelineResult` mit `lanes` als
`(m, b)` (`y = m*x + b` im ROI). `live=False` = nur Simulation/A/B; erst nach Tests auf `True`.

Für das Pipeline Studio zusätzlich `dashboard_stages()` als Klassenmethode (Stufen, Regler, Schalter, siehe
`pipeline_processors.py` und ARCHITECTURE.md 7.2). Ohne Beschreibung zeigt das Studio nur Rohbild und Linien.
Eingetragen und fahrfreigegeben (`live=True`) ist der `OpusLaneProcessor` (Schlüssel `opus`).

## Offline (Laptop, ohne Hardware)

```bash
python Camp2CodeDashboard.py --offline [--port 8051]     # http://127.0.0.1:8051
```

Session-Ordner aus `Data_AI/sessions/` auf den Laptop kopieren. Kamera, Motoren, Bergung
und Aufnahme sind deaktiviert; Replay Lab, Pipeline Studio (Replay → Studio, A/B) und
Simulation funktionieren. Benötigt: Python 3.11+, `opencv-python`, `numpy`, `dash`,
`dash-bootstrap-components`, `plotly` (siehe `requirements-c2c.txt`).


## Wiedergabe (PAKET-H1)

* **Tempo** 0,25x / 0,5x / 1x / 2x / 4x bezieht sich auf die aufgezeichneten Zeitstempel der Frames
  (`SimFrame.captured_ns`), nicht auf feste fps; Aufnahmen haben ca. 13–16 Hz, schwankend. Tempowechsel
  braucht keine Neuberechnung. Am Ende wird automatisch pausiert.
* **Echtzeit:** zeitgenau, Anzeigeframes dürfen übersprungen werden. **Jeder Frame:** nichts wird übersprungen,
  die Wiedergabe läuft langsamer, wenn die Anzeige nicht hinterherkommt.
* **Anzeigerate** (Frames/s) wird serverseitig gemessen (ausgelieferte Frames). Der Anzeigetakt beträgt
  150 ms (`PLAY_TICK_MS`), daher sind höchstens etwa 6–7 Anzeigeframes/s möglich. Die Darstellung im Browser
  ist nicht gemessen.
* Änderungen an Quelle, Prozessor, Parameterquelle, Frame-Bereich oder Vorlauf verwerfen das Ergebnis; Play ist
  erst nach erfolgreicher Berechnung möglich.

## Verarbeitungsgröße (PAKET-H3)
Aufnahmen ab Revision 4 speichern das große Bild (`image_width` x `image_height`) und die Verarbeitungsgröße
(`process_width` x `process_height`). Die Simulation und das A/B-Labor verkleinern das gespeicherte Bild per Software
(INTER_AREA) auf diese Größe und rechnen dort; Linien gelten im Verarbeitungsbild und werden nur für die Anzeige auf das große
Bild umgerechnet. **Abweichung zur Aufnahme:** Kam das damalige kleine Bild aus dem zweiten Kamera-Stream (`process_source =
camera`, ISP-Skalierung und YUV420), unterscheidet es sich leicht von dem aus dem JPEG verkleinerten Bild (Pixelwerte, Kanten,
JPEG-Artefakte); einzelne Linien und Winkel können minimal abweichen. Sessions ohne diese Spalten (Revision 2 und 3) werden
wie bisher auf dem gespeicherten Bild gerechnet.


## Simulation Runs (PAKET-I1)

Berechnen, speichern und abspielen sind getrennt (`simulation_runs.py`):

1. **Simulation berechnen** startet einen Hintergrund-Job (hoechstens einer gleichzeitig). Die Seite bleibt bedienbar;
   Progressbar, „Frame X von Y“, Phase, Run-Name und Fehlertext erscheinen unter *Berechnung*, **Abbrechen** stoppt zwischen
   zwei Frames.
2. Das Ergebnis wird als **Run** in `Data_AI/simulation_runs/<run_id>/` gespeichert (`manifest.json` atomar, `results.jsonl`
   je Frame eine Zeile, inkrementell). Die Bilder bleiben in der Session (`image_ref`), Sessions werden nie veraendert.
3. **Gespeicherte Simulation Runs**: Auswahl eines Runs laedt die Ergebnisse (keine Lane-, Winkel- oder Speed-Berechnung) und
   spielt sie mit der bestehenden Wiedergabe ab. Bereits berechnete Runs bleiben erhalten.
4. **Fingerprint** (SHA-256) ueber Prozessor, Parameter (Studio-Slider oder die damaligen Parameter der gerechneten Frames),
   Frame-Bereich/Vorlauf, Regler (`steering_gain`, `lost_line_timeout`, `v_min`, `v_max`), Speed Policy und Fahrmodus. Existiert
   fuer Quell-Session und Fingerprint ein vollstaendiger Run, wird er ausgewaehlt statt neu gerechnet; **Neu berechnen
   (erzwingen)** legt bewusst einen weiteren Run an.
5. Quelle sind **gespeicherte Sessions**; der fluechtige Live-Ringbuffer kann nicht als Run gespeichert werden.
   Die Simulation uebernimmt jetzt die aktuelle Speed Policy der Anwendung (vorher immer fest 30).


## Schnellere Wiedergabe (PAKET-I2)
Die Anzeige rechnet nichts neu; teuer waren Bildaufbereitung und Uebertragung. Jetzt: ein Dekodieren je Frame, HUD auf dem
verkleinerten Bild, Cache der letzten 48 Anzeigebilder und Vorausberechnung der naechsten Frames waehrend Play.
**Bildgroesse** (Schnell 640 / Standard 960 / Hoch 1280) bestimmt die Breite beider Bilder; die gespeicherten Bilder bleiben
unveraendert. Messung auf dem Pi: `python scripts/sim_render_bench.py SESSION`.


## Wiedergabe im Browser (PAKET-I3)

Die Wiedergabe laeuft im Browser (`assets/sim_player.js`, Server-Teil `sim_player.py`): Play, Pause, Slider, Tempo,
Einzelschritte und Bildgroesse wirken sofort und brauchen waehrend der Wiedergabe keine Dash-Callbacks mehr. Der Server liefert
`/_c2c/sim/<token>/meta.json` (Werte je Frame) und `/_c2c/sim/<token>/<then|now>/<index>.jpg?w=640|960|1280` (fertige Bilder mit
HUD); der Browser laedt die naechsten Bilder im Voraus (3 Bildpaare parallel) und spielt nach den aufgezeichneten Zeitstempeln
(Echtzeit mit Frame-Ueberspringen oder "Jeder Frame"). Der Frame-Balken im Diagramm folgt der Wiedergabe, Ziehen und Klick ins
Diagramm springen lokal. Umschalter "Ueber den Server (alt)" zeigt die bisherige Server-Wiedergabe.


## Replay Lab im Browser, HUD-Stil, Vollbild (PAKET-I4)

Das Replay Lab spielt gespeicherte Sessions wie die Simulation im Browser ab (`replay_player.py`, `assets/replay_player.js`):
Play, Pause, Slider, Tempo, Einzelschritte, Bildgroesse, HUD-Stil und Vollbild wirken sofort, waehrend der Wiedergabe gibt es keine
Dash-Callbacks. HUD-Stil: wie im Cockpit, klassisch, Jetpilot, Arcade oder ohne HUD; Jetpilot und Arcade zeigen Lenkspur und
Spur-Streak (Verlauf aus den vorangehenden Frames der Session). Vollbild (Taste F, Doppelklick aufs Bild) mit Bedienleiste; ebenso fuer
die Simulation (`assets/sim_player_fullscreen.js`). Der Umschalter "Ueber den Server (alt)" zeigt die bisherige Anzeige. Der
Live-Ringbuffer ist nur ueber den Server abspielbar.
