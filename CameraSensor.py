"""Kamerasensor: Bildaufnahme und genau eine Bildverarbeitung je Frame.

Verantwortung (Soll-Architektur, Prinzip 1 und 11):
    * Bild aufnehmen, Zeitstempel direkt nach der Aufnahme setzen.
    * Das Bild genau einmal mit dem gewaehlten Pipeline-Prozessor verarbeiten.
    * Ergebnis als ``CameraData`` im ``CameraCar`` veroeffentlichen.
Keine Fahrentscheidung, keine Dashboard-Logik.
"""
import logging
import threading
import time
from typing import Optional

import ConfigReader
from basisklassen_cam import Camera
from CameraCar import CameraCar
from models import CameraData
from processor_registry import create_processor, is_live_processor, key_for_class  # PAKET-F


def _parameters_from_settings(settings: dict) -> tuple:
    """Wandelt die gespeicherte Studio-Konfiguration in das Parameter-Tupel."""
    return (
        tuple(settings['hue']), tuple(settings['sat']), tuple(settings['val']),
        tuple(settings['crop']), int(settings['blur']), int(settings['morph']),
        tuple(settings['canny']), dict(settings['enabled']),
    )


class CameraSensor(Camera):

    def __init__(self, car: CameraCar, sensor_config: Optional[ConfigReader.ConfigReader] = None,
                 logger: Optional[logging.Logger] = None, mode_provider=None) -> None:
        self._log = logger if logger is not None else logging.getLogger(self.__class__.__name__)
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader('cam_sensor', logger=self._log)
        self._lock = threading.Lock()
        self._debug_mode = False
        self._car = car
        # Laufende Nummer fuer die Zuordnung von Bild und Fahrentscheidung.
        self._frame_id = 0
        # PAKET-H1: Fahrzeugzustand je Frame (Aufnahmemoment)
        self._mode_provider = mode_provider
        self._mode_provider_failed = False
        # Gespeicherte Studio-Konfiguration auch ohne offenen Browser nutzen.
        from pipeline_config import load as load_pipeline_config
        settings = load_pipeline_config()
        self._pack_processing_parameters = _parameters_from_settings(settings)
        # Der Prozessor ist austauschbar; gespeicherte Auswahl wird jetzt beachtet
        # (vorher wurde beim Start immer der Diagnose-Prozessor verwendet).
        self._processor = create_processor(  # PAKET-F: nur fahrfreigegebene Prozessoren
            settings.get('processor') if is_live_processor(settings.get('processor')) else 'copilot')
        colorspace = str(self._cfg.get_str('colormode', 'gray'))
        self._log.info('Kamera-Farbraum: %s', colorspace)
        super().__init__(colorspace=colorspace,
                         height=self._cfg.get_int('height', 480),
                         width=self._cfg.get_int('width', 640))
        try:
            self.set_camera_controls({
                "AeEnable": True,
                "ExposureValue": 1.0,
            })
        except AttributeError:
            # OpenCV-Backend (USB-Kamera) besitzt keine Picamera2-Controls.
            self._log.warning('Kamerasteuerung vom Backend nicht unterstuetzt')

    @property
    def scan_frequency(self) -> int:
        with self._lock:
            return self._cfg.get_int('scan_frequency', 25)

    @property
    def save_image(self) -> bool:
        with self._lock:
            return self._cfg.get_bool('save_image', False)

    @property
    def processing_parameters(self):
        with self._lock:
            return self._pack_processing_parameters

    @processing_parameters.setter
    def processing_parameters(self, pack_processing_parameters):
        with self._lock:
            self._pack_processing_parameters = pack_processing_parameters

    @property
    def mode_provider(self):
        """PAKET-H1: Funktion, die (Modus, Winkel, Speed) des Fahrzeugs liefert (CarApplication.vehicle_mode)."""
        with self._lock:
            return self._mode_provider

    @mode_provider.setter
    def mode_provider(self, provider):
        with self._lock:
            self._mode_provider = provider
        self._mode_provider_failed = False

    def _vehicle_snapshot(self):
        """PAKET-H1: (Modus, Winkel, Speed) im Aufnahmemoment oder (None, None, None).

        Fehler duerfen die Kameraschleife nie stoeren; sie werden einmalig protokolliert.
        """
        provider = self._mode_provider
        if provider is None:
            return None, None, None
        try:
            mode, angle, speed = provider()
            if mode not in ('idle', 'auto', 'manual', 'recovery'):
                mode = None
            return mode, angle, speed
        except Exception:
            if not self._mode_provider_failed:
                self._mode_provider_failed = True
                self._log.warning('Fahrzeugzustand nicht lesbar (einmalige Meldung)', exc_info=True)
            return None, None, None

    def set_processor(self, name: str):
        """Wechselt zwischen Prozessor ohne ('legacy') und mit Diagnosebildern ('copilot').

        Der Wechsel wird unter dem bestehenden Sensor-Lock vorgenommen.
        """
        if not is_live_processor(name):
            raise ValueError('Unbekannter Prozessor')
        with self._lock:
            self._processor = create_processor(name)

    @property
    def processor_name(self) -> str:
        with self._lock:
            return key_for_class(type(self._processor).__name__)

    def read_loop(self, stop_event: threading.Event):
        """Kamerathread. Taktet auf ``scan_frequency`` inklusive Verarbeitungszeit.

        Vorher wurde nach jeder Verarbeitung zusaetzlich die volle Periode gewartet;
        dadurch lag die Ist-Frequenz deutlich unter dem Sollwert (Screenshot: 25 Hz
        Soll, ca. 15 Hz Ist).
        """
        try:
            self._log.debug('Entering Camera-Sensor loop')
            while not stop_event.is_set():
                cycle_start = time.monotonic()
                if self._debug_mode:
                    self._cfg._load_config_file()
                image = self.get_frame()
                # Zeitstempel direkt nach der Aufnahme (Definition siehe models/camera_data.py).
                captured_wall_ns = time.time_ns()
                captured_mono_ns = time.monotonic_ns()
                vehicle_mode, vehicle_angle, vehicle_speed = self._vehicle_snapshot()  # PAKET-H1
                # Leere Frames werden nicht an die Verarbeitung weitergereicht.
                if image is None or image.size == 0:
                    self._log.warning('Leeres Kamerabild verworfen')
                    stop_event.wait(0.1)
                    continue
                # Parameter und Prozessor fuer diesen Frame einmalig lesen.
                parameters = self.processing_parameters
                with self._lock:
                    processor = self._processor
                # Genau eine Verarbeitung je Frame, mit optionalen Diagnosebildern.
                result = processor.process(image, parameters)
                self._frame_id += 1
                cameradata = CameraData(
                    raw_image=image,
                    timestamp=captured_wall_ns,
                    frame_id=self._frame_id,
                    monotonic_ns=captured_mono_ns,
                    crop=(tuple(parameters[3]) if len(parameters) < 8 or
                          parameters[7].get("roi", True) else (0.0, 1.0)),
                    lanes=result.lanes,
                    process_image=result.processed_image,
                    stages=result.stages,
                    processing_parameters=parameters,
                    processor_name=type(processor).__name__,
                    processing_ns=time.monotonic_ns() - captured_mono_ns,
                    vehicle_mode=vehicle_mode, vehicle_angle=vehicle_angle,
                    vehicle_speed=vehicle_speed,
                )
                self._car.camera_data = cameradata
                period = 1.0 / max(1, self.scan_frequency)
                stop_event.wait(max(0.0, period - (time.monotonic() - cycle_start)))
        except Exception:
            self._log.exception("Kameraschleife fehlgeschlagen")
            raise
        finally:
            self._log.debug('Exit Camera loop')
            self.release()


if __name__ == '__main__':
    print('This is the main.')
