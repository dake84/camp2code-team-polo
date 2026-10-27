"""Zentrale Laufzeitverwaltung für eine Kamera und alle vier Ansichten."""
from __future__ import annotations
import logging
import threading
from collections import deque
from time import monotonic, monotonic_ns
from CameraCar import CameraCar
from CameraSensor import CameraSensor
from Save_Data_AI import SaveData_4_AI
from pipeline_config import load, save
import Driving

log = logging.getLogger(__name__)


class CarApplication:
    """Besitzt Hardware, Threads, Fahrmodi und Recorder genau einmal."""

    def __init__(self, car=None, sensor=None, modes=None, recorder=None, start_camera=True):
        self._lock = threading.RLock()
        self.car = car if car is not None else CameraCar()
        self.sensor = sensor if sensor is not None else CameraSensor(self.car)
        self.modes = modes if modes is not None else {
            'camera': Driving.CameraMode(car=self.car),
            'gamepad': Driving.PSControllerMode(car=self.car),
        }
        self.recorder = recorder if recorder is not None else SaveData_4_AI(car=self.car, frequency=5)
        self.stop_event = threading.Event()
        self._camera_thread = None
        self._telemetry_thread = None
        self._closed = False
        self.active_mode = None
        self._history = deque(maxlen=240)
        self._last_state_id = None
        self._events = deque(maxlen=60)
        self._freq_history = deque(maxlen=240)
        if start_camera:
            self.start_camera()
        self._telemetry_thread = threading.Thread(target=self._sample_loop,
            name='telemetry-sampler', daemon=True)
        self._telemetry_thread.start()

    def start_camera(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('Anwendung bereits beendet')
            if self._camera_thread and self._camera_thread.is_alive():
                return
            self._camera_thread = threading.Thread(target=self.sensor.read_loop,
                args=(self.stop_event,), name='camera-sensor', daemon=True)
            self._camera_thread.start()

    def running(self):
        return any(mode.is_running() for mode in self.modes.values())

    def start_mode(self, key):
        with self._lock:
            if self._closed:
                raise RuntimeError('Anwendung bereits beendet')
            if key not in self.modes:
                raise ValueError('Unbekannter Fahrmodus')
            if self.running():
                raise RuntimeError('Zuerst aktiven Fahrmodus stoppen')
            self.modes[key].start()
            self.active_mode = key

    def stop_modes(self):
        with self._lock:
            # Stop sendet selbst den sicheren Stoppbefehl an das Fahrzeug.
            for mode in self.modes.values():
                if mode.is_running():
                    mode.stop()
            self.car.stop()
            self.active_mode = None

    def set_processor(self, name):
        with self._lock:
            if self.running():
                raise RuntimeError('Prozessorwechsel nur im Stillstand')
            self.sensor.set_processor(name)

    def set_pipeline(self, config):
        with self._lock:
            if self.running():
                raise RuntimeError('Kalibrierung nur im Stillstand ändern')
            if not 0 <= config['crop'][0] < config['crop'][1] <= 1:
                raise ValueError('Ungültiger Bildausschnitt')
            self.sensor.processing_parameters = (
                tuple(config['hue']), tuple(config['sat']), tuple(config['val']),
                tuple(config['crop']), int(config['blur']), int(config['morph']),
                tuple(config['canny']), dict(config['enabled']))
            self.sensor.set_processor(config['processor'])

    def save_pipeline(self, config):
        self.set_pipeline(config)
        save(config)

    def toggle_recording(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('Anwendung bereits beendet')
            if self.recorder.is_running():
                self.recorder.stop()
            else:
                self.recorder.start()
            return self.recorder.is_running()

    def _sample_loop(self):
        """Erfasst die Historie unabhängig von Dash-Callbacks."""
        while not self.stop_event.is_set():
            try:
                self._sample_once()
            except Exception:
                log.exception('Telemetrieerfassung fehlgeschlagen')
            self.stop_event.wait(0.25)

    def _sample_once(self):
        """Erfasst Telemetrie unabhängig vom Browser in begrenzten Puffern."""
        with self._lock:
            state = self.car.driving_state
            if state is not None and state.frame_id != self._last_state_id:
                self._last_state_id = state.frame_id
                missing = not state.lanes
                self._history.append((monotonic(), state.frame_id,
                                      state.steering_angle, state.speed, int(missing)))
                if missing:
                    self._events.append((state.frame_id, 'Keine Linien erkannt'))
            metrics = getattr(self.car, 'thread_metrics', {})
            self._freq_history.append((monotonic(),
                metrics['camera'].hz if 'camera' in metrics else 0,
                metrics['controller'].hz if 'controller' in metrics else 0))

    def sample(self):
        """Liefert eine unveränderliche Momentaufnahme der Historie."""
        with self._lock:
            return tuple(self._history), tuple(self._freq_history), tuple(self._events)

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.stop_event.set()
        try:
            self.stop_modes()
        finally:
            try:
                self.recorder.stop()
            finally:
                telemetry_thread = self._telemetry_thread
                if telemetry_thread and telemetry_thread is not threading.current_thread():
                    telemetry_thread.join(timeout=2)
                thread = self._camera_thread
                if thread and thread is not threading.current_thread():
                    thread.join(timeout=3)
                    if thread.is_alive():
                        log.warning('Kamerathread läuft noch')
                # Das Kamerabackend darf nicht parallel zum aktiven Lesethread freigegeben werden.
                if not thread or not thread.is_alive():
                    if hasattr(self.sensor, 'release'):
                        self.sensor.release()
