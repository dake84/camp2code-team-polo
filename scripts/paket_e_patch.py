#!/usr/bin/env python3
"""Paket E: Telemetrie-Performance, Regler 100 Hz, Recording -> Replay.

Setzt Paket D (5613475) voraus. Verankerte Aenderungen; fehlt ein Anker,
wird KEINE Datei geschrieben. Dateien mit Marker PAKET-E werden uebersprungen.
"""
import sys
from pathlib import Path

MARKER = 'PAKET-E'


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
        raise PatchError(f'{file}: Text {old[:70]!r} {n}x gefunden (erwartet 1)')
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
    # Startzeile ist die @app.callback-Zeile direkt davor, falls der Anker in der Folgezeile steht
    if not lines[s].lstrip().startswith('@app.callback') and lines[s - 1].lstrip().startswith('@app.callback'):
        s -= 1
    return '\n'.join(lines[:s] + block.strip('\n').split('\n') + [''] + lines[e:])


HELPERS = r'''
# PAKET-E: Diagramme als einfache dicts. go.Figure validiert jeden Trace in Python;
# auf dem Pi kostete das pro Takt viel CPU und bremste (GIL) den Kamerathread.
CHART_EVERY = 3          # Telemetrie-Diagramme nur jeden 3. Takt (ca. 2 s)
CHART_WINDOW_S = 60      # Zeitfenster der Verlaeufe
AXIS = dict(gridcolor='#2a3550', zerolinecolor='#3a4766', color='#c8d3e6')


def axis(title=None, **kwargs):
    result = dict(AXIS, **kwargs)
    if title:
        result['title'] = {'text': title}
    return result


def plain_figure(traces, height=260, **layout):
    base = dict(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#111a2b', height=height,
                font={'color': '#e9f1ff'}, margin=dict(l=45, r=20, t=25, b=40),
                legend=dict(orientation='h', y=1.12, x=0), xaxis=axis(), yaxis=axis())
    base.update(layout)
    return {'data': list(traces), 'layout': base}


def thin(rows, limit=240):
    step = max(1, -(-len(rows) // limit))
    return list(rows)[::step]

'''

