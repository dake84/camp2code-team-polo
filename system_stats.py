"""Systemwerte des Pi fuer Dashboard und Telemetrie (Paket H2): CPU, RAM, Temperatur, Speicherplatz.

WICHTIG - genau EIN Aufrufer: ``SystemSampler.sample()`` wird ausschliesslich vom Telemetrie-Sampler-Thread der
``CarApplication`` aufgerufen (ca. 4 Hz). ``psutil.cpu_percent(interval=None)`` liefert die Auslastung seit dem
vorherigen Aufruf; mehrere Aufrufer wuerden sich gegenseitig die Messintervalle verkuerzen und die Werte
verfaelschen. Dash-Callbacks lesen nur ``last()``. Ruft ein anderer Thread ``sample()`` auf, bekommt er den letzten
Wert, ohne dass gemessen wird.

Fehlende Werte bleiben ``None`` und werden als Gedankenstrich angezeigt, nie als 0.
Speicherplatz und Aufnahme-Schaetzung werden nur alle ``DISK_EVERY_S`` Sekunden neu bestimmt.
"""
import logging
import os
import shutil
import threading
import time
from pathlib import Path

try:  # optional
    import psutil
except ImportError:  # pragma: no cover - abhaengig von der Installation
    psutil = None

log = logging.getLogger(__name__)

THERMAL_PATH = '/sys/class/thermal/thermal_zone0/temp'
DISK_EVERY_S = 30.0
CPU_WARN, CPU_BAD = 60.0, 85.0     # gruen < 60 %, gelb < 85 %, rot darueber
TEMP_BAD = 80.0                    # ab 80 Grad C immer rot (der Pi 4 taktet ab ca. 80 Grad herunter)
SAMPLE_FILES = 40                  # so viele JPEG-Dateien werden fuer die mittlere Groesse gemessen


def empty():
    """Alle Werte unbekannt (z. B. Offline-Betrieb oder vor der ersten Messung)."""
    return {'cpu': None, 'ram': None, 'temp': None, 'disk': None, 'time': None}


def cpu_level(cpu, temp=None):
    """Ampel der CPU-Last: 'ok' (< 60 %), 'warn' (< 85 %), 'bad'; ab 80 Grad C immer 'bad'; 'off' ohne Wert."""
    if temp is not None and temp >= TEMP_BAD:
        return 'bad'
    if cpu is None:
        return 'off'
    return 'ok' if cpu < CPU_WARN else 'warn' if cpu < CPU_BAD else 'bad'


def temp_level(temp):
    return 'off' if temp is None else 'bad' if temp >= TEMP_BAD else 'ok'


def percent_text(value):
    return '–' if value is None else f'{value:.0f} %'


def temp_text(value):
    return '–' if value is None else f'{value:.0f} °C'


def load_summary(stats):
    """Anzeige der Auslastung: CPU gross, RAM und Temperatur klein (Texte und Ampelstufen)."""
    cpu, ram, temp = stats.get('cpu'), stats.get('ram'), stats.get('temp')
    return {'cpu': percent_text(cpu), 'cpu_level': cpu_level(cpu, temp),
            'ram': percent_text(ram), 'ram_level': 'off' if ram is None else 'ok',
            'temp': temp_text(temp), 'temp_level': temp_level(temp)}


def read_temperature(path=THERMAL_PATH):
    """CPU-Temperatur in Grad C: /sys/class/thermal (Pi), sonst psutil; ``None`` wenn nicht lesbar."""
    try:
        with open(path, encoding='ascii') as handle:
            value = float(handle.read().strip()) / 1000.0
        if -50.0 < value < 150.0:
            return value
    except (OSError, ValueError):
        pass
    if psutil is not None:
        try:
            sensors = psutil.sensors_temperatures()
            for entries in sensors.values():
                for entry in entries:
                    if entry.current is not None and -50.0 < entry.current < 150.0:
                        return float(entry.current)
        except (AttributeError, OSError, RuntimeError):
            pass
    return None


