#!/usr/bin/env python3
"""Paket H2: fuenf Reiter, Ereignisprotokoll in Cockpit und Replay Lab, Auslastung, gekoppeltes Telemetrie-Diagramm,
Speicherplatz, Frame aus dem Cockpit ins Pipeline Studio.

Setzt Paket H1b (PAKET-H1B) voraus. Verankerte Aenderungen; fehlt ein Anker oder ist er mehrdeutig, wird KEINE
Datei geaendert oder geloescht (alles oder nichts). Dateien mit dem Marker PAKET-H2 werden uebersprungen
(idempotent). Geloescht wird ``dashboard/tabs/recording.py`` (der Reiter "Recording & Events" entfaellt).

Aufruf: python scripts/paket_h2_patch.py [REPO_ROOT]
"""
import sys
from pathlib import Path

MARKER = 'PAKET-H2'
DELETE = ('dashboard/tabs/recording.py',)


class PatchError(Exception):
    pass


def _hits(lines, needle, start=0):
    return [i for i in range(start, len(lines)) if needle in lines[i]]


def _one(lines, needle, file, start=0):
    hits = _hits(lines, needle, start)
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


def insert(text, needle, block, file, after=True, indent=None, blank_after=None, blank_before=0):
    """Fuegt ``block`` vor/nach der eindeutigen Zeile mit ``needle`` ein (Einrueckung der Ankerzeile)."""
    lines = text.split('\n')
    i = _one(lines, needle, file)
    ind = _indent(lines[i]) if indent is None else indent
    body = block.strip('\n').split('\n')
    new = [(ind + line) if line.strip() else '' for line in body]
    if blank_after is None:
        blank_after = 1 if (not after and len(body) > 1) else 0
    new = [''] * blank_before + new + [''] * blank_after
    pos = i + 1 if after else i
    return '\n'.join(lines[:pos] + new + lines[pos:])


def replace_block(text, start_needle, end_needle, block, file, gap=1):
    """Ersetzt die Zeilen ab ``start_needle`` (inklusive) bis vor ``end_needle`` (exklusive)."""
    lines = text.split('\n')
    s = _one(lines, start_needle, file)
    e = _one(lines, end_needle, file, start=s + 1)
    return '\n'.join(lines[:s] + block.strip('\n').split('\n') + [''] * gap + lines[e:])


def replace_to_end(text, start_needle, block, file, tail_check):
    lines = text.split('\n')
    s = _one(lines, start_needle, file)
    if tail_check not in '\n'.join(lines[s:]):
        raise PatchError(f'{file}: Dateiende nach {start_needle!r} entspricht nicht der Erwartung ({tail_check!r})')
    return '\n'.join(lines[:s]) + '\n' + block.strip('\n') + '\n'


def append(text, block, indent=''):
    body = [(indent + line) if line.strip() else '' for line in block.strip('\n').split('\n')]
    return text.rstrip('\n') + '\n\n' + '\n'.join(body) + '\n'


def indented(block, prefix):
    return '\n'.join((prefix + line) if line.strip() else '' for line in block.strip('\n').split('\n'))


# ------------------------------------------------------------------ dashboard/tabs/__init__.py
def patch_tabs_init(t, f):
    t = sub_once(t, 'from .recording import RecordingTab\n', '', f)
    t = sub_once(t, 'DEFAULT_TABS = (DrivingTab, PipelineTab, TelemetryTab, RecordingTab, ReplayTab, SettingsTab)',
                 '# PAKET-H2: der Reiter "Recording & Events" entfaellt (Ereignisprotokoll in Cockpit und Replay Lab)\n'
                 'DEFAULT_TABS = (DrivingTab, PipelineTab, TelemetryTab, ReplayTab, SettingsTab)', f)
    return sub_once(t, "'RecordingTab', 'ReplayTab', 'SettingsTab']", "'ReplayTab', 'SettingsTab']", f)


# ------------------------------------------------------------------ dashboard/shell.py
def patch_shell(t, f):
    t = sub_once(t, 'from .helpers import MODES, lane_quality\n',
                 'import event_log  # PAKET-H2\nimport system_stats\nfrom help_texts import tip\n'
                 'from .helpers import MODES, lane_quality\n', f)
    t = sub_once(t, "html.Span(id='global-frame', className='ms-1 small text-secondary')],",
                 "html.Span(id='global-frame', className='ms-1 small text-secondary'),\n"
                 "                       dbc.Tooltip(id='global-rec-tooltip', target='global-rec-state',\n"
                 "                                   placement='bottom')],", f)
    t = sub_once(t, "dbc.Col([c.help_label('Spurqualität', 'qual'), html.Div(id='kpi-qual', className='c2c-kpi-small')])],\n",
                 "dbc.Col([c.help_label('Spurqualität', 'qual'), html.Div(id='kpi-qual', className='c2c-kpi-small')]),\n"
                 "                       dbc.Col([c.help_label('Auslastung', 'cpu', suffix='kpi'),\n"
                 "                                html.Div(id='kpi-load', className='c2c-kpi-small')])],\n", f)
    t = sub_once(t, "Output('kpi-qual', 'children'),\n                      Input('refresh', 'n_intervals'))",
                 "Output('kpi-qual', 'children'),\n                      Output('kpi-load', 'children'), Input('refresh', 'n_intervals'))", f)
    t = sub_once(t, '            return rate_view, lat_view, qual_view\n', '''            # PAKET-H2: Auslastung = CPU gross (Ampel), RAM und Temperatur klein; nur lesen, nie messen
            stats = application.system_stats() if hasattr(application, 'system_stats') else system_stats.empty()
            summary = system_stats.load_summary(stats)
            load_view = [kpi(summary['cpu'], summary['cpu_level']),
                         html.Span(f" RAM {summary['ram']}", className='small text-secondary ms-2', title=tip('ram')),
                         html.Span(f" · {summary['temp']}", className='small text-secondary', title=tip('temp'))]
            return rate_view, lat_view, qual_view, load_view
''', f)
    t = sub_once(t, "Output('global-action-message', 'children'),\n                      Input('refresh', 'n_intervals'), Input('last-action', 'data'))",
                 "Output('global-action-message', 'children'), Output('global-rec-tooltip', 'children'),\n"
                 "                      Input('refresh', 'n_intervals'), Input('last-action', 'data'))", f)
    return sub_once(t, '                    not recording, running,\n                    message)\n',
                    '                    not recording, running,\n'
                    '                    message, event_log.recording_summary(application.recorder))\n', f)


# ------------------------------------------------------------------ dashboard/tabs/driving.py
DRIVING_LAYOUT = '''
html.Hr(),
dbc.Row([
    dbc.Col([c.help_label('Auslastung (CPU)', 'cpu'), html.Div(id='drive-cpu', className='c2c-kpi')], width=6),
    dbc.Col([c.help_label('RAM', 'ram'), html.Div(id='drive-ram', className='c2c-kpi-small'),
             c.help_label('Temperatur', 'temp'), html.Div(id='drive-temp', className='c2c-kpi-small')], width=6),
], className='g-2'),
html.Hr(),
html.Div(id='drive-rec-line', className='small text-secondary mb-2'),
dbc.Button('Frame im Pipeline Studio öffnen', id='drive-to-studio', color='info', size='sm'),
html.Div(id='drive-studio-msg', className='small mt-1'),
'''

