"""Zentrale Laufzeitverwaltung: eine Kamera, ein Fahrzeug, ein Recorder.

``CarApplication`` ist die einzige Stelle, die Hardware-Objekte, Threads,
Fahrmodi und den Recorder erzeugt und beendet (Prinzip 9 und 10).
Das Dashboard greift ausschliesslich ueber die oeffentlichen Methoden zu.

Threads im Normalbetrieb:
    camera-sensor       Bildaufnahme und Bildverarbeitung (CameraSensor.read_loop)
    telemetry-sampler   Historie und Health-Monitor (4 Hz)
    recovery-watchdog   Bergungssteuerung (RecoveryControl)
    <Fahrmodus>         nur waehrend einer Fahrt (DrivingMode)
    session-recorder    nur waehrend einer Aufzeichnung
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from time import monotonic

import Driving
from CameraCar import CameraCar
from CameraSensor import CameraSensor
from driving_health import DrivingHealthMonitor
from pipeline_config import save
from recovery_control import RecoveryControl
from session_recorder import SessionRecorder
from telemetry import DurationStats

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
        self.stop_event = threading.Event()
        self._camera_thread = None
        self._telemetry_thread = None
        self._closed = False
        self.active_mode = None
        self._history = deque(maxlen=240)
        self._last_state_id = None
        # VERALTET: zweite Ereignisliste; Anzeige nutzt nur noch self.health.
        self._events = deque(maxlen=60)
        self.health = DrivingHealthMonitor()
        # Ein einziger Recorder (Session-Format v2). SaveData_4_AI ist veraltet.
        self.recorder = recorder if recorder is not None else SessionRecorder(
            self.car, event_source=self.health.events, is_driving=self.running)
        self.recovery = RecoveryControl(self.car, self.running)
        self.timings = DurationStats()
        self._requested_stop = False
        self._freq_history = deque(maxlen=240)
        if start_camera:
            self.start_camera()
        self._telemetry_thread = threading.Thread(target=self._sample_loop,
                                                  name='telemetry-sampler', daemon=True)
        self._telemetry_thread.start()

    # ------------------------------------------------------------ Lebenszyklus
    def start_camera(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('Anwendung bereits beendet')
            if self._camera_thread and self._camera_thread.is_alive():
                return
            self._camera_thread = threading.Thread(target=self.sensor.read_loop,
                                                   args=(self.stop_event,), name='camera-sensor', daemon=True)
            self._camera_thread.start()

    def camera_alive(self) -> bool:
        thread = self._camera_thread
        return thread is not None and thread.is_alive()

    def running(self):
        return any(mode.is_running() for mode in self.modes.values())

    def start_mode(self, key):
        """Startet genau einen Fahrmodus. Bergung und Fahrmodus schliessen sich aus."""
        with self._lock:
            if self._closed:
                raise RuntimeError('Anwendung bereits beendet')
            if key not in self.modes:
                raise ValueError('Unbekannter Fahrmodus')
            if self.running():
                raise RuntimeError('Zuerst aktiven Fahrmodus stoppen')
            self.recovery.disable()
            self._requested_stop = False
            camera = self.car.camera_data
            self.health.mark_start(key, getattr(camera, 'frame_id', None))
            self.modes[key].start()
            self.active_mode = key

    def stop_modes(self):
        """STOP: synchron, damit der Stoppbefehl sofort an das Fahrzeug geht."""
        with self._lock:
            self._requested_stop = True
            # Stop sendet selbst den sicheren Stoppbefehl an das Fahrzeug.
            for mode in self.modes.values():
                if mode.is_running():
                    mode.stop()
            self.car.stop()
            self.active_mode = None

    def enable_recovery(self):
        """Aktiviert die Bergung unter demselben Lock wie start_mode (kein Wettlauf)."""
        with self._lock:
            if self._closed:
                raise RuntimeError('Anwendung bereits beendet')
            if self.running():
                raise RuntimeError('Autonomen Fahrmodus zuerst stoppen')
            self.recovery.enable()
            self.health.record('RECOVERY_ENABLED', 'Manuelle Bergung aktiviert')

    def disable_recovery(self):
        with self._lock:
            was_enabled = self.recovery.enabled
            self.recovery.disable()
            if was_enabled:
                self.health.record('RECOVERY_DISABLED', 'Manuelle Bergung beendet')

    def stop_all(self):
        """Globaler STOP: Bergung beenden und alle Fahrmodi stoppen."""
        self.disable_recovery()
        self.stop_modes()

    # ------------------------------------------------------------ Kalibrierung
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

    # ------------------------------------------------------------ Aufzeichnung
    def toggle_recording(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('Anwendung bereits beendet')
            if self.recorder.is_running():
                self.recorder.stop()
                self.health.record('RECORDING_STOPPED', 'Aufzeichnung beendet')
            else:
                self.recorder.start()
                self.health.record('RECORDING_STARTED', str(getattr(self.recorder, 'path', '')))
            return self.recorder.is_running()

    # ------------------------------------------------------------ Telemetrie
    @property
    def controller_poll_hz(self) -> float:
        mode = self.modes.get('camera')
        return float(getattr(mode, 'poll_frequency', getattr(mode, '_frequency', 0)) or 0)

    def stop_reason(self):
        if self._requested_stop:
            return 'Benutzer-STOP'
        mode = self.modes.get('camera')
        return getattr(mode, 'last_stop_reason', None)

    def last_diagnostic(self):
        return getattr(self.modes.get('camera'), 'last_diagnostic', None)

    def _sample_loop(self):
        """Erfasst die Historie unabhaengig von Dash-Callbacks."""
        while not self.stop_event.is_set():
            try:
                self._sample_once()
            except Exception:
                log.exception('Telemetrieerfassung fehlgeschlagen')
            self.stop_event.wait(0.25)

    def _sample_once(self):
        """Erfasst Telemetrie unabhaengig vom Browser in begrenzten Puffern."""
        with self._lock:
            state = self.car.driving_state
            running = self.running()
            self.health.observe(self.car.camera_data, state, running, self.stop_reason(),
                                self.last_diagnostic())
            if state is not None and state.frame_id != self._last_state_id:
                self._last_state_id = state.frame_id
                lanes = state.lanes or ()
                lane_count = sum(lane is not None for lane in lanes)
                missing = lane_count == 0
                # Tupel: (Zeit, Frame, Rohwinkel, Speed, Linie fehlt, Linienanzahl, Stellwert)
                self._history.append((monotonic(), state.frame_id, state.steering_angle,
                                      state.speed, int(missing), lane_count,
                                      state.steering_angle_car))
                if missing:
                    self._events.append((state.frame_id, 'Keine Linien erkannt'))
            metrics = getattr(self.car, 'thread_metrics', {})
            self._freq_history.append((monotonic(),
                metrics['camera'].hz if 'camera' in metrics else 0,
                metrics['controller'].hz if 'controller' in metrics else 0))

    def sample(self):
        """Liefert eine unveraenderliche Momentaufnahme der Historie."""
        with self._lock:
            return tuple(self._history), tuple(self._freq_history), tuple(self._events)

    # ------------------------------------------------------------ Shutdown
    def shutdown(self):
        """Idempotent: Bergung, Fahrmodi, Recorder, Threads, Kamera."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.stop_event.set()
        try:
            self.recovery.shutdown()
            self.stop_modes()
        finally:
            try:
                self.recorder.stop()
            except Exception:
                log.exception('Recorder konnte nicht sauber beendet werden')
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
