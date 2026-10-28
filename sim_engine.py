"""Offline-Simulation: Bildquelle -> Prozessor -> CameraMode -> Fahrzeug-Attrappe.

Kern von Paket F. Rechnet eine Session (oder den Ringbuffer) mit einem frei
waehlbaren Prozessor und Parametersatz neu und vergleicht die Entscheidungen
von damals mit denen von jetzt.

Sicherheit: Der Regler ist eine eigene ``CameraMode``-Instanz an einer
``ReplayCar``. Der Thread des Fahrmodus wird nie gestartet; die Simulation ruft
``_pre_run``/``_run`` direkt auf. Es werden keine Motoren angesteuert.

Zeitbasis (Punkt 3): ``CameraMode`` erhaelt eine eigene Uhr, die auf die
aufgezeichneten Zeitstempel zeigt (Aufnahme + Verarbeitungsdauer).
``LANE_TIMEOUT`` verhaelt sich dadurch wie damals, unabhaengig davon, wie
schnell die Simulation rechnet.

Gedaechtnis: Regler und (spaetere) zustandsbehaftete Prozessoren merken sich
Vorframes. Vor dem gewaehlten Bereich wird deshalb ein Vorlauf (``warmup``)
mitgerechnet, der nicht in die Kennzahlen eingeht.
"""
import logging
import statistics
import time
from dataclasses import dataclass, field

from processor_registry import create_processor, label_for
from replay_car import ReplayCar

log = logging.getLogger(__name__)

DEFAULT_FRAME_NS = int(1e9 / 25)     # Ersatzabstand, falls kein Zeitstempel aufgezeichnet ist
MAX_FRAMES = 3000                     # Obergrenze je Lauf (RAM/CPU auf dem Pi)


def params_from_settings(settings):
    """Pipeline-Studio-Einstellungen (dict) -> Parameter-Tupel des Prozessors."""
    return (tuple(settings['hue']), tuple(settings['sat']), tuple(settings['val']),
            tuple(settings['crop']), int(settings['blur']), int(settings['morph']),
            tuple(settings['canny']), dict(settings['enabled']))


def crop_from_params(params):
    enabled = params[7] if len(params) > 7 else {}
    return tuple(params[3]) if enabled.get('roi', True) else (0.0, 1.0)


def roi_size(params, image_shape):
    """(Breite, Hoehe) des ROI in Pixeln wie im LaneProcessor."""
    h, w = image_shape[:2]
    low, high = crop_from_params(params)
    top, bottom = int(h * (1 - high)), int(h * (1 - low))
    return w, max(1, bottom - top)


def lanes_plausible(lanes, width, height, width_frac=(0.25, 0.98)):
    """Zwei Linien, links < rechts an ROI-Ober- und -Unterkante, Spurbreite unten im Rahmen."""
    lanes = [lane for lane in (lanes or ()) if lane is not None]
    if len(lanes) != 2:
        return False

    def x_at(lane, y):
        m, b = lane
        return (y - b) / m if abs(m) > 1e-9 else float('nan')

    xl_b, xr_b = x_at(lanes[0], height - 1), x_at(lanes[1], height - 1)
    xl_t, xr_t = x_at(lanes[0], 0), x_at(lanes[1], 0)
    return bool(xl_b < xr_b and xl_t < xr_t and width_frac[0] * width <= xr_b - xl_b <= width_frac[1] * width)


@dataclass
class SimFrame:
    """Ergebnis eines Frames (keine Bilder, nur Werte)."""
    index: int
    frame_id: int
    warmup: bool
    crop: tuple
    lanes: tuple
    lane_count: int
    plausible: bool
    raw_angle: float | None
    requested_angle: float | None
    car_angle: float | None
    speed: float | None
    held: bool
    stop_reason: str | None
    process_ms: float
    then_raw_angle: float | None
    then_car_angle: float | None
    then_speed: float | None
    then_lane_count: int | None
    then_plausible: bool | None
    then_status: str | None
    captured_ns: int = 0  # PAKET-H1: aufgezeichneter Zeitstempel (Grundlage der Wiedergabe)

    @property
    def angle_delta(self):
        if self.car_angle is None or self.then_car_angle is None:
            return None
        return self.car_angle - self.then_car_angle


