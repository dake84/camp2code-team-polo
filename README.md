# Camp2Code: SensorCar Control System 🚗

Ein modulares, Python-basiertes Steuerungssystem für das Sunfounder PiCar-S (Raspberry Pi). Dieses Projekt implementiert eine multi-threaded Architektur zur Echtzeit-Auswertung von Ultraschall- und Infrarotsensoren sowie verschiedene autonome Fahrmodi (inkl. PID-gesteuerter Linienverfolgung und Hindernisvermeidung).

Entwickelt von **Team Polo** im Rahmen der Camp2Code Projektphase 1.
🔗 **Repository:** [dake84/camp2code-team-polo](https://github.com/dake84/camp2code-team-polo)

## 🏗️ Software-Architektur

Das Projekt folgt einem strikt objektorientierten Ansatz und trennt die physischen Fahrzeugkomponenten, die Sensorik und die Fahrlogik sauber voneinander.

### 1. Cars (Fahrzeug-Klassen)
Die Basis des Fahrzeugs ist hierarchisch aufgebaut, wobei jede Schicht spezifische Hardware abstrahiert:
* `BaseCar`: Verwaltet die Antriebs- und Lenkmotoren (`BackWheels`, `FrontWheels`). Beinhaltet Thread-sicheres Clamping für Geschwindigkeit und Lenkwinkel.
* `SonicCar` (erbt von `BaseCar`): Erweitert das Basisauto um das Speichern und Abrufen von Ultraschall-Distanzen.
* `SensorCar` (erbt von `SonicCar`): Das finale Modell, das zusätzlich Infrarot-Werte für die Linienverfolgung integriert und die PID-Regelparameter hält.

### 2. Sensors (Sensor-Klassen)
* `UltrasonicSensor` & `InfraredSensor`: Diese Klassen lesen die Hardware-Pins aus.
* **Threading:** Die Sensoren laufen in isolierten Threads und aktualisieren kontinuierlich die Werte im `SensorCar`-Objekt. Das Hauptprogramm (oder die Fahrmodi) muss so nicht auf Sensor-Inputs warten (Blockaden werden vermieden).

### 3. Driving Modes (Fahrlogik)
Die eigentliche Intelligenz ist in separate Klassen ausgelagert (z.B. `RoomExplorer`, `FollowLine`, `ApproachObstacle`). Sie erhalten bei der Initialisierung eine Instanz des Autos und steuern dieses basierend auf den kontinuierlich aktualisierten Sensordaten.

---

## 🚀 Getting Started

### Voraussetzungen
* Raspberry Pi OS
* Python 3.9+
* PiCar-S Hardware (inkl. PCA9685, Motor Driver, Ultraschall- & IR-Modul)

### Installation

1. **Repository klonen:**
```bash
   git clone [https://github.com/dake84/camp2code-team-polo.git](https://github.com/dake84/camp2code-team-polo.git)
   cd camp2code-team-polo
```


2. **Virtuelle Umgebung erstellen (empfohlen):**
```bash
python -m venv venv
source venv/bin/activate

```


3. **Abhängigkeiten installieren:**
```bash
pip install -r requirements.txt

```


4. **Hardware-Setup abschließen:**
Stelle sicher, dass die herstellerspezifische Datei `basisklassen.py` im Hauptverzeichnis des Projekts liegt.

---

## 💻 Bedienung & Nutzung

Das Projekt kann primär über das Terminal gesteuert werden. Die Datei `run_room_explorer.py` dient als interaktives Menü und orchestriert die verschiedenen Fahrmodi.

### Hauptprogramm starten

```bash
python run_room_explorer.py

```

**Was passiert hier im Hintergrund?** Die `run_room_explorer.py` zeigt exemplarisch das Zusammenspiel der Architektur:

1. Ein `SensorCar` (`sc`) wird instanziiert.
2. Die Sensoren (`us` und `ir`) werden mit dem Auto verknüpft und in eigenen Threads gestartet (`us_sensor_thread.start()`).
3. Die Driving Modes (`FollowLine`, `RoomExplorer`, etc.) werden mit dem Auto-Objekt initialisiert.
4. Per `input()`-Befehl wartet das Skript auf Nutzereingaben, um die `.start()` und `.stop()` Methoden der jeweiligen Fahrmodi auszuführen.

### IR-Sensoren kalibrieren (Utility)

Um die Infrarot-Sensoren für verschiedene Untergründe zu testen oder auszulesen, steht ein separates Utility-Skript zur Verfügung:

```bash
python -m utils.ir_sensor_utils

```

---

## ⚙️ Konfiguration (`config.json`)

Alle wichtigen Parameter des Autos, der Sensoren und der Fahrmodi können zur Laufzeit über die `config.json` angepasst werden. Hier ist ein Ausschnitt der wichtigsten Konfigurationsblöcke:

```json
{
    "room_explorer": {
        "explorer_max_time": 30,
        "ultrasonic_max_distance_to_stop": 30,
        "start_speed": 60,
        "start_angle": 90,
        "slow_down_window": 0.3
    },
    "sensorcar_controller": {
        "minimum_line_contrast": 0.5,
        "korrektur_proportional": 25,
        "korrektur_integral": 0.2,
        "korrektur_integral_boundary": 30,
        "korrektur_differential": 10,
        "slew_rate": 90
    }
}

```

### Parameter-Beschreibung:

* **`room_explorer`**
* `ultrasonic_max_distance_to_stop`: Distanz in cm, bei der das Auto vor einem Hindernis anhält oder ausweicht.
* `slow_down_window`: Faktor zur Reduzierung der Geschwindigkeit, wenn ein Hindernis näher rückt.


* **`sensorcar_controller` (PID-Regler für FollowLine)**
* `korrektur_proportional` (P): Bestimmt, wie stark das Auto lenkt, wenn es von der Linie abweicht.
* `korrektur_integral` (I): Gleicht dauerhafte Abweichungen (z.B. ein schiefes Chassis) über die Zeit aus.
* `korrektur_differential` (D): Dämpft die Lenkung, um ein "Aufschaukeln" (Schlangenlinien) zu verhindern.
* `slew_rate`: Limitiert die maximale Lenkgeschwindigkeit pro Frame für weichere Kurven.



---

## 🗺️ Roadmap / Status

* [x] Modus 1-4: Grundlegende Fahrfunktionen & Room Explorer
* [x] Modus 5: Follow Line (mit PID-Controller)
* [x] Live-Logging & Thread-Safety
* [ ] Modus 6: Erweiterte Linienverfolgung (In Entwicklung)
* [ ] Modus 7: Linienverfolgung kombiniert mit Hinderniserkennung (In Entwicklung)