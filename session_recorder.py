"""Persistente Session-Aufzeichnung unabhaengig vom Browser (Schema v2).

Session-Struktur::

    Data_AI/sessions/session_<zeitstempel>/
        manifest.json   Metadaten, Schema, Zeitdefinitionen, Statistik
        frames.csv      eine Zeile je gespeichertem Kameraframe
        events.csv      Ereignis-Zeitachse (DrivingHealthMonitor + Recorder)
        frames/         JPEG-Rohbilder, unveraendert (frame_<id>.jpg)

Grundsaetze:
    * Der Recorder liest nur vorhandene Daten aus dem Ringbuffer (keine zweite
      Bildverarbeitung, kein Hardwarezugriff).
    * Kameradaten und Fahrentscheidung werden ausschliesslich ueber die
      ``frame_id`` zusammengefuehrt.
    * Ein Frame wird erst geschrieben, wenn seine Fahrentscheidung vorliegt oder
      eine Wartefrist (``commit_grace_s``) abgelaufen ist. Vorher konnte ein Frame
      gespeichert werden, bevor der Regler ihn ausgewertet hatte; die
      Fahrentscheidung fehlte dann dauerhaft.
    * Fehlende Werte bleiben leer (CSV) bzw. ``null`` (JSON).
    * Luecken in der Frame-ID werden als Ereignis ``RECORDER_FRAME_GAP`` erfasst.

Linienkoeffizienten: ``(m, b)`` mit ``y = m*x + b`` im ROI-Koordinatensystem;
Vollbild: ``y_voll = y + roi_top_px``.
"""
import csv
import json
import logging
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import cv2

from replay_hud import encode_lanes

SCHEMA_VERSION = 2

FIELDS = (
    'frame_id', 'image_filename', 'data_status', 'driving_active', 'driving_mode',
    'camera_timestamp_ns', 'camera_monotonic_ns', 'processing_ns',
    'driving_timestamp_ns', 'driving_monotonic_ns', 'recorded_monotonic_ns',
    'steering_angle', 'requested_angle', 'steering_angle_car', 'speed',
    'image_width', 'image_height', 'roi_low', 'roi_high', 'roi_top_px', 'roi_bottom_px',
    'processor_name', 'processing_parameters_json',
    'lane_count', 'lanes_json', 'lane0_m', 'lane0_b', 'lane1_m', 'lane1_b',
    'ir_1', 'ir_2', 'ir_3', 'ir_4', 'ir_5', 'infrared_age_ns',
    'ultrasonic_cm', 'ultrasonic_age_ns', 'event_codes',
)
EVENT_FIELDS = ('timestamp_ns', 'frame_id', 'code', 'detail')

TIME_DEFINITIONS = {
    'camera_timestamp_ns': 'Systemuhr direkt nach der Bildaufnahme',
    'camera_monotonic_ns': 'Monotone Uhr direkt nach der Bildaufnahme',
    'processing_ns': 'Dauer der Bildverarbeitung im CameraSensor',
    'driving_timestamp_ns': 'Systemuhr zum Zeitpunkt der Fahrentscheidung',
    'driving_monotonic_ns': 'Monotone Uhr zum Zeitpunkt der Fahrentscheidung',
    'recorded_monotonic_ns': 'Monotone Uhr beim Schreiben der Zeile',
    'events.timestamp_ns': 'Monotone Uhr des Ereignisses (gleiche Uhr wie *_monotonic_ns)',
}
DATA_STATUS = {
    'complete': 'Kameraframe und Fahrentscheidung mit gleicher frame_id',
    'missing_decision': 'Fahrmodus aktiv, aber keine Fahrentscheidung fuer diesen Frame',
    'no_driving_state': 'Kein Fahrmodus aktiv (bzw. unbekannt); keine Fahrentscheidung erwartet',
}


def _blank(value):
    """None wird in CSV als leere Zelle geschrieben."""
    return '' if value is None else value


