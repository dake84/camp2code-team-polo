import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys
import threading
import time
from typing import Tuple

import numpy as np
from InfraredSensor import InfraredSensor
from SensorCar import SensorCar

import time

def starte_poti_tuning(ir_sensors:InfraredSensor) -> Tuple[list[float], list[float], list[list[float]]]:
    print("\n" + "="*60)
    print(" 🔧 HARDWARE-TUNING: INFRAROT-SENSOREN (POTI)")
    print("="*60)
    print(" ANLEITUNG:")
    print(" 1. Stelle das Auto so auf die Strecken-Kante, dass")
    print("    z. B. Sensor 1 auf WEISS und Sensor 5 auf SCHWARZ steht.")
    print(" 2. Nimm einen kleinen Schraubendreher und drehe")
    print("    MILLIMETERWEISE am Potentiometer des Sensor-Boards.")
    print(" 3. Dein Ziel: Maximiere das 'DELTA' (Differenz).")
    print("    -> Weißer Boden = Hoher Wert")
    print("    -> Schwarze Linie = Niedriger Wert")
    print(" -> Beenden mit STRG+C")
    print("="*60 + "\n")

    values = ir_sensors.sensor_values
    normalized_values =ir_sensors._normalize(values)
    min_values = values.copy()
    max_values = values.copy()
    min_normalized_values = normalized_values.copy()
    max_normalized_values = normalized_values.copy()
            
    all_values = [values]

    try:
        while True:
            ir_sensors._cfg._load_config_file()
            # 1. Aktuelle Werte holen (Passe den Methoden-Namen an euren Code an)
            values = ir_sensors.sensor_values
            normalized_values =ir_sensors._normalize(values)
            all_values.append(values)

            assign_smaller = lambda x,y: x if x < y else y
            assign_larger = lambda x,y: x if x > y else y
            for i in range(len(values)):
                min_values[i] = assign_smaller(values[i], min_values[i])
                max_values[i] = assign_larger(values[i], max_values[i])
                min_normalized_values[i] = assign_smaller(normalized_values[i], min_normalized_values[i])
                max_normalized_values[i] = assign_larger(normalized_values[i], max_normalized_values[i])

            # 2. Das Delta berechnen (Höchster Wert minus niedrigster Wert in diesem Moment)
            min_val = min(values)
            max_val = max(values)
            min_normalized_val = min(normalized_values)
            max_normalized_val = max(normalized_values)
            delta = max_val - min_val
            delta_normalized = min_normalized_val - max_normalized_val
            
            # 3. Den String schön formatieren (Feste Breite mit :4d sorgt dafür, 
            #    dass die Zahlen nicht wackeln)
            ausgabe = (
                f"  Live: "
                f"S1:[{values[0]:4d}]  S2:[{values[1]:4d}]  S3:[{values[2]:4d}]  "
                f"S4:[{values[3]:4d}]  S5:[{values[4]:4d}] "
                f" |  DELTA: {delta:4d} "
            )

            # 4. Visuelles Ampel-Feedback für das Team hinzufügen
            if delta < 50:
                ausgabe += "🔴 (Zu schwach - weiter drehen!)"
            elif delta < 150:
                ausgabe += "🟡 (Besser, aber geht da noch was?)"
            else:
                ausgabe += "🟢 (PERFEKT! Finger weg vom Poti!)"
                
            # \r springt an den Zeilenanfang, \033[K löscht den Rest der Zeile
            print(f"\r{ausgabe}", end="\033[K", flush=True)

            ausgabe2 = (
                f"  Norm: "
                f"S1:[{normalized_values[0]:4.2f}]  S2:[{normalized_values[1]:4.2f}]  S3:[{normalized_values[2]:4.2f}]  "
                f"S4:[{normalized_values[3]:4.2f}]  S5:[{normalized_values[4]:4.2f}] "
                f" |  DELTA: {delta_normalized:4.2f} "
            )
            # \033[F springt eine Zeile hoch. Da wir 2 Zeilen ausgeben, springen wir 
            # am Anfang JEDES Durchlaufs erst mal eine Zeile hoch, um die alten Daten sauber zu überschreiben.
            print(f"\033[F\r{ausgabe}\033[K\n\r{ausgabe2}\033[K", end="", flush=True)            
            # Kurze Pause, damit man die Zahlen noch lesen kann
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        # Fängt STRG+C ab, damit das Skript nicht mit einem roten Fehler abstürzt
        print("\n\n ✅ Tuning beendet. Ihr könnt jetzt die Kalibrierung starten!")    
    except Exception as e:
        print("\n\n")
        raise e
    
    return min_values, max_values, all_values

