"""Hardwarefreie Anwendung fuer ``python Camp2CodeDashboard.py --offline`` (Punkt 7 aus Paket F).

Fuer den Laptop: Replay Lab, Pipeline Studio (Replay-Frames, A/B), Simulation
und Telemetrie-Ansichten funktionieren; Kamera, Motoren, Bergung und Aufnahme
sind deaktiviert. Es werden keine Hardwaremodule importiert.

Bietet dieselbe oeffentliche Schnittstelle wie ``CarApplication``, soweit das
Dashboard sie nutzt.
"""
import logging
from types import SimpleNamespace

import config_store  # PAKET-H1
from driving_health import DrivingHealthMonitor
from pipeline_config import load, save
from processor_registry import create_processor, is_live_processor, key_for_class
from replay_car import ReplayCar
from telemetry import DurationStats, FrameRingBuffer, FrequencyCounter

log = logging.getLogger(__name__)
OFFLINE_MESSAGE = 'Offline-Modus: keine Hardware'


def _params(settings):
    return (tuple(settings['hue']), tuple(settings['sat']), tuple(settings['val']),
            tuple(settings['crop']), int(settings['blur']), int(settings['morph']),
            tuple(settings['canny']), dict(settings['enabled']))


class OfflineCar(ReplayCar):
    """Fahrzeug ohne Kamera: Ringbuffer bleibt leer, Fahrbefehle werden nur protokolliert."""

    def __init__(self):
        super().__init__()
        self.frame_history = FrameRingBuffer(maxlen=1)
        self.thread_metrics = {'camera': FrequencyCounter(), 'controller': FrequencyCounter()}


class OfflineSensor:
    """Haelt Pipeline-Parameter und Prozessorwahl wie ``CameraSensor``, ohne Kamera."""

    def __init__(self):
        settings = load()
        self.processing_parameters = _params(settings)
        key = settings.get('processor')
        self._processor = create_processor(key if is_live_processor(key) else 'copilot')
        try:
            import ConfigReader
            self.scan_frequency = ConfigReader.ConfigReader('cam_sensor').get_int('scan_frequency', 25)
        except Exception:  # Konfiguration fehlt auf dem Laptop
            self.scan_frequency = 25

    def set_processor(self, name):
        if not is_live_processor(name):
            raise ValueError('Unbekannter Prozessor')
        self._processor = create_processor(name)

    @property
    def processor_name(self):
        return key_for_class(type(self._processor).__name__)

    def release(self):
        return None


class OfflineApplication:
    offline = True

    def __init__(self):
        self.car = OfflineCar()
        self.sensor = OfflineSensor()
        self.health = DrivingHealthMonitor()
        self.timings = DurationStats()
        self.recovery = SimpleNamespace(enabled=False, command=self._refuse)
        self.recorder = SimpleNamespace(is_running=lambda: False, path=None, count=0, error=None,
                                        stats=lambda: {'frames': 0, 'path': None, 'error': OFFLINE_MESSAGE})
        self.active_mode = None
        self.controller_poll_hz = 0.0
        from speed_policy import SpeedPolicy  # PAKET-G
        self._speed_policy = config_store.load_speed_policy()  # PAKET-H1: gespeichert oder fest 30
        self.health.record('OFFLINE', 'Dashboard ohne Hardware gestartet')

    @staticmethod
    def _refuse(*_args, **_kwargs):
        raise RuntimeError(OFFLINE_MESSAGE)

    def running(self):
        return False

    def camera_alive(self):
        # True = Anzeige "KAMERA WARTET" statt "AUSGEFALLEN" (es gibt offline keine Kamera).
        return True

    def stop_reason(self):
        return None

    def last_diagnostic(self):
        return None

    def start_mode(self, key):
        self._refuse()

    def stop_all(self):
        self.car.stop()

    def enable_recovery(self):
        self._refuse()

    def disable_recovery(self):
        return None

    def speed_policy(self):  # PAKET-G
        return self._speed_policy

    def set_speed_policy(self, policy):
        self._speed_policy = policy
        try:  # PAKET-H1: dauerhaft speichern, falls config.json vorhanden ist (auf dem Laptop oft nicht)
            config_store.save_speed_policy(policy)
            return True
        except (config_store.ConfigStoreError, OSError, ValueError):
            return False

    def set_jpeg_quality(self, quality):
        self._refuse()
    def toggle_recording(self):
        self._refuse()

    def set_processor(self, name):
        self.sensor.set_processor(name)

    def set_pipeline(self, config):
        if not 0 <= config['crop'][0] < config['crop'][1] <= 1:
            raise ValueError('Ungültiger Bildausschnitt')
        self.sensor.processing_parameters = _params(config)
        self.sensor.set_processor(config['processor'])

    def save_pipeline(self, config):
        self.set_pipeline(config)
        save(config)

    def sample(self):
        return (), (), ()

    def shutdown(self):
        return None