TELEMETRY = r'''
    @app.callback(Output('freq-bars', 'figure'), Output('freq-history', 'figure'),
                  Output('drive-history', 'figure'), Output('data-age', 'children'),
                  Output('system-stats', 'children'), Output('telemetry-preview', 'src'),
                  Input('tick-telemetry', 'n_intervals'), Input('main-tabs', 'active_tab'))
    def telemetry_view(tick, tab):
        """PAKET-E: Diagramme als dict, nur jeden CHART_EVERY-ten Takt, Fenster 60 s."""
        if tab != 'telemetry':
            return (no_update,) * 6
        with measure(application, 'dashboard.telemetry'):
            camera = car.camera_data
            bars = fplot = dplot = no_update
            if ctx.triggered_id == 'main-tabs' or (tick or 0) % CHART_EVERY == 1:
                rows, freqs, _events = application.sample()
                metrics = car.thread_metrics
                running = application.running()
                camera_target = float(application.sensor.scan_frequency)
                actual = [metrics[key].hz if metrics[key].age_seconds is not None and metrics[key].age_seconds < 2 else 0
                          for key in ('camera', 'controller')]
                targets = [camera_target, camera_target if running else 0.0]
                names = ['Kamera', 'Entscheidungen']
                bars = plain_figure([dict(type='bar', name='Soll', x=names, y=targets, marker={'color': '#6c7a92'}),
                                     dict(type='bar', name='Ist', x=names, y=actual, marker={'color': '#2fbf8f'})],
                                    240, barmode='group', yaxis=axis('Hz'), uirevision='bars')
                ftraces = []
                if freqs:
                    end = freqs[-1][0]
                    recent = thin([r for r in freqs if end - r[0] <= CHART_WINDOW_S])
                    for i, name in ((1, 'Kamera'), (2, 'Entscheidungen')):
                        ftraces.append(dict(type='scatter', mode='lines', name=name,
                                            x=[round(r[0] - end, 2) for r in recent], y=[r[i] for r in recent]))
                fplot = plain_figure(ftraces, 240, xaxis=axis('Sekunden'), yaxis=axis('Hz'), uirevision='freq')
                dtraces = []
                if rows:
                    end = time.monotonic()
                    recent = thin([r for r in rows if end - r[0] <= CHART_WINDOW_S])
                    x = [round(r[0] - end, 2) for r in recent]
                    dtraces = [
                        dict(type='scatter', mode='lines', name='Rohwinkel', x=x, y=[r[2] for r in recent]),
                        dict(type='scatter', mode='lines', name='Stellwert', x=x,
                             y=[r[6] if len(r) > 6 else None for r in recent]),
                        dict(type='scatter', mode='lines', name='Speed-Steuerwert', x=x, y=[r[3] for r in recent]),
                        dict(type='scatter', mode='lines', name='Linienanzahl', x=x, line={'shape': 'hv'}, yaxis='y2',
                             y=[r[5] if len(r) > 5 else None for r in recent]),
                    ]
                dplot = plain_figure(dtraces, 300, xaxis=axis('Sekunden', range=[-CHART_WINDOW_S, 0]),
                                     yaxis=axis('Grad / Steuerwert'),
                                     yaxis2=axis('Linien', overlaying='y', side='right', range=[0, 3]),
                                     uirevision='drive')
            lines = []
            if camera is not None and camera.monotonic_ns:
                lines.append(f'Kameradatenalter (seit Aufnahme): {(time.monotonic_ns() - camera.monotonic_ns) / 1e6:.0f} ms')
                if getattr(camera, 'processing_ns', 0):
                    lines.append(f'Bildverarbeitung: {camera.processing_ns / 1e6:.1f} ms')
            record = car.frame_history.latest_complete()
            if record is not None and record.driving.monotonic_ns and record.camera.monotonic_ns:
                lines.append(f'Entscheidungslatenz (Aufnahme → Fahrbefehl): '
                             f'{(record.driving.monotonic_ns - record.camera.monotonic_ns) / 1e6:.1f} ms '
                             f'(Frame {record.camera.frame_id})')
            history = car.frame_history
            lines.extend(thread_cpu.lines())
            lines.append(f'Ringbuffer: {len(history.snapshot())}/{getattr(history, "maxlen", "?")} Frames · '
                         f'Regler-Abtastung {application.controller_poll_hz:.0f} Hz (nur neue Frames werden ausgewertet)')
            timings = getattr(application, 'timings', None)
            if timings is not None:
                for name, values in sorted(timings.summary().items()):
                    lines.append(f'{name}: Ø {values["mean_ms"]:.1f} ms · max {values["max_ms"]:.1f} ms')
            preview = None
            if camera is not None and camera.frame_id >= 0:
                image = render_calibration_overlay(camera)
                if image is not None:
                    h, w = image.shape[:2]
                    scale = min(1.0, 320 / max(1, w))
                    preview = uri(cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale)))), 60)
            return (bars, fplot, dplot, [html.Div(line) for line in lines] or 'Keine Daten verfügbar',
                    system_stats(), preview)

'''


