"""Camp2Code Control Center: eine Dash-Anwendung mit fuenf Ansichten.

Architektur (Version 6.0):
    * Aktionen (Start, STOP, Bergung, Aufzeichnung) laufen ueber genau einen
      Callback und die oeffentlichen Methoden von ``CarApplication``.
    * Alle Anzeigen werden aus dem tatsaechlichen Anwendungszustand abgeleitet,
      nicht aus dem letzten Buttonklick.
    * Jede Ansicht aktualisiert sich nur, wenn ihr Tab sichtbar ist. Unsichtbare
      Browser-Tabs pausieren das Polling (assets/visibility.js).
    * Das Dashboard erkennt keine Linien und rechnet keine Fahrentscheidungen;
      es zeichnet nur vorhandene Daten auf Bildkopien.
"""
import atexit
import base64
import contextlib
from types import SimpleNamespace
import logging
import os  # PAKET-D
import signal
import sys
import threading
import time

import cv2
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
try:  # PAKET-C: Teilaktualisierung des Diagramms (Dash >= 2.9)
    from dash import Patch
except ImportError:  # Dash-Attrappe in den Tests
    Patch = None

from car_application import CarApplication
from dashboard_overlay import render_calibration_overlay, render_overlay
from dashboard_steering import draw_steering_overlay
from pipeline_ab import PipelineAB
from pipeline_processors import CopilotLaneProcessor
from replay_studio import load_session_image, parameters_from_record, processor_key
from telemetry.thread_stats import ThreadCpuSampler
from pipeline_config import list_presets, load, load_preset, save_preset
from replay_hud import draw_hud
from replay_selection import event_window, record_frame_id, select_records, selected_bounds
from session_recorder import (export_live_records, export_session_range, list_sessions,
                              load_events, load_session)

try:  # optional, fuer CPU- und Speicheranzeige
    import psutil
except ImportError:  # pragma: no cover - abhaengig von der Installation
    psutil = None

log = logging.getLogger(__name__)

STAGES = [('raw', 'Rohbild'), ('roi', 'ROI'), ('hsv', 'HSV (Falschfarben)'), ('mask', 'Farbmaske'),
          ('blur', 'Blur'), ('morph', 'Morphologie'), ('edges', 'Canny'), ('overlay', 'Linien')]
SWITCHES = ('roi', 'mask', 'blur', 'morph', 'canny', 'lanes')
STAGE_SWITCH = {'roi': 'roi', 'mask': 'mask', 'blur': 'blur', 'morph': 'morph',
                'edges': 'canny', 'overlay': 'lanes'}
LIVE = '__live__'
TABS = ('drive', 'pipeline', 'telemetry', 'record', 'replay')
ACTIVE_PRESET = '__active__'
MODES = [{'label': 'CameraMode (autonom)', 'value': 'camera'},
         {'label': 'PSControllerMode (Gamepad)', 'value': 'gamepad'}]
IMAGE_STYLE = {'width': '100%', 'background': '#0b1220', 'borderRadius': '8px', 'objectFit': 'contain'}


# --------------------------------------------------------------------- Hilfen
def uri(image, quality=70):
    """Kodiert ein Bild als JPEG-Data-URI (eine Kodierung je Bild und Tick)."""
    if image is None or getattr(image, 'size', 0) == 0:
        return None
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii') if ok else None


def dark_figure(figure, height=260, **layout):
    figure.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
                         plot_bgcolor='#111a2b', height=height,
                         margin=dict(l=45, r=20, t=25, b=40),
                         legend=dict(orientation='h', y=1.12, x=0), **layout)
    return figure


def fmt(value, digits=0, unit=''):
    """Formatiert Zahlen; fehlende Werte werden als Gedankenstrich angezeigt."""
    if value is None:
        return '–'
    try:
        return f'{float(value):.{digits}f}{unit}'
    except (TypeError, ValueError):
        return str(value)


def settings_from_controls(processor, crop, hue, sat, val, blur, morph, canny, switches):
    return dict(processor=processor, crop=crop, hue=hue, sat=sat, val=val, blur=blur,
                morph=morph, canny=canny,
                enabled={name: 'on' in (v or []) for name, v in zip(SWITCHES, switches)})


def parameters_from_settings(settings):
    return (tuple(settings['hue']), tuple(settings['sat']), tuple(settings['val']),
            tuple(settings['crop']), int(settings['blur']), int(settings['morph']),
            tuple(settings['canny']), dict(settings['enabled']))


def diagnostic_text(diagnostic, stop_reason, running):
    """Controllerdiagnose als Klartext; ohne Fahrt als 'letzte Fahrt' gekennzeichnet."""
    if not diagnostic:
        return 'Noch keine Controllerdiagnose'
    lanes = [x for x in diagnostic.get('lanes', ()) if x is not None]
    prefix = 'Aktuell' if running else 'Letzte Fahrt'
    return (f"{prefix} · Frame {diagnostic.get('frame_id')} · Linien: {len(lanes)} · "
            f"Rohwinkel: {fmt(diagnostic.get('raw_angle'))} · "
            f"Vorwert gehalten: {'ja' if diagnostic.get('held_previous_angle') else 'nein'} · "
            f"Soll: {fmt(diagnostic.get('requested_angle'))} · "
            f"Stellwert: {fmt(diagnostic.get('actual_angle'))} · "
            f"Stoppursache: {stop_reason or 'keine'}")


def system_stats():
    """CPU und Speicher des Prozesses (nur wenn psutil installiert ist)."""
    if psutil is None:
        return 'psutil nicht installiert – CPU/Speicher nicht verfügbar'
    process = psutil.Process()
    load_avg = ''
    with contextlib.suppress(AttributeError, OSError):
        load_avg = ' · Load ' + ' / '.join(f'{x:.2f}' for x in psutil.getloadavg())
    return (f'Prozess-CPU {process.cpu_percent(interval=None):.0f} % · '
            f'RSS {process.memory_info().rss / 2**20:.0f} MB · '
            f'Threads {threading.active_count()}{load_avg}')


def card(title, content, **kwargs):
    return dbc.Card([dbc.CardHeader(title), dbc.CardBody(content)], className='h-100', **kwargs)


def slider(label, key, low, high, value, step=1, pair=False):
    component = dcc.RangeSlider if pair else dcc.Slider
    return html.Div([html.Label(label, className='small'),
                     component(id=f'cfg-{key}', min=low, max=high, step=step, value=value,
                               marks=None, tooltip={'placement': 'bottom'})],
                    className='mb-2')


