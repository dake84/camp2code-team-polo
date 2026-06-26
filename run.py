import logging
import threading
import time
import os
import sys

import AdvancedKeyboardMode
import Driving
import InfraredSensor
from SensorCar import SensorCar  # MockSensorCar entfernt
import UltrasonicSensor
import logging_setup
from ConfigReader import ConfigReader

def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

def hole_modus_parameter(wahl: str) -> str:
    """Liest die spezifischen Parameter für den gewählten Modus aus der Config."""
    cfg_sonic = ConfigReader("soniccar_controller")
    cfg_pid = ConfigReader("sensorcar_controller")
    
    if wahl in ["1", "2"]:  # Basisfahrt / Kreise
        return f"Start-Tempo: {cfg_sonic.get_int('start_speed', 60)}%"
    elif wahl in ["3", "4"]:  # Hindernis / Explorer
        return f"Stopp-Abstand: {cfg_sonic.get_int('ultrasonic_max_distance_to_stop', 5)}cm | Start-Tempo: {cfg_sonic.get_int('start_speed', 60)}%"
    elif wahl == "5":  # Follow Line
        return (f"P: {cfg_pid.get_int('korrektur_proportional', 25)} | "
                f"D: {cfg_pid.get_int('korrektur_differential', 10)} | "
                f"Kontrast-Limit: {cfg_pid.get_float('minimum_line_contrast', 0.5)}")
    return "Keine spezifischen Parameter"

def zeige_live_dashboard(sc, wahl: str):
    """Baut das erweiterte Dashboard im Speicher und gibt es flackerfrei aus."""
    speed = getattr(sc, "speed", 0)
    distance = getattr(sc, "distance", 300)
    steering_angle = getattr(sc, "steering_angle", 90)
    
    p_val = getattr(sc, "_p_wert", 0.0)
    i_val = getattr(sc, "_i_wert", 0.0)
    d_val = getattr(sc, "_d_wert", 0.0)
    
    ir_values = getattr(sc, "ir_sensor_values", [[1.,1.,1.,1.,1.]])
    if isinstance(ir_values, list) and len(ir_values) > 0 and isinstance(ir_values[0], list):
        ir_display = ir_values[-1]
    else:
        ir_display = ir_values

    # Sensor-Statistiken berechnen
    s_min = min(ir_display) if ir_display else 0.0
    s_max = max(ir_display) if ir_display else 0.0
    s_delta = s_max - s_min

    try:
        is_on_line_state = "JA 🛣️ " if sc.is_on_line() else "NEIN"
    except Exception:
        is_on_line_state = "UNKNOWN"

    ir_str = " ".join([f"S{i+1}:[{v:.2f}]" for i, v in enumerate(ir_display)])
    aktuelle_params = hole_modus_parameter(wahl)

    buffer = (
        "\033[H"  # Cursor nach oben links
        f"┌" + "─"*74 + "┐\033[K\n"
        f"│ 📊 LIVE-DASHBOARD (Fahrt aktiv)                                         │\033[K\n"
        f"├" + "─"*74 + "┤\033[K\n"
        f"│ 🏎️  Tempo: {speed:4d} %   | 📐 Lenkwinkel: {steering_angle:3d}°   | 🛣️  Auf Linie?: {is_on_line_state:<10s} │\033[K\n"
        f"│ 📏 US-Distanz: {distance:3d} cm                                                 │\033[K\n"
        f"│ 📷 IR-Sensoren: {ir_str:<53s} │\033[K\n"
        f"│ 📈 IR-Stats:    Min: [{s_min:.2f}]  |  Max: [{s_max:.2f}]  |  Delta: [{s_delta:.2f}]           │\033[K\n"
        f"│ 🎛️  PID-Status:  P: {p_val:5.2f}  |  I: {i_val:5.2f}  |  D: {d_val:5.2f}                       │\033[K\n"
        f"├" + "─"*74 + "┤\033[K\n"
        f"│ ⚙️  Aktive Config-Werte: {aktuelle_params:<47s} │\033[K\n"
        f"└" + "─"*74 + "┘\033[K\n"
        f"\n  🛑 HINWEIS: Drücke [STRG + C] um die Fahrt zu BEENDEN.\033[J"
    )
    
    sys.stdout.write(buffer)
    sys.stdout.flush()