def patch_dashboard(t, f):
    t = insert(t, 'import os  # PAKET-D', 'from pathlib import Path  # PAKET-E', f)
    t = insert(t, 'def card(title, content, **kwargs):', HELPERS, f, after=False)
    t = sub_once(t, "html.Div(id='session-stats', className='small text-secondary mt-2')]), md=8),",
                 "html.Div(id='session-stats', className='small text-secondary mt-2'),\n"
                 "            dbc.Button('Letzte Session im Replay öffnen', id='record-open-replay', color='info',\n"
                 "                       size='sm', className='mt-2')]), md=8),", f)
    t = replace_block(t, "Output('freq-bars', 'figure')",
                      '# ------------------------------------------------ Recording & Events', TELEMETRY, f)
    # Recording -> Replay im bestehenden Callback (main-tabs behaelt genau einen Besitzer)
    t = sub_once(t, "Output('ab-source', 'children'), Output('replay-studio-token', 'data'),\n"
                    "                  Input('replay-to-studio', 'n_clicks'),",
                 "Output('ab-source', 'children'), Output('replay-studio-token', 'data'),\n"
                 "                  Output('session-choice', 'value'),\n"
                 "                  Input('replay-to-studio', 'n_clicks'), Input('record-open-replay', 'n_clicks'),", f)
    t = sub_once(t, 'def replay_to_studio(_, name, bounds, index):',
                 'def replay_to_studio(_, _open, name, bounds, index):', f)
    lines = t.split('\n')
    i = _unique_line(lines, 'def replay_to_studio(_, _open, name, bounds, index):', f)
    j = i + 1
    if lines[j].strip().startswith('"""'):          # Docstring ueberspringen
        while not (lines[j].rstrip().endswith('"""') and (j > i + 1 or lines[j].strip().count('"""') == 2)):
            j += 1
    body = ["        if ctx.triggered_id == 'record-open-replay':  # PAKET-E: letzte Session im Replay",
            "            path = getattr(application.recorder, 'path', None)",
            "            if not path:",
            "                return (no_update,) * 5",
            "            return 'replay', no_update, no_update, no_update, Path(str(path)).name"]
    t = '\n'.join(lines[:j + 1] + body + lines[j + 1:])
    t = sub_once(t, "return no_update, f'Übergabe nicht möglich: {exc}', no_update, no_update",
                 "return no_update, f'Übergabe nicht möglich: {exc}', no_update, no_update, no_update", f)
    t = sub_once(t, "{'frame_id': frame_id, 'source': source, 'nonce': time.time()})",
                 "{'frame_id': frame_id, 'source': source, 'nonce': time.time()}, no_update)", f)
    return t


def patch_camera_mode(t, f):
    t = insert(t, 'SPEED_CONTROL_VALUE = 30', '''
# PAKET-E: Regler-Abtastung, falls cameracar_controller.scan_frequency fehlt (vorher 500 Hz)
DEFAULT_POLL_HZ = 100''', f)
    return insert(t, 'super().__init__(name, car, logger, cfg)', '''
if cfg.get_int('scan_frequency', 0) <= 0:  # PAKET-E
    self._frequency = self.DEFAULT_POLL_HZ''', f)


def patch_test_c(t, f):
    return sub_once(t, "tab, message, source, _token = self.callbacks['replay_to_studio'](1, '__live__', None, 1)",
                    "tab, message, source, _token, _s = self.callbacks['replay_to_studio'](1, None, '__live__', None, 1)  # PAKET-E", f)


def patch_test_d(t, f):
    lines = t.split('\n')
    hits = [k for k, l in enumerate(lines) if "['replay_to_studio'](" in l]
    if len(hits) != 1:
        raise PatchError(f'{f}: replay_to_studio-Aufruf {len(hits)}x gefunden (erwartet 1)')
    line = lines[hits[0]]
    lhs, rhs = line.split('=', 1)
    call = rhs.split("['replay_to_studio'](", 1)
    lines[hits[0]] = (lhs.rstrip() + ', _s = ' + call[0].strip() + "['replay_to_studio'](1, None, "
                      + call[1].split(',', 1)[1].strip() + '  # PAKET-E')
    return '\n'.join(lines)


CHANGELOG = '''
## 6.3 – Paket E (PAKET-E)

* **Telemetrie-Lag:** Diagramme als dicts statt go.Figure, nur jeden 3. Takt, Fenster 60 s,
  max. 240 Punkte je Linie (vorher Lenkwinkel-Achse bis -400 s).
* **Regler:** 100 Hz Abtastung, falls cameracar_controller.scan_frequency fehlt (vorher 500 Hz).
* **Recording → Replay:** Button „Letzte Session im Replay öffnen“.
'''

PATCHES = {
    'Camp2CodeDashboard.py': patch_dashboard,
    'driving/camera_mode.py': patch_camera_mode,
    'tests/test_paket_c.py': patch_test_c,
    'tests/test_paket_d.py': patch_test_d,
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