class SessionRecorder:
    """Schreibt nur neue Frames aus dem vorhandenen Ringbuffer auf die SD-Karte."""

    def __init__(self, car, root='Data_AI/sessions', interval=0.12, event_source=None,
                 is_driving=None, commit_grace_s=0.25, jpeg_quality=95, driving_since=None):
        self.car = car
        self.event_source = event_source
        self.is_driving = is_driving
        # PAKET-C: Frames vor dem Fahrmodusstart erwarten keine Fahrentscheidung
        self.driving_since = driving_since
        self.root = Path(root)
        self.interval = interval
        self.commit_grace_ns = int(commit_grace_s * 1e9)
        self.jpeg_quality = int(jpeg_quality)
        self._event_keys = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last_id = -1
        self.count = 0
        self.gap_frames = 0
        self.missing_decisions = 0
        self.skipped_empty = 0
        self.path = None
        self.error = None
        self._created_at = None
        self._log = logging.getLogger(__name__)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if not self._stop.is_set():
                    return
                raise RuntimeError('Recorder beendet noch den vorherigen Thread')
            self.root.mkdir(parents=True, exist_ok=True)
            self._created_at = datetime.now().isoformat()
            self.path = self.root / ('session_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            (self.path / 'frames').mkdir(parents=True)
            with (self.path / 'events.csv').open('w', newline='', encoding='utf-8') as event_file:
                csv.DictWriter(event_file, fieldnames=EVENT_FIELDS).writeheader()
            with (self.path / 'frames.csv').open('w', newline='', encoding='utf-8') as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()
            self._event_keys = set()
            self._last_id = -1
            self.count = self.gap_frames = self.missing_decisions = self.skipped_empty = 0
            self.error = None
            self._write_manifest('recording')
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='session-recorder', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError('Recorder-Thread konnte nicht beendet werden')
        if self.path and self._created_at is not None:
            try:
                # Restliche Frames ohne Wartefrist sichern.
                if thread is not None and self.error is None:
                    self.capture_once(final=True)
            except Exception as exc:  # Fehler beim Abschluss dokumentieren
                self.error = str(exc)
                self._log.exception('Abschluss der Session fehlgeschlagen')
            self._write_manifest('error' if self.error else 'stopped')
            self._created_at = None

    def stats(self):
        """Kennzahlen fuer Dashboard und Manifest."""
        return {'frames': self.count, 'gap_frames': self.gap_frames,
                'missing_decisions': self.missing_decisions,
                'skipped_empty': self.skipped_empty, 'error': self.error,
                'path': str(self.path) if self.path else None,
                'running': self.is_running()}

    def _write_manifest(self, status):
        manifest = {
            'schema_version': SCHEMA_VERSION,
            'created_at': self._created_at,
            'updated_at': datetime.now().isoformat(),
            'status': status,
            'frames': self.count,
            'gap_frames': self.gap_frames,
            'missing_decisions': self.missing_decisions,
            'skipped_empty_images': self.skipped_empty,
            'error': self.error,
            'recorder': {'interval_s': self.interval, 'commit_grace_s': self.commit_grace_ns / 1e9,
                         'jpeg_quality': self.jpeg_quality,
                         'ringbuffer_maxlen': getattr(getattr(self.car, 'frame_history', None), 'maxlen', None)},
            'lane_model': 'y = m*x + b; x,y in Pixeln im ROI; Vollbild: y_voll = y + roi_top_px',
            'time_definitions': TIME_DEFINITIONS,
            'data_status': DATA_STATUS,
            'frames_fields': list(FIELDS),
            'events_fields': list(EVENT_FIELDS),
        }
        target = self.path / 'manifest.json'
        temp = target.with_suffix('.json.tmp')
        temp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
        temp.replace(target)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.capture_once()
            except Exception as exc:
                self.error = str(exc)
                self._log.exception('Session-Aufzeichnung fehlgeschlagen')
                self._stop.set()
            self._stop.wait(self.interval)

    def _new_events(self):
        if self.event_source is None:
            return []
        events = []
        for event in self.event_source():
            key = (event.timestamp_ns, event.frame_id, event.code, event.detail)
            if key not in self._event_keys:
                events.append((key, event))
        return events

    def _driving_active(self):
        if self.is_driving is None:
            return None
        try:
            return bool(self.is_driving())
        except Exception:
            return None

    def _driving_since_ns(self):
        """PAKET-C: Startzeit des Fahrmodus (monoton) oder None."""
        if self.driving_since is None:
            return None
        try:
            return self.driving_since()
        except Exception:
            return None

    @staticmethod
    def _frame_active(cam, active, since):
        """PAKET-C: Ringbuffer-Vorlauf vor dem Fahrmodusstart ist kein 'missing_decision'.

        Fehler vorher: Beim REC-Start schreibt der Recorder bis zu 60 Frames Vorlauf aus
        dem Ringbuffer. Lief der Fahrmodus beim Schreiben, wurden auch Frames von VOR
        dem Start als 'missing_decision' markiert (Session 15:07: 51 von 51).
        """
        if active is True and since is not None:
            captured = getattr(cam, 'monotonic_ns', 0) or 0
            if captured and captured < since:
                return False
        return active
    def _ready(self, record, now_ns, active, final):
        """Darf der Frame geschrieben werden? (Wartefrist fuer die Fahrentscheidung)."""
        if final or record.driving is not None or active is not True:
            return True
        captured = getattr(record.camera, 'monotonic_ns', 0) or 0
        return not captured or now_ns - captured >= self.commit_grace_ns

    def _row(self, cam, drive, active, events_for_frame, sensors, now_ns):
        image = cam.raw_image
        height, width = image.shape[:2]
        crop = getattr(cam, 'crop', None)
        if crop is not None and len(crop) == 2:
            low, high = float(crop[0]), float(crop[1])
            top = max(0, min(height, int(height * (1 - high))))
            bottom = max(top, min(height, int(height * (1 - low))))
        else:
            low = high = top = bottom = None
        lanes = tuple(cam.lanes) if cam.lanes is not None else ()
        lane0 = lanes[0] if len(lanes) > 0 and lanes[0] is not None else (None, None)
        lane1 = lanes[1] if len(lanes) > 1 and lanes[1] is not None else (None, None)
        if drive is not None:
            status = 'complete'
        elif active is True:
            status = 'missing_decision'
        else:
            status = 'no_driving_state'
        ir = (None,) * 5
        ultrasonic = ir_age = us_age = None
        if sensors is not None:
            ir = (tuple(sensors.infrared) + (None,) * 5)[:5]
            ultrasonic = sensors.ultrasonic_cm
            ir_age = sensors.infrared_age_ns(now_ns)
            us_age = sensors.ultrasonic_age_ns(now_ns)
        params = getattr(cam, 'processing_parameters', None)
        row = dict(
            frame_id=cam.frame_id, data_status=status,
            driving_active='' if active is None else int(active),
            driving_mode=getattr(drive, 'driving_mode', None),
            camera_timestamp_ns=cam.timestamp, camera_monotonic_ns=cam.monotonic_ns,
            processing_ns=getattr(cam, 'processing_ns', None) or None,
            driving_timestamp_ns=getattr(drive, 'timestamp_ns', None),
            driving_monotonic_ns=getattr(drive, 'monotonic_ns', None),
            recorded_monotonic_ns=now_ns,
            steering_angle=getattr(drive, 'steering_angle', None),
            requested_angle=getattr(drive, 'requested_angle', None),
            steering_angle_car=getattr(drive, 'steering_angle_car', None),
            speed=getattr(drive, 'speed', None),
            image_width=width, image_height=height,
            roi_low=low, roi_high=high, roi_top_px=top, roi_bottom_px=bottom,
            processor_name=getattr(cam, 'processor_name', None),
            processing_parameters_json=json.dumps(params, default=str) if params is not None else None,
            lane_count=sum(lane is not None for lane in lanes), lanes_json=encode_lanes(lanes),
            lane0_m=lane0[0], lane0_b=lane0[1], lane1_m=lane1[0], lane1_b=lane1[1],
            ir_1=ir[0], ir_2=ir[1], ir_3=ir[2], ir_4=ir[3], ir_5=ir[4],
            infrared_age_ns=ir_age, ultrasonic_cm=ultrasonic, ultrasonic_age_ns=us_age,
            event_codes='|'.join(events_for_frame.get(cam.frame_id, ())),
        )
        return {key: _blank(value) for key, value in row.items()}

    def capture_once(self, final=False):
        """Schreibt neue Ereignisse und alle fertigen neuen Frames (ein Dateizugriff je Datei)."""
        if self.path is None:
            return
        pending = self._new_events()
        recorder_events = []
        events_for_frame = {}
        for _, event in pending:
            events_for_frame.setdefault(event.frame_id, []).append(event.code)
        records = self.car.frame_history.snapshot()
        sensors = getattr(self.car, 'sensor_snapshot', None)
        active = self._driving_active()
        since = self._driving_since_ns()
        now_ns = time.monotonic_ns()
        rows = []
        written_images = []
        try:
            for record in records:
                cam = record.camera
                if cam.frame_id <= self._last_id:
                    continue
                if not self._ready(record, now_ns, self._frame_active(cam, active, since), final):
                    break  # Reihenfolge wahren: spaetere Frames im naechsten Zyklus
                image = cam.raw_image
                if image is None or not getattr(image, 'size', 0):
                    self.skipped_empty += 1
                    self._last_id = cam.frame_id
                    continue
                if self._last_id >= 0 and cam.frame_id > self._last_id + 1:
                    gap = cam.frame_id - self._last_id - 1
                    self.gap_frames += gap
                    recorder_events.append(dict(timestamp_ns=now_ns, frame_id=cam.frame_id,
                        code='RECORDER_FRAME_GAP',
                        detail=f'{gap} Frames zwischen {self._last_id} und {cam.frame_id} nicht gespeichert'))
                drive = record.driving if record.driving is not None and record.driving.frame_id == cam.frame_id else None
                filename = f'frames/frame_{cam.frame_id:010d}.jpg'
                target = self.path / filename
                if not cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]):
                    raise OSError(f'JPEG konnte nicht geschrieben werden: {target}')
                written_images.append(target)
                row = self._row(cam, drive, self._frame_active(cam, active, since), events_for_frame, sensors, now_ns)
                row['image_filename'] = filename
                if row['data_status'] == 'missing_decision':
                    self.missing_decisions += 1
                rows.append(row)
                self._last_id = cam.frame_id
            if rows:
                with (self.path / 'frames.csv').open('a', newline='', encoding='utf-8') as f:
                    csv.DictWriter(f, fieldnames=FIELDS).writerows(rows)
        except Exception:
            for target in written_images:
                target.unlink(missing_ok=True)
            raise
        self.count += len(rows)
        if pending or recorder_events:
            with (self.path / 'events.csv').open('a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=EVENT_FIELDS)
                for key, event in pending:
                    writer.writerow(dict(timestamp_ns=event.timestamp_ns, frame_id=_blank(event.frame_id),
                                         code=event.code, detail=event.detail))
                    self._event_keys.add(key)
                writer.writerows(recorder_events)


# ---------------------------------------------------------------- Laden / Export

_CACHE = {}
_CACHE_LOCK = threading.Lock()


def list_sessions(root='Data_AI/sessions'):
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted((p.name for p in base.iterdir() if p.is_dir() and (p / 'frames.csv').is_file()), reverse=True)


def _session_path(name, root):
    base = Path(root).resolve()
    path = (base / name).resolve()
    if path.parent != base or not (path / 'frames.csv').is_file():
        raise ValueError('Ungültige Session')
    return path


def _read_csv(path):
    if not path.is_file():
        return []
    with path.open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def load_session(name, root='Data_AI/sessions'):
    """Liefert ``(pfad, zeilen)``. Ergebnis wird bis zur naechsten Dateiaenderung gecacht.

    Vorher wurde frames.csv bei jedem Dashboard-Tick bis zu viermal neu gelesen.
    """
    path = _session_path(name, root)
    frames = path / 'frames.csv'
    stat = frames.stat()
    key = (str(path), 'frames')
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == stamp:
            return path, cached[1]
    rows = _read_csv(frames)
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, rows)
    return path, rows


