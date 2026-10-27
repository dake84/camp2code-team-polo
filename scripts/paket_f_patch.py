#!/usr/bin/env python3
"""Paket F: verankerte Aenderungen an bestehenden Dateien.

Fehlt ein Anker oder ist er mehrdeutig, wird KEINE Datei geschrieben.
Dateien mit Marker PAKET-F werden uebersprungen (idempotent).
"""
import re
import sys
from pathlib import Path

MARKER = 'PAKET-F'


class PatchError(Exception):
    pass


def _hits(lines, needle):
    return [i for i, line in enumerate(lines) if needle in line]


def _one(lines, needle, file):
    hits = _hits(lines, needle)
    if len(hits) != 1:
        raise PatchError(f'{file}: Anker {needle!r} {len(hits)}x gefunden (erwartet 1)')
    return hits[0]


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def sub_once(text, old, new, file):
    count = text.count(old)
    if count != 1:
        raise PatchError(f'{file}: Text {old[:70]!r} {count}x gefunden (erwartet 1)')
    return text.replace(old, new)


def insert(text, needle, block, file, after=True):
    lines = text.split('\n')
    i = _one(lines, needle, file)
    ind = _indent(lines[i])
    new = [(ind + l) if l.strip() else '' for l in block.strip('\n').split('\n')]
    pos = i + 1 if after else i
    return '\n'.join(lines[:pos] + new + lines[pos:])


def replace_line(text, needle, block, file):
    lines = text.split('\n')
    i = _one(lines, needle, file)
    ind = _indent(lines[i])
    new = [(ind + l) if l.strip() else '' for l in block.strip('\n').split('\n')]
    return '\n'.join(lines[:i] + new + lines[i + 1:])


# ------------------------------------------------------------------ driving/camera_mode.py (Punkt 3)
def patch_camera_mode(t, f):
    count = t.count('time.monotonic_ns()')
    if count != 4:
        raise PatchError(f'{f}: time.monotonic_ns() {count}x gefunden (erwartet 4)')
    t = t.replace('time.monotonic_ns()', 'self._clock()')
    return insert(t, 'self._cameracar = car', '''
# PAKET-F: austauschbare monotone Uhr (ns). Live = time.monotonic_ns; die Simulation
# setzt die aufgezeichneten Zeitstempel ein, damit LANE_TIMEOUT wie damals wirkt.
self._clock = time.monotonic_ns''', f, after=False)


# ------------------------------------------------------------------ CameraSensor.py (Punkt 1)
def patch_camera_sensor(t, f):
    t = sub_once(t, 'from pipeline_processors import CopilotLaneProcessor, LaneProcessor',
                 'from processor_registry import create_processor, is_live_processor, key_for_class  # PAKET-F', f)
    t = sub_once(t, "self._processor = LaneProcessor() if settings.get('processor') == 'legacy' else CopilotLaneProcessor()",
                 "self._processor = create_processor(  # PAKET-F: nur fahrfreigegebene Prozessoren\n"
                 "            settings.get('processor') if is_live_processor(settings.get('processor')) else 'copilot')", f)
    t = sub_once(t, "if name not in ('legacy', 'copilot'):", 'if not is_live_processor(name):', f)
    t = sub_once(t, "self._processor = LaneProcessor() if name == 'legacy' else CopilotLaneProcessor()",
                 'self._processor = create_processor(name)', f)
    return sub_once(t, "return 'copilot' if isinstance(self._processor, CopilotLaneProcessor) else 'legacy'",
                    'return key_for_class(type(self._processor).__name__)', f)


# ------------------------------------------------------------------ pipeline_ab.py (Punkt 1)
def patch_pipeline_ab(t, f):
    return replace_line(t, "instance = CopilotLaneProcessor() if name == 'copilot' else LaneProcessor()", '''
if name == 'copilot':
    instance = CopilotLaneProcessor()
elif name == 'legacy':
    instance = LaneProcessor()
else:  # PAKET-F: weitere Prozessoren aus dem zentralen Verzeichnis
    from processor_registry import create_processor
    instance = create_processor(name)''', f)