def newest_session_jpeg_mean(sessions_root):
    """(mittlere JPEG-Groesse in Bytes, Median-fps aus dem Manifest oder None, Sessionname) der neuesten Session.

    ``None`` ohne Session oder ohne Bilder. Es werden hoechstens ``SAMPLE_FILES`` gleichmaessig verteilte Dateien
    gemessen, nicht alle.
    """
    root = Path(sessions_root)
    try:
        sessions = sorted((p for p in root.iterdir() if p.is_dir() and (p / 'frames.csv').is_file()), reverse=True)
    except OSError:
        return None
    for session in sessions:
        try:
            files = sorted((session / 'frames').glob('*.jpg'))
        except OSError:
            continue
        if not files:
            continue
        step = max(1, len(files) // SAMPLE_FILES)
        sizes = []
        for path in files[::step][:SAMPLE_FILES]:
            try:
                sizes.append(path.stat().st_size)
            except OSError:
                pass
        if not sizes:
            continue
        fps = None
        try:
            import json
            value = json.loads((session / 'manifest.json').read_text(encoding='utf-8')).get('capture', {}).get('median_fps')
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                fps = float(value)
        except (OSError, ValueError, AttributeError):
            pass
        return sum(sizes) / len(sizes), fps, session.name
    return None


def recording_estimate(free_bytes, sessions_root, live_fps=None):
    """Schaetzung: Minuten Aufnahme, die auf den freien Speicher passen. ``None`` ohne Session oder ohne fps.

    Grundlage: mittlere JPEG-Groesse der neuesten Session und Kamera-fps (live gemessen, sonst Median der Session).
    Das Ergebnis ist eine Schaetzung (die Bildgroesse haengt vom Bildinhalt ab; CSV-Dateien sind nicht eingerechnet).
    """
    found = newest_session_jpeg_mean(sessions_root)
    if found is None:
        return None
    mean_bytes, session_fps, name = found
    fps = live_fps if live_fps and live_fps > 0 else session_fps
    if not fps or mean_bytes <= 0:
        return None
    return {'minutes': free_bytes / (mean_bytes * fps) / 60.0, 'mean_kb': mean_bytes / 1024.0, 'fps': fps,
            'session': name}


def disk_text(disk):
    """Anzeige des Speicherplatzes; ``None`` ergibt einen Gedankenstrich."""
    if not disk:
        return '–'
    text = f"Frei {disk['free_gb']:.1f} GB ({disk['free_pct']:.0f} %)"
    estimate = disk.get('estimate')
    if estimate:
        text += (f" · reicht für ca. {estimate['minutes']:.0f} min Aufnahme (Schätzung aus mittlerer JPEG-Größe "
                 f"{estimate['mean_kb']:.0f} kB und {estimate['fps']:.0f} fps)")
    else:
        text += ' · Aufnahmedauer nicht schätzbar (noch keine Session)'
    return text


class SystemSampler:
    """Misst CPU, RAM, Temperatur und (selten) Speicherplatz; siehe Modul-Docstring zum einen Aufrufer."""

    def __init__(self, data_dir='Data_AI', sessions_dir=None, thermal_path=THERMAL_PATH, clock=time.monotonic,
                 fps_source=None, disk_every_s=DISK_EVERY_S):
        self.data_dir = Path(data_dir)
        self.sessions_dir = Path(sessions_dir) if sessions_dir is not None else self.data_dir / 'sessions'
        self.thermal_path = thermal_path
        self.fps_source = fps_source
        self.disk_every_s = disk_every_s
        self._clock = clock
        self._lock = threading.Lock()
        self._owner = None
        self._last = empty()
        self._disk_at = None
        self._warned = False
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)   # Bezugspunkt setzen; der erste Rueckgabewert ist bedeutungslos
            except Exception:
                log.exception('cpu_percent nicht verfuegbar')

    def last(self):
        """Letzter Wert (Kopie); darf von beliebig vielen Threads gelesen werden."""
        with self._lock:
            return dict(self._last)

    def _disk(self):
        target = self.data_dir
        while not target.exists() and target != target.parent:
            target = target.parent
        usage = shutil.disk_usage(target)
        disk = {'free_gb': usage.free / 1e9, 'free_pct': usage.free / usage.total * 100.0 if usage.total else 0.0,
                'total_gb': usage.total / 1e9, 'estimate': None}
        try:
            fps = self.fps_source() if self.fps_source is not None else None
            disk['estimate'] = recording_estimate(usage.free, self.sessions_dir, fps)
        except Exception:
            log.exception('Aufnahme-Schaetzung nicht moeglich')
        return disk

    def sample(self):
        """Neue Messung (nur vom ersten aufrufenden Thread); andere Threads erhalten den letzten Wert."""
        ident = threading.get_ident()
        with self._lock:
            if self._owner is None:
                self._owner = ident
            elif self._owner != ident:
                if not self._warned:
                    self._warned = True
                    log.warning('SystemSampler.sample() von einem zweiten Thread aufgerufen - ignoriert')
                return dict(self._last)
        values = {'cpu': None, 'ram': None, 'temp': None, 'time': self._clock()}
        if psutil is not None:
            try:
                values['cpu'] = float(psutil.cpu_percent(interval=None))
            except Exception:
                log.debug('CPU nicht lesbar', exc_info=True)
            try:
                values['ram'] = float(psutil.virtual_memory().percent)
            except Exception:
                log.debug('RAM nicht lesbar', exc_info=True)
        values['temp'] = read_temperature(self.thermal_path)
        with self._lock:
            now = self._clock()
            disk = self._last.get('disk')
            if self._disk_at is None or now - self._disk_at >= self.disk_every_s:
                self._disk_at = now
                try:
                    disk = self._disk()
                except Exception:
                    log.debug('Speicherplatz nicht lesbar', exc_info=True)
                    disk = None
            values['disk'] = disk
            self._last = values
            return dict(values)