DRIVING_EVENTS_CARD = '''
dbc.Col(c.card(['Ereignisprotokoll (neueste zuerst)', *c.info('eventlog')], [
    dbc.RadioItems(id='drive-events-filter', options=event_log.FILTER_OPTIONS, value='important', inline=True,
                   className='mb-2'),
    html.Div(id='drive-events', className='c2c-events')]), lg=12),
'''

DRIVING_RETURN_PREFIX = '''
# PAKET-H2: Auslastung (nur lesen) und Aufnahmezustand
summary = system_stats.load_summary(application.system_stats() if hasattr(application, 'system_stats')
                                    else system_stats.empty())
'''

DRIVING_EVENTS_CALLBACK = '''
# PAKET-H2: Ereignisprotokoll im Cockpit (Frame-Nummer = Link ins Replay Lab)
@app.callback(Output('drive-events', 'children'),
              Input(self.tick_id, 'n_intervals'), Input('drive-events-filter', 'value'),
              Input('main-tabs', 'active_tab'))
def drive_events(_, mode, tab):
    if tab != self.tab_id:
        return no_update
    recorder = application.recorder
    path = getattr(recorder, 'path', None)
    ring_ids = {record.camera.frame_id for record in car.frame_history.snapshot()}
    entries = event_log.live_entries(application.health.events(), time.monotonic_ns(), mode,
                                     Path(str(path)).name if path else None, getattr(recorder, 'has_frame', None),
                                     ring_ids)
    return event_log.event_table(c.html, c.dcc, entries)
'''


def patch_driving(t, f):
    t = sub_once(t, 'import time\n', 'import time\nfrom pathlib import Path  # PAKET-H2\n\nimport event_log\nimport system_stats\n', f)
    t = insert(t, "html.Div(id='drive-diagnostic', className='small'),", DRIVING_LAYOUT, f)
    t = sub_once(t, "            ]), lg=4),\n        ], className='g-3')\n",
                 "            ]), lg=4),\n" + indented(DRIVING_EVENTS_CARD, '            ') + "\n        ], className='g-3')\n", f)
    t = sub_once(t, "Output('drive-age', 'children'),\n                      Input(self.tick_id, 'n_intervals')",
                 "Output('drive-age', 'children'),\n                      Output('drive-cpu', 'children'), Output('drive-ram', 'children'),\n"
                 "                      Output('drive-temp', 'children'), Output('drive-rec-line', 'children'),\n"
                 "                      Input(self.tick_id, 'n_intervals')", f)
    t = sub_once(t, 'return (no_update,) * 10', 'return (no_update,) * 14', f)
    t = insert(t, "return (image, key, fmt(car.steering_angle, 0, '°'), fmt(car.speed),", DRIVING_RETURN_PREFIX, f,
               after=False, blank_after=0)
    t = sub_once(t, "f'Frame #{shown.frame_id}' if shown is not None else '', age)",
                 "f'Frame #{shown.frame_id}' if shown is not None else '', age,\n"
                 "                        c.kpi(summary['cpu'], summary['cpu_level']), c.kpi(summary['ram'], summary['ram_level']),\n"
                 "                        c.kpi(summary['temp'], summary['temp_level']),\n"
                 "                        event_log.recording_summary(application.recorder))", f)
    return append(t, DRIVING_EVENTS_CALLBACK, '        ')


# ------------------------------------------------------------------ dashboard/tabs/telemetry.py
TELEMETRY_BODY = '''
    def layout(self):
        c = self.ctx
        dbc, html = c.dbc, c.html
        return dbc.Row([
            dbc.Col(c.card('Live · Mini-Preview', html.Img(id='telemetry-preview', style=IMAGE_STYLE)), md=4),
            # PAKET-H2: Speicherplatz der SD-Karte (Aktualisierung alle ca. 30 s im Sampler, nicht je Takt)
            dbc.Col(c.card('Speicherplatz (SD-Karte, Data_AI)', [
                c.help_label('Frei', 'disk'), html.Div(id='telemetry-disk-free', className='c2c-kpi-small'),
                c.help_label('Aufnahmedauer (Schätzung)', 'diskest'),
                html.Div(id='telemetry-disk-estimate', className='small')]), md=8),
            dbc.Col(c.card(['Telemetrie · letzte 60 s ', *c.info('telechart'), ' · Entscheidungen ',
                            *c.info('decisions')],
                           c.chart_block('telemetry-chart', telemetry_chart.HEIGHT, 'Noch keine Messwerte')), md=12),
            dbc.Col(c.card(['Datenalter · Latenzen · System', *c.info('dataage')],
                           html.Details([html.Summary('Anzeigen / ausblenden', className='text-info small'),
                                         html.Div(id='data-age', className='mt-2'),
                                         html.Div(id='system-stats', className='small text-secondary mt-2')])),
                    md=12),
        ], className='g-3')

    def register_callbacks(self, app):
        c = self.ctx
        Input, Output = c.Input, c.Output
        ctx, no_update, html = c.ctx, c.no_update, c.html
        application, car = c.application, c.car

        @app.callback(Output('telemetry-chart', 'figure'), Output('data-age', 'children'),
                      Output('system-stats', 'children'), Output('telemetry-disk-free', 'children'),
                      Output('telemetry-disk-estimate', 'children'), Output('telemetry-preview', 'src'),
                      Input(self.tick_id, 'n_intervals'), Input('main-tabs', 'active_tab'))
        def telemetry_view(tick, tab):
            """Ein gekoppeltes Diagramm (dict), nur jeden CHART_EVERY-ten Takt; Systemwerte nur lesen."""
            if tab != self.tab_id:
                return (no_update,) * 6
            with c.measure('dashboard.telemetry'):
                camera = car.camera_data
                chart = no_update
                if ctx.triggered_id == 'main-tabs' or (tick or 0) % CHART_EVERY == 1:
                    rows, freqs, _events = application.sample()
                    extra = getattr(application, 'sample_extra', None)
                    latency, system = extra() if extra is not None else ((), ())
                    figure = telemetry_chart.build_figure(time.monotonic(), float(application.sensor.scan_frequency),
                                                          freqs, latency, system, rows)
                    chart = figure if figure is not None else c.empty_figure('Noch keine Messwerte')
                lines = []
                if camera is not None and camera.monotonic_ns:
                    lines.append('Kameradatenalter (seit Aufnahme): '
                                 f'{(time.monotonic_ns() - camera.monotonic_ns) / 1e6:.0f} ms')
                    if getattr(camera, 'processing_ns', 0):
                        lines.append(f'Bildverarbeitung: {camera.processing_ns / 1e6:.1f} ms')
                record = car.frame_history.latest_complete()
                if record is not None and record.driving.monotonic_ns and record.camera.monotonic_ns:
                    lines.append(f'Entscheidungslatenz (Aufnahme → Fahrbefehl): '
                                 f'{(record.driving.monotonic_ns - record.camera.monotonic_ns) / 1e6:.1f} ms '
                                 f'(Frame {record.camera.frame_id})')
                history = car.frame_history
                lines.extend(c.thread_cpu.lines())
                lines.append(f'Ringbuffer: {len(history.snapshot())}/{getattr(history, "maxlen", "?")} Frames · '
                             f'Regler-Abtastung {application.controller_poll_hz:.0f} Hz '
                             '(nur neue Frames werden ausgewertet)')
                timings = getattr(application, 'timings', None)
                if timings is not None:
                    for name, values in sorted(timings.summary().items()):
                        lines.append(f'{name}: Ø {values["mean_ms"]:.1f} ms · max {values["max_ms"]:.1f} ms')
                preview = None
                if camera is not None and camera.frame_id >= 0:
                    preview = preview_uri(render_calibration_overlay(camera))
                stats = application.system_stats() if hasattr(application, 'system_stats') else system_stats.empty()
                disk = stats.get('disk')
                free = f"{disk['free_gb']:.1f} GB ({disk['free_pct']:.0f} %)" if disk else '–'
                estimate = disk.get('estimate') if disk else None
                estimate_text = (f"reicht für ca. {estimate['minutes']:.0f} min Aufnahme (Schätzung aus mittlerer "
                                 f"JPEG-Größe {estimate['mean_kb']:.0f} kB und {estimate['fps']:.0f} fps)"
                                 if estimate else '–' if not disk else 'nicht schätzbar (noch keine Session)')
                return (chart, [html.Div(line) for line in lines] or 'Keine Daten verfügbar', system_stats_text(),
                        free, estimate_text, preview)
'''