def load_events(name, root='Data_AI/sessions'):
    """Liefert die Ereignisse einer Session (leer bei Schema v1 ohne events.csv)."""
    path = _session_path(name, root)
    events = path / 'events.csv'
    if not events.is_file():
        return []
    stat = events.stat()
    key = (str(path), 'events')
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    rows = _read_csv(events)
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, rows)
    return rows


def load_manifest(name, root='Data_AI/sessions'):
    path = _session_path(name, root)
    manifest = path / 'manifest.json'
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text(encoding='utf-8'))
    except ValueError:
        return {}


def _in_range(value, lo, hi):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    return (lo is None or number >= lo) and (hi is None or number <= hi)


def export_session_range(name, lo=None, hi=None, root='Data_AI/sessions', out_dir='Data_AI/exports'):
    """Exportiert einen Frame-ID-Bereich einer Session als ZIP auf die SD-Karte.

    Bilder werden unveraendert kopiert (keine Neukodierung). Rueckgabe: Pfad.
    """
    path, rows = load_session(name, root)
    selected = [row for row in rows if _in_range(row.get('frame_id'), lo, hi)]
    events = [row for row in load_events(name, root) if _in_range(row.get('frame_id'), lo, hi)]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    suffix = f"{lo if lo is not None else 'start'}-{hi if hi is not None else 'ende'}"
    target = out / f'{name}_{suffix}.zip'
    fields = list(rows[0].keys()) if rows else list(FIELDS)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
        for row in selected:
            image = (path / row.get('image_filename', '')).resolve()
            if image.is_relative_to(path) and image.is_file():
                archive.write(image, row['image_filename'])
        archive.writestr('frames.csv', _csv_text(fields, selected))
        archive.writestr('events.csv', _csv_text(list(EVENT_FIELDS), events))
        manifest = load_manifest(name, root)
        manifest['export'] = {'source_session': name, 'frame_id_min': lo, 'frame_id_max': hi,
                              'frames': len(selected), 'created_at': datetime.now().isoformat()}
        archive.writestr('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False))
    return target


def export_live_records(records, out_dir='Data_AI/exports', jpeg_quality=95):
    """Exportiert FrameRecords aus dem fluechtigen Ringbuffer (Schema v2-Spalten)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / ('ringbuffer_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.zip')
    helper = SessionRecorder(car=None)
    now_ns = time.monotonic_ns()
    rows = []
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
        for record in records:
            cam = record.camera
            if cam.raw_image is None or not getattr(cam.raw_image, 'size', 0):
                continue
            ok, encoded = cv2.imencode('.jpg', cam.raw_image, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if not ok:
                continue
            filename = f'frames/frame_{cam.frame_id:010d}.jpg'
            archive.writestr(filename, encoded.tobytes())
            drive = record.driving if record.driving is not None and record.driving.frame_id == cam.frame_id else None
            row = helper._row(cam, drive, None, {}, None, now_ns)
            row['image_filename'] = filename
            rows.append(row)
        archive.writestr('frames.csv', _csv_text(list(FIELDS), rows))
        archive.writestr('manifest.json', json.dumps({
            'schema_version': SCHEMA_VERSION, 'source': 'live_ringbuffer',
            'note': 'Kurzexport des fluechtigen Ringbuffers, keine vollstaendige Session.',
            'frames': len(rows), 'lane_model': 'y = m*x + b im ROI; y_voll = y + roi_top_px',
            'time_definitions': TIME_DEFINITIONS}, indent=2, ensure_ascii=False))
    return target


def _csv_text(fields, rows):
    import io
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