def do_funny_stats(min_values:list[float], max_values:list[float], all_values:list[list[float]]):
        print(f"\nAusgelesene min values {min_values}")
        print(f"Ausgelesene max values {max_values}")
        print(f"\nsensor_min_values (dynamisch kalibrierte Werte): {ir.sensor_min_values}")
        print(f"sensor_max_values (dynamisch kalibrierte Werte): {ir.sensor_max_values}")

        for i in range(2):
            print(f"\nMathematics on axis {i}: mean: {np.mean(all_values, axis=i)}, min: {np.min(all_values, axis=i)}, max: {np.max(all_values, axis=i)}")    



if __name__ == '__main__':


    
    log_format = logging.Formatter('%(filename)s:%(lineno)d]: %(asctime)s - %(name)s - %(levelname)s - %(message)s')

    logger = logging.getLogger("run_ir_sensors")
    logger.setLevel(logging.DEBUG)
        
    # Handler hinzufügen, falls noch keiner existiert (verhindert doppelte Logs)
    file_handler = TimedRotatingFileHandler(
        filename="run_ir_sensors.log",
        when = "m",  # LogFile vergisst Einträge älter als eine Minute 
        interval=5, 
        encoding="utf-8")
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)
            
    stop_event = threading.Event()

    sc = SensorCar()
    ir = InfraredSensor(car=sc, logger=logger)


    while True:     
        min_values, max_values, all_values = (None, None, None)
        try:
            mode = int(input("Welchen Modus ausführen? <1> = Poti-Tuning-Tools, <2> = Test dynamische Kalibierung, <3> zuletzt ausgelesene Werte in cfg speichern, <andere Taste> = beenden"))

            if (mode == 1):
                os.system('clear')
                min_values, max_values, all_values = starte_poti_tuning(ir)
                do_funny_stats(min_values, max_values, all_values)
            elif (mode == 2):
                os.system('clear')
            elif (mode == 3):
                if (min_values is None or max_values is None or all_values is None):
                    print("Bitte zuerst eine Funktion ausführen, um Werte auszulesen")
                else:
                    ir._cfg.set_config("sensor_min_values", min_values)
                    ir._cfg.set_config("sensor_max_values", max_values)
                    ir._cfg._save_config()
                    print("Config-Datei aktualisiert")

            input("<ENTER>")
            os.system('clear')

        except ValueError:
            print("IR-Sensor-Utils beendet")
            break



    # ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event])
    
    # min_values = []
    # max_values = []
    # all_values = []

    # try:
    #     ir_sensor_thread.start()
    #     values = ir.sensor_values
    #     min_values = values
    #     max_values = values
    #     all_values = [values]
    #     normalized_values = [ir._normalize(values)]

    #     while not stop_event.is_set():
    #         values = ir.sensor_values
    #         all_values.append(values)
    #         normalized_values.append(ir._normalize(values))

    #         time.sleep(0.1)

    #         print(f"Ausgelesene Sensor-Werte: {values}", end="\r", flush=True)
    #         for i in range(len(values)):
    #             min_values[i] = values[i] if values[i] < min_values[i] else min_values[i]
    #             max_values[i] = values[i] if values[i] > max_values[i] else max_values[i]

    # except KeyboardInterrupt: 
    #     stop_event.set()
    #     print("Stopp!")

    #     print(f"Ausgelesene min values {min_values}")
    #     print(f"Ausgelesene max values {max_values}")
    #     print(f"sensor_min_values (dynamisch kalibrierte Werte): {ir.sensor_min_values}")
    #     print(f"sensor_max_values (dynamisch kalibrierte Werte): {ir.sensor_max_values}")

    #     for i in range(2):
    #         print(f"Mathematics on axis {i}: mean: {np.mean(all_values, axis=i)}, min: {np.min(all_values, axis=i)}, max: {np.max(all_values, axis=i)}")

    # except Exception as e:
    #     logger.exception(e)     