def measure(application, name):
    timings = getattr(application, 'timings', None)
    return timings.measure(name) if timings is not None else contextlib.nullcontext()


# --------------------------------------------------------------------- Layout
def build_layout(cfg):
    def stage_controls(key):
        """Regler stehen direkt unter der Bildstufe, die sie beeinflussen."""
        if key == 'roi':
            return [slider('ROI (unten / oben, Anteil)', 'crop', 0, 1, cfg['crop'], .01, True)]
        if key == 'hsv':
            return [slider('Hue', 'hue', 0, 179, cfg['hue'], 1, True),
                    slider('Saturation', 'sat', 0, 255, cfg['sat'], 1, True),
                    slider('Value', 'val', 0, 255, cfg['val'], 1, True)]
        if key == 'blur':
            return [slider('Blur-Kernel', 'blur', 1, 15, cfg['blur'])]
        if key == 'morph':
            return [slider('Morph-Kernel', 'morph', 1, 15, cfg['morph'])]
        if key == 'edges':
            return [slider('Canny-Schwellen', 'canny', 0, 255, cfg['canny'], 1, True)]
        return []

    def stage_switch(key):
        switch_key = STAGE_SWITCH.get(key)
        if switch_key is None:
            return []
        return [dbc.Checklist(id=f'cfg-enable-{switch_key}',
                              options=[{'label': 'Schritt aktiv', 'value': 'on'}],
                              value=['on'] if cfg['enabled'].get(switch_key, True) else [],
                              switch=True, className='small')]

    control_bar = dbc.Card(dbc.CardBody(dbc.Row([
        dbc.Col([dbc.Badge(id='global-camera-state', color='secondary', className='me-1'),
                 dbc.Badge(id='global-mode-state', color='secondary', className='me-1'),
                 dbc.Badge(id='global-rec-state', color='secondary', className='me-1'),
                 html.Span(id='global-frame', className='ms-1 small text-secondary')],
                xs=12, lg=4, className='mb-2 mb-lg-0'),
        dbc.Col(dcc.Dropdown(id='mode', options=MODES, value='camera', clearable=False,
                             className='c2c-dropdown'), xs=12, md=4, lg=3, className='mb-2 mb-md-0'),
        dbc.Col([dbc.Button('▶ START', id='global-start', color='success', className='me-2'),
                 dbc.Button('■ STOP', id='global-stop', color='danger', className='me-2'),
                 dbc.Button('Bergung', id='recovery-toggle', color='warning', outline=True, className='me-2'),
                 dbc.Button('● REC', id='record-toggle', color='secondary', outline=True)],
                xs=12, md=8, lg=5, className='text-lg-end'),
    ], align='center', className='g-2')), className='mb-2 c2c-control-bar')

    recovery_panel = dbc.Collapse(dbc.Alert([
        html.Strong('Manuelle Bergung: '),
        'Leertaste gedrückt halten + W/S (vor/zurück), A/D (lenken). '
        'Ohne Tastensignal stoppt der Watchdog nach 0,35 s. Nur mit Sichtkontakt verwenden.',
        html.Div(id='recovery-status', className='small mt-1'),
    ], color='warning', className='mb-2'), id='recovery-panel', is_open=False)

    cockpit = dbc.Row([
        dbc.Col(card('Live Vision · Lane Overlay', [
            html.Div([dbc.Badge(id='drive-frame-badge', color='dark', className='me-2'),
                      html.Span(id='drive-age', className='small text-secondary')], className='mb-2'),
            html.Img(id='drive-overlay', style={**IMAGE_STYLE, 'maxHeight': '68vh'}),
            html.Div('Grün/Gelb: erkannte Linien · Magenta: Reglerrichtung aus den Linien · '
                     'Instrument: Fahrzeug-Stellwert · Rohbild bleibt unverändert',
                     className='text-secondary small mt-2')]), lg=8),
        dbc.Col(card('Fahrzustand', [
            dbc.Row([
                dbc.Col([html.Div('Stellwert (Fahrzeug)', className='c2c-kpi-label'),
                         html.Div(id='drive-angle', className='c2c-kpi')], width=6),
                dbc.Col([html.Div('Speed-Steuerwert', className='c2c-kpi-label'),
                         html.Div(id='drive-speed', className='c2c-kpi')], width=6),
                dbc.Col([html.Div('Sollwinkel (Regler)', className='c2c-kpi-label'),
                         html.Div(id='drive-requested', className='c2c-kpi-small')], width=6),
                dbc.Col([html.Div('Rohwinkel (Linien)', className='c2c-kpi-label'),
                         html.Div(id='drive-raw', className='c2c-kpi-small')], width=6),
            ], className='g-2'),
            html.Hr(),
            html.Div(id='drive-mode-status', className='mb-2'),
            html.Div('Controllerdiagnose', className='c2c-kpi-label'),
            html.Div(id='drive-diagnostic', className='small'),
        ]), lg=4),
    ], className='g-3')

    pipeline = html.Div([
        dbc.Row([
            dbc.Col(html.Div(id='pipeline-lock'), lg=7),
            dbc.Col([dbc.Button('Freeze / Live', id='pipeline-freeze', color='warning', className='me-2'),
                     html.Span(id='pipeline-frame-label', className='small')], lg=5, className='text-lg-end'),
        ], className='g-2 mb-2', align='center'),
        dbc.Row([dbc.Col(card(f'{index:02d} · {title}', [
            html.Img(id=f'stage-{key}', style={**IMAGE_STYLE, 'maxHeight': '200px'}),
            html.Div(id=f'stage-info-{key}', className='small text-secondary mt-1'),
            *stage_controls(key), *stage_switch(key),
            dbc.Button('Vergrößern', id=f'inspect-{key}', color='info', outline=True, size='sm',
                       className='mt-2')]), md=6, xl=3) for index, (key, title) in enumerate(STAGES, 1)],
            className='g-3'),
        dbc.Row([
            dbc.Col(card('Pipeline Inspector', [
                html.Div(id='inspect-title', className='fw-semibold mb-2'),
                html.Img(id='inspect-image', style={**IMAGE_STYLE, 'maxHeight': '430px'}),
                html.Div('Zeigt das veröffentlichte Zwischenbild. Keine zweite Bildverarbeitung.',
                         className='text-secondary small mt-2')]), lg=7),
            dbc.Col(card('Pipeline-Konfiguration & Presets', [
                html.Label('Bildprozessor', className='small'),
                dcc.Dropdown(id='cfg-processor', className='c2c-dropdown', clearable=False,
                             value=cfg['processor'], options=[
                                 {'label': 'Mit Diagnosebildern (Copilot)', 'value': 'copilot'},
                                 {'label': 'Ohne Diagnosebilder (Legacy)', 'value': 'legacy'}]),
                html.Div([dbc.Button('Übernehmen', id='cfg-apply', size='sm', className='me-2 mt-2'),
                          dbc.Button('Als aktiv speichern', id='cfg-save', size='sm', color='success',
                                     className='mt-2')]),
                html.Div(id='cfg-message', className='small mt-2'),
                html.Hr(),
                html.Label('Preset laden', className='small'),
                dcc.Dropdown(id='preset-choice', className='c2c-dropdown', clearable=False,
                             value=ACTIVE_PRESET, options=[]),
                dbc.Button('Laden', id='preset-load', size='sm', color='info', className='mt-2'),
                html.Label('Preset speichern unter', className='small mt-3 d-block'),
                dcc.Input(id='preset-name', type='text', placeholder='z. B. halle_hell',
                          className='form-control form-control-sm', debounce=True),
                dbc.Button('Preset speichern', id='preset-save', size='sm', color='success', className='mt-2'),
                html.Div(id='preset-message', className='small mt-2')]), lg=5),
        ], className='g-3 mt-1'),
        dbc.Row([dbc.Col(card('A/B Calibration Lab · gleicher Frame', [
            html.Div('A = Referenz beim Einfrieren · B = aktuelle Slider. Keine Änderung am Fahrmodus.',
                     className='small text-secondary mb-2'),
            dbc.Button('Referenzframe einfrieren', id='ab-freeze', color='info', className='me-2'),
            dbc.Button('A/B berechnen', id='ab-run', color='primary', className='me-2'),
            dbc.Button('A/B zurücksetzen', id='ab-reset', color='secondary'),
            html.Div(id='ab-status', className='small mt-2'),
            html.Div(id='ab-source', className='small text-info'),
            dbc.Row([dbc.Col([html.H6('A · Referenz'), html.Img(id='ab-image-a', style=IMAGE_STYLE),
                              html.Div(id='ab-lanes-a', className='small mt-2')], md=6),
                     dbc.Col([html.H6('B · Kandidat'), html.Img(id='ab-image-b', style=IMAGE_STYLE),
                              html.Div(id='ab-lanes-b', className='small mt-2')], md=6)], className='g-3 mt-1'),
        ]), md=12)], className='mt-3'),
    ])

    telemetry = dbc.Row([
        dbc.Col(card('Live · Mini-Preview', html.Img(id='telemetry-preview', style=IMAGE_STYLE)), md=4),
        dbc.Col(card('Soll / Ist Frequenz', dcc.Graph(id='freq-bars', config={'displayModeBar': False})), md=8),
        dbc.Col(card('Frequenzverlauf (letzte 60 s)', dcc.Graph(id='freq-history', config={'displayModeBar': False})), md=12),
        dbc.Col(card('Lenkwinkel / Speed / Linienanzahl', dcc.Graph(id='drive-history', config={'displayModeBar': False})), md=12),
        dbc.Col(card('Datenalter · Latenzen · System', [html.Div(id='data-age'),
                                                      html.Div(id='system-stats', className='small text-secondary mt-2')]), md=12),
    ], className='g-3')

    recording = dbc.Row([
        dbc.Col(card('Live · Mini-Preview', html.Img(id='record-preview', style=IMAGE_STYLE)), md=4),
        dbc.Col(card('Aufzeichnung', [
            html.Div('Start/Stopp über den REC-Button in der Steuerleiste.', className='small text-secondary'),
            html.Div(id='record-status', className='mt-2 fw-semibold'),
            html.Div(id='session-stats', className='small text-secondary mt-2')]), md=8),
        dbc.Col(card('Ereignisprotokoll (neueste zuerst)', html.Div(id='event-list', className='c2c-events')), md=12),
    ], className='g-3')

    replay = html.Div([
        dbc.Row([
            dbc.Col(dcc.Dropdown(id='session-choice', className='c2c-dropdown', clearable=False,
                                 options=[{'label': 'Live-Ringbuffer (flüchtig)', 'value': LIVE}], value=LIVE), md=6),
            dbc.Col(dbc.Button('Sessions aktualisieren', id='session-refresh', color='secondary'), md=2),
            dbc.Col(html.Div('Replay steuert keine Motoren.', className='small text-secondary'), md=4),
        ], className='g-2 mb-2', align='center'),
        dbc.Row([
            dbc.Col(card('Frame Inspector · Replay-HUD', [
                html.Div(id='replay-frame-label', className='fw-semibold mb-2'),
                html.Img(id='replay-image', style={**IMAGE_STYLE, 'maxHeight': '60vh'}),
                html.Div(id='replay-details', className='small mt-2')]), lg=8),
            dbc.Col(card('Replay-Steuerung', [
                dbc.ButtonGroup([dbc.Button('◀', id='replay-prev'),
                                 dbc.Button('▶ Play', id='replay-play'),
                                 dbc.Button('▶|', id='replay-next')], className='mb-2'),
                dcc.Slider(id='replay-index', min=0, max=1, step=1, value=0, marks=None,
                           tooltip={'placement': 'bottom'}),
                html.Div(id='replay-range', className='small text-secondary mt-2'),
                html.Hr(),
                html.Label('Zu Ereignis springen', className='small'),
                dcc.Dropdown(id='replay-event', className='c2c-dropdown', options=[], value=None),
                dbc.Row([dbc.Col([html.Label('Vorlauf (Frames)', className='small'),
                                  dcc.Input(id='replay-pre', type='number', min=0, step=1, value=30,
                                            className='form-control form-control-sm')]),
                         dbc.Col([html.Label('Nachlauf (Frames)', className='small'),
                                  dcc.Input(id='replay-post', type='number', min=0, step=1, value=30,
                                            className='form-control form-control-sm')])], className='g-2 mt-1'),
                dbc.Button('Bereich zurücksetzen', id='replay-range-reset', color='secondary', size='sm',
                           className='mt-2 me-2'),
                dbc.Button('Bereich exportieren', id='replay-export', color='success', size='sm', className='mt-2'),
                dcc.Download(id='replay-download'),
                html.Hr(className='my-2'),
                dbc.Button('Frame im Pipeline Studio analysieren', id='replay-to-studio',
                           color='info', size='sm'),
                html.Div(id='replay-studio-msg', className='small mt-1')]), lg=4),
        ], className='g-3'),
        dbc.Row([dbc.Col(card('Zeitliche Navigation', [
            dcc.Graph(id='replay-chart', config={'displayModeBar': False}),
            html.Div('Klick auf Messpunkt = Frame wählen · Ziehen = Bereich wählen · Doppelklick = alles',
                     className='small text-secondary')]), md=12)], className='mt-3'),
    ])

    return dbc.Container([
        html.H2('Camp2Code · Control Center', className='mt-3 mb-2'),
        control_bar,
        html.Div(id='global-action-message', className='small mb-2'),
        recovery_panel,
        dbc.Tabs([dbc.Tab(cockpit, label='Driving Cockpit', tab_id='drive'),
                  dbc.Tab(pipeline, label='Pipeline Studio', tab_id='pipeline'),
                  dbc.Tab(telemetry, label='Telemetrie & Performance', tab_id='telemetry'),
                  dbc.Tab(recording, label='Recording & Events', tab_id='record'),
                  dbc.Tab(replay, label='Replay Lab', tab_id='replay')],
                 id='main-tabs', active_tab='drive', className='mb-3'),
        dcc.Store(id='last-action', data=None, storage_type='memory'),
        dcc.Store(id='recovery-keys', data={'deadman': False}, storage_type='memory'),
        dcc.Store(id='replay-playing', data=False, storage_type='memory'),
        dcc.Store(id='replay-bounds', data=None, storage_type='memory'),
        dcc.Store(id='pipeline-frozen', data=None, storage_type='memory'),
        dcc.Store(id='replay-studio-token', data=None, storage_type='memory'),
        dcc.Store(id='inspected-stage', data='overlay', storage_type='memory'),
        dcc.Store(id='drive-rendered', data=None, storage_type='memory'),
        dcc.Store(id='pipeline-rendered', data=None, storage_type='memory'),
        # Globale Statusleiste; Ansichten haben eigene Takte, nur der sichtbare Tab ist aktiv.
        dcc.Interval(id='refresh', interval=700, n_intervals=0),
        *[dcc.Interval(id=f'tick-{tab}', interval=700, n_intervals=0, disabled=tab != 'drive') for tab in TABS],
        dcc.Interval(id='recovery-pulse', interval=120, n_intervals=0, disabled=True),
    ], fluid=True)