def patch_telemetry(t, f):
    t = sub_once(t, 'import time\n', 'import time\n\nimport system_stats  # PAKET-H2\nimport telemetry_chart\n', f)
    t = sub_once(t, 'from ..helpers import (CHART_EVERY, CHART_WINDOW_S, IMAGE_STYLE, axis, plain_figure, preview_uri,\n'
                    '                       system_stats, thin)\n',
                 'from ..helpers import CHART_EVERY, IMAGE_STYLE, preview_uri\n'
                 'from ..helpers import system_stats as system_stats_text\n', f)
    return replace_to_end(t, '    def layout(self):', TELEMETRY_BODY, f, "'telemetry-preview', 'src'")


# ------------------------------------------------------------------ dashboard/tabs/replay.py
REPLAY_EVENTS_CARD = '''
dbc.Row([dbc.Col(c.card(['Ereignisprotokoll', *c.info('eventlog', suffix='replay')], [
    dbc.RadioItems(id='replay-events-filter', options=event_log.FILTER_OPTIONS, value='important', inline=True,
                   className='mb-2'),
    html.Div(id='replay-events', className='c2c-events')]), md=12)], className='mt-3'),
'''

REPLAY_EVENTS_METHOD = '''
def events_view(self, name, mode):
    \"\"\"PAKET-H2: Ereignisprotokoll des Replay Labs (events.csv der Session bzw. Live-Ringbuffer).\"\"\"
    c, application, car = self.ctx, self.ctx.application, self.ctx.car
    if name in (None, LIVE):
        recorder = application.recorder
        path = getattr(recorder, 'path', None)
        ring_ids = {record.camera.frame_id for record in car.frame_history.snapshot()}
        entries = event_log.live_entries(application.health.events(), time.monotonic_ns(), mode,
                                         Path(str(path)).name if path else None, getattr(recorder, 'has_frame', None),
                                         ring_ids, limit=300)
    else:
        try:
            _path, frame_rows = load_session(name)
            entries = event_log.session_entries(load_events(name), frame_rows, name, mode)
        except (ValueError, OSError):
            return event_log.event_table(c.html, c.dcc, [], 'Session nicht lesbar')
    return event_log.event_table(c.html, c.dcc, entries)
'''

REPLAY_LIMITS = '''
@app.callback(Output('replay-index', 'max'), Output('replay-range', 'children'), Output('replay-events', 'children'),
              Input('replay-bounds', 'data'), Input('session-choice', 'value'),
              Input(self.tick_id, 'n_intervals'), State('main-tabs', 'active_tab'),
              Input('replay-events-filter', 'value'))
def replay_limits(bounds, name, _, tab, mode='important'):
    \"\"\"Index-Maximum, Bereichstext und (PAKET-H2) Ereignisprotokoll der Quelle in EINEM Takt-Callback.\"\"\"
    if tab != self.tab_id and ctx.triggered_id == self.tick_id:
        return no_update, no_update, no_update
    records, _, _ = records_for(name, bounds)
    source = 'Live-Ringbuffer (gleitend)' if name in (None, LIVE) else 'gespeicherte Session'
    selection = f' · Bereich Frame {int(bounds[0])}–{int(bounds[1])}' if bounds else ''
    if name not in (None, LIVE) and c.triggered_ids() == {self.tick_id}:
        events = no_update            # gespeicherte Session: der Takt allein aendert nichts
    else:
        events = self.events_view(name, mode)
    return max(0, len(records) - 1), f'{len(records)} Frames · {source}{selection}', events
'''


