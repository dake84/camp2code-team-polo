"""Persistente Simulation Runs (Paket I1): einmal berechnen, speichern, schnell abspielen.

Trennung (bisher rechnete jede Simulation dieselben Frames erneut):

    1. Berechnung   ``SimulationRunner`` ruft die vorhandene ``sim_engine.SimEngine`` in EINEM Hintergrund-Thread auf.
    2. Speicherung  ``SimulationRunStore``: je Lauf ein Ordner mit ``manifest.json`` (atomar) und ``results.jsonl``
                    (je Frame eine Zeile, inkrementell angehaengt).
    3. Wiedergabe   ``SimulationResultReader`` liest ``results.jsonl`` einmal in den Speicher (nur Zahlenwerte, keine
                    Bilder) und baut daraus ein ``SimResult``; die Wiedergabe rechnet nichts mehr.

Verzeichnis (neben den Sessions, Standard ``Data_AI/simulation_runs``)::

    simulation_runs/<run_id>/
        manifest.json   Metadaten, Status, Fortschritt, Parameter-Snapshot, Fingerprint, Formatversion
        results.jsonl   eine JSON-Zeile je Frame (``sim_engine.SimFrame``, Linien als [[m, b], ...])

Die Frames selbst werden NICHT kopiert: jede Zeile verweist ueber ``image_ref`` auf die Bilddatei der Quell-Session;
Sessions werden nie veraendert.

Parameter-Fingerprint (SHA-256): ueber eine normalisierte, stabil sortierte Darstellung aller Parameter, die das
Ergebnis beeinflussen (``build_parameter_snapshot``). Er enthaelt keine fluechtigen Werte (keine Zeit, keine IDs, keine
Pfade). Ein vorhandener, vollstaendig erfolgreicher Run mit gleicher Quell-Session, gleichem Fingerprint und
kompatibler Formatversion wird wiederverwendet (``SimulationRunStore.find_matching``); neu rechnen ist nur bewusst moeglich.

Dieses Modul importiert weder Dash noch Hardware.
"""
import copy
import dataclasses
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FORMAT_NAME = 'c2c-simulation-run'
RESULT_FORMAT_VERSION = 1              # Version des Ergebnisformats (manifest.json + results.jsonl)
COMPATIBLE_FORMATS = (1,)              # Versionen, die dieser Code lesen UND wiederverwenden darf
MANIFEST_NAME = 'manifest.json'
RESULTS_NAME = 'results.jsonl'
DEFAULT_ROOT = 'Data_AI/simulation_runs'
MAX_CACHED_READERS = 2                 # begrenzter Cache: hoechstens zwei Ergebnisdateien gleichzeitig im RAM
MANIFEST_EVERY_S = 1.0                 # Zwischenstand des Manifests hoechstens einmal je Sekunde
MANIFEST_EVERY_FRAMES = 50

STATUSES = ('queued', 'running', 'completed', 'failed', 'cancelled')
ACTIVE_STATUSES = ('queued', 'running')
STATUS_TEXT = {'queued': 'wartet', 'running': 'läuft', 'completed': 'fertig', 'failed': 'FEHLGESCHLAGEN',
               'cancelled': 'abgebrochen', 'incomplete': 'unvollständig (abgebrochen)', 'corrupt': 'DEFEKT',
               'idle': 'bereit'}
_RUN_ID = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')

# Zusaetzliche Regler-Schluessel (``cameracar_controller``), die in den Snapshot gehoeren. Neue ergebnisrelevante
# Regler-Einstellungen (z. B. Lookahead) hier und in ``SimEngine.controller_settings`` eintragen.
EXTRA_CONTROLLER_KEYS = ()


class RunStoreError(Exception):
    """Run nicht lesbar, ungueltig oder nicht anlegbar (die uebrigen Runs sind davon nicht betroffen)."""


class RunnerBusyError(RuntimeError):
    """Es laeuft bereits eine Berechnung (es darf immer nur eine geben)."""