@dataclass
class SimResult:
    source_label: str
    processor_key: str
    param_mode: str
    frames: list = field(default_factory=list)
    events: list = field(default_factory=list)       # (frame_id, text)
    duration_s: float = 0.0
    truncated: bool = False
    errors: int = 0

    @property
    def measured(self):
        return [frame for frame in self.frames if not frame.warmup]

    def stats(self):
        """Kennzahlen ueber den gewaehlten Bereich (ohne Vorlauf)."""
        frames = self.measured
        n = len(frames)

        def rate(values):
            values = [v for v in values if v is not None]
            return (sum(bool(v) for v in values) / len(values)) if values else None

        def longest(values):
            best = run = 0
            for value in values:
                run = 0 if value else run + 1
                best = max(best, run)
            return best

        deltas = [abs(f.angle_delta) for f in frames if f.angle_delta is not None]
        then_known = [f for f in frames if f.then_plausible is not None]
        return {
            'frames': n,
            'two_lanes_now': rate([f.lane_count == 2 for f in frames]),
            'two_lanes_then': rate([None if f.then_lane_count is None else f.then_lane_count == 2 for f in frames]),
            'plausible_now': rate([f.plausible for f in frames]),
            'plausible_then': rate([f.then_plausible for f in frames]),
            'longest_gap_now': longest([f.plausible for f in frames]),
            'longest_gap_then': longest([f.then_plausible for f in then_known]) if then_known else None,
            'stops_now': sum(1 for f in frames if f.stop_reason),
            'held_now': sum(1 for f in frames if f.held),
            'mean_abs_delta': statistics.fmean(deltas) if deltas else None,
            'differs_5deg': sum(1 for d in deltas if d >= 5),
            'process_ms_median': statistics.median([f.process_ms for f in frames]) if frames else None,
            'errors': self.errors,
        }


