#!/usr/bin/env python3
"""Paket C: gezielte, verankerte Aenderungen an bestehenden Dateien.

Jede Aenderung sucht eine eindeutige Ankerzeile. Fehlt ein Anker oder ist er
mehrdeutig, bricht das Skript ab, ohne irgendeine Datei zu schreiben.
Bereits gepatchte Dateien (Marker PAKET-C) werden uebersprungen.
"""
import sys
from pathlib import Path

MARKER = 'PAKET-C'


class PatchError(Exception):
    pass


def _unique_line(lines, needle, file, start=0):
    hits = [i for i in range(start, len(lines)) if needle in lines[i]]
    if len(hits) != 1:
        raise PatchError(f'{file}: Anker {needle!r} {len(hits)}x gefunden (erwartet 1)')
    return hits[0]


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def sub_once(text, old, new, file):
    count = text.count(old)
    if count != 1:
        raise PatchError(f'{file}: Text {old!r} {count}x gefunden (erwartet 1)')
    return text.replace(old, new)


def insert(text, needle, block, file, after=True, extra_indent=''):
    lines = text.split('\n')
    i = _unique_line(lines, needle, file)
    ind = _indent(lines[i]) + extra_indent
    new = [(ind + l) if l.strip() else '' for l in block.strip('\n').split('\n')]
    pos = i + 1 if after else i
    return '\n'.join(lines[:pos] + new + lines[pos:])


def replace_block(text, start_needle, end_needle, block, file):
    lines = text.split('\n')
    s = _unique_line(lines, start_needle, file)
    e = _unique_line(lines, end_needle, file, start=s + 1)
    return '\n'.join(lines[:s] + block.strip('\n').split('\n') + [''] + lines[e:])


# --------------------------------------------------------------------------- car_application.py
def patch_car_application(t, f):
    t = sub_once(t, 'from time import monotonic\n', 'from time import monotonic, monotonic_ns\n', f)
    t = insert(t, 'self.health = DrivingHealthMonitor()',
               '# PAKET-C: Startzeit des Fahrmodus (monoton) fuer den Recorder\nself._mode_started_ns = None',
               f, after=False)
    t = sub_once(t, 'is_driving=self.running)', 'is_driving=self.running, driving_since=self._driving_since)', f)
    t = insert(t, 'self.modes[key].start()', 'self._mode_started_ns = monotonic_ns()', f, after=False)
    t = insert(t, 'def stop_modes(self):', '''
def _driving_since(self):
    """PAKET-C: Startzeit des laufenden Fahrmodus (monoton) oder None."""
    return self._mode_started_ns if self.running() else None

''', f, after=False)
    return t


# --------------------------------------------------------------------------- session_recorder.py
def patch_session_recorder(t, f):
    t = sub_once(t, 'is_driving=None, commit_grace_s=0.25, jpeg_quality=95):',
                 'is_driving=None, commit_grace_s=0.25, jpeg_quality=95, driving_since=None):', f)
    t = insert(t, 'self.is_driving = is_driving',
               '# PAKET-C: Frames vor dem Fahrmodusstart erwarten keine Fahrentscheidung\n'
               'self.driving_since = driving_since', f)
    t = insert(t, 'def _ready(self, record, now_ns, active, final):', '''
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

''', f, after=False)
    t = insert(t, 'active = self._driving_active()', 'since = self._driving_since_ns()', f)
    t = sub_once(t, 'self._ready(record, now_ns, active, final)',
                 'self._ready(record, now_ns, self._frame_active(cam, active, since), final)', f)
    t = sub_once(t, 'self._row(cam, drive, active, events_for_frame, sensors, now_ns)',
                 'self._row(cam, drive, self._frame_active(cam, active, since), events_for_frame, sensors, now_ns)', f)
    return t