# ------------------------------------------------------------------ replay_studio.py (Punkt 1)
def patch_replay_studio(t, f):
    return replace_line(t, "return 'legacy' if processor_name in ('LaneProcessor', 'legacy') else 'copilot'", '''
from processor_registry import key_for_class  # PAKET-F: zentrales Verzeichnis
return key_for_class(processor_name)''', f)


# ------------------------------------------------------------------ Camp2CodeDashboard.py (Punkte 1, 5, 7)
def patch_dashboard(t, f):
    t = replace_line(t, 'from car_application import CarApplication', '''
try:  # PAKET-F: ohne Raspberry-Pi-Bibliotheken (Laptop) nur mit --offline
    from car_application import CarApplication
    _HARDWARE_ERROR = None
except (ImportError, RuntimeError, OSError) as _exc:
    CarApplication = None
    _HARDWARE_ERROR = _exc''', f)
    t = insert(t, 'from pipeline_ab import PipelineAB',
               'from processor_registry import processor_options  # PAKET-F\n'
               'from sim_lab import SimLab  # PAKET-F', f)
    lines = t.split('\n')
    i = _one(lines, "{'label': 'Mit Diagnosebildern (Copilot)', 'value': 'copilot'},", f)
    if "'Ohne Diagnosebilder (Legacy)'" not in lines[i + 1] or not lines[i + 1].rstrip().endswith(']),'):
        raise PatchError(f'{f}: Prozessor-Optionen nicht wie erwartet aufgebaut')
    lines[i:i + 2] = [_indent(lines[i]) + '*processor_options(live_only=True)]),  # PAKET-F']
    t = '\n'.join(lines)
    t = insert(t, 'pipeline = html.Div([', 'cockpit = SimLab.wrap_cockpit(cockpit, dbc, dcc, html)  # PAKET-F', f,
               after=False)
    t = insert(t, 'thread_cpu = ThreadCpuSampler()  # PAKET-C', '''
# PAKET-F: Cockpit-Simulation (Live | Simulation)
sim = SimLab(application, SimpleNamespace(dbc=dbc, dcc=dcc, html=html, Input=Input, Output=Output,
                                          State=State, ctx=ctx, no_update=no_update), uri)''', f)
    t = insert(t, 'car = application.car', 'sim.register(app)  # PAKET-F', f)
    t = insert(t, 'application.start_mode(mode)', '''
if sim.active:  # PAKET-F: im Simulationsmodus kein START
    raise RuntimeError('Simulation aktiv – Cockpit zuerst auf Live umschalten')''', f, after=False)
    t = sub_once(t, 'running or recovery, not (running or recovery),',
                 'running or recovery or sim.active, not (running or recovery),  # PAKET-F', f)
    t = sub_once(t, '\ndef main():', '''
def _port_from_argv(default):
    """PAKET-F: ``--port N`` (z. B. Laptop-Offline parallel zu einem anderen Dienst)."""
    if '--port' in sys.argv:
        index = sys.argv.index('--port')
        try:
            return int(sys.argv[index + 1])
        except (IndexError, ValueError):
            raise SystemExit('--port erwartet eine Zahl')
    return default


def main():''', f)
    t = replace_line(t, 'application = CarApplication()', '''
offline = '--offline' in sys.argv  # PAKET-F
if offline:
    from offline_application import OfflineApplication
    application = OfflineApplication()
    log.warning('OFFLINE-Modus: keine Kamera, keine Motoren')
elif CarApplication is None:
    raise SystemExit(f'Hardware-Module nicht ladbar ({_HARDWARE_ERROR}). Ohne Hardware starten: --offline')
else:
    application = CarApplication()''', f)
    return sub_once(t, "create_app(application).run(host='0.0.0.0', port=8050, debug=False, use_reloader=False)",
                    "create_app(application).run(host='127.0.0.1' if offline else '0.0.0.0',\n"
                    "                                    port=_port_from_argv(8050), debug=False, use_reloader=False)",
                    f)


