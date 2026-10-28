"""Bildquellen fuer die Simulation (Punkt 2 aus Paket F).

Gemeinsame Schnittstelle fuer gespeicherte Sessions und den Live-Ringbuffer:

* ``frames``      Liste von ``SourceFrame`` (Metadaten, keine Bilder im RAM)
* ``image(i)``    Rohbild des i-ten Frames, unveraendert
* ``then_image(i)`` Darstellung "damals" (Replay-HUD bzw. Live-Overlay)
* ``events``      Ereignisse mit Frame-ID (falls vorhanden)

Die Quellen lesen nur. Es wird keine Hardware angesprochen.
"""
import json
from dataclasses import dataclass, field

from replay_studio import load_session_image, parameters_from_record


def _int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fps_info(frames, manifest=None):
    """PAKET-H1: Median-fps der Aufnahme: aus dem Manifest (capture.median_fps) oder aus den Zeitstempeln."""
    value = ((manifest or {}).get('capture') or {}).get('median_fps')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value), 'Manifest'
    times = [f.monotonic_ns for f in frames if f.monotonic_ns]
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not gaps:
        return None, None
    mid = len(gaps) // 2
    median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
    return 1e9 / median, 'berechnet aus Zeitstempeln'


@dataclass(frozen=True)
class SourceFrame:
    """Ein aufgezeichneter Frame mit den Werten von damals (fehlende Werte = None)."""
    frame_id: int
    monotonic_ns: int
    wall_ns: int
    processing_ns: int
    params: tuple | None
    processor_name: str | None
    then_raw_angle: float | None = None
    then_requested: float | None = None
    then_car_angle: float | None = None
    then_speed: float | None = None
    then_lane_count: int | None = None
    then_status: str | None = None
    then_lanes: tuple = field(default_factory=tuple)


class SessionSource:
    """Gespeicherte Session aus ``Data_AI/sessions`` (Schema v2)."""

    kind = 'session'

    def __init__(self, name, root='Data_AI/sessions'):
        from session_recorder import load_events, load_manifest, load_session
        self.name = name
        self.label = f'Session {name}'
        self.path, rows = load_session(name, root)
        self._rows = [row for row in rows if _int(row.get('frame_id')) is not None]
        self.frames = [self._frame(row) for row in self._rows]
        self.events = [(fid, row.get('code', ''), row.get('detail', ''))
                       for row in load_events(name, root)
                       if (fid := _int(row.get('frame_id'))) is not None and fid >= 0]
        self.hud_events = [{'frame_id': fid, 'code': code, 'detail': detail} for fid, code, detail in self.events]
        self.median_fps, self.fps_source = fps_info(self.frames, load_manifest(name, root))  # PAKET-H1

    @staticmethod
    def _frame(row):
        try:
            params = parameters_from_record(row.get('processing_parameters_json'))
        except ValueError:
            params = None
        try:
            lanes = tuple(tuple(lane) if lane else None for lane in json.loads(row.get('lanes_json') or '[]'))
        except (ValueError, TypeError):
            lanes = ()
        return SourceFrame(
            frame_id=_int(row.get('frame_id')),
            monotonic_ns=_int(row.get('camera_monotonic_ns'), 0) or 0,
            wall_ns=_int(row.get('camera_timestamp_ns'), 0) or 0,
            processing_ns=_int(row.get('processing_ns'), 0) or 0,
            params=params,
            processor_name=row.get('processor_name') or None,
            then_raw_angle=_float(row.get('steering_angle')),
            then_requested=_float(row.get('requested_angle')),
            then_car_angle=_float(row.get('steering_angle_car')),
            then_speed=_float(row.get('speed')),
            then_lane_count=_int(row.get('lane_count')),
            then_status=row.get('data_status') or None,
            then_lanes=lanes,
        )

    def image(self, index):
        return load_session_image(self.path, self._rows[index])

    def then_image(self, index):
        from replay_hud import draw_hud
        return draw_hud(self.image(index), self._rows[index], events=self.hud_events,
                        footer='DAMALS (Aufzeichnung)')


class RingbufferSource:
    """Momentaufnahme des Live-Ringbuffers (fluechtig, ca. 60 Frames)."""

    kind = 'ringbuffer'

    def __init__(self, records, events=()):
        self.name = '__live__'
        self.label = 'Live-Ringbuffer (Momentaufnahme)'
        self._records = [r for r in records
                         if r.camera.raw_image is not None and getattr(r.camera.raw_image, 'size', 0)]
        self.frames = [self._frame(r) for r in self._records]
        ids = {frame.frame_id for frame in self.frames}
        self.events = [(e.frame_id, e.code, e.detail) for e in events if e.frame_id in ids]
        self.hud_events = [{'frame_id': fid, 'code': code, 'detail': detail} for fid, code, detail in self.events]
        self.median_fps, self.fps_source = fps_info(self.frames, None)  # PAKET-H1

    @staticmethod
    def _frame(record):
        cam, drive = record.camera, record.driving
        params = None
        if cam.processing_parameters is not None:
            try:
                params = parameters_from_record(cam.processing_parameters)
            except ValueError:
                params = None
        lanes = tuple(cam.lanes or ())
        return SourceFrame(
            frame_id=cam.frame_id, monotonic_ns=cam.monotonic_ns or 0, wall_ns=cam.timestamp or 0,
            processing_ns=getattr(cam, 'processing_ns', 0) or 0, params=params,
            processor_name=cam.processor_name,
            then_raw_angle=getattr(drive, 'steering_angle', None),
            then_requested=getattr(drive, 'requested_angle', None),
            then_car_angle=getattr(drive, 'steering_angle_car', None),
            then_speed=getattr(drive, 'speed', None),
            then_lane_count=sum(lane is not None for lane in lanes),
            then_status='complete' if drive is not None else 'no_driving_state',
            then_lanes=lanes,
        )

    def image(self, index):
        return self._records[index].camera.raw_image

    def then_image(self, index):
        from replay_hud import draw_hud, row_from_camera  # PAKET-H1: HUD wie im Replay
        record = self._records[index]
        row = row_from_camera(record.camera, record.driving, events=self.hud_events)
        return draw_hud(record.camera.raw_image, row, events=self.hud_events, footer='DAMALS (Ringbuffer)')
