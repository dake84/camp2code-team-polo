"""Beobachtet Fahrzustände ohne die Fahrregelung zu verändern."""
from collections import deque
from dataclasses import dataclass
from threading import RLock
from time import monotonic_ns

@dataclass(frozen=True)
class HealthEvent:
    timestamp_ns: int
    frame_id: int | None
    code: str
    detail: str

class DrivingHealthMonitor:
    """Erfasst Zustandsübergänge und begrenzt den Speicherverbrauch."""
    def __init__(self, max_events=120):
        self._lock = RLock()
        self._events = deque(maxlen=max_events)
        self._lane_state = None
        self._was_running = False
        self._last_frame = None
        self._last_diagnostic_frame = None
        self._last_held = None
        self._last_reason = None

    def observe(self, camera, driving, running, stop_reason=None, diagnostic=None):
        with self._lock:
            frame_id = getattr(camera, 'frame_id', None)
            if camera is not None and frame_id != self._last_frame:
                self._last_frame = frame_id
                raw_lanes = getattr(camera, 'lanes', ())
                try:
                    lanes = tuple(raw_lanes) if raw_lanes is not None else ()
                except TypeError:
                    lanes = ()
                count = sum(lane is not None for lane in lanes)
                state = 'LANE_LOST' if count == 0 else ('LANE_PARTIAL' if count == 1 else 'LANES_DETECTED')
                if state != self._lane_state:
                    self._events.append(HealthEvent(monotonic_ns(), frame_id, state,
                        f'{count} erkannte Linien; geometrische Gültigkeit nicht geprüft'))
                    self._lane_state = state
            if diagnostic is not None:
                diag_frame = diagnostic.get('frame_id')
                if diag_frame != self._last_diagnostic_frame:
                    self._last_diagnostic_frame = diag_frame
                    held = bool(diagnostic.get('held_previous_angle'))
                    if held != self._last_held:
                        self._events.append(HealthEvent(monotonic_ns(), diag_frame,
                            'STEERING_HELD' if held else 'STEERING_COMPUTED',
                            f"Rohwinkel={diagnostic.get('raw_angle')}, "
                            f"vorheriger Winkel={diagnostic.get('previous_angle')}, "
                            f"Soll={diagnostic.get('requested_angle')}, "
                            f"Stellwert={diagnostic.get('actual_angle')}"))
                        self._last_held = held
            if stop_reason and stop_reason != self._last_reason:
                self._last_reason = stop_reason
                self._events.append(HealthEvent(monotonic_ns(), frame_id,
                    'STOP_REASON', stop_reason))
            if self._was_running and not running:
                self._events.append(HealthEvent(monotonic_ns(), frame_id,
                    'CONTROLLER_STOPPED', stop_reason or 'Ursache nicht veröffentlicht'))
            self._was_running = running

    def events(self):
        with self._lock:
            return tuple(self._events)