REPLAY_NAVIGATION = '''
@app.callback(Output('main-tabs', 'active_tab'), Output('replay-studio-msg', 'children'),
              Output('ab-source', 'children'), Output('replay-studio-token', 'data'),
              Output('session-choice', 'value'), Output('drive-studio-msg', 'children'),
              Input('replay-to-studio', 'n_clicks'), Input('replay-open-latest', 'n_clicks'),
              State('session-choice', 'value'), State('replay-bounds', 'data'),
              State('replay-index', 'value'), Input('jump-url', 'hash'),
              Input('drive-to-studio', 'n_clicks'), State('drive-rendered', 'data'), prevent_initial_call=True)
def replay_to_studio(_, _latest, name, bounds, index, jump=None, _drive=None, drive_key=None):
    """EINZIGER Besitzer von main-tabs.active_tab (Navigation).

    Ausloeser: Replay -> Pipeline Studio, "Neueste Session oeffnen", Sprunglink im Ereignisprotokoll und
    Cockpit -> Pipeline Studio (Fehlermeldung dann im Cockpit: drive-studio-msg).
    """
    trigger = ctx.triggered_id
    if trigger == 'jump-url':  # Link im Ereignisprotokoll
        target = parse_jump(jump)
        if target is None:
            return (no_update,) * 6
        return self.tab_id, no_update, no_update, no_update, target[0], no_update
    if trigger == 'replay-open-latest':  # PAKET-H2: neueste Session, auch ohne laufende Aufnahme
        sessions = list_sessions()
        if not sessions:
            return no_update, 'Keine gespeicherte Session vorhanden', no_update, no_update, no_update, no_update
        return self.tab_id, no_update, no_update, no_update, sessions[0], no_update
    if trigger == 'drive-to-studio':  # PAKET-H2: den im Cockpit angezeigten Frame einfrieren
        try:
            frame_id = parse_drive_key(drive_key)
            if frame_id is None:
                raise ValueError('im Cockpit ist kein Frame angezeigt')
            record = car.frame_history.find(frame_id)
            camera = record.camera if record is not None else None
            live = car.camera_data
            if camera is None and live is not None and live.frame_id == frame_id:
                camera = live
            if camera is None or camera.raw_image is None or not getattr(camera.raw_image, 'size', 0):
                raise ValueError(f'Frame {frame_id} liegt nicht mehr im Ringbuffer')
            ab.freeze(camera, parameters_from_record(camera.processing_parameters),
                      processor_key(camera.processor_name))
        except (ValueError, TypeError, OSError, cv2.error) as exc:
            return (no_update,) * 5 + (f'Übergabe nicht möglich: {exc}',)
        return ('pipeline', no_update,
                f'Referenz A = Frame {frame_id} aus dem Cockpit mit den damaligen Parametern. '
                '„A/B berechnen“ vergleicht mit den aktuellen Slidern (B).',
                {'frame_id': frame_id, 'source': 'Cockpit', 'nonce': time.time()}, no_update, '')
    try:
        _, _, path, record = record_at(name, bounds, index)
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
        return no_update, f'Übergabe nicht möglich: {exc}', no_update, no_update, no_update, no_update
    source = 'Live-Ringbuffer' if name in (None, LIVE) else name
    return ('pipeline', f'Frame {frame_id} an das Pipeline Studio übergeben',
            f'Referenz A = Frame {frame_id} aus {source} mit den damaligen Parametern. '
            '„A/B berechnen“ vergleicht mit den aktuellen Slidern (B).',
            {'frame_id': frame_id, 'source': source, 'nonce': time.time()}, no_update, no_update)
'''

PARSE_DRIVE_KEY = '''
def parse_drive_key(key):
    """PAKET-H2: Frame-ID aus dem Render-Schluessel des Cockpits (``drive:<id>``, ``cal:<id>``, optional ``:hud``)."""
    if not isinstance(key, str) or ':' not in key:
        return None
    try:
        number = int(key.split(':')[1])
    except ValueError:
        return None
    return number if number >= 0 else None
'''


def patch_replay(t, f):
    t = sub_once(t, 'import cv2\n', 'import cv2\n\nimport event_log  # PAKET-H2\n', f)
    t = sub_once(t, 'log = logging.getLogger(__name__)\n', 'log = logging.getLogger(__name__)\n' + PARSE_DRIVE_KEY, f)
    t = sub_once(t, "dbc.Col(dbc.Button('Sessions aktualisieren', id='session-refresh', color='secondary'), md=2),\n"
                    "                dbc.Col(html.Div('Replay steuert keine Motoren.', className='small text-secondary'), md=4),",
                 "dbc.Col([dbc.Button('Sessions aktualisieren', id='session-refresh', color='secondary',\n"
                 "                                  className='me-2'),\n"
                 "                         dbc.Button('Neueste Session öffnen', id='replay-open-latest', color='info')], md=4),\n"
                 "                dbc.Col(html.Div('Replay steuert keine Motoren.', className='small text-secondary'), md=2),", f)
    t = insert(t, "dcc.Store(id='replay-playing', data=False, storage_type='memory'),", REPLAY_EVENTS_CARD, f,
               after=False, blank_after=0)
    t = sub_once(t, "@app.callback(Output('session-choice', 'options'), Input('session-refresh', 'n_clicks'),\n"
                    "                      Input('last-action', 'data'))\n        def refresh_sessions(_, _action):\n",
                 "@app.callback(Output('session-choice', 'options'), Input('session-refresh', 'n_clicks'),\n"
                 "                      Input('last-action', 'data'), Input('replay-open-latest', 'n_clicks'))\n"
                 "        def refresh_sessions(_, _action, _latest=None):\n", f)
    t = replace_block(t, "@app.callback(Output('main-tabs', 'active_tab'), Output('replay-studio-msg', 'children'),",
                      "@app.callback(Output('replay-download', 'data'), Input('replay-export', 'n_clicks'),",
                      indented(REPLAY_NAVIGATION, '        '), f)
    t = sub_once(t, "        # ---- Navigation (tabuebergreifend): Replay/Recording/Sprunglink -> Tab, Studio, Session\n",
                 "        # ---- Navigation (tabuebergreifend): Replay/Cockpit/Sprunglink -> Tab, Studio, Session (PAKET-H2)\n", f)
    t = replace_block(t, "@app.callback(Output('replay-index', 'max'), Output('replay-range', 'children'),",
                      "@app.callback(Output('replay-playing', 'data'), Input('replay-play', 'n_clicks'),",
                      indented(REPLAY_LIMITS, '        '), f)
    return insert(t, '# --------------------------------------------------------------- Callbacks',
                  REPLAY_EVENTS_METHOD, f, after=False, indent='    ')


# ------------------------------------------------------------------ help_texts.py
HELP_ENTRIES = """
# PAKET-H2: Auslastung, Speicherplatz, Telemetrie-Diagramm, Ereignisprotokoll
'cpu': ('Auslastung (CPU)', 'gemessen',
        'CPU-Last des Pi in Prozent als Mittel über alle vier Kerne, die bis 60 Prozent grün, bis 85 Prozent gelb '
        'und darüber oder ab 80 Grad Celsius rot angezeigt wird und mit Bildverarbeitung, Aufnahme und '
        'Dashboard steigt.'),
'ram': ('Arbeitsspeicher', 'gemessen',
        'Belegter Anteil des Arbeitsspeichers in Prozent, der mit Ringbuffer, Aufnahme und offenen '
        'Browser-Tabs steigt.'),
'temp': ('Temperatur', 'gemessen',
         'CPU-Temperatur in Grad Celsius, ab etwa 80 Grad taktet der Pi 4 herunter, weshalb ab diesem Wert '
         'die Anzeige immer rot ist.'),
'disk': ('Freier Speicher', 'gemessen',
         'Freier Platz der SD-Karte unter Data_AI in Gigabyte und Prozent, der nur etwa alle 30 Sekunden '
         'neu bestimmt wird.'),
'diskest': ('Aufnahmedauer', 'berechnet',
            'Geschätzte Minuten Aufnahme, die auf den freien Speicher passen, berechnet aus der mittleren '
            'JPEG-Größe der neuesten Session und der Kamera-fps, ohne vorhandene Session nicht bestimmbar.'),
'decisions': ('Entscheidungen', 'gemessen',
              'Wie oft der Regler pro Sekunde einen neuen Kameraframe auswertet, höchstens so hoch wie die '
              'Kamerarate.'),
'telechart': ('Telemetrie-Diagramm', 'gemessen',
              'Vier Spuren (Frequenzen, Latenzen, Fahren, System) mit gemeinsamer Zeitachse der letzten '
              '60 Sekunden, in denen im Stillstand Lücken entstehen, weil nichts fortgeschrieben wird.'),
'eventlog': ('Ereignisprotokoll', 'gemessen',
             'Zustandsänderungen und Meldungen des Fahrzeugs mit dem Frame, zu dem sie gehören, wobei der '
             'Filter Wichtig die häufigen Linien- und Lenkereignisse ausblendet.'),
"""

