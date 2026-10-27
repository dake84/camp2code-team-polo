#!/usr/bin/env python3
"""Paket D: Replay-Frame im Pipeline Studio, leise Konsole.

Verankerte Aenderungen; fehlt ein Anker, wird KEINE Datei geschrieben.
Bereits gepatchte Dateien (Marker PAKET-D) werden uebersprungen.
"""
import sys
from pathlib import Path

MARKER = 'PAKET-D'


class PatchError(Exception):
    pass


def _unique_line(lines, needle, file, start=0):
    hits = [i for i in range(start, len(lines)) if needle in lines[i]]
    if len(hits) != 1:
        raise PatchError(f'{file}: Anker {needle!r} {len(hits)}x gefunden (erwartet 1)')
    return hits[0]


def sub_once(text, old, new, file):
    n = text.count(old)
    if n != 1:
        raise PatchError(f'{file}: Text {old[:60]!r} {n}x gefunden (erwartet 1)')
    return text.replace(old, new)


def insert(text, needle, block, file, after=True):
    lines = text.split('\n')
    i = _unique_line(lines, needle, file)
    ind = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    new = [(ind + l) if l.strip() else '' for l in block.strip('\n').split('\n')]
    pos = i + 1 if after else i
    return '\n'.join(lines[:pos] + new + lines[pos:])


def replace_block(text, start_needle, end_needle, block, file):
    lines = text.split('\n')
    s = _unique_line(lines, start_needle, file)
    e = _unique_line(lines, end_needle, file, start=s + 1)
    return '\n'.join(lines[:s] + block.strip('\n').split('\n') + [''] + lines[e:])


# ------------------------------------------------------------------ pipeline_ab.py
def patch_pipeline_ab(t, f):
    return insert(t, 'def clear(self):', '''
def reference(self):
    """PAKET-D: (Rohbild, Frame-ID, Referenzparameter, Prozessor) der Referenz A, nur lesen."""
    with self._lock:
        return self._raw, self._frame_id, self._reference, self._processor
''', f, after=False)


# ------------------------------------------------------------------ Camp2CodeDashboard.py
FREEZE = r'''
    @app.callback(Output('pipeline-frozen', 'data'), Input('pipeline-freeze', 'n_clicks'),
                  Input('replay-studio-token', 'data'),
                  State('pipeline-frozen', 'data'), prevent_initial_call=True)
    def freeze(_, token, frozen):
        """LIVE <-> FREEZE. PAKET-D: ein Replay-Frame wird als eigener Freeze-Zustand gesetzt."""
        if ctx.triggered_id == 'replay-studio-token':
            return dict(token) if token else no_update
        if frozen is not None:
            return None
        camera = car.camera_data
        return camera.frame_id if camera is not None and camera.frame_id >= 0 else None
'''

REPLAY_VIEW = r'''
def replay_pipeline_view(frozen, inspected, rendered, lock):
    """PAKET-D: Stufen 01-08 fuer einen aus dem Replay uebergebenen Frame.

    Fehler vorher: Die Kacheln lasen immer aus dem Live-Ringbuffer; der Replay-Frame
    lag nur im A/B-Labor. Jetzt wird das Rohbild der Referenz A mit den AKTUELLEN
    Slidern verarbeitet (Slider wirken sofort), A/B unten zeigt die damaligen Parameter.
    """
    if not isinstance(frozen, dict):
        return None
    raw, frame_id, reference, _processor = ab.reference()
    if raw is None or str(frame_id) != str(frozen.get('frame_id')):
        return None
    params = getattr(application.sensor, 'processing_parameters', None) or reference
    key = f'replay:{frame_id}:{params!r}:{inspected}'
    label = f"REPLAY · Frame {frame_id} · {frozen.get('source') or ''}"
    info = dbc.Alert('Replay-Frame: Stufen = dieser Frame mit den aktuellen Slidern · '
                     'A/B unten = damalige Parameter · „Freeze / Live“ zurück zum Livebild.',
                     color='info', className='mb-0 py-2')
    if key == rendered:
        return (*([no_update] * (2 * len(STAGES) + 2)), label, info, no_update)
    try:
        result = CopilotLaneProcessor().process(raw, params)
    except (ValueError, TypeError, IndexError, cv2.error) as exc:
        return (*([None] * len(STAGES)), *([''] * len(STAGES)), None,
                f'Replay-Frame nicht verarbeitbar: {exc}', label, info, None)
    enabled = params[7] if len(params) > 7 else {}
    crop = tuple(params[3]) if enabled.get('roi', True) else (0.0, 1.0)
    view = SimpleNamespace(raw_image=raw, frame_id=frame_id, crop=crop, lanes=result.lanes,
                           process_image=result.processed_image)
    stages = dict(result.stages)
    stages['raw'] = raw
    stages['overlay'] = render_calibration_overlay(view)
    encoded = {name: uri(stages.get(name)) for name, _ in STAGES}
    infos = [('Replay · verfügbar' if encoded[name] else 'Replay · Schritt deaktiviert') for name, _ in STAGES]
    title = dict(STAGES).get(inspected, inspected)
    return (*[encoded[name] for name, _ in STAGES], *infos, encoded.get(inspected), title,
            label, info, key)

'''


