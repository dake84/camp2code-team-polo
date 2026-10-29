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
from camera_geometry import resolve_process_size  # PAKET-H3
from camera_settings import safe_camera_settings
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
        # PAKET-H3: Aufnahmegroesse (width/height), Verarbeitungsgroesse (process_width/process_height), Dual-Stream
        # (downscale), Kamera-Bildrate (frame_rate) und Belichtungskorrektur (exposure_value) aus cam_sensor.
        # Ohne neue Eintraege wird die Kamera exakt wie bisher erzeugt. Fehlerhafte Werte ergeben Standardwerte.
        settings = safe_camera_settings(self._cfg, self._log)
        small_size, hint = resolve_process_size((settings.width, settings.height),
                                                settings.process_width, settings.process_height)
        if hint:
            self._log.warning('Verarbeitungsgroesse: %s', hint)
        camera_args = dict(colorspace=colorspace, height=settings.height, width=settings.width)
        if small_size is not None:
            camera_args.update(small_size=small_size, small_mode=settings.downscale)
        if settings.frame_rate:
            camera_args['frame_rate'] = settings.frame_rate
        super().__init__(**camera_args)
        self._camera_settings = settings
        self._process_size, self._process_source = self._small_image_state()
        self._last_sensor_ts = None
        self._log.info('Aufnahme %s, Verarbeitung %s (%s), Kamera-Bildrate %s', self._capture_size_text(),
                       self._process_size or 'wie Aufnahme', self._process_source or 'kein Verkleinern',
                       f'{settings.frame_rate:g} fps' if settings.frame_rate else 'automatisch')
        try:
            self.set_camera_controls({
                "AeEnable": True,
                "ExposureValue": settings.exposure_value,
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
            return None, None, None, None
        try:

            values = provider()
            mode, angle, speed = values[:3]
            key = values[3] if len(values) > 3 else None
            if mode not in ('idle', 'auto', 'manual', 'recovery'):
                mode = None
                key = None   # PAKET-H2B: ungültige Art -> auch kein Modusname
            if not isinstance(key, str) or not key:
                key = None
            return mode, angle, speed, key

        except Exception:
            if not self._mode_provider_failed:
                self._mode_provider_failed = True
                self._log.warning('Fahrzeugzustand nicht lesbar (einmalige Meldung)', exc_info=True)
            return None, None, None, None

    def _small_image_state(self):
        """PAKET-H3: (Groesse, Herkunft) des kleinen Bildes aus der Kamera; (None, None) ohne kleines Bild."""
        getter = getattr(self, 'get_small_size', None)
        size = getter() if callable(getter) else None
        if size:
            return tuple(size), getattr(self, 'small_source', None) or 'software'
        return None, None

    def _grab(self):
        """PAKET-H3: ``(grosses Bild, kleines Bild oder None, Metadaten oder None)`` aus EINER Aufnahme."""
        getter = getattr(self, 'get_frames', None)
        if getter is None:                       # Kamera-Attrappe ohne Dual-Stream-Methoden
            return self.get_frame(), None, None
        image, small = getter()
        reader = getattr(self, 'get_metadata', None)
        return image, small, (reader() if callable(reader) else None)

    def camera_info(self):
        """PAKET-H3: Groessen und Einstellungen der Kamera (fuer Ringbuffer-Laenge, Recorder-Manifest, Diagnose).

        ``frame_rate_set`` ist die feste Bildrate der KAMERA (FrameDurationLimits, None = Automatik) und nicht
        der Schleifentakt ``scan_frequency``. ``process_size`` ist None, wenn auf dem Aufnahmebild verarbeitet wird.
        """
        size = None
        getter = getattr(self, 'get_size', None)
        if callable(getter):
            try:
                size = tuple(getter())
            except (TypeError, ValueError):
                size = None
            if not size or len(size) != 2 or size[0] <= 0 or size[1] <= 0:
                size = None
        settings = getattr(self, '_camera_settings', None)
        return {'capture_size': size, 'process_size': self._process_size,
                'process_source': self._process_source or 'none',
                'frame_rate_set': (settings.frame_rate or None) if settings is not None else None,
                'downscale': settings.downscale if settings is not None else None,
                'exposure_value': settings.exposure_value if settings is not None else None}

    def _capture_size_text(self):
        getter = getattr(self, 'get_size', None)
        try:
            return tuple(getter()) if callable(getter) else 'unbekannt'
        except TypeError:
            return 'unbekannt'

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
                image, small, meta = self._grab()  # PAKET-H3: grosses + kleines Bild + Metadaten
                # Zeitstempel direkt nach der Aufnahme (Definition siehe models/camera_data.py).
                captured_wall_ns = time.time_ns()
                captured_mono_ns = time.monotonic_ns()
                vehicle_mode, vehicle_angle, vehicle_speed, mode_key = self._vehicle_snapshot()  # PAKET-H1
                # Leere Frames werden nicht an die Verarbeitung weitergereicht.
                if image is None or image.size == 0:
                    self._log.warning('Leeres Kamerabild verworfen')
                    stop_event.wait(0.1)
                    continue
                # PAKET-H3: dieselbe Aufnahme (gleicher Sensor-Zeitstempel) nie ein zweites Mal verarbeiten.
                sensor_ts = meta.get('sensor_timestamp_ns') if meta else None
                if sensor_ts is not None and sensor_ts == self._last_sensor_ts:
                    stop_event.wait(0.002)
                    continue
                self._last_sensor_ts = sensor_ts
                # Parameter und Prozessor fuer diesen Frame einmalig lesen.
                parameters = self.processing_parameters
                with self._lock:
                    processor = self._processor
                # Genau eine Verarbeitung je Frame, mit optionalen Diagnosebildern.
                # PAKET-H3: Bildverarbeitung auf dem kleinen Bild; Aufnahme/Anzeige nutzen das grosse.
                process_image = small if small is not None else image
                result = processor.process(process_image, parameters)
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
                    vehicle_mode_key=mode_key,
                    process_size=(process_image.shape[1], process_image.shape[0]),
                    process_source=self._process_source or 'none',
                    exposure_us=(meta or {}).get('exposure_us'), gain=(meta or {}).get('gain'),
                    frame_duration_us=(meta or {}).get('frame_duration_us'),
                    lux=(meta or {}).get('lux')
                )
                self._car.camera_data = cameradata
                # scan_frequency = Takt dieser Schleife; frame_rate/FrameDurationLimits = Takt der KAMERA.
                # PAKET-H3: die Schleife wird nie schneller getaktet, als die Kamera liefert.
                period = 1.0 / max(1, self.scan_frequency)
                frame_us = (meta or {}).get('frame_duration_us')
                if frame_us:
                    period = max(period, frame_us / 1e6)
                stop_event.wait(max(0.0, period - (time.monotonic() - cycle_start)))
        except Exception:
            self._log.exception("Kameraschleife fehlgeschlagen")
            raise
        finally:
            self._log.debug('Exit Camera loop')
            self.release()


if __name__ == '__main__':
    print('This is the main.')