# ------------------------------------------------------------------ Normalisierung und Fingerprint
def _plain(value):
    """NumPy-Skalare in Python-Werte umwandeln."""
    item = getattr(value, 'item', None)
    if callable(item) and not isinstance(value, (list, tuple, dict, str, bytes)):
        try:
            return item()
        except (TypeError, ValueError):
            return value
    return value


def normalize(value):
    """Stabile, JSON-faehige Darstellung fuer den Fingerprint.

    Tupel -> Listen, Dict-Schluessel -> Text (sortiert beim Serialisieren), ganzzahlige Floats -> int
    (2.0 und 2 sind derselbe Parameter), nicht endliche Zahlen -> None.
    """
    value = _plain(value)
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        return [normalize(item) for item in items]
    return str(value)


def canonical_json(value):
    return json.dumps(normalize(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def fingerprint(snapshot):
    """SHA-256 (hex) der normalisierten, sortierten Parameterdarstellung."""
    return hashlib.sha256(canonical_json(snapshot).encode('utf-8')).hexdigest()


def jsonable(value):
    """Wie ``normalize``, aber ohne Zahlen zu veraendern (fuer gespeicherte Ergebnisse)."""
    value = _plain(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def _policy_dict(policy):
    if policy is None:
        from speed_policy import SpeedPolicy
        policy = SpeedPolicy()
    return {'mode': policy.mode, 'fixed_speed': policy.fixed_speed, 'v_min': policy.v_min, 'v_max': policy.v_max}


def build_parameter_snapshot(source, processor_key, param_mode, studio_params, plan, controller, speed_policy,
                             max_frames, driving_mode='camera', extra=None):
    """Alle Parameter, die das Simulationsergebnis beeinflussen, als normalisiertes Dict.

    ``param_mode='studio'``: die Studio-Parameter (Crop, Blur, Canny, Hue/Sat/Val, Schalter ...).
    ``param_mode='recorded'``: die damals aufgezeichneten Parameter der gerechneten Frames (als sortierte Menge
    der verschiedenen Parametersaetze), damit eine geaenderte Aufnahme nicht unbemerkt denselben Run trifft.
    ``plan`` = ``SimEngine.plan(...)``. Keine fluechtigen Werte: keine Uhrzeit, keine Run-ID, keine Pfade.
    """
    start, first, last, truncated = plan
    frames = source.frames
    snapshot = {
        'source': {'kind': getattr(source, 'kind', 'session'), 'name': getattr(source, 'name', None),
                   'frames': len(frames), 'first_frame_id': frames[0].frame_id, 'last_frame_id': frames[-1].frame_id},
        'range': {'start_frame_id': frames[start].frame_id, 'first_measured_frame_id': frames[first].frame_id,
                  'last_frame_id': frames[last].frame_id, 'truncated': bool(truncated)},
        'processor': processor_key,
        'param_mode': param_mode,
        'driving_mode': driving_mode,
        'controller': dict(controller),
        'speed_policy': _policy_dict(speed_policy),
        'options': {'max_frames': int(max_frames)},
    }
    if param_mode == 'studio':
        snapshot['processing_parameters'] = normalize(studio_params)
    else:
        recorded = {canonical_json(frame.params) for frame in frames[start:last + 1]}
        snapshot['recorded_parameters'] = sorted(recorded)
    if extra:
        snapshot['extra'] = normalize(extra)
    return normalize(snapshot)


def summarize(snapshot):
    """Kurze Parameterzusammenfassung fuer Listen und Hinweise."""
    try:
        rng, ctl, pol = snapshot['range'], snapshot['controller'], snapshot['speed_policy']
        warm = rng['first_measured_frame_id'] != rng['start_frame_id']
        speed = (f"fest {pol['fixed_speed']}" if pol['mode'] == 'fixed' else f"adaptiv {pol['v_min']}–{pol['v_max']}")
        params = 'Studio-Parameter' if snapshot.get('param_mode') == 'studio' else 'Parameter von damals'
        if snapshot.get('param_mode') == 'studio':
            pp = snapshot.get('processing_parameters') or []
            if len(pp) > 4:
                params += f' (Crop {pp[3]}, Blur {pp[4]})'
        return (f"{snapshot['processor']} · {params} · Frames {rng['first_measured_frame_id']}–{rng['last_frame_id']}"
                + (f" (+Vorlauf ab {rng['start_frame_id']})" if warm else '')
                + f" · Lenkverstärkung {ctl['steering_gain']:g} · Linien-Timeout {ctl['lost_line_timeout']:g} s"
                f" · Steuerwert {ctl['v_min']}–{ctl['v_max']} · Speed {speed}")
    except (KeyError, TypeError, IndexError):
        return ''


# ------------------------------------------------------------------ Datei-Helfer
def _fsync_directory(directory):
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # z. B. Windows
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path, text):
    """Temporaere Datei im selben Ordner, fsync, ``os.replace``, fsync des Ordners."""
    path = Path(path)
    temp_name = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
        _fsync_directory(path.parent)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _now_iso():
    return datetime.now().isoformat(timespec='seconds')


# ------------------------------------------------------------------ Manifest
@dataclass
class SimulationRunManifest:
    """``manifest.json``: alles ausser den Ergebnissen je Frame. Unbekannte Felder bleiben in ``extra`` erhalten."""
    run_id: str
    display_name: str
    created_at: str
    status: str = 'queued'
    source_session: str = ''
    parameters: dict = field(default_factory=dict)       # Parameter-Snapshot (unveraenderlich nach dem Anlegen)
    parameter_summary: str = ''
    fingerprint: str = ''
    format_version: int = RESULT_FORMAT_VERSION
    total_frames: int = 0
    frames_done: int = 0                                 # bereits berechnete (und geschriebene) Frames
    phase: str = ''
    error: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None
    runtime: dict = field(default_factory=dict)          # duration_s, process_ms_median, errors, versions
    events: list = field(default_factory=list)           # [[frame_id, text], ...] (erst beim Abschluss)
    truncated: bool = False
    results_bytes: int = 0                               # Groesse von results.jsonl beim Abschluss (Plausibilitaet)
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        data = {f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name != 'extra'}
        data = jsonable(data)
        data.update({k: v for k, v in jsonable(self.extra).items() if k not in data})
        data['format'] = FORMAT_NAME
        return data

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise RunStoreError('Manifest ist kein JSON-Objekt')
        for key in ('run_id', 'status', 'fingerprint'):
            if not isinstance(data.get(key), str) or not data.get(key):
                raise RunStoreError(f'Manifest: Feld {key} fehlt')
        if data['status'] not in STATUSES:
            raise RunStoreError(f"Manifest: unbekannter Status {data['status']!r}")
        if not _RUN_ID.match(data['run_id']):
            raise RunStoreError('Manifest: ungueltige run_id')
        version = data.get('format_version')
        if isinstance(version, bool) or not isinstance(version, int):
            raise RunStoreError('Manifest: format_version fehlt')
        names = {f.name for f in dataclasses.fields(cls)} - {'extra'}
        values = {k: v for k, v in data.items() if k in names}
        values.setdefault('display_name', data['run_id'])
        values.setdefault('created_at', '')
        values['extra'] = {k: v for k, v in data.items() if k not in names and k != 'format'}
        try:
            manifest = cls(**values)
        except TypeError as exc:
            raise RunStoreError(f'Manifest: {exc}') from exc
        for key in ('total_frames', 'frames_done', 'results_bytes'):
            value = getattr(manifest, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                setattr(manifest, key, 0)
        if not isinstance(manifest.parameters, dict):
            manifest.parameters = {}
        return manifest


@dataclass
class RunEntry:
    """Eintrag der Run-Liste: ``manifest`` ist None bei defektem Manifest (``problem`` nennt den Grund)."""
    run_id: str
    manifest: SimulationRunManifest | None
    status: str              # effektiver Status: Manifest-Status, ``incomplete`` oder ``corrupt``
    problem: str | None = None

    @property
    def selectable(self):
        return self.manifest is not None and self.status != 'corrupt'

    @property
    def usable(self):
        return self.manifest is not None and self.status == 'completed'

    def label(self):
        m = self.manifest
        if m is None:
            return f'{self.run_id} · DEFEKT ({self.problem})'
        created = m.created_at[5:16].replace('T', ' ') if m.created_at else '?'
        frames = f'{m.frames_done}/{m.total_frames}' if self.status != 'completed' else str(m.frames_done)
        text = f'{m.display_name} · {created} · {STATUS_TEXT.get(self.status, self.status)} · {frames} Frames'
        return text + (f' ({self.problem})' if self.problem else '')


# ------------------------------------------------------------------ Ergebnisse je Frame
def frame_to_record(frame):
    """``SimFrame`` -> JSON-Zeile (Linien als [[m, b], ...])."""
    return jsonable({f.name: getattr(frame, f.name) for f in dataclasses.fields(frame)})


def record_to_frame(record):
    """JSON-Zeile -> ``SimFrame``. Unbekannte Felder werden ignoriert, fehlende Pflichtfelder ergeben ``ValueError``."""
    from sim_engine import SimFrame
    if not isinstance(record, dict):
        raise ValueError('Zeile ist kein Objekt')
    names = {f.name for f in dataclasses.fields(SimFrame)}
    values = {k: v for k, v in record.items() if k in names}
    values['lanes'] = tuple(tuple(lane) if isinstance(lane, (list, tuple)) and len(lane) == 2 else None
                            for lane in (values.get('lanes') or ()))
    for key in ('crop', 'process_size'):
        if isinstance(values.get(key), list):
            values[key] = tuple(values[key])
    try:
        return SimFrame(**values)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc


class ResultsWriter:
    """Haengt Frame-Zeilen an ``results.jsonl`` an (nie die ganze Datei neu schreiben)."""

    def __init__(self, path):
        self.path = Path(path)
        self._handle = self.path.open('a', encoding='utf-8', newline='\n')
        self.count = 0
        self.bytes = 0

    def write(self, frame):
        line = json.dumps(frame_to_record(frame), separators=(',', ':'), ensure_ascii=True) + '\n'
        self._handle.write(line)
        self._handle.flush()
        self.count += 1
        self.bytes += len(line.encode('utf-8'))

    def sync(self):
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self):
        if not self._handle.closed:
            try:
                self.sync()
            finally:
                self._handle.close()


class SimulationResultReader:
    """Liest ``results.jsonl`` EINMAL und haelt nur die (kleinen) Frame-Ergebnisse im Speicher.

    Index ``frame_id -> Position``. Bilder werden nie gelesen oder gehalten. Beschaedigte Zeilen werden uebersprungen
    und gezaehlt (``bad_lines``); ``complete`` ist nur wahr, wenn die Zeilenzahl zum Manifest passt.
    """

    def __init__(self, directory, manifest):
        self.manifest = manifest
        self.directory = Path(directory)
        self.frames = []
        self.bad_lines = 0
        self.index = {}
        path = self.directory / RESULTS_NAME
        try:
            handle = path.open('r', encoding='utf-8')
        except OSError as exc:
            raise RunStoreError(f'results.jsonl nicht lesbar: {exc}') from exc
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = record_to_frame(json.loads(line))
                except (ValueError, TypeError):
                    self.bad_lines += 1
                    continue
                self.index.setdefault(frame.frame_id, len(self.frames))
                self.frames.append(frame)

    def __len__(self):
        return len(self.frames)

    @property
    def complete(self):
        return self.bad_lines == 0 and len(self.frames) == self.manifest.frames_done

    def position_of(self, frame_id):
        return self.index.get(frame_id)

    def as_result(self):
        """``SimResult`` fuer SimLab (Diagramm, Kennzahlen, Wiedergabe) - ohne erneute Berechnung."""
        from sim_engine import SimResult
        m = self.manifest
        runtime = m.runtime if isinstance(m.runtime, dict) else {}
        events = [(item[0], str(item[1])) for item in (m.events or []) if isinstance(item, (list, tuple)) and len(item) == 2]
        return SimResult(f'Session {m.source_session}', m.parameters.get('processor', '?'),
                         m.parameters.get('param_mode', 'recorded'), frames=list(self.frames), events=events,
                         duration_s=float(runtime.get('duration_s') or 0.0), truncated=bool(m.truncated),
                         errors=int(runtime.get('errors') or 0))


# ------------------------------------------------------------------ Store
class SimulationRunStore:
    """Verzeichnis ``simulation_runs/`` (wird bei Bedarf angelegt). Aendert nie eine Session."""

    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._readers = OrderedDict()   # run_id -> (Stempel, Reader); hoechstens MAX_CACHED_READERS

    # ---- Pfade
    def ensure_root(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    @staticmethod
    def new_run_id():
        return f'{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}'

    def run_dir(self, run_id):
        if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
            raise RunStoreError('Ungueltige Run-ID')
        return self.root / run_id

    # ---- Anlegen und Schreiben
    def create(self, manifest):
        """Legt den Run-Ordner an (nie einen bestehenden ueberschreiben) und schreibt Manifest und leere Ergebnisdatei."""
        with self._lock:
            self.ensure_root()
            directory = self.run_dir(manifest.run_id)
            try:
                directory.mkdir()
            except FileExistsError as exc:
                raise RunStoreError(f'Run {manifest.run_id} existiert bereits') from exc
            (directory / RESULTS_NAME).write_text('', encoding='utf-8')
            self.write_manifest(manifest)
            return directory

    def write_manifest(self, manifest):
        manifest.updated_at = _now_iso()
        atomic_write_text(self.run_dir(manifest.run_id) / MANIFEST_NAME,
                          json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + '\n')

    def writer(self, run_id):
        return ResultsWriter(self.run_dir(run_id) / RESULTS_NAME)

    # ---- Lesen
    def load_manifest(self, run_id):
        path = self.run_dir(run_id) / MANIFEST_NAME
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise RunStoreError('manifest.json fehlt') from exc
        except (OSError, ValueError) as exc:
            raise RunStoreError(f'manifest.json nicht lesbar: {exc}') from exc
        manifest = SimulationRunManifest.from_dict(data)
        if manifest.run_id != run_id:
            raise RunStoreError('Manifest gehoert zu einem anderen Run')
        return manifest

    def _entry(self, run_id, active_run_id):
        try:
            manifest = self.load_manifest(run_id)
        except RunStoreError as exc:
            return RunEntry(run_id, None, 'corrupt', str(exc))
        status, problem = manifest.status, None
        if status in ACTIVE_STATUSES and run_id != active_run_id:
            status, problem = 'incomplete', f'{manifest.frames_done}/{manifest.total_frames} Frames'
        elif status == 'completed':
            if manifest.format_version not in COMPATIBLE_FORMATS:
                status, problem = 'corrupt', f'Formatversion {manifest.format_version} nicht unterstuetzt'
            else:
                try:
                    size = (self.run_dir(run_id) / RESULTS_NAME).stat().st_size
                except OSError:
                    size = None
                if size is None or size != manifest.results_bytes:
                    status, problem = 'corrupt', 'results.jsonl fehlt oder passt nicht zum Manifest'
        if status == 'failed':
            problem = manifest.error
        return RunEntry(run_id, manifest, status, problem)

    def list_runs(self, active_run_id=None):
        """Alle Runs, neueste zuerst. Ein defekter Run wird einzeln als ``corrupt`` gefuehrt (nie eine Ausnahme)."""
        try:
            names = sorted((p.name for p in self.root.iterdir() if p.is_dir() and _RUN_ID.match(p.name)), reverse=True)
        except OSError:
            return []
        return [self._entry(name, active_run_id) for name in names]

    def get_entry(self, run_id, active_run_id=None):
        return self._entry(run_id, active_run_id)

    def find_matching(self, source_name, fingerprint_hex, active_run_id=None):
        """Neuester vollstaendig erfolgreicher, kompatibler Run mit gleicher Quell-Session und gleichem Fingerprint."""
        for entry in self.list_runs(active_run_id):
            m = entry.manifest
            if (entry.usable and m.source_session == source_name and m.fingerprint == fingerprint_hex
                    and m.format_version in COMPATIBLE_FORMATS and m.frames_done > 0):
                return entry
        return None

    def open_reader(self, run_id):
        """Reader eines fertigen Runs; wiederholte Aufrufe lesen die Datei nicht erneut (LRU, hoechstens 2)."""
        entry = self._entry(run_id, None)
        if not entry.usable:
            raise RunStoreError(f'Run nicht abspielbar ({STATUS_TEXT.get(entry.status, entry.status)})')
        path = self.run_dir(run_id) / RESULTS_NAME
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, entry.manifest.updated_at)
        with self._lock:
            cached = self._readers.get(run_id)
            if cached is not None and cached[0] == stamp:
                self._readers.move_to_end(run_id)
                return cached[1]
        reader = SimulationResultReader(self.run_dir(run_id), entry.manifest)
        if not reader.complete:
            raise RunStoreError(f'Ergebnisse unvollstaendig ({len(reader)} von {entry.manifest.frames_done} Frames lesbar)')
        with self._lock:
            self._readers[run_id] = (stamp, reader)
            self._readers.move_to_end(run_id)
            while len(self._readers) > MAX_CACHED_READERS:
                self._readers.popitem(last=False)
        return reader


# ------------------------------------------------------------------ Fortschritt
@dataclass(frozen=True)
class SimulationProgress:
    """Unveraenderliche Momentaufnahme des Berechnungsstatus (thread-sicher lesbar)."""
    run_id: str | None = None
    status: str = 'idle'              # idle | queued | running | completed | failed | cancelled
    current_frame: int = 0            # bereits verarbeitete Frames
    total_frames: int = 0
    phase: str = ''
    frame_id: int | None = None       # Frame-ID des Frames in Arbeit
    elapsed_s: float = 0.0
    error: str | None = None
    cancel_requested: bool = False
    display_name: str = ''
    source_session: str = ''
    job: int = 0                      # laufende Nummer des Auftrags (aendert sich mit jedem Start)

    @property
    def progress_percent(self):
        if self.status == 'completed':
            return 100.0
        if self.total_frames <= 0:
            return 0.0
        return round(min(100.0, 100.0 * self.current_frame / self.total_frames), 1)

    @property
    def active(self):
        return self.status in ACTIVE_STATUSES

    @property
    def finished(self):
        return self.status in ('completed', 'failed', 'cancelled')


class ProgressBoard:
    """Kleine thread-sichere Statusschnittstelle zwischen Runner-Thread und Dash-Polling."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}
        self._job = 0
        self._t0 = self._t1 = None

    def begin(self, run_id, display_name, source_session, total_frames):
        with self._lock:
            self._job += 1
            self._t0, self._t1 = time.monotonic(), None
            self._state = dict(run_id=run_id, status='queued', current_frame=0, total_frames=int(total_frames),
                               phase='Warte auf Start', frame_id=None, error=None, cancel_requested=False,
                               display_name=display_name, source_session=source_session)

    def update(self, **values):
        with self._lock:
            self._state.update(values)
            if values.get('status') in ('completed', 'failed', 'cancelled') and self._t1 is None:
                self._t1 = time.monotonic()

    def snapshot(self):
        with self._lock:
            if not self._state:
                return SimulationProgress()
            end = self._t1 if self._t1 is not None else time.monotonic()
            return SimulationProgress(elapsed_s=round(end - self._t0, 1), job=self._job, **self._state)


# ------------------------------------------------------------------ Auftrag und Runner
@dataclass(frozen=True)
class RunRequest:
    """Unveraenderlicher Berechnungsauftrag: Snapshot und Fingerprint stehen vor dem Start fest."""
    source_name: str
    processor_key: str
    param_mode: str                    # recorded | studio
    studio_params: tuple | None
    lo: int | None
    hi: int | None
    warmup: int
    speed_policy: object               # SpeedPolicy oder None
    snapshot: dict
    fingerprint: str
    total_frames: int
    display_name: str
    summary: str


def prepare_request(engine, source, processor_key, param_mode, studio_params=None, lo=None, hi=None, warmup=0,
                    speed_policy=None):
    """Validiert die Eingaben und bildet Snapshot und Fingerprint. ``ValueError`` mit verstaendlicher Meldung."""
    from processor_registry import specs
    from sim_engine import MAX_FRAMES
    if getattr(source, 'kind', None) != 'session':
        raise ValueError('Nur gespeicherte Sessions koennen als Run gespeichert werden '
                         '(der Live-Ringbuffer ist fluechtig - bitte eine Aufnahme starten und die Session waehlen)')
    if processor_key not in {spec.key for spec in specs()}:
        raise ValueError(f'Unbekannter Prozessor: {processor_key}')
    if param_mode not in ('recorded', 'studio'):
        raise ValueError(f'Unbekannte Parameterquelle: {param_mode}')
    if param_mode == 'studio':
        if studio_params is None:
            raise ValueError('Keine aktuellen Studio-Parameter verfuegbar')
        studio_params = copy.deepcopy(tuple(studio_params))
    else:
        studio_params = None
    warmup = max(0, int(warmup or 0))
    plan = engine.plan(source.frames, lo, hi, warmup, MAX_FRAMES)     # ValueError bei leerer Quelle / leerem Bereich
    snapshot = build_parameter_snapshot(source, processor_key, param_mode, studio_params, plan,
                                        engine.controller_settings(), speed_policy, MAX_FRAMES,
                                        driving_mode=getattr(engine, 'DRIVING_MODE', 'camera'))
    digest = fingerprint(snapshot)
    return RunRequest(source.name, processor_key, param_mode, studio_params, lo, hi, warmup, speed_policy,
                      snapshot, digest, plan[2] - plan[0] + 1,
                      f'{processor_key} · {source.name} · {digest[:8]}', summarize(snapshot))


class SimulationRunner:
    """Fuehrt HOECHSTENS EINE Berechnung gleichzeitig in einem kontrollierten Hintergrund-Thread aus.

    ``start`` kehrt sofort zurueck; ein zweiter Start waehrend einer laufenden Berechnung ergibt ``RunnerBusyError``.
    Fortschritt: ``progress.snapshot()``; Abbruch: ``cancel()`` (wirkt zwischen zwei Frames). Das Ergebnis wird je
    Frame an ``results.jsonl`` angehaengt, das Manifest hoechstens einmal je Sekunde aktualisiert.
    """

    def __init__(self, store, source_factory):
        self.store = store
        self._source_factory = source_factory
        self.progress = ProgressBoard()
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = threading.Event()

    # ---- Steuerung
    def is_active(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def active_run_id(self):
        snap = self.progress.snapshot()
        return snap.run_id if snap.active else None

    def start(self, request, engine, abort_if=None):
        """Legt den Run an und startet den Hintergrund-Thread; kehrt sofort zurueck.

        ``abort_if()`` (optional) wird vor jedem Frame gefragt; liefert es True, wird der Lauf wie ein Abbruch beendet
        (z. B. weil eine Fahrt gestartet wurde und die Simulation die CPU nicht belasten soll).
        """
        with self._lock:
            if self.is_active():
                snap = self.progress.snapshot()
                raise RunnerBusyError(f'Es läuft bereits eine Berechnung ({snap.display_name or snap.run_id}). '
                                      'Bitte abwarten oder abbrechen.')
            manifest = SimulationRunManifest(
                run_id=self.store.new_run_id(), display_name=request.display_name, created_at=_now_iso(),
                status='queued', source_session=request.source_name, parameters=copy.deepcopy(request.snapshot),
                parameter_summary=request.summary, fingerprint=request.fingerprint,
                total_frames=request.total_frames, phase='Warte auf Start')
            self.store.create(manifest)
            self._cancel = threading.Event()
            self.progress.begin(manifest.run_id, manifest.display_name, request.source_name, request.total_frames)
            self._thread = threading.Thread(target=self._work,
                                            args=(manifest, request, engine, self._cancel, abort_if),
                                            name='simulation-runner', daemon=True)
            self._thread.start()
            return manifest

    def cancel(self):
        """Fordert den Abbruch an; True, wenn gerade eine Berechnung laeuft."""
        if not self.is_active():
            return False
        self._cancel.set()
        self.progress.update(cancel_requested=True, phase='Abbruch angefordert ...')
        return True

    def wait(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.is_active()

    # ---- Hintergrund-Thread
    def _save(self, manifest, **values):
        for key, value in values.items():
            setattr(manifest, key, value)
        try:
            self.store.write_manifest(manifest)
        except (OSError, RunStoreError):
            log.exception('Manifest von Run %s nicht schreibbar', manifest.run_id)

    def _work(self, manifest, request, engine, cancel, abort_if=None):
        started = time.monotonic()
        writer = None
        reason = {}

        def should_cancel():
            if cancel.is_set():
                return True
            if abort_if is not None:
                try:
                    if abort_if():
                        reason['text'] = 'Abgebrochen (Fahrt gestartet)'
                        return True
                except Exception:  # eine fehlerhafte Pruefung darf den Lauf nicht beenden
                    log.exception('abort_if fehlgeschlagen')
            return False

        try:
            self.progress.update(status='running', phase='Quelle laden')
            self._save(manifest, status='running', phase='Quelle laden')
            source = self._source_factory(request.source_name)
            expected = request.snapshot.get('source', {})
            if (len(source.frames), source.frames[0].frame_id, source.frames[-1].frame_id) != (
                    expected.get('frames'), expected.get('first_frame_id'), expected.get('last_frame_id')):
                raise RuntimeError('Die Quell-Session hat sich seit der Auswahl geändert')
            writer = self.store.writer(manifest.run_id)
            last_saved = [time.monotonic(), 0]

            def on_progress(done, total, frame_id):
                self.progress.update(current_frame=done, total_frames=total, frame_id=frame_id,
                                     phase=f'Simulation: Frame {done} von {total}')
                now = time.monotonic()
                if now - last_saved[0] >= MANIFEST_EVERY_S or writer.count - last_saved[1] >= MANIFEST_EVERY_FRAMES:
                    last_saved[:] = [now, writer.count]
                    self._save(manifest, frames_done=writer.count, total_frames=total,
                               phase=f'Simulation: Frame {done} von {total}')

            result = engine.run(source, request.processor_key, request.studio_params, request.lo, request.hi,
                                request.warmup, speed_policy=request.speed_policy, on_frame=writer.write,
                                progress=on_progress, cancel=should_cancel)
            self.progress.update(phase='Ergebnisse sichern')
            writer.close()
            runtime = {'duration_s': round(time.monotonic() - started, 2), 'engine_s': round(result.duration_s, 2),
                       'errors': result.errors, 'frames_written': writer.count,
                       'process_ms_median': result.stats().get('process_ms_median') if result.measured else None}
            common = dict(frames_done=writer.count, runtime=runtime, events=[list(e) for e in result.events],
                          truncated=result.truncated, results_bytes=writer.bytes, finished_at=_now_iso())
            if result.cancelled:
                phase = reason.get('text', 'Abgebrochen')
                self._save(manifest, status='cancelled', phase=phase, **common)
                self.progress.update(status='cancelled', current_frame=writer.count, phase=phase)
            else:
                self._save(manifest, status='completed', phase='Fertig', **common)
                self.progress.update(status='completed', current_frame=request.total_frames, phase='Fertig')
        except BaseException as exc:  # auch Systemfehler: der Lauf wird als fehlgeschlagen markiert, nie als erfolgreich
            log.exception('Simulation Run %s fehlgeschlagen', manifest.run_id)
            text = f'{type(exc).__name__}: {exc}'[:500]
            if writer is not None:
                try:
                    writer.close()
                except OSError:
                    pass
            self._save(manifest, status='failed', error=text, phase='Fehlgeschlagen',
                       frames_done=writer.count if writer is not None else 0, finished_at=_now_iso(),
                       runtime={'duration_s': round(time.monotonic() - started, 2)})
            self.progress.update(status='failed', error=text, phase='Fehlgeschlagen')