HELP_TIP = '''
def tip(key):
    """PAKET-H2: Kurztext fuer ``title``-Attribute (Tooltip ohne Popover): ``Titel (Herkunft): Satz``."""
    title, origin, text = HELP[key]
    return f'{title} ({origin}): {text}'
'''


def patch_help_texts(t, f):
    t = insert(t, "'simrate': ('Anzeigerate', 'gemessen',", HELP_ENTRIES, f, after=False, blank_after=0)
    return append(t, HELP_TIP)


# ------------------------------------------------------------------ session_recorder.py
HAS_FRAME = '''
def has_frame(self, frame_id):
    """PAKET-H2: Wurde dieser Frame in der laufenden bzw. zuletzt aufgezeichneten Session geschrieben?"""
    if isinstance(frame_id, bool):
        return False
    try:
        number = int(frame_id)
    except (TypeError, ValueError):
        return False
    if isinstance(frame_id, float) and frame_id != number:
        return False                  # 7.5 ist kein Frame
    return number in self._frame_ids
'''


def patch_session_recorder(t, f):
    t = insert(t, 'self._frame_times = []  # PAKET-H1: camera_monotonic_ns der geschriebenen Frames',
               'self._frame_ids = set()  # PAKET-H2: IDs der geschriebenen Frames (has_frame)', f)
    t = sub_once(t, '            self._frame_times = []\n',
                 '            self._frame_times = []\n            self._frame_ids = set()  # PAKET-H2\n', f)
    t = insert(t, "if r.get('camera_monotonic_ns') not in ('', None))  # PAKET-H1",
               "self._frame_ids.update(int(r['frame_id']) for r in rows)  # PAKET-H2", f, indent='        ')
    return insert(t, 'def stats(self):', HAS_FRAME, f, after=False)


# ------------------------------------------------------------------ car_application.py
APP_METHODS = '''
def _camera_hz(self):
    """PAKET-H2: aktuelle Kamerarate (None bei veraltetem Wert) fuer die Aufnahme-Schaetzung."""
    return self._fresh_hz(getattr(self.car, 'thread_metrics', {}).get('camera')) or None

def _sample_extra(self):
    """PAKET-H2: Latenz- und Systemverlauf (4 Hz) im Telemetrie-Thread; einziger Aufrufer des SystemSampler."""
    now = monotonic()
    camera = self.car.camera_data
    age = processing = reaction = None
    if camera is not None and camera.frame_id >= 0 and camera.monotonic_ns:
        age = (monotonic_ns() - camera.monotonic_ns) / 1e6
        processing = camera.processing_ns / 1e6 if camera.processing_ns else None
    record = self.car.frame_history.latest_complete()
    if (record is not None and self.running() and record.driving.frame_id != self._last_latency_frame
            and record.driving.monotonic_ns and record.camera.monotonic_ns):
        self._last_latency_frame = record.driving.frame_id
        reaction = (record.driving.monotonic_ns - record.camera.monotonic_ns) / 1e6
    values = self.system.sample()
    with self._extra_lock:
        self._latency_history.append((now, age, processing, reaction))
        self._system_history.append((now, values['cpu'], values['ram'], values['temp']))

def sample_extra(self):
    """PAKET-H2: (Latenzverlauf, Systemverlauf) als unveraenderliche Momentaufnahme."""
    with self._extra_lock:
        return tuple(self._latency_history), tuple(self._system_history)

def system_stats(self):
    """PAKET-H2: letzter Systemwert (CPU, RAM, Temperatur, Speicherplatz); misst nicht selbst."""
    return self.system.last()
'''


def patch_car_application(t, f):
    t = insert(t, 'import runtime_settings  # PAKET-H1B', 'import system_stats  # PAKET-H2', f)
    t = insert(t, 'self._freq_history = deque(maxlen=240)', '''
# PAKET-H2: Latenz- und Systemverlauf; SystemSampler wird nur vom Telemetrie-Thread aufgerufen
self._extra_lock = threading.Lock()
self._latency_history = deque(maxlen=240)
self._system_history = deque(maxlen=240)
self._last_latency_frame = None
self.system = system_stats.SystemSampler(fps_source=self._camera_hz)''', f)
    t = sub_once(t, """            self._freq_history.append((monotonic(),
                self._fresh_hz(metrics.get('camera')),  # PAKET-G: veraltete Werte = 0
                self._fresh_hz(metrics.get('controller'))))
""", """            # PAKET-H2: veraltete Werte (kein Tick seit 2 s) werden None statt 0 und ergeben Luecken im Diagramm
            self._freq_history.append((monotonic(),
                self._fresh_hz(metrics.get('camera')) or None,
                self._fresh_hz(metrics.get('controller')) or None))
        self._sample_extra()
""", f)
    return insert(t, 'def sample(self):', APP_METHODS, f, after=False)


# ------------------------------------------------------------------ offline_application.py
OFFLINE_METHODS = '''
def system_stats(self):  # PAKET-H2
    """Offline wird nichts gemessen (es gibt keinen Sampler-Thread): alle Werte unbekannt."""
    return system_stats.empty()

def sample_extra(self):
    return (), ()
'''


def patch_offline(t, f):
    t = insert(t, 'import runtime_settings  # PAKET-H1B', 'import system_stats  # PAKET-H2', f)
    return insert(t, 'def sample(self):', OFFLINE_METHODS, f, after=False)


# ------------------------------------------------------------------ assets/studio.css
CSS = """
/* PAKET-H2: Ereignisprotokoll als Tabelle */
.c2c-events-table td,.c2c-events-table th{padding:.15rem .45rem;border-color:#26314a}
.c2c-events-table th{color:var(--c2c-muted);font-weight:600}
"""


def patch_css(t, f):
    return append(t, CSS)