class SimEngine:
    """Rechnet eine Bildquelle mit einem Prozessor und dem CameraMode-Regler nach."""

    def __init__(self, controller_cfg=None, v_min=None, v_max=None):
        import ConfigReader
        self._cfg = controller_cfg if controller_cfg is not None else ConfigReader.ConfigReader('cameracar_controller')
        if v_min is None or v_max is None:
            car_cfg = ConfigReader.ConfigReader('car')
            v_min = car_cfg.get_int('v_min', 20) if v_min is None else v_min
            v_max = car_cfg.get_int('v_max', 100) if v_max is None else v_max
        self._v_min, self._v_max = v_min, v_max

    def _controller(self, car, clock):
        from driving.camera_mode import CameraMode
        mode = CameraMode(name='CameraModus-Simulation', car=car, cfg=self._cfg)
        if not hasattr(mode, '_clock'):
            raise RuntimeError('CameraMode ohne austauschbare Uhr (Paket F nicht vollstaendig installiert)')
        mode._clock = clock
        # Eigener Logger: Simulations-Stopps erscheinen nicht als Warnung des Live-Reglers.
        mode._log = logging.getLogger('sim.CameraMode')
        mode._log.setLevel(logging.ERROR)   # Stopps stehen im Ergebnis, nicht in der Konsole
        return mode

    def run(self, source, processor_key, params_override=None, lo=None, hi=None, warmup=30,
            max_frames=MAX_FRAMES):
        """Simuliert die Frames ``lo..hi`` (Frame-IDs) plus ``warmup`` Frames Vorlauf.

        ``params_override`` = Studio-Parameter; ``None`` = jeweils die damals
        aufgezeichneten Parameter des Frames.
        """
        frames = source.frames
        if not frames:
            raise ValueError('Quelle enthaelt keine Frames')
        ids = [frame.frame_id for frame in frames]
        first = next((i for i, fid in enumerate(ids) if lo is None or fid >= lo), None)
        last = max((i for i, fid in enumerate(ids) if hi is None or fid <= hi), default=None)
        if first is None or last is None or first > last:
            raise ValueError('Kein Frame im gewaehlten Bereich')
        start = max(0, first - max(0, int(warmup or 0)))
        truncated = last - start + 1 > max_frames
        last = min(last, start + max_frames - 1)

        processor = create_processor(processor_key)
        car = ReplayCar(self._v_min, self._v_max)
        now = [0]
        mode = self._controller(car, lambda: now[0])
        result = SimResult(source.label, processor_key,
                           'studio' if params_override is not None else 'recorded', truncated=truncated)
        if truncated:
            result.events.append((ids[last], f'Abgeschnitten nach {max_frames} Frames'))
        from models import CameraData

        started = time.perf_counter()
        armed = False
        previous_ns = None
        for index in range(start, last + 1):
            frame = frames[index]
            params = params_override if params_override is not None else frame.params
            if params is None:
                result.errors += 1
                continue
            try:
                raw = source.image(index)
            except (ValueError, OSError) as exc:
                result.errors += 1
                log.debug('Frame %s nicht lesbar: %s', frame.frame_id, exc)
                continue
            captured = frame.monotonic_ns or ((previous_ns or 0) + DEFAULT_FRAME_NS)
            previous_ns = captured
            now[0] = captured + (frame.processing_ns or 0)
            if not armed:
                mode._pre_run()          # wie START: Vorwert 90 Grad, Timer neu
                armed = True
            t0 = time.perf_counter()
            try:
                processed = processor.process(raw, params)
            except (ValueError, TypeError, IndexError) as exc:
                result.errors += 1
                log.debug('Prozessorfehler Frame %s: %s', frame.frame_id, exc)
                continue
            process_ms = (time.perf_counter() - t0) * 1000
            lanes = tuple(processed.lanes)
            car.camera_data = CameraData(
                process_image=processed.processed_image, raw_image=raw, timestamp=frame.wall_ns,
                frame_id=frame.frame_id, monotonic_ns=captured, crop=crop_from_params(params), lanes=lanes,
                processing_parameters=params, processor_name=type(processor).__name__)
            ok = mode._run()
            diag = mode.last_diagnostic if mode.last_diagnostic and mode.last_diagnostic.get('frame_id') == frame.frame_id else {}
            state = car.driving_state if car.driving_state is not None and car.driving_state.frame_id == frame.frame_id else None
            stop = None
            if not ok:
                stop = mode.last_stop_reason or 'STOP'
                result.events.append((frame.frame_id, f'Regler-Stopp (Simulation): {stop}'))
                car.stop()
                armed = False      # naechster Frame: Regler neu scharf, wie ein erneutes START
            w, h = roi_size(params, raw.shape)
            then_plausible = (lanes_plausible(frame.then_lanes, w, h)
                              if frame.then_lane_count is not None else None)
            result.frames.append(SimFrame(
                index=index, frame_id=frame.frame_id, captured_ns=captured, warmup=index < first,
                crop=crop_from_params(params),
                lanes=lanes, lane_count=sum(lane is not None for lane in lanes),
                plausible=lanes_plausible(lanes, w, h),
                raw_angle=diag.get('raw_angle'),
                requested_angle=getattr(state, 'requested_angle', None),
                car_angle=getattr(state, 'steering_angle_car', None),
                speed=getattr(state, 'speed', None),
                held=bool(diag.get('held_previous_angle')),
                stop_reason=stop, process_ms=process_ms,
                then_raw_angle=frame.then_raw_angle, then_car_angle=frame.then_car_angle,
                then_speed=frame.then_speed, then_lane_count=frame.then_lane_count,
                then_plausible=then_plausible, then_status=frame.then_status))
        result.duration_s = time.perf_counter() - started
        lo_id, hi_id = ids[first], ids[last]
        result.events.extend((fid, f'{code} (damals)') for fid, code, _ in getattr(source, 'events', ())
                             if lo_id <= fid <= hi_id and code in ('STOP_REASON', 'LANE_LOST', 'STEERING_HELD'))
        result.events.sort(key=lambda item: item[0])
        log.info('Simulation %s mit %s: %d Frames in %.1f s', source.label, label_for(processor_key),
                 len(result.frames), result.duration_s)
        return result
