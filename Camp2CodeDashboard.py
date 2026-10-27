"""Eine Dash-Anwendung mit vier Ansichten und gemeinsamem Fahrzeugzustand."""
import atexit
import base64
import logging
import threading
import time
import io
import json
import zipfile
import csv
import cv2
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, dcc, html, ctx, no_update
from car_application import CarApplication
from dashboard_overlay import render_overlay, render_calibration_overlay
from pipeline_config import load
from pipeline_ab import PipelineAB
from dashboard_steering import draw_steering_overlay
from session_recorder import SessionRecorder, list_sessions, load_session
from replay_hud import draw_hud

log = logging.getLogger(__name__)
STAGES = [('raw', 'Rohbild'), ('roi', 'ROI'), ('hsv', 'HSV'), ('mask', 'Farbmaske'),
          ('blur', 'Blur'), ('morph', 'Morphologie'), ('edges', 'Canny'), ('overlay', 'Linien')]

def uri(image):
    if image is None or getattr(image, 'size', 0) == 0:
        return None
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii') if ok else None

def slider(label, key, low, high, value, step=1, pair=False):
    component = dcc.RangeSlider if pair else dcc.Slider
    return html.Div([html.Label(label), component(id=f'cfg-{key}', min=low, max=high,
        step=step, value=value)], className='mb-3')