# ------------------------------------------------------------------ Tests und Doku
def patch_test_callbacks(t, f):
    t = sub_once(t, "                    'recording_view', 'refresh_sessions',",
                 "                    'drive_events', 'refresh_sessions',", f)
    t = sub_once(t, "        self.assertTrue(all(v is no for v in self.callbacks['recording_view'](1, None, 'drive')))\n",
                 "        self.assertIs(self.callbacks['drive_events'](1, 'important', 'pipeline'), no)  # PAKET-H2\n"
                 "", f)
    t = sub_once(t, "self.assertEqual(self.callbacks['tab_intervals']('telemetry', False), (True, True, False, True, True))\n"
                    "        self.assertEqual(self.callbacks['tab_intervals']('telemetry', True), (True,) * 5)\n",
                 "self.assertEqual(self.callbacks['tab_intervals']('telemetry', False), (True, True, False, True))  # PAKET-H2\n"
                 "        self.assertEqual(self.callbacks['tab_intervals']('telemetry', True), (True,) * 4)\n", f)
    t = sub_once(t, "for t in ('drive', 'pipeline', 'telemetry', 'record', 'replay')), 3)",
                 "for t in ('drive', 'pipeline', 'telemetry', 'replay')), 3)", f)
    t = sub_once(t, "maximum, text = self.callbacks['replay_limits'](bounds, '__live__', 1, 'replay')",
                 "maximum, text, _events = self.callbacks['replay_limits'](bounds, '__live__', 1, 'replay')  # PAKET-H2", f)
    return replace_block(t, '    def test_ereignisliste(self):', "if __name__ == '__main__':", '''
    def test_ereignisliste(self):
        self.app_state.health.record('TEST_EVENT', 'Prüfung', 3)       # PAKET-H2: Ereignisprotokoll im Cockpit
        table = self.callbacks['drive_events'](1, 'all', 'drive')
        texts = [c.children for c in table.walk() if isinstance(c.children, str)]
        self.assertIn('TEST_EVENT', texts)
        important = self.callbacks['drive_events'](1, 'important', 'drive')
        self.assertNotIn('TEST_EVENT', [c.children for c in important.walk() if isinstance(c.children, str)])
''', f, gap=2)


def patch_test_plugins(t, f):
    t = sub_once(t, "        'RecordingTab': {'recording_view'},\n", '', f)
    t = sub_once(t, "'DrivingTab': {'cockpit_view'},", "'DrivingTab': {'cockpit_view', 'drive_events'},  # PAKET-H2", f)
    t = sub_once(t, "'replay_play_label', 'replay_render', 'replay_chart', 'replay_to_studio', 'export_range'},",
                 "'replay_play_label', 'replay_render', 'replay_chart', 'replay_to_studio', 'export_range'},", f)
    t = sub_once(t, "    def test_sechs_tabs_in_dieser_reihenfolge(self):", "    def test_fuenf_tabs_in_dieser_reihenfolge(self):  # PAKET-H2", f)
    t = sub_once(t, "['drive', 'pipeline', 'telemetry', 'record', 'replay', 'settings'])",
                 "['drive', 'pipeline', 'telemetry', 'replay', 'settings'])", f)
    t = sub_once(t, "                         ['Driving Cockpit', 'Pipeline Studio', 'Telemetrie & Performance',\n"
                    "                          'Recording & Events', 'Replay Lab', 'Settings'])",
                 "                         ['Driving Cockpit', 'Pipeline Studio', 'Telemetrie & Performance',\n"
                 "                          'Replay Lab', 'Settings'])", f)
    t = sub_once(t, "{'tick-drive', 'tick-pipeline', 'tick-telemetry', 'tick-record', 'tick-replay'})",
                 "{'tick-drive', 'tick-pipeline', 'tick-telemetry', 'tick-replay'})", f)
    return sub_once(t, "self.assertEqual(len(app.callbacks['tab_intervals'].outputs), 6)              # 5 Standardtakte + Echo",
                    "self.assertEqual(len(app.callbacks['tab_intervals'].outputs), 5)              # 4 Standardtakte + Echo", f)


def patch_test_real_dash(t, f):
    t = sub_once(t, 'self.assertEqual(len(deps), 34)', 'self.assertEqual(len(deps), 34)  # PAKET-H2: - recording_view, + drive_events', f)
    t = sub_once(t, "('mode', 'disabled'), ('global-action-message', 'children')]",
                 "('mode', 'disabled'), ('global-action-message', 'children'), ('global-rec-tooltip', 'children')]", f)
    return sub_once(t, "('drive-frame-badge', 'children'), ('drive-age', 'children')]",
                    "('drive-frame-badge', 'children'), ('drive-age', 'children'),\n"
                    "                   ('drive-cpu', 'children'), ('drive-ram', 'children'), ('drive-temp', 'children'),\n"
                    "                   ('drive-rec-line', 'children')]  # PAKET-H2", f)


def patch_test_paket_c(t, f):
    return sub_once(t, "tab, message, source, _token, _s = self.callbacks['replay_to_studio'](1, None, '__live__', None, 1)  # PAKET-E",
                    "tab, message, source, _token, _s, _d = self.callbacks['replay_to_studio'](1, None, '__live__', None, 1)  # PAKET-H2", f)


def patch_test_paket_d(t, f):
    t = sub_once(t, "tab, _msg, _src, token, _s = self.cb['replay_to_studio'](1, None, '__live__', None, 0)  # PAKET-E",
                 "tab, _msg, _src, token, _s, _d = self.cb['replay_to_studio'](1, None, '__live__', None, 0)  # PAKET-H2", f)
    return sub_once(t, "_tab, _msg, _src, token, _s = self.cb['replay_to_studio'](1, None, '__live__', None, 0)\n",
                    "_tab, _msg, _src, token, _s, _d = self.cb['replay_to_studio'](1, None, '__live__', None, 0)\n", f)


TEST_E_NEW = '''
    def test_telemetrie_dicts_und_drosselung(self):
        self.ctx.set('main-tabs', 'active_tab')
        chart = self.callbacks['telemetry_view'](1, 'telemetry')[0]        # PAKET-H2: EIN gekoppeltes Diagramm
        self.assertIsInstance(chart, dict)
        self.assertIn('layout', chart)
        self.ctx.set('tick-telemetry', 'n_intervals')
        self.assertIs(self.callbacks['telemetry_view'](2, 'telemetry')[0], self.module.no_update)
        self.assertIsInstance(self.callbacks['telemetry_view'](4, 'telemetry')[0], dict)

    def test_neueste_session_oeffnet_das_replay_lab(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                for name in ('session_20261028_100000_000000', 'session_20261028_110000_000000'):
                    os.makedirs(os.path.join('Data_AI', 'sessions', name))
                    open(os.path.join('Data_AI', 'sessions', name, 'frames.csv'), 'w').write('frame_id\\n')
                self.fake.recorder.path = None                         # auch ohne laufende Aufnahme
                self.ctx.set('replay-open-latest')
                result = self.callbacks['replay_to_studio'](None, 1, None, None, 0)
                self.assertEqual(result[0], 'replay')
                self.assertEqual(result[4], 'session_20261028_110000_000000')
            finally:
                os.chdir(cwd)

    def test_ohne_session_kein_sprung_aber_meldung(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            cwd = os.getcwd()
            os.chdir(folder)
            try:
                self.ctx.set('replay-open-latest')
                result = self.callbacks['replay_to_studio'](None, 1, None, None, 0)
                self.assertEqual(result[1], 'Keine gespeicherte Session vorhanden')
                self.assertTrue(all(v is self.module.no_update for i, v in enumerate(result) if i != 1))
            finally:
                os.chdir(cwd)
'''


