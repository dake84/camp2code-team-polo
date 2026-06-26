
import logging
from logging import handlers
import os


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
        "UltrasonicSensor": "logs/ultrasonic_sensor.log",
        "MockSensorCar": "logs/mocksensor_car.log"
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
