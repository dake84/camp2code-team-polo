import csv
import os
import threading
import time
from datetime import datetime

import cv2

from CameraCar import CameraCar


class SaveData_4_AI:

    def __init__(self, car: CameraCar, frequency: float = 5.0):
        self._car = car
        self._frequency = frequency

        self._stop_event = None
        self._thread = None

        self._data_dir = None
        self._csv_file = None


    def start(self):
        if self.is_running():
            return

        # Hauptordner
        main_dir = 'Data_AI'
        os.makedirs(main_dir, exist_ok=True)

        # Unterordner für diese Aufnahme
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self._data_dir = os.path.join(main_dir, timestamp)
        os.makedirs(self._data_dir, exist_ok=True)

        # CSV-Datei
        self._csv_file = os.path.join(
            self._data_dir,
            'data.csv'
        )

        with open(self._csv_file, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([
                'image',
                'speed',
                'steering_angle'
            ])

        self._stop_event = threading.Event()

        self._thread = threading.Thread(
            target=self._save_loop,
            daemon=True,
            name='SaveData'
        )

        self._thread.start()


    def stop(self):
        if self._stop_event is None:
            return

        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=2)

        self._stop_event = None
        self._thread = None


    def is_running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
        )


    def _save_loop(self):
        period = 1.0 / self._frequency

        while not self._stop_event.is_set():

            start_time = time.monotonic()

            self._save_data()

            elapsed_time = time.monotonic() - start_time

            self._stop_event.wait(
                max(0.0, period - elapsed_time)
            )


    def _save_data(self):

        # Ein vollständiges zusammengehöriges Datenpaket holen
        camera_data_processed = self._car.camera_data_processed

        if camera_data_processed is None:
            return

        # Alle Daten aus genau diesem Paket
        image = camera_data_processed.process_image_lines
        speed = camera_data_processed.speed
        steering_angle = camera_data_processed.steering_angle
        timestamp = camera_data_processed.timestamp

        if image is None or image.size == 0:
            return

        if speed is None or steering_angle is None:
            return

        # Timestamp des Datenpakets für eindeutigen Bildnamen verwenden
        image_name = f'image_{timestamp}.jpg'

        image_path = os.path.join(
            self._data_dir,
            image_name
        )

        # Bild speichern
        success = cv2.imwrite(
            image_path,
            image
        )

        if not success:
            return

        # Daten desselben Pakets speichern
        with open(self._csv_file, 'a', newline='') as file:
            writer = csv.writer(file)

            writer.writerow([
                image_name,
                speed,
                steering_angle
            ])