def patch_test_paket_e(t, f):
    return replace_block(t, '    def test_telemetrie_dicts_und_drosselung(self):', "if __name__ == '__main__':", TEST_E_NEW, f, gap=2)


def patch_test_paket_g(t, f):
    t = sub_once(t, "rate, lat, qual = self.cb['kpi_bar'](2)", "rate, lat, qual, load = self.cb['kpi_bar'](2)  # PAKET-H2", f)
    t = sub_once(t, """        _status, _details, items, _preview = self.cb['recording_view'](1, None, 'record')
        links = [c for item in items for c in item.walk() if type(c).__name__ == 'Link']
""", """        table = self.cb['drive_events'](1, 'all', 'drive')   # PAKET-H2: Ereignisprotokoll im Cockpit
        links = [c for c in table.walk() if type(c).__name__ == 'Link']
""", f)
    t = sub_once(t, "self.assertFalse(any('/3/' in link.props['href'] for link in links))\n",
                 "self.assertFalse(any('/3/' in link.props['href'] for link in links))\n"
                 "        grey = [c for c in table.walk() if c.props.get('title') == 'Weder in einer Session noch im Ringbuffer']\n"
                 "        self.assertEqual([c.children for c in grey], ['3'])        # Frame 3: grau mit Tooltip\n", f)
    t = sub_once(t, "('replay-studio-token', 'data'), ('session-choice', 'value')]\n",
                 "('replay-studio-token', 'data'), ('session-choice', 'value'), ('drive-studio-msg', 'children')]\n", f)
    t = sub_once(t, """                           {'id': 'record-open-replay', 'property': 'n_clicks', 'value': None},
                           {'id': 'jump-url', 'property': 'hash', 'value': '#jump/__live__/11/1'}],
                'state': [{'id': 'session-choice', 'property': 'value', 'value': '__live__'},
                          {'id': 'replay-bounds', 'property': 'data', 'value': None},
                          {'id': 'replay-index', 'property': 'value', 'value': 0}],""",
                 """                           {'id': 'replay-open-latest', 'property': 'n_clicks', 'value': None},
                           {'id': 'jump-url', 'property': 'hash', 'value': '#jump/__live__/11/1'},
                           {'id': 'drive-to-studio', 'property': 'n_clicks', 'value': None}],
                'state': [{'id': 'session-choice', 'property': 'value', 'value': '__live__'},
                          {'id': 'replay-bounds', 'property': 'data', 'value': None},
                          {'id': 'replay-index', 'property': 'value', 'value': 0},
                          {'id': 'drive-rendered', 'property': 'data', 'value': None}],""", f)
    return t


TEST_H1_TELE = '''
    def test_telemetrie_leer_und_mit_daten(self):
        """PAKET-H2: EIN gekoppeltes Diagramm statt drei; leer = dunkle Figur mit Hinweis, ohne layout.height."""
        self.fake.sample = lambda: ((), (), ())
        self.ctx.set('main-tabs', 'active_tab')
        chart = self.cb['telemetry_view'](1, 'telemetry')[0]
        self.figures_without_height(chart)
        self.assertEqual(chart['data'], [])
        self.assertTrue(chart['layout']['annotations'][0]['text'])
        now = time.monotonic()
        rows = tuple((now - 3 + i * .5, 100 + i, 97, 30, 0, 2, 99) for i in range(6))
        freqs = tuple((now - 3 + i * .5, 25, 24) for i in range(6))
        self.fake.sample = lambda: (rows, freqs, ())
        chart = self.cb['telemetry_view'](1, 'telemetry')[0]
        self.figures_without_height(chart)
        names = [trace['name'] for trace in chart['data']]
        for needed in ('Kamera (Ist)', 'Entscheidungen (Ist)', 'Kamera (Soll)', 'Rohwinkel', 'Stellwert',
                       'Speed-Steuerwert', 'Linienanzahl'):
            self.assertIn(needed, names)
        self.assertEqual(chart['layout']['uirevision'], 'telemetry')            # Zoom/Legende ueberleben Updates
        self.fake.sample = lambda: ((), (), ())

'''


def patch_test_paket_h1(t, f):
    t = replace_block(t, '    def test_telemetrie_leer_und_mit_daten(self):',
                      '    def test_replay_diagramm_uirevision_und_leere_figur(self):', TEST_H1_TELE, f, gap=0)
    t = sub_once(t, "self.assertEqual(ids, {'freq-bars', 'freq-history', 'drive-history', 'replay-chart', 'sim-chart'})",
                 "self.assertEqual(ids, {'telemetry-chart', 'replay-chart', 'sim-chart'})  # PAKET-H2", f)
    t = sub_once(t, "                         {'freq-bars', 'freq-history', 'drive-history', 'replay-chart', 'sim-chart'})",
                 "                         {'telemetry-chart', 'replay-chart', 'sim-chart'})  # PAKET-H2", f)
    t = sub_once(t, "{'freq-bars-box', 'replay-chart-box', 'sim-chart-box'} <= boxes", "{'telemetry-chart-box', 'replay-chart-box', 'sim-chart-box'} <= boxes", f)
    return _h1_http(t, f)


def _h1_http(t, f):
    t = sub_once(t, """        outputs = [('freq-bars', 'figure'), ('freq-history', 'figure'), ('drive-history', 'figure'),
                   ('data-age', 'children'), ('system-stats', 'children'), ('telemetry-preview', 'src')]""",
                 """        outputs = [('telemetry-chart', 'figure'), ('data-age', 'children'), ('system-stats', 'children'),
                   ('telemetry-disk-free', 'children'), ('telemetry-disk-estimate', 'children'),
                   ('telemetry-preview', 'src')]  # PAKET-H2""", f)
    return sub_once(t, """        for name in ('freq-bars', 'freq-history', 'drive-history'):
            self.assertNotIn('height', response.get_json()['response'][name]['figure']['layout'])""",
                    """        self.assertNotIn('height', response.get_json()['response']['telemetry-chart']['figure']['layout'])""", f)


def patch_test_paket_h1b(t, f):
    return sub_once(t, "for graph in ('freq-bars', 'freq-history', 'drive-history'):", "for graph in ('telemetry-chart',):  # PAKET-H2", f)


