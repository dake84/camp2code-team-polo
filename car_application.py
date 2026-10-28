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
from time import monotonic, monotonic_ns

import Driving
from CameraCar import CameraCar
from CameraSensor import CameraSensor
import config_store  # PAKET-H1
import runtime_settings  # PAKET-H1B
import system_stats  # PAKET-H2
from driving_health import DrivingHealthMonitor
from pipeline_config import save
from recovery_control import RecoveryControl
from session_recorder import SessionRecorder
from telemetry import DurationStats

import mode_registry


log = logging.getLogger(__name__)


class CarApplication:
    """Besitzt Hardware, Threads, Fahrmodi und Recorder genau einmal."""

    def __init__(self, car=None, sensor=None, modes=None, recorder=None, start_camera=True):
        self._lock = threading.RLock()
        self.car = car if car is not None else CameraCar()
        self.sensor = sensor if sensor is not None else CameraSensor(self.car)
        self.modes = modes if modes is not None else mode_registry.create_all(self.car)
        self.stop_event = threading.Event()
        self._camera_thread = None
        self._telemetry_thread = None
        self._closed = False
        self.active_mode = None
        self._history = deque(maxlen=240)
        self._last_state_id = None
        # VERALTET: zweite Ereignisliste; Anzeige nutzt nur noch self.health.
        self._events = deque(maxlen=60)
        # PAKET-C: Startzeit des Fahrmodus (monoton) fuer den Recorder
        self._mode_started_ns = None
        self.health = DrivingHealthMonitor()
        # Ein einziger Recorder (Session-Format v2). SaveData_4_AI ist veraltet.
        self.recorder = recorder if recorder is not None else SessionRecorder(
            self.car, event_source=self.health.events, is_driving=self.running, driving_since=self._driving_since)
        self.recovery = RecoveryControl(self.car, self.running)
        self.timings = DurationStats()
        self._requested_stop = False
        self._freq_history = deque(maxlen=240)
        # PAKET-H2: Latenz- und Systemverlauf; SystemSampler wird nur vom Telemetrie-Thread aufgerufen
        self._extra_lock = threading.Lock()
        self._latency_history = deque(maxlen=240)
        self._system_history = deque(maxlen=240)
        self._last_latency_frame = None
        self.system = system_stats.SystemSampler(fps_source=self._camera_hz)
        # PAKET-H1: Fahrzeugzustand fuer die Kamera (Aufnahmemoment) und gespeicherte Einstellungen
        self._attach_vehicle_mode()
        self._apply_stored_settings()
        self.settings_revision = 0  # PAKET-H1B: zaehlt Aenderungen (Simulation liest danach neu)
        self._apply_stored_runtime_settings()
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
            self._mode_started_ns = monotonic_ns()
            self.modes[key].start()
            self.active_mode = key

    def _driving_since(self):
        """PAKET-C: Startzeit des laufenden Fahrmodus (monoton) oder None."""
        return self._mode_started_ns if self.running() else None
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

    # ------------------------------------------------------------ Fahrzeugzustand, dauerhafte Einstellungen (PAKET-H1)
    def vehicle_mode(self):
        """PAKET-H1: (Modus, Winkel, Speed) des Fahrzeugs fuer den Kamerathread (je Frame).

        Nimmt bewusst NICHT das Anwendungs-Lock: ein STOP haelt es bis zu 2 s und wuerde die
        Kameraschleife blockieren. Modus: recovery | manual (Gamepad) | auto (CameraMode) | idle.
        Winkel/Speed sind die zuletzt gesendeten Stellwerte (Speed vorzeichenbehaftet).
        """
        car = self.car
        with car._lock:
            angle = car.steering_angle
            speed = car.speed
        if self.recovery.enabled:
            mode = 'recovery'
        elif self.running():
            mode = 'auto' if self.active_mode == 'camera' else 'manual'
        else:
            mode = 'idle'
        return mode, angle, speed

    def _attach_vehicle_mode(self):
        """PAKET-H1: der CameraSensor liest den Fahrzeugzustand je Frame ueber diesen Provider."""
        try:
            self.sensor.mode_provider = self.vehicle_mode
        except AttributeError:
            log.warning('Sensor ohne mode_provider - Fahrzeugzustand wird nicht aufgezeichnet')

    def _apply_stored_settings(self):
        """PAKET-H1: dauerhafte Einstellungen laden; Fehler duerfen den Start nie verhindern."""
        try:
            policy = config_store.load_speed_policy()
            mode = self.modes.get('camera')
            if mode is not None and hasattr(mode, 'speed_policy'):
                mode.speed_policy = policy
            self.recorder.jpeg_quality = config_store.load_jpeg_quality()
        except Exception:
            log.exception('Gespeicherte Einstellungen nicht anwendbar - Standardwerte bleiben')

    def _persist(self, action, what):
        """PAKET-H1: dauerhaft speichern. False (mit Protokoll), wenn nicht moeglich - der Wert gilt dann bis zum Neustart."""
        try:
            action()
            self.health.record('CONFIG_SAVED', f'{what} dauerhaft gespeichert')  # PAKET-H1B
            return True
        except (config_store.ConfigStoreError, OSError, ValueError) as exc:
            log.warning('%s nicht dauerhaft gespeichert: %s', what, exc)
            self.health.record('CONFIG_NOT_SAVED', f'{what}: {exc}')
            return False

    # ------------------------------------------------------------ Kamera- und Reglereinstellungen (PAKET-H1B)
    def runtime_settings(self):
        """Je Einstellung aktiver und gespeicherter Wert (siehe runtime_settings.snapshot)."""
        return runtime_settings.snapshot(self)

    def set_runtime_settings(self, values):
        """Validiert, uebernimmt live und speichert dauerhaft; nur im Stillstand (kein Fahrmodus, keine Bergung).

        Liefert {'saved': True|False, 'applied': {schluessel: live uebernommen}, 'values': normalisierte Werte}.
        ``ValueError`` bei ungueltigen Werten, ``RuntimeError`` bei laufender Fahrt; dann wurde nichts veraendert.
        """
        with self._lock:
            if self.running() or self.recovery.enabled:
                raise RuntimeError('Kamera- und Reglereinstellungen nur im Stillstand ändern')
            values = runtime_settings.validate(values)
            applied = runtime_settings.apply_live(self, values)
            self.settings_revision += 1
        saved = self._persist(lambda: runtime_settings.save(values), 'Kamera- und Reglereinstellungen')
        return {'saved': saved, 'applied': applied, 'values': values}

    def _apply_stored_runtime_settings(self):
        """PAKET-H1B: gespeicherte Kamera-/Reglerwerte anwenden; Fehler duerfen den Start nie verhindern."""
        try:
            runtime_settings.apply_live(self, runtime_settings.startup_values())
        except Exception:
            log.exception('Gespeicherte Kamera-/Reglereinstellungen nicht anwendbar - Standardwerte bleiben')

    # ------------------------------------------------------------ Einstellungen (PAKET-G)
    @staticmethod
    def _fresh_hz(counter, max_age_s=2.0):
        """Frequenz nur, wenn der letzte Tick juenger als ``max_age_s`` ist (sonst 0).

        Fehler vorher: Nach einem Stopp wurde die letzte Entscheidungsrate im
        Frequenzverlauf weiter fortgeschrieben.
        """
        if counter is None:
            return 0
        age = counter.age_seconds
        return counter.hz if age is not None and age < max_age_s else 0

    def speed_policy(self):
        """Aktuelle Geschwindigkeitsstrategie des CameraMode (oder None)."""
        return getattr(self.modes.get('camera'), 'speed_policy', None)

    def set_speed_policy(self, policy):
        """Setzt die Geschwindigkeitsstrategie; nur im Stillstand."""
        with self._lock:
            if self.running():
                raise RuntimeError('Geschwindigkeit nur im Stillstand ändern')
            mode = self.modes.get('camera')
            if mode is None or not hasattr(mode, 'speed_policy'):
                raise RuntimeError('CameraMode ohne Geschwindigkeitsstrategie')
            mode.speed_policy = policy
            self.health.record('SPEED_POLICY', policy.describe())
        return self._persist(lambda: config_store.save_speed_policy(policy), 'Geschwindigkeit')

    def set_jpeg_quality(self, quality):
        """JPEG-Qualitaet der naechsten Aufnahme (nur ohne laufende Aufzeichnung)."""
        quality = int(quality)
        if not 50 <= quality <= 100:
            raise ValueError('JPEG-Qualität 50–100')
        with self._lock:
            if self.recorder.is_running():
                raise RuntimeError('Bildqualität nur ohne laufende Aufzeichnung ändern')
            self.recorder.jpeg_quality = quality
        return self._persist(lambda: config_store.save_jpeg_quality(quality), 'JPEG-Qualität')

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
            # PAKET-H2: veraltete Werte (kein Tick seit 2 s) werden None statt 0 und ergeben Luecken im Diagramm
            self._freq_history.append((monotonic(),
                self._fresh_hz(metrics.get('camera')) or None,
                self._fresh_hz(metrics.get('controller')) or None))
        self._sample_extra()

    def _camera_hz(self):
        """PAKET-H2: aktuelle Kamerarate (None bei veraltetem Wert) fuer die Aufnahme-Schaetzung."""
        return self._fresh_hz(getattr(self.car, 'thread_metrics', {}).get('camera')) or None

    def _sample_extra(self):
        """PAKET-H2: Latenz- und Systemverlauf (4 Hz) im Telemetrie-Thread; einziger Aufrufer des SystemSampler."""
        now = monotonic()
        camera = self.car.camera_data
        age = processing = reaction = None
        if camera is not None and camera.frame_id >= 0 and camera.monotonic_ns:
            age = (monotonic_ns() - camera.monotonic_ns) / 1e6
            processing = camera.processing_ns / 1e6 if camera.processing_ns else None
        record = self.car.frame_history.latest_complete()
        if (record is not None and self.running() and record.driving.frame_id != self._last_latency_frame
                and record.driving.monotonic_ns and record.camera.monotonic_ns):
            self._last_latency_frame = record.driving.frame_id
            reaction = (record.driving.monotonic_ns - record.camera.monotonic_ns) / 1e6
        values = self.system.sample()
        with self._extra_lock:
            self._latency_history.append((now, age, processing, reaction))
            self._system_history.append((now, values['cpu'], values['ram'], values['temp']))

    def sample_extra(self):
        """PAKET-H2: (Latenzverlauf, Systemverlauf) als unveraenderliche Momentaufnahme."""
        with self._extra_lock:
            return tuple(self._latency_history), tuple(self._system_history)

    def system_stats(self):
        """PAKET-H2: letzter Systemwert (CPU, RAM, Temperatur, Speicherplatz); misst nicht selbst."""
        return self.system.last()

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