# --------------------------------------------------------------------------- Camp2CodeDashboard.py
REPLAY_BLOCK = r'''
    # ------------------------------------------------ Replay: Bild, Diagramm, Studio (PAKET-C)
    def replay_record_at(name, bounds, index):
        """Datensaetze des Bereichs und der Datensatz an der Slider-Position."""
        records, events, path = replay_records(name, bounds)
        if not records:
            return records, events, path, None
        position = max(0, min(int(index or 0), len(records) - 1))
        return records, events, path, records[position]

    def triggered_props():
        """Alle ausloesenden Komponenten (ctx.triggered_id liefert nur die erste)."""
        return {str(t.get('prop_id', '')).split('.')[0] for t in (ctx.triggered or [])}

    @app.callback(Output('replay-image', 'src'), Output('replay-frame-label', 'children'),
                  Output('replay-details', 'children'),
                  Input('replay-index', 'value'), Input('session-choice', 'value'),
                  Input('replay-bounds', 'data'), Input('tick-replay', 'n_intervals'),
                  State('main-tabs', 'active_tab'))
    def replay_render(index, name, bounds, _, tab):
        """Nur das HUD-Bild.

        Fehler vorher: Beim Abspielen loesen Takt und (ueber seek) der Index im selben
        Zyklus aus. ctx.triggered_id meldete nur den Takt, daher wurde das Bild einer
        gespeicherten Session verworfen: Slider lief, Bild stand.
        """
        if tab != 'replay':
            return (no_update,) * 3
        live = name in (None, LIVE)
        if not live and triggered_props() == {'tick-replay'}:
            return (no_update,) * 3
        with measure(application, 'dashboard.replay'):
            _, _, path, record = replay_record_at(name, bounds, index)
            if record is None:
                return None, 'Keine Frames', 'Kamera starten oder Session auswählen'
            if isinstance(record, dict):
                try:
                    image = load_session_image(path, record)
                except ValueError as exc:
                    return None, str(exc), f"Frame {record.get('frame_id')}"
                details = (f"Status {record.get('data_status')} · Stellwert {record.get('steering_angle_car') or '–'} · "
                           f"Soll {record.get('requested_angle') or '–'} · Roh {record.get('steering_angle') or '–'} · "
                           f"Speed {record.get('speed') or '–'} · Linien {record.get('lane_count') or '–'} · "
                           f"Ereignisse {record.get('event_codes') or '–'}")
                return uri(draw_hud(image, record), 80), f"Frame #{record.get('frame_id')}", details
            image = (render_overlay(record.camera, record.driving) if record.driving
                     else render_calibration_overlay(record.camera))
            info = (f'Frame {record.camera.frame_id} · '
                    + (f'Stellwert {fmt(record.driving.steering_angle_car)} · Roh {fmt(record.driving.steering_angle)} · '
                       f'Speed {fmt(record.driving.speed)}' if record.driving else 'Keine zugeordnete Fahrentscheidung'))
            return uri(image, 80), f'Frame #{record.camera.frame_id}', info

    @app.callback(Output('replay-chart', 'figure'),
                  Input('session-choice', 'value'), Input('replay-bounds', 'data'),
                  Input('replay-index', 'value'),
                  State('main-tabs', 'active_tab'))
    def replay_chart(name, bounds, index, tab):
        """Diagramm nur bei Session-/Bereichswechsel neu; sonst wandert nur der Cursor."""
        if tab != 'replay':
            return no_update
        props = triggered_props()
        live = name in (None, LIVE)
        structural = live or not props or bool(props & {'session-choice', 'replay-bounds', ''})
        if not structural and 'replay-index' not in props:
            return no_update
        records, events, _path, record = replay_record_at(name, bounds, index)
        cursor = record_frame_id(record) if record is not None else None
        if not structural and Patch is not None and cursor is not None:
            patch = Patch()
            patch['data'][2]['x'] = [cursor, cursor]
            return patch
        with measure(application, 'dashboard.replay_chart'):
            chart = go.Figure()
            dark_figure(chart, 240, xaxis_title='Frame-ID', yaxis_title='Winkel',
                        yaxis2=dict(overlaying='y', side='right', range=[0, 3], title='Linien'),
                        uirevision=f'{name}|{bounds}')
            if not records:
                return chart
            ids = [record_frame_id(r) for r in records]
            if isinstance(records[0], dict):
                def number(row, key):
                    try:
                        return float(row.get(key) or 'nan')
                    except ValueError:
                        return None
                angles = [number(r, 'steering_angle_car') for r in records]
                lanes = [number(r, 'lane_count') for r in records]
            else:
                angles = [r.driving.steering_angle_car if r.driving else None for r in records]
                lanes = [sum(lane is not None for lane in (r.camera.lanes or ())) for r in records]
            chart.add_scatter(x=ids, y=angles, customdata=ids, mode='lines+markers', name='Stellwert',
                              marker={'size': 4})
            chart.add_scatter(x=ids, y=lanes, customdata=ids, mode='lines', name='Linienanzahl',
                              line={'shape': 'hv'}, yaxis='y2')
            # Trace 2 = Cursor; wird beim Blaettern per Patch verschoben.
            chart.add_scatter(x=[cursor, cursor], y=[45, 135], mode='lines', name='Aktueller Frame',
                              line={'color': '#4da3ff', 'width': 2}, hoverinfo='skip')
            valid = [i for i in ids if i is not None]
            lo, hi = (min(valid), max(valid)) if valid else (0, -1)
            for event in events:
                try:
                    fid = int(event.get('frame_id'))
                except (TypeError, ValueError):
                    continue
                if lo <= fid <= hi and event.get('code') not in ('LANES_DETECTED', 'STEERING_COMPUTED'):
                    chart.add_vline(x=fid, line_width=1, line_dash='dot', line_color='#f0ad4e')
            return chart

    @app.callback(Output('main-tabs', 'active_tab'), Output('replay-studio-msg', 'children'),
                  Output('ab-source', 'children'),
                  Input('replay-to-studio', 'n_clicks'),
                  State('session-choice', 'value'), State('replay-bounds', 'data'),
                  State('replay-index', 'value'), prevent_initial_call=True)
    def replay_to_studio(_, name, bounds, index):
        """Uebergibt Rohbild + damalige Parameter als Referenz A an das A/B-Labor."""
        try:
            _, _, path, record = replay_record_at(name, bounds, index)
            if record is None:
                raise ValueError('Kein Frame ausgewählt')
            if isinstance(record, dict):
                image = load_session_image(path, record)
                params = parameters_from_record(record.get('processing_parameters_json'))
                processor = processor_key(record.get('processor_name'))
                frame_id = record.get('frame_id')
            else:
                image = record.camera.raw_image
                params = parameters_from_record(record.camera.processing_parameters)
                processor = processor_key(record.camera.processor_name)
                frame_id = record.camera.frame_id
            ab.freeze(SimpleNamespace(raw_image=image, frame_id=frame_id), params, processor)
        except (ValueError, TypeError, OSError, cv2.error) as exc:
            return no_update, f'Übergabe nicht möglich: {exc}', no_update
        source = 'Live-Ringbuffer' if name in (None, LIVE) else name
        return ('pipeline', f'Frame {frame_id} an das Pipeline Studio übergeben',
                f'Referenz A = Frame {frame_id} aus {source} mit den damaligen Parametern. '
                '„A/B berechnen“ vergleicht mit den aktuellen Slidern (B).')
'''