CHANGELOG = '''
## 6.9 – Paket H2 (PAKET-H2)
* **Fünf Reiter:** „Recording & Events“ entfällt (`dashboard/tabs/recording.py` gelöscht, Callback `recording_view` und alle
  IDs `record-*`, `session-stats`, `event-list`, `tick-record` entfernt). Aufnahmezustand und Zähler (Frames, Lücken, fehlende
  Entscheidungen, Session, Fehler): Tooltip am REC-Chip der Steuerleiste und eine Zeile im Cockpit. Der wirkungslose Button
  „Letzte Session im Replay öffnen“ entfällt; im Replay Lab öffnet „Neueste Session öffnen“ die neueste Session aus
  `list_sessions()` auch ohne laufende Aufnahme. `replay_to_studio` bleibt einziger Besitzer von `main-tabs.active_tab`.
* **Ereignisprotokoll** (`event_log.py`) im Cockpit und im Replay Lab: Zeit, Frame, Ereignistyp, Beschreibung, neueste zuerst,
  Filter „Wichtig“ / „Alle“. Die Frame-Nummer ist ein Link `#jump/<session>/<frame>/<nonce>` (nur anspringbar, wenn der Frame in der
  aufgezeichneten Session – neu `SessionRecorder.has_frame` – oder im Ringbuffer liegt, sonst grau mit Tooltip; Frame -1/None
  ohne Link). Replay Lab: `events.csv` der gewählten Session bzw. Live-Ringbuffer; die Ereignismarker im Replay-Diagramm waren
  schon vorhanden und bleiben.
* **Auslastung** (`system_stats.py`, `SystemSampler`): CPU-Last (Mittel über alle Kerne) prominent, RAM und Temperatur klein, in
  Kennzahlenleiste und Cockpit; Ampel < 60 % grün, < 85 % gelb, darüber rot, ab 80 °C immer rot. Genau ein Aufrufer (Telemetrie-
  Sampler-Thread, ca. 4 Hz); Dash liest nur den letzten Wert; fehlende Werte sind `None` (Anzeige „–“).
* **Telemetrie & Performance:** Speicherplatz der SD-Karte mit Schätzung „reicht für ca. X min Aufnahme“ (alle ca. 30 s);
  EIN gekoppeltes Diagramm (`telemetry_chart.py`) statt drei: Frequenzen, Latenzen, Fahren, System auf gemeinsamer Zeitachse
  (60 s), echte Messwerte mit Lücken im Stillstand, dict-Figur, nur jeden 3. Takt, max. 240 Punkte je Kurve, `uirevision`.
  Das Textblock „Datenalter, Latenzen, System“ ist einklappbar. Info-Text zu „Entscheidungen“.
* **Cockpit:** Button „Frame im Pipeline Studio öffnen“ (gleiche Übergabe wie im Replay Lab); Fehlermeldung im Cockpit, wenn kein
  Frame vorliegt. Das Frequenzhistorie-Tupel enthält veraltete Werte jetzt als `None` statt 0.
'''


def patch_architecture(t, f):
    t = sub_once(t, 'DashboardTab (abc) ── DrivingTab · PipelineTab · TelemetryTab · RecordingTab · ReplayTab · SettingsTab',
                 'DashboardTab (abc) ── DrivingTab · PipelineTab · TelemetryTab · ReplayTab · SettingsTab   (PAKET-H2: fünf Reiter)', f)
    t = sub_once(t, '| Recording & Events | `tick-record` | 1 |\n', '', f)
    t = sub_once(t, '    driving.py · pipeline.py · telemetry.py · recording.py · replay.py · settings.py',
                 '    driving.py · pipeline.py · telemetry.py · replay.py · settings.py', f)
    t = sub_once(t, '(Navigation Replay/Recording → Pipeline Studio, Besitzer: `ReplayTab`)',
                 '(Navigation Replay/Cockpit/Sprunglink → Tab, Pipeline Studio, Session; einziger Besitzer von `main-tabs.active_tab`)', f)
    t = sub_once(t, '| „Vier Ansichten“ | fünf Ansichten (zusätzlich Recording & Events) |',
                 '| „Vier Ansichten“ | fünf Ansichten (Recording & Events entfiel mit Paket H2) |', f)
    return sub_once(t, '| sechs Ansichten als Plug-ins, ausschließlich über `CarApplication` |',
                    '| fünf Ansichten als Plug-ins, ausschließlich über `CarApplication` |', f)


def patch_readme(t, f):
    t = sub_once(t, '| Recording & Events | Aufzeichnungsstatus, Zähler, Ereignisprotokoll |\n', '', f)
    t = sub_once(t, '| Telemetrie & Performance | Soll/Ist-Frequenzen, Verläufe, Linienanzahl, Datenalter, Latenzen, CPU/RAM |',
                 '| Telemetrie & Performance | ein gekoppeltes Diagramm (Frequenzen, Latenzen, Fahren, System), Speicherplatz mit Aufnahme-Schätzung (PAKET-H2) |', f)
    return sub_once(t, '| Steuerleiste (immer sichtbar) | Kamera-/Fahr-/REC-Status, Frame-ID, Fahrmodus, **START**, **STOP**, Bergung, REC |',
                    '| Steuerleiste (immer sichtbar) | Kamera-/Fahr-/REC-Status (Tooltip: Aufnahmezähler), Frame-ID, Fahrmodus, **START**, **STOP**, Bergung, REC |', f)


PATCHES = {
    'dashboard/tabs/__init__.py': patch_tabs_init,
    'dashboard/shell.py': patch_shell,
    'dashboard/tabs/driving.py': patch_driving,
    'dashboard/tabs/telemetry.py': patch_telemetry,
    'dashboard/tabs/replay.py': patch_replay,
    'help_texts.py': patch_help_texts,
    'session_recorder.py': patch_session_recorder,
    'car_application.py': patch_car_application,
    'offline_application.py': patch_offline,
    'assets/studio.css': patch_css,
    'tests/test_dashboard_callbacks.py': patch_test_callbacks,
    'tests/test_dashboard_plugins.py': patch_test_plugins,
    'tests/test_dashboard_real_dash.py': patch_test_real_dash,
    'tests/test_paket_c.py': patch_test_paket_c,
    'tests/test_paket_d.py': patch_test_paket_d,
    'tests/test_paket_e.py': patch_test_paket_e,
    'tests/test_paket_g.py': patch_test_paket_g,
    'tests/test_paket_h1.py': patch_test_paket_h1,
    'tests/test_paket_h1b.py': patch_test_paket_h1b,
    'CHANGELOG.md': lambda t, f: insert(t, '# Changelog', CHANGELOG, f, blank_after=1),
    'ARCHITECTURE.md': patch_architecture,
    'README.md': patch_readme,
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
            if MARKER not in new:
                raise PatchError(f'{name}: Marker fehlt nach dem Patch')
            if name.endswith('.py'):
                compile(new, name, 'exec')
            results[path] = new
        for name in DELETE:
            if not (root / name).is_file():
                raise PatchError(f'{name}: zu loeschende Datei fehlt')
    except (PatchError, SyntaxError, OSError) as exc:
        print(f'PATCH ABGEBROCHEN, keine Datei geaendert: {exc}', file=sys.stderr)
        return 1
    for path, new in results.items():
        path.write_text(new, encoding='utf-8', newline='\n')
        print(f'  + {path.relative_to(root)}')
    for name in DELETE:
        (root / name).unlink()
        print(f'  - {name} (geloescht)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
