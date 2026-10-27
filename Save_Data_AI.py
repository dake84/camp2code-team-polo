"""Bild- und Multisensor-Aufzeichnung ohne Abhängigkeit vom DrivingMode."""
import csv
import logging
import os
import threading
import time
from datetime import datetime

import cv2
from CameraCar import CameraCar
import json


class SaveData_4_AI:
    """Speichert den neuesten Kameraframe und zeitlich markierte Sensordaten."""

    FIELDS = (
        'image', 'speed', 'steering_angle', 'frame_id', 'timestamp_ns',
        'data_status', 'processor_name', 'driving_mode', 'processing_parameters',
        'lane_count', 'lane_left_m', 'lane_left_b', 'lane_right_m', 'lane_right_b',
        'monotonic_ns', 'camera_timestamp_ns', 'camera_monotonic_ns',
        'camera_age_ns', 'driving_timestamp_ns', 'infrared_timestamp_ns',
        'infrared_monotonic_ns', 'infrared_age_ns',
        'ultrasonic_timestamp_ns', 'ultrasonic_monotonic_ns',
        'ultrasonic_age_ns', 'ultrasonic_cm',
        'ir_1', 'ir_2', 'ir_3', 'ir_4', 'ir_5',
    )

    def __init__(self, car: CameraCar, frequency: float = 5.0):
        if not 0 < frequency <= 1000:
            raise ValueError('frequency muss zwischen 0 und 1000 liegen')
        self._car = car
        self._frequency = frequency
        self._stop_event = None
        self._thread = None
        self._data_dir = None
        self._csv_file = None
        self._last_saved_id = -1
        self._lock = threading.RLock()
        self._log = logging.getLogger(type(self).__name__)

    def start(self):
        with self._lock:
            if self.is_running():
                return
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError('Vorheriger Recorder läuft noch')
            main_dir = 'Data_AI'
            os.makedirs(main_dir, exist_ok=True)
            stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
            self._data_dir = os.path.join(main_dir, stamp)
            os.makedirs(self._data_dir, exist_ok=False)
            self._csv_file = os.path.join(self._data_dir, 'data.csv')
            with open(self._csv_file, 'w', newline='', encoding='utf-8') as file:
                csv.DictWriter(file, fieldnames=self.FIELDS).writeheader()
            self._last_saved_id = -1
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._save_loop, daemon=True, name='SaveData')
            self._thread.start()

    def stop(self):
        with self._lock:
            event, thread = self._stop_event, self._thread
            if event is None:
                return
            event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                self._log.warning('Recorder-Thread konnte noch nicht beendet werden')
                return
        with self._lock:
            if self._thread is thread:
                self._thread = None
                self._stop_event = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _save_loop(self):
        event = self._stop_event
        period = 1.0 / self._frequency
        while not event.is_set():
            started = time.monotonic()
            try:
                self._save_data()
            except Exception:
                self._log.exception('Fehler bei der Datenerfassung')
            event.wait(max(0.0, period - (time.monotonic() - started)))

    def _save_data(self):
        """Schreibt einen Datensatz je Frame; Fahrdaten nur mit identischer ID."""
        with self._car._lock:
            records = self._car.frame_history.snapshot()
            sensors = self._car.sensor_snapshot
        if not records:
            return
        # Auch ältere Frames im Ringbuffer können verspätete Fahrentscheidungen tragen.
        # Bereits gespeicherte Frames werden niemals erneut geschrieben.
        for record in records:
            camera, driving = record.camera, record.driving
            frame_id = camera.frame_id
            if frame_id < 0 or frame_id <= self._last_saved_id:
                continue
            image = camera.raw_image
            if image is None or image.size == 0:
                self._last_saved_id = frame_id
                continue
            # Ein Frame ohne Fahrentscheidung bleibt explizit unvollständig.
            now_ns, mono_ns = time.time_ns(), time.monotonic_ns()
            image_name = f'image_{frame_id}_{camera.timestamp}.jpg'
            image_path = os.path.join(self._data_dir, image_name)
            if not cv2.imwrite(image_path, image):
                self._log.error('Bild konnte nicht geschrieben werden: %s', image_path)
                continue
            lanes = tuple(camera.lanes or ())
            left = lanes[0] if len(lanes) > 0 else (None, None)
            right = lanes[1] if len(lanes) > 1 else (None, None)
            ir = (tuple(sensors.infrared) + (None,) * 5)[:5]
            row = dict(
                image=image_name, frame_id=frame_id, timestamp_ns=now_ns,
                monotonic_ns=mono_ns, data_status='complete' if driving else 'no_driving_state',
                speed=driving.speed if driving else None,
                steering_angle=driving.steering_angle_car if driving else None,
                driving_mode=driving.driving_mode if driving else None,
                processor_name=camera.processor_name,
                processing_parameters=json.dumps(camera.processing_parameters, default=str)
                if camera.processing_parameters is not None else None,
                lane_count=len(lanes), lane_left_m=left[0], lane_left_b=left[1],
                lane_right_m=right[0], lane_right_b=right[1],
                camera_timestamp_ns=camera.timestamp,
                camera_monotonic_ns=camera.monotonic_ns or None,
                camera_age_ns=max(0, mono_ns - camera.monotonic_ns) if camera.monotonic_ns else None,
                driving_timestamp_ns=driving.timestamp_ns if driving else None,
                infrared_timestamp_ns=sensors.infrared_timestamp_ns,
                infrared_monotonic_ns=sensors.infrared_monotonic_ns,
                infrared_age_ns=sensors.infrared_age_ns(mono_ns),
                ultrasonic_timestamp_ns=sensors.ultrasonic_timestamp_ns,
                ultrasonic_monotonic_ns=sensors.ultrasonic_monotonic_ns,
                ultrasonic_age_ns=sensors.ultrasonic_age_ns(mono_ns),
                ultrasonic_cm=sensors.ultrasonic_cm,
            )
            row.update({f'ir_{i}': v for i, v in enumerate(ir, 1)})
            try:
                with open(self._csv_file, 'a', newline='', encoding='utf-8') as file:
                    csv.DictWriter(file, fieldnames=self.FIELDS).writerow(row)
            except Exception:
                try:
                    os.remove(image_path)
                except OSError:
                    self._log.exception('Verwaistes Bild konnte nicht entfernt werden')
                raise
            self._last_saved_id = frame_id