def patch_dashboard(t, f):
    t = insert(t, 'import logging', 'import os  # PAKET-D', f)
    t = insert(t, 'from pipeline_ab import PipelineAB', 'from pipeline_processors import CopilotLaneProcessor', f)
    t = insert(t, "dcc.Store(id='pipeline-frozen'",
               "dcc.Store(id='replay-studio-token', data=None, storage_type='memory'),", f)
    t = replace_block(t, "@app.callback(Output('pipeline-frozen', 'data'), Input('pipeline-freeze', 'n_clicks'),",
                      "@app.callback(Output('inspected-stage', 'data'),", FREEZE, f)
    t = insert(t, "@app.callback(*[Output(f'stage-{key}', 'src') for key, _ in STAGES],",
               REPLAY_VIEW, f, after=False)
    t = insert(t, 'if camera is None or camera.frame_id < 0:', '''
replay_view = replay_pipeline_view(frozen, inspected, rendered, lock)  # PAKET-D
if replay_view is not None:
    return replay_view''', f, after=False)
    t = sub_once(t, "Output('ab-source', 'children'),\n                  Input('replay-to-studio', 'n_clicks'),",
                 "Output('ab-source', 'children'), Output('replay-studio-token', 'data'),\n"
                 "                  Input('replay-to-studio', 'n_clicks'),", f)
    t = sub_once(t, "return no_update, f'Übergabe nicht möglich: {exc}', no_update",
                 "return no_update, f'Übergabe nicht möglich: {exc}', no_update, no_update", f)
    t = sub_once(t, "'„A/B berechnen“ vergleicht mit den aktuellen Slidern (B).')",
                 "'„A/B berechnen“ vergleicht mit den aktuellen Slidern (B).',\n"
                 "                {'frame_id': frame_id, 'source': source, 'nonce': time.time()})", f)
    t = insert(t, "logging.getLogger('werkzeug').setLevel(logging.WARNING)", '''
# PAKET-D: Fahrbefehls-Meldungen ("Changed speed ...") nur mit C2C_LOG=INFO oder DEBUG
_level = getattr(logging, os.environ.get('C2C_LOG', 'WARNING').upper(), logging.WARNING)
for _name in ('BaseCar', 'SonicCar', 'SensorCar', 'CameraCar', 'picamera2'):
    logging.getLogger(_name).setLevel(_level)''', f)
    return t


def patch_test_paket_c(t, f):
    return sub_once(t, "tab, message, source = self.callbacks['replay_to_studio']",
                    "tab, message, source, _token = self.callbacks['replay_to_studio']", f)


CHANGELOG = '''
## 6.2 – Paket D (PAKET-D)

* **Replay → Pipeline Studio:** Alle acht Stufen zeigen jetzt den übergebenen Replay-Frame
  (vorher weiter Live/Freeze). Die Stufen nutzen die aktuellen Slider, A/B die damaligen
  Parameter. „Freeze / Live“ kehrt zum Livebild zurück.
* **Konsole:** `Changed speed/steering_angle` und picamera2-Statusmeldungen nur noch mit
  `C2C_LOG=INFO python Camp2CodeDashboard.py`.
'''

PATCHES = {
    'pipeline_ab.py': patch_pipeline_ab,
    'Camp2CodeDashboard.py': patch_dashboard,
    'tests/test_paket_c.py': patch_test_paket_c,
    'CHANGELOG.md': lambda t, f: insert(t, '# Changelog', CHANGELOG, f),
}


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    results = {}
    try:
        for name, func in PATCHES.items():
            path = root / name
            text = path.read_text(encoding='utf-8')
            if MARKER in text:
                print(f'  = {name} (bereits gepatcht)')
                continue
            new = func(text, name)
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