def create_app(application):
    cfg = load()
    ab = PipelineAB()
    sessions = SessionRecorder(application.car, event_source=application.health.events)
    atexit.register(sessions.stop)
    app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY], suppress_callback_exceptions=False)
    app.title = 'Camp2Code | Cockpit'
    image_style = {'width': '100%', 'background': '#101827', 'borderRadius': '8px'}
    def card(title, content):
        return dbc.Card([dbc.CardHeader(title), dbc.CardBody(content)], className='h-100')
    cockpit = html.Div([
        dbc.Row([
            dbc.Col(card('Live Vision · Lane Overlay', [
                html.Div([dbc.Badge('KAMERA', color='success', className='me-2'),
                          dbc.Badge(id='drive-camera-badge', color='secondary'),
                          html.Span(id='drive-frame-badge', className='ms-2 text-secondary small')], className='mb-2'),
                html.Img(id='drive-overlay', style={**image_style, 'maxHeight': '65vh', 'objectFit': 'contain'}),
                html.Div('Fahrspuren und Lenkrichtung · Rohbild wird nicht verändert',
                         className='text-secondary small mt-2')]), lg=8),
            dbc.Col(card('Vehicle Control', [
                html.Label('Driving Mode', className='text-secondary small'),
                dcc.Dropdown(id='mode', options=[
                    {'label': 'CameraMode', 'value': 'camera'},
                    {'label': 'PSControllerMode', 'value': 'gamepad'}],
                    value='camera', clearable=False),
                html.Div([dbc.Button('▶ Start', id='drive-start', color='success', className='me-2 mt-3'),
                          dbc.Button('■ STOP', id='drive-stop', color='danger', className='mt-3')]),
                html.Div(id='drive-message', className='text-secondary small mt-3'),
                dbc.Row([dbc.Col([html.Div('Lenkwinkel', className='text-secondary small'),
                                  html.H2(id='drive-angle')], width=6),
                         dbc.Col([html.Div('Speed-Steuerwert', className='text-secondary small'),
                                  html.H2(id='drive-speed')], width=6)], className='mt-4'),
                html.Hr(),
                html.Div(id='drive-status', className='small'),
                html.Div(id='drive-diagnostic', className='small text-warning mt-2'),
                html.Div(id='drive-mode-status', className='mt-2')]), lg=4)
        ], className='g-3')])
    def stage_controls(key):
        """Zeigt Regler direkt unter der Bildstufe, die sie beeinflussen."""
        if key == 'roi':
            return [slider('ROI', 'crop', 0, 1, cfg['crop'], .01, True)]
        if key == 'hsv':
            return [slider('Hue', 'hue', 0, 179, cfg['hue'], 1, True),
                    slider('Saturation', 'sat', 0, 255, cfg['sat'], 1, True),
                    slider('Value', 'val', 0, 255, cfg['val'], 1, True)]
        if key == 'blur':
            return [slider('Blur', 'blur', 1, 15, cfg['blur'])]
        if key == 'morph':
            return [slider('Morphologie', 'morph', 1, 15, cfg['morph'])]
        if key == 'edges':
            return [slider('Canny', 'canny', 0, 255, cfg['canny'], 1, True)]
        return []

    def stage_switch(key):
        switch_key = {'overlay': 'lanes', 'edges': 'canny'}.get(key, key)
        if switch_key not in ('roi', 'mask', 'blur', 'morph', 'canny', 'lanes'):
            return []
        return [dbc.Checklist(id=f'cfg-enable-{switch_key}',
            options=[{'label': 'Schritt aktiv', 'value': 'on'}],
            value=['on'] if cfg['enabled'].get(switch_key, True) else [], switch=True)]

    pipeline = html.Div([
        dbc.Row([dbc.Col(dbc.Alert('LIVE: Bilder folgen der Kamera. FREEZE: Ein Frame bleibt für die Analyse stehen.', color='secondary'), md=8),
                 dbc.Col([dbc.Button('Freeze / Live', id='pipeline-freeze', color='warning', className='me-2'),
                          html.Span(id='pipeline-frame-label')], md=4)], className='g-2 mb-2'),
        dbc.Alert('Kalibrierung ohne Fahrmodus. Während einer Fahrt sind Änderungen gesperrt.', color='info'),
        dbc.Row([dbc.Col(card(f'{index:02d} · {title}', [
            html.Img(id=f'stage-{key}', style={**image_style, 'maxHeight': '220px', 'objectFit': 'contain'}),
            html.Div(id=f'stage-info-{key}', className='small text-secondary mt-2'),
            *stage_controls(key), *stage_switch(key),
            dbc.Button('Vergrößern', id=f'inspect-{key}', color='outline-info',
                       size='sm', className='mt-2')]), md=6, xl=3)
                 for index, (key, title) in enumerate(STAGES, 1)], className='g-3'),
        dbc.Row([dbc.Col(card('Pipeline Inspector', [
            html.Div(id='inspect-title', className='fw-semibold mb-2'),
            html.Img(id='inspect-image', style={**image_style, 'maxHeight': '430px', 'objectFit': 'contain'}),
            html.Div('Der Inspector zeigt das veröffentlichte Zwischenbild. Keine zweite Bildverarbeitung.',
                     className='text-secondary small mt-2')]), md=12)], className='mt-3'),
        dbc.Row([dbc.Col(card('A/B Calibration Lab · gleicher Frame', [
            html.Div('A = Referenz beim Einfrieren · B = aktuelle Slider. Keine Änderung am Fahrmodus.',
                     className='small text-secondary mb-2'),
            dbc.Button('Referenzframe einfrieren', id='ab-freeze', color='info', className='me-2'),
            dbc.Button('A/B berechnen', id='ab-run', color='primary', className='me-2'),
            dbc.Button('A/B zurücksetzen', id='ab-reset', color='secondary'),
            html.Div(id='ab-status', className='small mt-2'),
            dbc.Row([dbc.Col([html.H6('A · Referenz'), html.Img(id='ab-image-a', style=image_style),
                              html.Div(id='ab-lanes-a', className='small mt-2')], md=6),
                     dbc.Col([html.H6('B · Kandidat'), html.Img(id='ab-image-b', style=image_style),
                              html.Div(id='ab-lanes-b', className='small mt-2')], md=6)], className='g-3 mt-1')
        ]), md=12)], className='mt-3'),
        dbc.Row([dbc.Col(card('Pipeline-Konfiguration', [
            html.Label('Bildprozessor'), dcc.Dropdown(id='cfg-processor', options=[
                {'label': 'Copilot', 'value': 'copilot'},
                {'label': 'Legacy', 'value': 'legacy'}], value=cfg['processor'], clearable=False),
            dbc.Button('Übernehmen', id='cfg-apply', className='me-2 mt-3'),
            dbc.Button('Speichern', id='cfg-save', color='success', className='mt-3'),
            html.Div(id='cfg-message', className='mt-2')]), md=12)], className='mt-3')])
    telemetry = html.Div([dbc.Row([
        dbc.Col(card('Live · Mini-Preview', html.Img(id='telemetry-preview', style=image_style)), md=4),
        dbc.Col(card('Soll / Ist Frequenz', dcc.Graph(id='freq-bars')), md=6),
        dbc.Col(card('Frequenzverlauf', dcc.Graph(id='freq-history')), md=6),
        dbc.Col(card('Lenkwinkel / Speed / Linienverlust', dcc.Graph(id='drive-history')), md=12),
        dbc.Col(card('Datenalter / Latenz', html.Div(id='data-age')), md=12)], className='g-3')])
    replay = html.Div([
        dbc.Alert('Replay: Live-Ringbuffer oder gespeicherte Session. Keine Motorsteuerung.', color='info'),
        dcc.Dropdown(id='session-choice', options=[{'label':'Live-Ringbuffer','value':'__live__'}], value='__live__', clearable=False),
        dbc.Button('Sessions aktualisieren', id='session-refresh', color='secondary', className='my-2'),
        dbc.Row([dbc.Col(card('Frame Inspector', [
            html.Div(id='replay-frame-label', className='fw-semibold mb-2'),
            html.Img(id='replay-image', style=image_style),
            html.Div(id='replay-details', className='small mt-2')]), md=8),
            dbc.Col(card('Replay-Steuerung', [
                dbc.Button('◀', id='replay-prev', className='me-2'),
                dbc.Button('▶ / Pause', id='replay-play', className='me-2'),
                dbc.Button('▶', id='replay-next'),
                dcc.Slider(id='replay-index', min=0, max=1, step=1, value=0,
                           tooltip={'placement':'bottom'}),
                html.Div(id='replay-range', className='small text-secondary mt-2'),
                dbc.Button('Ringbuffer exportieren', id='replay-export', color='success', className='mt-3'),
                dcc.Download(id='replay-download')]), md=4)], className='g-3'),
        dbc.Row([dbc.Col(card('Zeitliche Navigation', [
            dcc.Graph(id='replay-chart', config={'displayModeBar': False}),
            html.Div('Klicke auf einen Messpunkt, um den zugehörigen Frame auszuwählen.', className='small text-secondary')]), md=12)], className='mt-3'),
    ])
    recording = html.Div([dbc.Row([
        dbc.Col(card('Live · Mini-Preview', html.Img(id='record-preview', style=image_style)), md=4),
        dbc.Col(card('Aufzeichnung', [dbc.Button('Session starten / stoppen', id='record-toggle', color='success'),
            html.Div(id='record-status', className='mt-3'), html.Div(id='session-stats', className='small text-secondary mt-2')]), md=6),
        dbc.Col(card('Ereignisse (Linienbeobachtungen)', html.Div(id='event-list')), md=6)], className='g-3')])
    app.layout = dbc.Container([
        html.H2('Camp2Code · Control Center', className='mt-3'),
        dbc.Card(dbc.CardBody(dbc.Row([
            dbc.Col([dbc.Badge(id='global-camera-state', color='info'),
                     dbc.Badge(id='global-mode-state', color='secondary', className='ms-2'),
                     html.Span(id='global-frame', className='ms-2 small')], md=5),
            dbc.Col([dbc.Button('▶ START', id='global-start', color='success', className='me-2'),
                     dbc.Button('■ STOP', id='global-stop', color='danger', className='me-2'),
                     dbc.Button('Bergung aktivieren', id='recovery-toggle', color='warning', outline=True)], md=7),
        ], align='center')), className='mb-3', style={'position':'sticky','top':'0','zIndex':1000}),
        html.Div(id='global-action-message', className='small mb-2'),
        dbc.Collapse(dbc.Card(dbc.CardBody([
            html.Div('Manuelle Bergung: Leertaste gedrückt halten + W/S/A/D. Bei Verbindungsverlust stoppt der Watchdog.', className='small'),
            html.Div(id='recovery-status'),
            dcc.Store(id='recovery-keys', data={'deadman':False}),
            dcc.Interval(id='recovery-pulse', interval=120, n_intervals=0),
        ])), id='recovery-panel', is_open=False),
        dbc.Tabs([dbc.Tab(cockpit, label='Driving Cockpit', tab_id='drive'),
                  dbc.Tab(pipeline, label='Pipeline Studio', tab_id='pipeline'),
                  dbc.Tab(telemetry, label='Telemetrie & Performance', tab_id='telemetry'),
                  dbc.Tab(recording, label='Recording & Events', tab_id='record'),
                  dbc.Tab(replay, label='Replay Lab', tab_id='replay')], id='main-tabs', active_tab='drive', className='mb-3'),
        dcc.Store(id='replay-playing', data=False, storage_type='memory'),
        dcc.Store(id='pipeline-frozen', data=None, storage_type='memory'),
        dcc.Store(id='inspected-stage', data='overlay', storage_type='memory'),
        dcc.Interval(id='refresh', interval=700, n_intervals=0),
    ], fluid=True)

    @app.callback(Output('global-action-message', 'children'),
                  Input('global-start', 'n_clicks'), Input('global-stop', 'n_clicks'),
                  State('mode', 'value'), prevent_initial_call=True)
    def global_action(_start, _stop, mode):
        try:
            if ctx.triggered_id == 'global-stop':
                application.recovery.disable()
                application.stop_modes()
                return 'Fahrzeug gestoppt'
            application.start_mode(mode)
            return 'Fahrmodus gestartet'
        except Exception as exc:
            return f'Aktion abgewiesen: {exc}'

    @app.callback(Output('recovery-panel', 'is_open'),
                  Input('recovery-toggle', 'n_clicks'),
                  State('recovery-panel', 'is_open'), prevent_initial_call=True)
    def recovery_toggle(_, opened):
        try:
            if opened:
                application.recovery.disable()
                return False
            application.recovery.enable()
            return True
        except RuntimeError:
            return False

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
            return 'Bergung aktiv · Steuerung nur mit Leertaste'
        except RuntimeError as exc:
            return str(exc)

    @app.callback(Output('session-stats', 'children'), Input('refresh', 'n_intervals'))
    def session_stats(_):
        return f"{'REC ●' if sessions.is_running() else 'REC AUS'} · {sessions.count} Frames · {sessions.path or 'keine Session'} · {sessions.error or 'kein Fehler'}"

    @app.callback(Output('global-camera-state', 'children'),
                  Output('global-mode-state', 'children'), Output('global-frame', 'children'),
                  Output('global-start', 'disabled'), Output('global-start', 'outline'),
                  Output('global-stop', 'outline'), Output('recovery-toggle', 'outline'),
                  Output('telemetry-preview', 'src'), Output('record-preview', 'src'),
                  Input('refresh', 'n_intervals'), State('main-tabs', 'active_tab'))
    def global_status(_, tab):
        camera = application.car.camera_data
        active = application.running()
        frame = camera.frame_id if camera is not None else None
        preview = None
        if tab in ('telemetry', 'record') and camera is not None:
            # Niedrig aufgelöstes Rohbild, keine zweite Linienerkennung.
            raw = camera.raw_image
            if raw is not None and getattr(raw, 'size', 0):
                h, w = raw.shape[:2]
                preview_image = render_calibration_overlay(camera)
                if preview_image is not None:
                    ph, pw = preview_image.shape[:2]
                    preview = uri(cv2.resize(preview_image, (min(320, pw), max(1, int(ph * min(320,pw)/pw)))))
        return ('KAMERA LIVE' if camera is not None else 'KAMERA WARTET',
                'FAHRT AKTIV' if active else 'FAHRMODUS AUS',
                f'Frame #{frame}' if frame is not None else '',
                active or application.recovery.enabled, not active,
                active, not application.recovery.enabled,
                preview if tab == 'telemetry' else no_update,
                preview if tab == 'record' else no_update)

    @app.callback(Output('drive-message', 'children'), Input('drive-start', 'n_clicks'),
                  Input('drive-stop', 'n_clicks'), State('mode', 'value'), prevent_initial_call=True)
    def drive_action(_start, _stop, mode):
        try:
            if ctx.triggered_id == 'drive-start':
                application.start_mode(mode)
                return 'Fahrmodus gestartet'
            application.recovery.disable()
            application.stop_modes()
            return 'Fahrzeug gestoppt'
        except Exception as exc:
            log.exception('Fahraktion fehlgeschlagen')
            return f'Fehler: {exc}'

    @app.callback(Output('cfg-message', 'children'), Input('cfg-apply', 'n_clicks'),
                  Input('cfg-save', 'n_clicks'),
                  Input('cfg-processor', 'value'), Input('cfg-crop', 'value'),
                  Input('cfg-hue', 'value'), Input('cfg-sat', 'value'),
                  Input('cfg-val', 'value'), Input('cfg-blur', 'value'),
                  Input('cfg-morph', 'value'), Input('cfg-canny', 'value'),
                  *[Input(f'cfg-enable-{name}', 'value') for name in ('roi','mask','blur','morph','canny','lanes')],
                  prevent_initial_call=True)
    def configure(_apply, _save, processor, crop, hue, sat, val, blur, morph, canny, *switches):
        names = ('roi','mask','blur','morph','canny','lanes')
        settings = dict(processor=processor, crop=crop, hue=hue, sat=sat, val=val,
                        blur=blur, morph=morph, canny=canny,
                        enabled={name: 'on' in (v or []) for name,v in zip(names,switches)})
        try:
            if ctx.triggered_id == 'cfg-save':
                application.save_pipeline(settings)
                return 'Einstellungen gespeichert'
            application.set_pipeline(settings)
            return 'Live-Vorschau aktualisiert · zum dauerhaften Speichern auf Speichern klicken'
        except Exception as exc:
            return f'Nicht übernommen: {exc}'

    @app.callback(Output('ab-status', 'children'), Output('ab-image-a', 'src'),
                  Output('ab-image-b', 'src'), Output('ab-lanes-a', 'children'),
                  Output('ab-lanes-b', 'children'),
                  Input('ab-freeze', 'n_clicks'), Input('ab-run', 'n_clicks'),
                  Input('ab-reset', 'n_clicks'),
                  State('cfg-processor', 'value'), State('cfg-crop', 'value'),
                  State('cfg-hue', 'value'), State('cfg-sat', 'value'),
                  State('cfg-val', 'value'), State('cfg-blur', 'value'),
                  State('cfg-morph', 'value'), State('cfg-canny', 'value'),
                  *[State(f'cfg-enable-{name}', 'value') for name in ('roi','mask','blur','morph','canny','lanes')],
                  prevent_initial_call=True)
    def ab_calibrate(_freeze, _run, _reset, processor, crop, hue, sat, val, blur, morph, canny, *switches):
        trigger = ctx.triggered_id
        if trigger == 'ab-reset':
            ab.clear()
            return 'A/B zurückgesetzt', None, None, '', ''
        enabled = {name: 'on' in (v or []) for name, v in
                   zip(('roi','mask','blur','morph','canny','lanes'), switches)}
        params = (tuple(hue), tuple(sat), tuple(val), tuple(crop),
                  int(blur), int(morph), tuple(canny), enabled)
        try:
            if trigger == 'ab-freeze':
                frame = ab.freeze(application.car.camera_data, params, processor)
                return f'Referenz A: Frame {frame} eingefroren', None, None, '', ''
            comparison = ab.compare(params, processor)
            return (f'A/B auf Frame {comparison.frame_id} berechnet',
                    uri(comparison.image_a), uri(comparison.image_b),
                    f'Linien A: {len(comparison.lanes_a)}',
                    f'Linien B: {len(comparison.lanes_b)}')
        except (ValueError, TypeError, cv2.error) as exc:
            return f'A/B nicht möglich: {exc}', no_update, no_update, no_update, no_update

    @app.callback(Output('record-status', 'children'), Input('record-toggle', 'n_clicks'),
                  prevent_initial_call=True)
    def toggle(_):
        try:
            if sessions.is_running(): sessions.stop()
            else: sessions.start()
            return ('REC AKTIV' if sessions.is_running() else 'Aufzeichnung gestoppt')
        except Exception as exc:
            return f'Aufzeichnungsfehler: {exc}'

    @app.callback(Output('session-choice', 'options'), Input('session-refresh', 'n_clicks'))
    def refresh_sessions(_):
        return [{'label':'Live-Ringbuffer','value':'__live__'}] + [
            {'label':name,'value':name} for name in list_sessions()]

    @app.callback(Output('pipeline-frozen', 'data'), Input('pipeline-freeze', 'n_clicks'),
                  State('pipeline-frozen', 'data'), prevent_initial_call=True)
    def freeze(_, frozen):
        if frozen is not None:
            return None
        camera = application.car.camera_data
        return camera.frame_id if camera is not None else None

    @app.callback(Output('inspected-stage', 'data'),
                  *[Input(f'inspect-{key}', 'n_clicks') for key, _ in STAGES],
                  prevent_initial_call=True)
    def inspect(*_):
        button = ctx.triggered_id
        return button.removeprefix('inspect-') if button else 'overlay'

    @app.callback(Output('drive-overlay', 'src'),
                  *[Output(f'stage-{key}', 'src') for key,_ in STAGES],
                  Output('drive-status', 'children'), Output('drive-angle', 'children'),
                  Output('drive-speed', 'children'), Output('drive-camera-badge', 'children'),
                  Output('drive-frame-badge', 'children'), Output('drive-mode-status', 'children'),
                  Output('record-status', 'title'),
                  Output('pipeline-frame-label', 'children'),
                  Output('inspect-image', 'src'), Output('inspect-title', 'children'),
                  *[Output(f'stage-info-{key}', 'children') for key,_ in STAGES],
                  Input('refresh', 'n_intervals'), State('pipeline-frozen', 'data'),
                  State('inspected-stage', 'data'), State('main-tabs', 'active_tab'))
    def images(_, frozen, inspected, active_tab):
        camera = application.car.camera_data
        if camera is None or camera.frame_id < 0:
            return (None, *([None] * len(STAGES)), 'Noch kein Kameraframe', '–', '–', 'WARTET', '', 'Fahrmodus aus', '', 'Warte auf Kamera', None, '', *([''] * len(STAGES)))
        if frozen is not None:
            match = next((r.camera for r in application.car.frame_history.snapshot()
                          if r.camera.frame_id == frozen), None)
            if match is not None:
                pipeline_camera = match
            else:
                pipeline_camera = camera
                frozen = None
        else:
            pipeline_camera = camera
        stages = getattr(pipeline_camera, 'stages', {}) or {}
        state = application.car.driving_state
        record = next((r for r in reversed(application.car.frame_history.snapshot())
                       if state is not None and r.camera.frame_id == state.frame_id and r.driving is not None), None)
        overlay = None
        if active_tab == 'drive':
            overlay = (render_overlay(record.camera, record.driving)
                       if record and record.camera.frame_id == camera.frame_id and application.running()
                       else render_calibration_overlay(camera))
            overlay = draw_steering_overlay(overlay, application.car.steering_angle)
        stage_images = ({**stages, 'raw': pipeline_camera.raw_image,
                         'overlay': render_calibration_overlay(pipeline_camera)}
                        if active_tab == 'pipeline' else {})
        speed = state.speed if state and application.running() else application.car.speed
        angle = state.steering_angle_car if state and application.running() else application.car.steering_angle
        text = f'Frame {camera.frame_id} · Speed-Steuerwert {speed} · Lenkwinkel {angle} · Modus {application.active_mode or "aus"}'
        return (uri(overlay) if active_tab == 'drive' else no_update,
                *[(uri(stage_images.get(key)) if active_tab == 'pipeline' else no_update) for key,_ in STAGES], text,
                f'{angle}°', str(speed), 'LIVE', f'Frame #{camera.frame_id}',
                f'Modus: {application.active_mode or "aus"}',
                f'Frame {camera.frame_id} · Recorder {"aktiv" if application.recorder.is_running() else "inaktiv"}',
                f'{"FREEZE" if frozen is not None else "LIVE"} · Frame {pipeline_camera.frame_id}',
                (uri(stage_images.get(inspected)) if active_tab == 'pipeline' else no_update), inspected,
                *[('Verfügbar' if stage_images.get(key) is not None else 'Nicht bereitgestellt')
                  for key, _ in STAGES])

    @app.callback(Output('drive-diagnostic', 'children'), Input('refresh', 'n_intervals'))
    def driving_diagnostic(_):
        mode = application.modes.get('camera')
        diagnostic = getattr(mode, 'last_diagnostic', None)
        if not diagnostic:
            return 'Noch keine Controllerdiagnose'
        return (f"Frame {diagnostic.get('frame_id')} · "
                f"Linien: {len([x for x in diagnostic.get('lanes', ()) if x is not None])} · "
                f"Rohwinkel: {diagnostic.get('raw_angle')} · "
                f"Vorheriger Winkel gehalten: {diagnostic.get('held_previous_angle')} · "
                f"Soll: {diagnostic.get('requested_angle')} · "
                f"Stellwert: {diagnostic.get('actual_angle')} · "
                f"Stoppursache: {getattr(mode, 'last_stop_reason', None) or 'keine'}")

    @app.callback(Output('freq-bars', 'figure'), Output('freq-history', 'figure'),
                  Output('drive-history', 'figure'), Output('data-age', 'children'),
                  Output('event-list', 'children'), Input('refresh', 'n_intervals'))
    def charts(_):
        rows, freqs, events = application.sample()
        camera = application.car.camera_data
        state = application.car.driving_state
        metrics = application.car.thread_metrics
        targets = [float(application.sensor.scan_frequency),
                   float(getattr(application.modes.get('camera'), '_frequency', 0))]
        actual = [metrics[key].hz if metrics[key].age_seconds is not None and metrics[key].age_seconds < 2 else 0
                  for key in ('camera', 'controller')]
        bars = go.Figure()
        bars.add_bar(name='Soll', x=['Kamera','Controller'], y=targets)
        bars.add_bar(name='Ist', x=['Kamera','Controller'], y=actual)
        bars.update_layout(barmode='group')
        fplot = go.Figure()
        if freqs:
            start = freqs[-1][0]
            for i, name in ((1, 'Kamera'), (2, 'Controller')):
                fplot.add_scatter(x=[r[0]-start for r in freqs], y=[r[i] for r in freqs], name=name)
        dplot = go.Figure()
        if rows:
            start = rows[-1][0]
            for i, name in ((2, 'Lenkwinkel'), (3, 'Speed'), (4, 'Linie fehlt')):
                dplot.add_scatter(x=[r[0]-start for r in rows], y=[r[i] for r in rows], name=name)
        age = 'Keine Daten verfügbar'
        if camera is not None and camera.monotonic_ns:
            camera_ms = max(0, time.monotonic_ns() - camera.monotonic_ns) / 1e6
            age = f'Kameradatenalter: {camera_ms:.1f} ms'
            if state and state.frame_id == camera.frame_id:
                age += f' · Entscheidungsverzögerung: {(state.timestamp_ns-camera.timestamp)/1e6:.1f} ms (Systemuhr)'
        return bars, fplot, dplot, age, (
            [html.Div(f'Frame {event.frame_id}: {event.code} · {event.detail}')
             for event in application.health.events()[-12:]] +
            [html.Div(f'Frame {fid}: {text}') for fid,text in list(events)[-5:]])
    def replay_rows(name):
        if not name or name == '__live__':
            return application.car.frame_history.snapshot()
        try:
            return load_session(name)[1]
        except (ValueError, OSError):
            return []

    def record_id(record):
        return int(record['frame_id']) if isinstance(record, dict) else record.camera.frame_id

    @app.callback(Output('replay-index', 'max'), Output('replay-range', 'children'),
                  Input('refresh', 'n_intervals'), Input('session-choice', 'value'))
    def replay_limits(_, session_name):
        records = replay_rows(session_name)
        return max(0, len(records)-1), f'{len(records)} Frames · '+('Live-Ringbuffer' if session_name == '__live__' else 'gespeicherte Session')

    @app.callback(Output('replay-playing', 'data'), Input('replay-play', 'n_clicks'),
                  State('replay-playing', 'data'), prevent_initial_call=True)
    def replay_toggle(_, playing):
        return not bool(playing)

    @app.callback(Output('replay-index', 'value'),
                  Input('replay-prev', 'n_clicks'), Input('replay-next', 'n_clicks'),
                  Input('replay-chart', 'clickData'), Input('refresh', 'n_intervals'),
                  State('replay-index', 'value'), State('replay-playing', 'data'), State('session-choice', 'value'))
    def seek(_prev, _next, click, _tick, index, playing, session_name):
        records = replay_rows(session_name)
        if not records:
            return 0
        index = max(0, min(int(index or 0), len(records)-1))
        trigger = ctx.triggered_id
        if trigger == 'replay-prev':
            return max(0, index-1)
        if trigger == 'replay-next':
            return min(len(records)-1, index+1)
        if trigger == 'replay-chart' and click and click.get('points'):
            fid = click['points'][0].get('customdata')
            return next((i for i,r in enumerate(records) if record_id(r) == fid), index)
        if trigger == 'refresh' and playing:
            return min(len(records)-1, index+1)
        return no_update

    @app.callback(Output('replay-image', 'src'), Output('replay-frame-label', 'children'),
                  Output('replay-details', 'children'), Output('replay-chart', 'figure'),
                  Input('replay-index', 'value'), Input('refresh', 'n_intervals'), Input('session-choice', 'value'))
    def replay_render(index, _, session_name):
        records = replay_rows(session_name)
        chart = go.Figure()
        if not records:
            return None, 'Keine Frames', 'Kamera starten', chart
        ids = [record_id(r) for r in records]
        angles = [float(r['steering_angle']) if r.get('steering_angle') else None for r in records] if isinstance(records[0], dict) else [r.driving.steering_angle if r.driving else None for r in records]
        chart.add_scatter(x=ids, y=angles, customdata=ids, mode='lines+markers', name='Lenkwinkel')
        chart.update_layout(xaxis_title='Frame-ID', yaxis_title='Lenkwinkel',
                            margin=dict(l=35,r=15,t=20,b=35), height=220)
        record = records[max(0, min(int(index or 0),len(records)-1))]
        if isinstance(record, dict):
            path, _rows = load_session(session_name)
            filename = (path/record['image_filename']).resolve()
            if not filename.is_relative_to(path):
                return None, 'Ungültiger Bildpfad', '', chart
            image = draw_hud(cv2.imread(str(filename)), record)
            return uri(image), f"Frame #{record_id(record)}", f"Status: {record['data_status']} · Winkel: {record['steering_angle'] or 'fehlt'}", chart
        image = render_overlay(record.camera, record.driving) if record.driving else render_calibration_overlay(record.camera)
        info = (f'Frame {record.camera.frame_id} · '
                + (f'Lenkwinkel {record.driving.steering_angle} · Speed {record.driving.speed}'
                   if record.driving else 'Keine zugeordnete Fahrentscheidung'))
        return uri(image), f'Frame #{record.camera.frame_id}', info, chart

    @app.callback(Output('replay-download', 'data'), Input('replay-export', 'n_clicks'),
                  prevent_initial_call=True)
    def export_buffer(_):
        records = application.car.frame_history.snapshot()
        if not records:
            return no_update
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            rows = []
            for record in records:
                camera, driving = record.camera, record.driving
                image = camera.raw_image
                if image is None or image.size == 0:
                    continue
                success, encoded = cv2.imencode('.jpg', image)
                if not success:
                    continue
                name = f'frames/frame_{camera.frame_id}.jpg'
                archive.writestr(name, encoded.tobytes())
                rows.append(dict(frame_id=camera.frame_id, image=name,
                                 timestamp_ns=camera.timestamp,
                                 steering_angle=driving.steering_angle if driving else '',
                                 speed=driving.speed if driving else '',
                                 status='complete' if driving else 'no_driving_state'))
            text = io.StringIO()
            writer = csv.DictWriter(text, fieldnames=['frame_id','image','timestamp_ns',
                'steering_angle','speed','status'])
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr('frames.csv', text.getvalue())
            archive.writestr('README.txt', 'Kurzexport des flüchtigen Ringbuffers. Keine vollständige Session.\n')
        return dcc.send_bytes(buffer.getvalue(), 'camp2code_ringbuffer_export.zip')

    return app


def main():
    application = CarApplication()
    atexit.register(application.shutdown)
    try:
        create_app(application).run(host='0.0.0.0', port=8050, debug=False, use_reloader=False)
    finally:
        application.shutdown()

if __name__ == '__main__':
    main()