# ------------------------------------------------------------------ Tests
def patch_test_callbacks(t, f):
    return sub_once(t, "'replay_to_studio'}",
                    "'replay_to_studio', 'sim_view_mode', 'sim_run', 'sim_seek', 'sim_play_toggle', 'sim_render'}"
                    "  # PAKET-F", f)


def patch_test_real_dash(t, f):
    pattern = re.compile(r'self\.assertEqual\(len\(deps\), (\d+)\)')
    found = pattern.findall(t)
    if len(found) != 1:
        raise PatchError(f'{f}: Anzahl-Pruefung der Callbacks {len(found)}x gefunden')
    return pattern.sub(lambda m: f'self.assertEqual(len(deps), {int(m.group(1)) + 5})  # PAKET-F', t)


CHANGELOG = '''
## 6.4 – Paket F: Simulation (PAKET-F)

* **Zentrales Prozessor-Verzeichnis** (`processor_registry.py`): CameraSensor, Pipeline Studio,
  A/B-Labor, Replay und Simulation lesen dieselbe Liste. Neue Prozessoren: ein Eintrag/`register()`.
* **MockProcessor** (`mock_processor.py`): verschiebt Linien, laesst jede 7. rechte Linie weg;
  nur Simulation (`live=False`), vom CameraSensor abgewiesen.
* **Bildquellen** (`frame_source.py`): `SessionSource`, `RingbufferSource`.
* **Austauschbare Uhr im CameraMode** (`self._clock`): Live unveraendert `time.monotonic_ns`;
  Simulation nutzt aufgezeichnete Zeitstempel (LANE_TIMEOUT wie damals).
* **ReplayCar** (`replay_car.py`): Fahrzeug-Attrappe mit BaseCar-Begrenzungen, keine Hardware.
* **Cockpit-Umschalter Live | Simulation** (`sim_lab.py`, `sim_engine.py`): Session/Ringbuffer
  mit beliebigem Prozessor und Parametern (damals/Studio) neu rechnen, Vorlauf, damals vs. jetzt
  je Frame und als Diagramm, Kennzahlen. START im Simulationsmodus gesperrt.
* **Batch-Report** `scripts/sim_report.py`: Kennzahlen, CSV und HTML mit auffaelligen Frames.
* **Offline-Start** `python Camp2CodeDashboard.py --offline [--port N]` ohne Hardware (Laptop).
'''

PATCHES = {
    'driving/camera_mode.py': patch_camera_mode,
    'CameraSensor.py': patch_camera_sensor,
    'pipeline_ab.py': patch_pipeline_ab,
    'replay_studio.py': patch_replay_studio,
    'Camp2CodeDashboard.py': patch_dashboard,
    'tests/test_dashboard_callbacks.py': patch_test_callbacks,
    'tests/test_dashboard_real_dash.py': patch_test_real_dash,
    'CHANGELOG.md': lambda t, f: insert(t, '# Changelog', CHANGELOG, f),
}


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    only = set(sys.argv[2:])
    results = {}
    try:
        for name, func in PATCHES.items():
            if only and name not in only:
                continue
            path = root / name
            text = path.read_text(encoding='utf-8')
            if MARKER in text:
                print(f'  = {name} (bereits gepatcht)')
                continue
            new = func(text, name)
            if MARKER not in new:
                raise PatchError(f'{name}: Marker fehlt nach dem Patch')
            if name.endswith('.py'):
                compile(new, name, 'exec')
            results[path] = new
    except (PatchError, SyntaxError, OSError) as exc:
        print(f'PATCH ABGEBROCHEN, keine Datei geaendert: {exc}', file=sys.stderr)
        return 1
    for path, new in results.items():
        path.write_text(new, encoding='utf-8')
        print(f'  + {path.relative_to(root)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