def patch_dashboard(t, f):
    t = insert(t, 'import contextlib', 'from types import SimpleNamespace', f)
    t = insert(t, 'from dash import Dash, Input, Output, State, ctx, dcc, html, no_update', '''
try:  # PAKET-C: Teilaktualisierung des Diagramms (Dash >= 2.9)
    from dash import Patch
except ImportError:  # Dash-Attrappe in den Tests
    Patch = None''', f)
    t = insert(t, 'from pipeline_ab import PipelineAB',
               'from replay_studio import load_session_image, parameters_from_record, processor_key\n'
               'from telemetry.thread_stats import ThreadCpuSampler', f)
    t = insert(t, 'ab = PipelineAB()', 'thread_cpu = ThreadCpuSampler()  # PAKET-C', f)
    t = insert(t, "lines.append(f'Ringbuffer: ", 'lines.extend(thread_cpu.lines())', f, after=False)
    t = sub_once(t, "html.Div(id='ab-status', className='small mt-2'),",
                 "html.Div(id='ab-status', className='small mt-2'),\n"
                 "            html.Div(id='ab-source', className='small text-info'),", f)
    t = sub_once(t, "dcc.Download(id='replay-download')]",
                 "dcc.Download(id='replay-download'),\n"
                 "                html.Hr(className='my-2'),\n"
                 "                dbc.Button('Frame im Pipeline Studio analysieren', id='replay-to-studio',\n"
                 "                           color='info', size='sm'),\n"
                 "                html.Div(id='replay-studio-msg', className='small mt-1')]", f)
    t = replace_block(t, "@app.callback(Output('replay-image', 'src')",
                      "@app.callback(Output('replay-download', 'data')", REPLAY_BLOCK, f)
    return t


# --------------------------------------------------------------------------- Tests / Doku
def patch_test_callbacks(t, f):
    t = sub_once(t, "'replay_render', 'export_range'}",
                 "'replay_render', 'export_range', 'replay_chart', 'replay_to_studio'}  # PAKET-C", f)
    return sub_once(t, "image, label, details, chart = self.callbacks['replay_render']",
                    "image, label, details = self.callbacks['replay_render']", f)


def patch_test_real_dash(t, f):
    return sub_once(t, 'self.assertEqual(len(deps), 21)', 'self.assertEqual(len(deps), 23)  # PAKET-C', f)


CHANGELOG = '''
## 6.1 – Paket C (PAKET-C)

* **Replay-Bugfix:** Beim Abspielen gespeicherter Sessions lief der Slider, das Bild stand.
  Ursache: Takt und Index loesen im selben Zyklus aus; `ctx.triggered_id` meldete nur den Takt.
  Bild und Diagramm sind getrennt; das Diagramm wird nur bei Session-/Bereichswechsel neu
  gebaut, beim Blaettern wandert nur der Cursor (`Patch`).
* **missing_decision korrigiert:** Ringbuffer-Vorlauf vor dem Fahrmodusstart wird als
  `no_driving_state` gespeichert (`driving_since`). Session 15:07: alle 51 „fehlenden“
  Entscheidungen waren Frames 96–146 vor `CONTROLLER_STARTED` (Frame 147).
* **Replay → Pipeline Studio:** Rohbild + damalige Parameter als Referenz A; B = aktuelle Slider.
* **Thread-CPU** je Thread in der Telemetrie (Messgrundlage fuer Threading-Entscheidungen).
* `requirements-c2c.txt` mit den direkten Abhaengigkeiten.
'''

PATCHES = {
    'car_application.py': patch_car_application,
    'session_recorder.py': patch_session_recorder,
    'Camp2CodeDashboard.py': patch_dashboard,
    'tests/test_dashboard_callbacks.py': patch_test_callbacks,
    'tests/test_dashboard_real_dash.py': patch_test_real_dash,
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