# --------------------------------------------------------------------- App
def create_app(application):
    cfg = load()
    ab = PipelineAB()
    thread_cpu = ThreadCpuSampler()  # PAKET-C
    app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY], suppress_callback_exceptions=False,
               title='Camp2Code | Control Center', update_title=None)
    app.layout = build_layout(cfg)
    car = application.car

    # ------------------------------------------------ Aktionen (ein Callback)
    @app.callback(Output('last-action', 'data'),
                  Input('global-start', 'n_clicks'), Input('global-stop', 'n_clicks'),
                  Input('recovery-toggle', 'n_clicks'), Input('record-toggle', 'n_clicks'),
                  State('mode', 'value'), prevent_initial_call=True)
    def handle_actions(_start, _stop, _recovery, _record, mode):
        trigger = ctx.triggered_id
        try:
            if trigger == 'global-stop':
                application.stop_all()
                text = 'STOP ausgeführt'
            elif trigger == 'global-start':
                application.start_mode(mode)
                text = f'Fahrmodus {mode} gestartet'
            elif trigger == 'recovery-toggle':
                if application.recovery.enabled:
                    application.disable_recovery()
                    text = 'Bergung beendet'
                else:
                    application.enable_recovery()
                    text = 'Bergung aktiv'
            elif trigger == 'record-toggle':
                text = 'Aufzeichnung läuft' if application.toggle_recording() else 'Aufzeichnung gestoppt'
            else:
                return no_update
            return {'text': text, 'ok': True, 'time': time.strftime('%H:%M:%S')}
        except Exception as exc:
            log.warning('Aktion %s abgewiesen: %s', trigger, exc)
            return {'text': f'Aktion abgewiesen: {exc}', 'ok': False, 'time': time.strftime('%H:%M:%S')}

    @app.callback(*[Output(f'tick-{tab}', 'disabled') for tab in TABS],
                  Input('main-tabs', 'active_tab'), Input('refresh', 'disabled'))
    def tab_intervals(active_tab, paused):
        """Nur der sichtbare Tab pollt; im Hintergrund-Browsertab pausiert alles."""
        return tuple(bool(paused) or tab != active_tab for tab in TABS)

    # ------------------------------------------------ Globale Steuerleiste
    @app.callback(Output('global-camera-state', 'children'), Output('global-camera-state', 'color'),
                  Output('global-mode-state', 'children'), Output('global-mode-state', 'color'),
                  Output('global-rec-state', 'children'), Output('global-rec-state', 'color'),
                  Output('global-frame', 'children'),
                  Output('global-start', 'disabled'), Output('global-stop', 'outline'),
                  Output('recovery-toggle', 'children'), Output('recovery-toggle', 'outline'),
                  Output('recovery-panel', 'is_open'), Output('recovery-pulse', 'disabled'),
                  Output('record-toggle', 'children'), Output('record-toggle', 'color'),
                  Output('record-toggle', 'outline'), Output('mode', 'disabled'),
                  Output('global-action-message', 'children'),
                  Input('refresh', 'n_intervals'), Input('last-action', 'data'))
    def global_status(_tick, last_action):
        camera = car.camera_data
        has_frame = camera is not None and camera.frame_id >= 0
        camera_ok = application.camera_alive() if hasattr(application, 'camera_alive') else has_frame
        running = application.running()
        recovery = application.recovery.enabled
        recording = application.recorder.is_running()
        stop_reason = application.stop_reason() if hasattr(application, 'stop_reason') else None
        if running:
            status = f'Fahrt aktiv · Modus {application.active_mode}'
        elif recovery:
            status = 'Bergung aktiv · autonomer Fahrmodus gesperrt'
        else:
            status = 'Fahrmodus aus' + (f' · letzte Stoppursache: {stop_reason}' if stop_reason else '')
        message = [html.Span(status, className='me-3')]
        if last_action:
            message.append(html.Span(f"{last_action.get('time')} · {last_action.get('text')}",
                                     className='text-success' if last_action.get('ok') else 'text-warning'))
        camera_label = ('KAMERA LIVE' if camera_ok and has_frame else
                        'KAMERA WARTET' if camera_ok else 'KAMERA AUSGEFALLEN')
        return (camera_label, 'info' if camera_ok and has_frame else 'danger' if not camera_ok else 'secondary',
                'FAHRT AKTIV' if running else 'BERGUNG' if recovery else 'FAHRMODUS AUS',
                'success' if running else 'warning' if recovery else 'secondary',
                'REC ●' if recording else 'REC AUS', 'danger' if recording else 'secondary',
                f'Frame #{camera.frame_id}' if has_frame else '',
                running or recovery, not (running or recovery),
                'Bergung beenden' if recovery else 'Bergung aktivieren', not recovery,
                recovery, not recovery,
                '■ REC stoppen' if recording else '● REC starten', 'danger' if recording else 'secondary',
                not recording, running,
                message)

    @app.callback(Output('recovery-status', 'children'),
                  Input('recovery-pulse', 'n_intervals'), State('recovery-keys', 'data'))
    def recovery_pulse(_, keys):
        if not application.recovery.enabled:
            return 'Bergung inaktiv'
        keys = keys or {}
        try:
            application.recovery.command(
                forward=bool(keys.get('w')), reverse=bool(keys.get('s')),
                left=bool(keys.get('a')), right=bool(keys.get('d')),
                deadman=bool(keys.get('deadman')))
            return 'Deadman gedrückt' if keys.get('deadman') else 'Bereit · Leertaste halten zum Fahren'
        except RuntimeError as exc:
            return str(exc)

    # ------------------------------------------------ Driving Cockpit
    @app.callback(Output('drive-overlay', 'src'), Output('drive-rendered', 'data'),
                  Output('drive-angle', 'children'), Output('drive-speed', 'children'),
                  Output('drive-requested', 'children'), Output('drive-raw', 'children'),
                  Output('drive-mode-status', 'children'), Output('drive-diagnostic', 'children'),
                  Output('drive-frame-badge', 'children'), Output('drive-age', 'children'),
                  Input('tick-drive', 'n_intervals'), Input('main-tabs', 'active_tab'),
                  State('drive-rendered', 'data'))
    def cockpit_view(_, tab, rendered):
        if tab != 'drive':
            return (no_update,) * 10
        with measure(application, 'dashboard.cockpit'):
            camera = car.camera_data
            running = application.running()
            diagnostic = application.last_diagnostic() if hasattr(application, 'last_diagnostic') else None
            stop_reason = application.stop_reason() if hasattr(application, 'stop_reason') else None
            record = car.frame_history.latest_complete() if running else None
            if record is not None:
                key = f'drive:{record.camera.frame_id}'
                shown = record.camera
            else:
                key = f'cal:{getattr(camera, "frame_id", None)}'
                shown = camera
            image = no_update
            if key != rendered and shown is not None and shown.frame_id >= 0:
                overlay = (render_overlay(record.camera, record.driving) if record is not None
                           else render_calibration_overlay(camera))
                image = uri(draw_steering_overlay(overlay, car.steering_angle))
            else:
                key = rendered
            age = '–'
            if shown is not None and getattr(shown, 'monotonic_ns', 0):
                age = f'Datenalter {(time.monotonic_ns() - shown.monotonic_ns) / 1e6:.0f} ms'
                if record is not None and record.driving.monotonic_ns:
                    age += f' · Entscheidung nach {(record.driving.monotonic_ns - shown.monotonic_ns) / 1e6:.0f} ms'
            mode_badge = dbc.Badge(f'Modus: {application.active_mode}' if running else 'Modus: aus',
                                   color='success' if running else 'secondary')
            return (image, key, fmt(car.steering_angle, 0, '°'), fmt(car.speed),
                    fmt(diagnostic.get('requested_angle'), 0, '°') if running and diagnostic else '–',
                    fmt(diagnostic.get('raw_angle'), 0, '°') if running and diagnostic else '–',
                    mode_badge, diagnostic_text(diagnostic, stop_reason, running),
                    f'Frame #{shown.frame_id}' if shown is not None else '', age)

    # ------------------------------------------------ Pipeline Studio
    @app.callback(Output('cfg-message', 'children'), Input('cfg-apply', 'n_clicks'),
                  Input('cfg-save', 'n_clicks'),
                  Input('cfg-processor', 'value'), Input('cfg-crop', 'value'),
                  Input('cfg-hue', 'value'), Input('cfg-sat', 'value'),
                  Input('cfg-val', 'value'), Input('cfg-blur', 'value'),
                  Input('cfg-morph', 'value'), Input('cfg-canny', 'value'),
                  *[Input(f'cfg-enable-{name}', 'value') for name in SWITCHES],
                  prevent_initial_call=True)
    def configure(_apply, _save, processor, crop, hue, sat, val, blur, morph, canny, *switches):
        settings = settings_from_controls(processor, crop, hue, sat, val, blur, morph, canny, switches)
        try:
            if ctx.triggered_id == 'cfg-save':
                application.save_pipeline(settings)
                return 'Als aktive Konfiguration gespeichert (pipeline_studio.json)'
            application.set_pipeline(settings)
            return 'Live übernommen · dauerhaft mit „Als aktiv speichern“'
        except Exception as exc:
            return f'Nicht übernommen: {exc}'

    @app.callback(Output('cfg-processor', 'value'), Output('cfg-crop', 'value'),
                  Output('cfg-hue', 'value'), Output('cfg-sat', 'value'), Output('cfg-val', 'value'),
                  Output('cfg-blur', 'value'), Output('cfg-morph', 'value'), Output('cfg-canny', 'value'),
                  *[Output(f'cfg-enable-{name}', 'value') for name in SWITCHES],
                  Output('preset-choice', 'options'), Output('preset-message', 'children'),
                  Input('preset-load', 'n_clicks'), Input('preset-save', 'n_clicks'),
                  Input('main-tabs', 'active_tab'),
                  State('preset-choice', 'value'), State('preset-name', 'value'),
                  State('cfg-processor', 'value'), State('cfg-crop', 'value'),
                  State('cfg-hue', 'value'), State('cfg-sat', 'value'), State('cfg-val', 'value'),
                  State('cfg-blur', 'value'), State('cfg-morph', 'value'), State('cfg-canny', 'value'),
                  *[State(f'cfg-enable-{name}', 'value') for name in SWITCHES])
    def preset_actions(_load, _save, _tab, choice, name, processor, crop, hue, sat, val, blur, morph, canny, *switches):
        options = [{'label': 'Aktive Konfiguration (pipeline_studio.json)', 'value': ACTIVE_PRESET}] + [
            {'label': preset, 'value': preset} for preset in list_presets()]
        keep = (no_update,) * (8 + len(SWITCHES))
        trigger = ctx.triggered_id
        try:
            if trigger == 'preset-save':
                settings = settings_from_controls(processor, crop, hue, sat, val, blur, morph, canny, switches)
                save_preset(name, settings)
                options = [options[0]] + [{'label': preset, 'value': preset} for preset in list_presets()]
                return (*keep, options, f'Preset „{name}“ gespeichert')
            if trigger == 'preset-load':
                if application.running():
                    return (*keep, options, 'Laden nur im Stillstand')
                data = load() if choice in (None, ACTIVE_PRESET) else load_preset(choice)
                values = (data['processor'], data['crop'], data['hue'], data['sat'], data['val'],
                          data['blur'], data['morph'], data['canny'],
                          *[['on'] if data['enabled'].get(s, True) else [] for s in SWITCHES])
                return (*values, options, f'Geladen: {choice or ACTIVE_PRESET} · wird live übernommen')
        except (ValueError, OSError, KeyError) as exc:
            return (*keep, options, f'Preset-Fehler: {exc}')
        return (*keep, options, no_update)

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

    @app.callback(Output('inspected-stage', 'data'),
                  *[Input(f'inspect-{key}', 'n_clicks') for key, _ in STAGES],
                  prevent_initial_call=True)
    def inspect(*_):
        button = ctx.triggered_id
        return button.removeprefix('inspect-') if button else 'overlay'

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
    @app.callback(*[Output(f'stage-{key}', 'src') for key, _ in STAGES],
                  *[Output(f'stage-info-{key}', 'children') for key, _ in STAGES],
                  Output('inspect-image', 'src'), Output('inspect-title', 'children'),
                  Output('pipeline-frame-label', 'children'), Output('pipeline-lock', 'children'),
                  Output('pipeline-rendered', 'data'),
                  Input('tick-pipeline', 'n_intervals'), Input('inspected-stage', 'data'),
                  Input('pipeline-frozen', 'data'),
                  Input('main-tabs', 'active_tab'), State('pipeline-rendered', 'data'))
    def pipeline_view(_, inspected, frozen, tab, rendered):
        count = 2 * len(STAGES) + 5
        if tab != 'pipeline':
            return (no_update,) * count
        with measure(application, 'dashboard.pipeline'):
            camera = car.camera_data
            running = application.running()
            lock = dbc.Alert('Fahrt aktiv: Änderungen gesperrt, Anzeige läuft weiter.', color='warning',
                             className='mb-0 py-2') if running else dbc.Alert(
                'Kalibrierung im Stillstand. LIVE folgt der Kamera, FREEZE hält einen Frame aus dem Ringbuffer.',
                color='secondary', className='mb-0 py-2')
            replay_view = replay_pipeline_view(frozen, inspected, rendered, lock)  # PAKET-D
            if replay_view is not None:
                return replay_view
            if camera is None or camera.frame_id < 0:
                return (*([None] * len(STAGES)), *([''] * len(STAGES)), None, 'Warte auf Kamera', '', lock, None)
            pipeline_camera = camera
            if frozen is not None:
                match = car.frame_history.find(frozen)
                if match is not None:
                    pipeline_camera = match.camera
                else:
                    frozen = None
            key = f'{pipeline_camera.frame_id}:{inspected}'
            label = f'{"FREEZE" if frozen is not None else "LIVE"} · Frame {pipeline_camera.frame_id}'
            if key == rendered:
                return (*([no_update] * (2 * len(STAGES) + 2)), label, lock, no_update)
            stages = dict(getattr(pipeline_camera, 'stages', {}) or {})
            stages['raw'] = pipeline_camera.raw_image
            stages['overlay'] = render_calibration_overlay(pipeline_camera)
            encoded = {name: uri(stages.get(name)) for name, _ in STAGES}
            infos = [('Verfügbar' if encoded[name] else
                      'Schritt deaktiviert oder ohne Diagnosebild (Legacy-Prozessor)') for name, _ in STAGES]
            title = dict(STAGES).get(inspected, inspected)
            return (*[encoded[name] for name, _ in STAGES], *infos, encoded.get(inspected), title,
                    label, lock, key)

    @app.callback(Output('ab-status', 'children'), Output('ab-image-a', 'src'),
                  Output('ab-image-b', 'src'), Output('ab-lanes-a', 'children'),
                  Output('ab-lanes-b', 'children'),
                  Input('ab-freeze', 'n_clicks'), Input('ab-run', 'n_clicks'),
                  Input('ab-reset', 'n_clicks'),
                  State('cfg-processor', 'value'), State('cfg-crop', 'value'),
                  State('cfg-hue', 'value'), State('cfg-sat', 'value'),
                  State('cfg-val', 'value'), State('cfg-blur', 'value'),
                  State('cfg-morph', 'value'), State('cfg-canny', 'value'),
                  *[State(f'cfg-enable-{name}', 'value') for name in SWITCHES],
                  prevent_initial_call=True)
    def ab_calibrate(_freeze, _run, _reset, processor, crop, hue, sat, val, blur, morph, canny, *switches):
        trigger = ctx.triggered_id
        if trigger == 'ab-reset':
            ab.clear()
            return 'A/B zurückgesetzt', None, None, '', ''
        params = parameters_from_settings(
            settings_from_controls(processor, crop, hue, sat, val, blur, morph, canny, switches))
        try:
            if trigger == 'ab-freeze':
                frame = ab.freeze(car.camera_data, params, processor)
                return f'Referenz A: Frame {frame} eingefroren', None, None, '', ''
            comparison = ab.compare(params, processor)
            return (f'A/B auf Frame {comparison.frame_id} berechnet',
                    uri(comparison.image_a), uri(comparison.image_b),
                    f'Linien A: {len(comparison.lanes_a)}',
                    f'Linien B: {len(comparison.lanes_b)}')
        except (ValueError, TypeError, cv2.error) as exc:
            return f'A/B nicht möglich: {exc}', no_update, no_update, no_update, no_update

    # ------------------------------------------------ Telemetrie
    @app.callback(Output('freq-bars', 'figure'), Output('freq-history', 'figure'),
                  Output('drive-history', 'figure'), Output('data-age', 'children'),
                  Output('system-stats', 'children'), Output('telemetry-preview', 'src'),
                  Input('tick-telemetry', 'n_intervals'), Input('main-tabs', 'active_tab'))
    def telemetry_view(_, tab):
        if tab != 'telemetry':
            return (no_update,) * 6
        with measure(application, 'dashboard.telemetry'):
            rows, freqs, _events = application.sample()
            camera = car.camera_data
            metrics = car.thread_metrics
            running = application.running()
            camera_target = float(application.sensor.scan_frequency)
            actual = [metrics[key].hz if metrics[key].age_seconds is not None and metrics[key].age_seconds < 2 else 0
                      for key in ('camera', 'controller')]
            # Der Regler entscheidet einmal je neuem Kameraframe; Soll = Kamerafrequenz.
            targets = [camera_target, camera_target if running else 0.0]
            bars = go.Figure()
            bars.add_bar(name='Soll', x=['Kamera', 'Entscheidungen'], y=targets, marker_color='#6c7a92')
            bars.add_bar(name='Ist', x=['Kamera', 'Entscheidungen'], y=actual, marker_color='#2fbf8f')
            dark_figure(bars, 240, barmode='group', yaxis_title='Hz', uirevision='bars')
            fplot = go.Figure()
            if freqs:
                end = freqs[-1][0]
                recent = [r for r in freqs if end - r[0] <= 60]
                for i, name in ((1, 'Kamera'), (2, 'Entscheidungen')):
                    fplot.add_scatter(x=[r[0] - end for r in recent], y=[r[i] for r in recent],
                                      name=name, mode='lines')
            dark_figure(fplot, 240, xaxis_title='Sekunden', yaxis_title='Hz', uirevision='freq')
            dplot = go.Figure()
            if rows:
                end = rows[-1][0]
                x = [r[0] - end for r in rows]
                dplot.add_scatter(x=x, y=[r[2] for r in rows], name='Rohwinkel', mode='lines')
                dplot.add_scatter(x=x, y=[r[6] if len(r) > 6 else None for r in rows], name='Stellwert', mode='lines')
                dplot.add_scatter(x=x, y=[r[3] for r in rows], name='Speed-Steuerwert', mode='lines')
                dplot.add_scatter(x=x, y=[r[5] if len(r) > 5 else None for r in rows], name='Linienanzahl',
                                  mode='lines', line={'shape': 'hv'}, yaxis='y2')
            dark_figure(dplot, 300, xaxis_title='Sekunden', yaxis_title='Grad / Steuerwert',
                        yaxis2=dict(overlaying='y', side='right', range=[0, 3], title='Linien'),
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

    # ------------------------------------------------ Recording & Events
    @app.callback(Output('record-status', 'children'), Output('session-stats', 'children'),
                  Output('event-list', 'children'), Output('record-preview', 'src'),
                  Input('tick-record', 'n_intervals'), Input('last-action', 'data'),
                  Input('main-tabs', 'active_tab'))
    def recording_view(_, _action, tab):
        if tab != 'record':
            return (no_update,) * 4
        recorder = application.recorder
        stats = recorder.stats() if hasattr(recorder, 'stats') else {
            'frames': getattr(recorder, 'count', 0), 'path': getattr(recorder, 'path', None),
            'error': getattr(recorder, 'error', None)}
        status = 'REC ● Aufzeichnung läuft' if recorder.is_running() else 'Keine Aufzeichnung aktiv'
        details = (f"{stats.get('frames', 0)} Frames · Lücken {stats.get('gap_frames', 0)} · "
                   f"fehlende Entscheidungen {stats.get('missing_decisions', 0)} · "
                   f"{stats.get('path') or 'keine Session'} · {stats.get('error') or 'kein Fehler'}")
        now = time.monotonic_ns()
        events = list(application.health.events())[-25:][::-1]
        colors = {'STOP_REASON': 'text-danger', 'CONTROLLER_STOPPED': 'text-warning',
                  'LANE_LOST': 'text-warning', 'RECORDER_FRAME_GAP': 'text-danger'}
        items = [html.Div([html.Span(f'vor {(now - e.timestamp_ns) / 1e9:5.1f} s', className='text-secondary me-2'),
                           html.Span(f'Frame {e.frame_id}' if e.frame_id is not None else 'Frame –', className='me-2'),
                           html.Strong(e.code, className=colors.get(e.code, '') + ' me-2'),
                           html.Span(e.detail, className='small')]) for e in events] or 'Keine Ereignisse'
        camera = car.camera_data
        preview = None
        if camera is not None and camera.frame_id >= 0:
            image = render_calibration_overlay(camera)
            if image is not None:
                h, w = image.shape[:2]
                scale = min(1.0, 320 / max(1, w))
                preview = uri(cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale)))), 60)
        return status, details, items, preview

    # ------------------------------------------------ Replay Lab
    def replay_records(name, bounds):
        """Liefert (Datensaetze, Ereignisse, Pfad) gefiltert auf den Frame-ID-Bereich."""
        bounds = tuple(bounds) if bounds else None
        if not name or name == LIVE:
            events = [{'frame_id': e.frame_id, 'code': e.code, 'detail': e.detail}
                      for e in application.health.events() if e.frame_id is not None]
            return select_records(car.frame_history.snapshot(), bounds), events, None
        try:
            path, rows = load_session(name)
            events = load_events(name)
        except (ValueError, OSError):
            return (), [], None
        return select_records(rows, bounds), events, path

    @app.callback(Output('session-choice', 'options'), Input('session-refresh', 'n_clicks'),
                  Input('last-action', 'data'))
    def refresh_sessions(_, _action):
        return [{'label': 'Live-Ringbuffer (flüchtig)', 'value': LIVE}] + [
            {'label': name, 'value': name} for name in list_sessions()]

    @app.callback(Output('replay-event', 'options'), Output('replay-event', 'value'),
                  Input('session-choice', 'value'))
    def replay_event_options(name):
        _, events, _ = replay_records(name, None)
        options = []
        for event in events[-300:]:
            try:
                frame_id = int(event.get('frame_id'))
            except (TypeError, ValueError):
                continue
            options.append({'label': f"Frame {frame_id} · {event.get('code')}", 'value': frame_id})
        return options, None

    @app.callback(Output('replay-index', 'value'), Output('replay-bounds', 'data'),
                  Input('replay-prev', 'n_clicks'), Input('replay-next', 'n_clicks'),
                  Input('replay-chart', 'clickData'), Input('replay-chart', 'relayoutData'),
                  Input('replay-event', 'value'), Input('replay-range-reset', 'n_clicks'),
                  Input('tick-replay', 'n_intervals'),
                  State('replay-index', 'value'), State('replay-playing', 'data'),
                  State('session-choice', 'value'), State('replay-bounds', 'data'),
                  State('replay-pre', 'value'), State('replay-post', 'value'),
                  State('main-tabs', 'active_tab'))
    def seek(_prev, _next, click, relayout, event_frame, _reset, _tick, index, playing, name, bounds,
             pre, post, tab):
        trigger = ctx.triggered_id
        if tab != 'replay' and trigger == 'tick-replay':
            return no_update, no_update
        if trigger == 'replay-range-reset':
            return 0, None
        if trigger == 'replay-chart' and ctx.triggered and ctx.triggered[0]['prop_id'].endswith('relayoutData'):
            if not isinstance(relayout, dict) or not any(k.startswith('xaxis') for k in relayout):
                return no_update, no_update
            chosen = selected_bounds(relayout)
            return 0, list(chosen) if chosen else None
        if trigger == 'replay-event' and event_frame is not None:
            window = event_window(event_frame, pre, post)
            records, _, _ = replay_records(name, window)
            ids = [record_frame_id(r) for r in records]
            position = next((i for i, fid in enumerate(ids) if fid is not None and fid >= event_frame), 0)
            return position, list(window)
        records, _, _ = replay_records(name, bounds)
        if not records:
            return 0, no_update
        index = max(0, min(int(index or 0), len(records) - 1))
        if trigger == 'replay-prev':
            return max(0, index - 1), no_update
        if trigger == 'replay-next':
            return min(len(records) - 1, index + 1), no_update
        if trigger == 'replay-chart' and click and click.get('points'):
            fid = click['points'][0].get('customdata')
            return next((i for i, r in enumerate(records) if record_frame_id(r) == fid), index), no_update
        if trigger == 'tick-replay' and playing:
            return min(len(records) - 1, index + 1), no_update
        return no_update, no_update

    @app.callback(Output('replay-index', 'max'), Output('replay-range', 'children'),
                  Input('replay-bounds', 'data'), Input('session-choice', 'value'),
                  Input('tick-replay', 'n_intervals'), State('main-tabs', 'active_tab'))
    def replay_limits(bounds, name, _, tab):
        if tab != 'replay' and ctx.triggered_id == 'tick-replay':
            return no_update, no_update
        records, _, _ = replay_records(name, bounds)
        source = 'Live-Ringbuffer (gleitend)' if name in (None, LIVE) else 'gespeicherte Session'
        selection = f' · Bereich Frame {int(bounds[0])}–{int(bounds[1])}' if bounds else ''
        return max(0, len(records) - 1), f'{len(records)} Frames · {source}{selection}'

    @app.callback(Output('replay-playing', 'data'), Input('replay-play', 'n_clicks'),
                  State('replay-playing', 'data'), prevent_initial_call=True)
    def replay_toggle(_, playing):
        return not bool(playing)

    @app.callback(Output('replay-play', 'children'), Input('replay-playing', 'data'))
    def replay_play_label(playing):
        return '⏸ Pause' if playing else '▶ Play'

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
                  Output('ab-source', 'children'), Output('replay-studio-token', 'data'),
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
            return no_update, f'Übergabe nicht möglich: {exc}', no_update, no_update
        source = 'Live-Ringbuffer' if name in (None, LIVE) else name
        return ('pipeline', f'Frame {frame_id} an das Pipeline Studio übergeben',
                f'Referenz A = Frame {frame_id} aus {source} mit den damaligen Parametern. '
                '„A/B berechnen“ vergleicht mit den aktuellen Slidern (B).',
                {'frame_id': frame_id, 'source': source, 'nonce': time.time()})

    @app.callback(Output('replay-download', 'data'), Input('replay-export', 'n_clicks'),
                  State('session-choice', 'value'), State('replay-bounds', 'data'),
                  prevent_initial_call=True)
    def export_range(_, name, bounds):
        lo, hi = (int(bounds[0]), int(bounds[1])) if bounds else (None, None)
        try:
            if name in (None, LIVE):
                records = select_records(car.frame_history.snapshot(), tuple(bounds) if bounds else None)
                if not records:
                    return no_update
                target = export_live_records(records)
            else:
                target = export_session_range(name, lo, hi)
        except (ValueError, OSError) as exc:
            log.warning('Export fehlgeschlagen: %s', exc)
            return no_update
        return dcc.send_file(str(target))

    return app


def main():
    logging.basicConfig(level=logging.INFO)
    # Jede Dash-Anfrage wuerde sonst eine Logzeile erzeugen (ca. 10 pro Sekunde).
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    # PAKET-D: Fahrbefehls-Meldungen ("Changed speed ...") nur mit C2C_LOG=INFO oder DEBUG
    _level = getattr(logging, os.environ.get('C2C_LOG', 'WARNING').upper(), logging.WARNING)
    for _name in ('BaseCar', 'SonicCar', 'SensorCar', 'CameraCar', 'picamera2'):
        logging.getLogger(_name).setLevel(_level)
    application = CarApplication()
    atexit.register(application.shutdown)

    def _terminate(signum, _frame):
        # SIGTERM (z. B. systemd, kill) beendet sonst den Prozess ohne Motorstopp.
        log.warning('Signal %s empfangen, beende Anwendung', signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _terminate)
    try:
        create_app(application).run(host='0.0.0.0', port=8050, debug=False, use_reloader=False)
    finally:
        application.shutdown()


if __name__ == '__main__':
    main()