def parameter_menue():
    cfg_controller = ConfigReader("soniccar_controller")
    cfg_pid = ConfigReader("sensorcar_controller")
    
    while True:
        clear_screen()
        print("="*65)
        print(" 🛠️  FAHRPARAMETER ANPASSEN (Temporär für diese Session)")
        print("="*65)
        print(f"  [1] Start-Geschwindigkeit (Sonic) : {cfg_controller.get_int('start_speed', 60)} %")
        print(f"  [2] Mindest-Stopp-Abstand (Sonic) : {cfg_controller.get_int('ultrasonic_max_distance_to_stop', 5)} cm")
        print(f"  [3] PID: Proportional (P-Wert)    : {cfg_pid.get_int('korrektur_proportional', 25)}")
        print(f"  [4] PID: Differential (D-Wert)    : {cfg_pid.get_int('korrektur_differential', 10)}")
        print("-"*65)
        print("  [0] Zurück zum Hauptmenü")
        print("="*65)
        
        wahl = input("\n  Welchen Parameter ändern? -> ").strip()
        if wahl == "0":
            break
        elif wahl == "1":
            val = input("  Neuer Wert für Start-Geschwindigkeit (20-100): ").strip()
            if val.isdigit(): cfg_controller._json_config["soniccar_controller"]["start_speed"] = int(val)
        elif wahl == "2":
            val = input("  Neuer Wert für Stopp-Abstand: ").strip()
            if val.isdigit(): cfg_controller._json_config["soniccar_controller"]["ultrasonic_max_distance_to_stop"] = int(val)
        elif wahl == "3":
            val = input("  Neuer P-Wert für Linienfolger: ").strip()
            if val.isdigit(): cfg_pid._json_config["sensorcar_controller"]["korrektur_proportional"] = int(val)
        elif wahl == "4":
            val = input("  Neuer D-Wert für Linienfolger: ").strip()
            if val.isdigit(): cfg_pid._json_config["sensorcar_controller"]["korrektur_differential"] = int(val)

def main():
    logging_setup.setup_project_logging(logging.INFO)
    root_logger = logging.getLogger()

    # Nutzt jetzt regulär das echte SensorCar()
    sc = SensorCar()
    sc.stop()

    us = UltrasonicSensor.UltrasonicSensor(sc)
    ir = InfraredSensor.InfraredSensor(sc)
    
    stop_event = threading.Event()
    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event], daemon=True)
    ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event], daemon=True)
    
    ir_sensor_thread.start()
    us_sensor_thread.start()

    modus_klassen = {
        "1": {"name": "Forward / Backward", "class": Driving.ModeOne, "interactive": False},
        "2": {"name": "Circles", "class": Driving.ModeTwo, "interactive": False},
        "3": {"name": "Approach Obstacle", "class": Driving.ApproachObstacle, "interactive": False},
        "4": {"name": "Room Explorer", "class": Driving.RoomExplorer, "interactive": False},
        "5": {"name": "Follow Line", "class": Driving.FollowLine, "interactive": False},
        "6": {"name": "Keyboard Control (Interactive)", "class": AdvancedKeyboardMode.KeyboardMode, "interactive": True}
    }

    try:
        while True:
            clear_screen()
            print("="*60)
            print("  🏎️   SENSOR CAR - CONTROL INTERFACE")
            print("="*60)
            print("  Verfügbare Fahrmodi:")
            print("-"*60)
            
            for key, info in modus_klassen.items():
                print(f"  [{key}] {info['name']}")
                
            print("-"*60)
            print("  [P] 🛠️  FAHRPARAMETER KONFIGURIEREN (Config)")
            print("  [0] ❌ PROGRAMM BEENDEN")
            print("="*60)

            wahl = input("\n  Deine Wahl -> ").strip()

            if wahl == "0":
                break
            
            elif wahl.upper() == "P":
                parameter_menue()

            elif wahl in modus_klassen:
                mode_info = modus_klassen[wahl]
                mode_instance = mode_info["class"](car=sc)
                
                if mode_info["interactive"]:
                    clear_screen()
                    mode_instance.start()
                    sc.stop()
                else:
                    root_logger.disabled = True
                    clear_screen()
                    mode_instance.start()
                    
                    try:
                        while True:
                            zeige_live_dashboard(sc, wahl)
                            time.sleep(0.1)
                    except KeyboardInterrupt:
                        sys.stdout.write("\n\n")
                        sys.stdout.flush()
                        print("  Abbruch-Signal registriert...")
                    
                    mode_instance.stop()
                    sc.stop()
                    
                    root_logger.disabled = False
                    print("\n  Modus erfolgreich beendet. Zurück zum Menü mit <ENTER>.")
                    input()
            else:
                print("\n  ❌ Ungültige Auswahl!")
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n  👋 Programm per Haupt-STRG+C abgebrochen.")
    finally:
        print("\n  Räume Hardware auf...")
        stop_event.set()
        sc.stop()
        us_sensor_thread.join()
        ir_sensor_thread.join()
        print("  [Done] Ready to pack!")

if __name__ == '__main__':
    main()