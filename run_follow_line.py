
import logging
import signal
import threading

import CarLogger
import ConfigReader
import Driving
import InfraredSensor
import UltrasonicSensor
import SensorCar
import os
from logging import handlers

import sys
import traceback


def setup_project_logging(default_level=logging.DEBUG):
    """
    Konfiguriert das Logging zentral. Erstellt für die Hauptkomponenten
    eigene Log-Dateien und setzt das initiale Log-Level.
    """
    # Verzeichnis für Logs erstellen, falls nicht vorhanden
    os.makedirs("logs", exist_ok=True)
    
    # Gemeinsames Format für alle Logs
    log_format = logging.Formatter('[%(filename)s:%(lineno)d]: %(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Definition der Module und ihrer Log-Dateien
    log_mapping = {
        "BaseCar": "logs/base_car.log",
        "SonicCar": "logs/sonic_car.log",
        "SensorCar": "logs/sensor_car.log",
        "DriveController": "logs/drive_controller.log",
        "InfraredSensor": "logs/infrared_sensor.log",
        "UltrasonicSensor": "logs/ultrasonic_sensor.log"
    }

    for logger_name, log_file in log_mapping.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(default_level)
        
        # Handler hinzufügen, falls noch keiner existiert (verhindert doppelte Logs)
        if not logger.handlers:
            file_handler = handlers.RotatingFileHandler(log_file, encoding="utf-8", backupCount=3)
            file_handler.setFormatter(log_format)
            logger.addHandler(file_handler)
            
    # Optional: Ein globaler Konsolen-Logger für wichtige Ausgaben
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    console_handler.setLevel(logging.WARNING) # Nur Warnungen/Fehler auf die Konsole
    logging.getLogger().addHandler(console_handler)    

if __name__ == '__main__':
    setup_project_logging()
    sc = SensorCar.SensorCar()
    sc.stop()

    # Liest IR-Sensor und schreibt Werte ins Auto
    ir = InfraredSensor.InfraredSensor(sc)
    us = UltrasonicSensor.UltrasonicSensor(sc)
    
    # Liest Werte aus dem Auto und schreibt sie in ein Log-File
    cl = CarLogger.CarLogger(sc)
    # Liest Werte aus dem Auto und steuert das Auto
    dc = Driving.DriveController(sc, Driving.DrivingMode.ADVANCED_FOLLOW_LINE)
    # Liest Werte aus dem Controller und schreibt sie in ein Log-File
    # dl = CarLogger.CarLogger(log_object=dc, logfile="driving_controller_log.json", log_name="driving_controller_log")


    stop_event = threading.Event()

    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event])
    ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event])
    controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event, Driving.DrivingMode.ADVANCED_FOLLOW_LINE])
    #dl_thread=threading.Thread(target=dl.run, args=[stop_event])
    cl_thread=threading.Thread(target=cl.run, args=[stop_event])

    try:

        print("Starting sensor thread...", end="")
        us_sensor_thread.start()
        ir_sensor_thread.start()
        print("...started!")

        print("Starting logging thread...", end="")
        #dl_thread.start()
        cl_thread.start()
        print("...started!")
        print("Starting controller thread...", end="")
        controller_thread.start()
        print("...started!")

        input("Stop?")

        stop_event.set()

    except KeyboardInterrupt:
        stop_event.set()

    finally:
        # Just to be surrrrre
        sc.stop()

        print("Ending sensor thread...", end="")
        ir_sensor_thread.join()
        us_sensor_thread.join()
        print("...ended!")
        print("Ending logging thread...", end="")
        # dl_thread.join()
        cl_thread.join()
        print("...ended!")
        print("Ending controller thread...", end="")
        controller_thread.join()
        print("...ended!")
        
        # if ("j" == input("Sollen die geänderten Kalibrierungswerte des IR-Sensors gespeichert werden (j)?")):
        #     ir.save_calibration()
