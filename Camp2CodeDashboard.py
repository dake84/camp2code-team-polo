"""Eine Dash-Anwendung mit vier Ansichten und gemeinsamem Fahrzeugzustand."""
import atexit
import base64
import logging
import threading
import time
import cv2
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, dcc, html, ctx, no_update
from car_application import CarApplication
from dashboard_overlay import render_overlay, render_calibration_overlay
from pipeline_config import load

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
    app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY], suppress_callback_exceptions=False)
    app.title = 'Camp2Code | Cockpit'
    image_style = {'width': '100%', 'background': '#101827', 'borderRadius': '8px'}
    def card(title, content):
        return dbc.Card([dbc.CardHeader(title), dbc.CardBody(content)], className='h-100')
    cockpit = html.Div([
        dbc.Row([dbc.Col(card('Fahrbetrieb', [
            html.Label('Fahrmodus'), dcc.Dropdown(id='mode', options=[
                {'label': 'CameraMode', 'value': 'camera'},
                {'label': 'PSControllerMode', 'value': 'gamepad'}], value='camera', clearable=False),
            html.Div([dbc.Button('Start', id='drive-start', color='success', className='me-2 mt-3'),
                      dbc.Button('STOP', id='drive-stop', color='danger', className='mt-3')]),
            html.Div(id='drive-message', className='mt-2')]), md=12)]),
        dbc.Row([dbc.Col(card('Rohbild', html.Img(id='drive-raw', style=image_style)), md=6),
                 dbc.Col(card('Fahrspur-Overlay', html.Img(id='drive-overlay', style=image_style)), md=6)], className='g-3 mt-1'),
        dbc.Row([dbc.Col(card('Fahrzeugstatus', html.Div(id='drive-status')), md=12)], className='mt-3')])
    pipeline = html.Div([
        dbc.Alert('Kalibrierung ohne Fahrmodus. Während einer Fahrt sind Änderungen gesperrt.', color='info'),
        dbc.Row([dbc.Col(card(title, html.Img(id=f'stage-{key}', style=image_style)), md=3)
                 for key, title in STAGES], className='g-3'),
        dbc.Row([dbc.Col(card('Bildverarbeitung', [
            html.Label('Prozessor'), dcc.Dropdown(id='cfg-processor', options=[
                {'label': 'Copilot', 'value': 'copilot'}, {'label': 'Legacy', 'value': 'legacy'}],
                value=cfg['processor'], clearable=False),
            slider('ROI', 'crop', 0, 1, cfg['crop'], .01, True),
            slider('Hue', 'hue', 0, 179, cfg['hue'], 1, True),
            slider('Saturation', 'sat', 0, 255, cfg['sat'], 1, True),
            slider('Value', 'val', 0, 255, cfg['val'], 1, True),
            slider('Blur', 'blur', 1, 15, cfg['blur']),
            slider('Morphologie', 'morph', 1, 15, cfg['morph']),
            slider('Canny', 'canny', 0, 255, cfg['canny'], 1, True),
            *[dbc.Checklist(id=f'cfg-enable-{name}', options=[{'label': f'{name} aktiv', 'value': 'on'}],
                value=['on'] if cfg['enabled'].get(name, True) else [], switch=True)
              for name in ('roi', 'mask', 'blur', 'morph', 'canny', 'lanes')],
            dbc.Button('Übernehmen', id='cfg-apply', className='me-2 mt-3'),
            dbc.Button('Speichern', id='cfg-save', color='success', className='mt-3'),
            html.Div(id='cfg-message', className='mt-2')]), md=12)], className='mt-3')])
    telemetry = html.Div([dbc.Row([
        dbc.Col(card('Soll / Ist Frequenz', dcc.Graph(id='freq-bars')), md=6),
        dbc.Col(card('Frequenzverlauf', dcc.Graph(id='freq-history')), md=6),
        dbc.Col(card('Lenkwinkel / Speed / Linienverlust', dcc.Graph(id='drive-history')), md=12),
        dbc.Col(card('Datenalter / Latenz', html.Div(id='data-age')), md=12)], className='g-3')])
    recording = html.Div([dbc.Row([
        dbc.Col(card('Aufzeichnung', [dbc.Button('Aufzeichnung starten / stoppen', id='record-toggle'),
            html.Div(id='record-status', className='mt-3')]), md=6),
        dbc.Col(card('Ereignisse (Linienbeobachtungen)', html.Div(id='event-list')), md=6)], className='g-3')])
    app.layout = dbc.Container([
        html.H2('Camp2Code · Control Center', className='mt-3'),
        dbc.Tabs([dbc.Tab(cockpit, label='Driving Cockpit'),
                  dbc.Tab(pipeline, label='Pipeline Studio'),
                  dbc.Tab(telemetry, label='Telemetrie & Performance'),
                  dbc.Tab(recording, label='Recording & Events')], className='mb-3'),
        dcc.Interval(id='refresh', interval=700, n_intervals=0),
    ], fluid=True)

    @app.callback(Output('drive-message', 'children'), Input('drive-start', 'n_clicks'),
                  Input('drive-stop', 'n_clicks'), State('mode', 'value'), prevent_initial_call=True)
    def drive_action(_start, _stop, mode):
        try:
            if ctx.triggered_id == 'drive-start':
                application.start_mode(mode)
                return 'Fahrmodus gestartet'
            application.stop_modes()
            return 'Fahrzeug gestoppt'
        except Exception as exc:
            log.exception('Fahraktion fehlgeschlagen')
            return f'Fehler: {exc}'

    @app.callback(Output('cfg-message', 'children'), Input('cfg-apply', 'n_clicks'),
                  Input('cfg-save', 'n_clicks'),
                  State('cfg-processor', 'value'), State('cfg-crop', 'value'),
                  State('cfg-hue', 'value'), State('cfg-sat', 'value'),
                  State('cfg-val', 'value'), State('cfg-blur', 'value'),
                  State('cfg-morph', 'value'), State('cfg-canny', 'value'),
                  *[State(f'cfg-enable-{name}', 'value') for name in ('roi','mask','blur','morph','canny','lanes')],
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
            return 'Einstellungen übernommen'
        except Exception as exc:
            return f'Nicht übernommen: {exc}'

    @app.callback(Output('record-status', 'children'), Input('record-toggle', 'n_clicks'),
                  prevent_initial_call=True)
    def toggle(_):
        try:
            running = application.toggle_recording()
            return 'Aufzeichnung läuft' if running else 'Aufzeichnung gestoppt'
        except Exception as exc:
            return f'Fehler: {exc}'

    @app.callback(Output('drive-raw', 'src'), Output('drive-overlay', 'src'),
                  *[Output(f'stage-{key}', 'src') for key,_ in STAGES],
                  Output('drive-status', 'children'), Output('record-status', 'title'),
                  Input('refresh', 'n_intervals'))
    def images(_):
        camera = application.car.camera_data
        if camera is None or camera.frame_id < 0:
            return (None, None, *([None] * len(STAGES)), 'Noch kein Kameraframe', '')
        stages = getattr(camera, 'stages', {}) or {}
        state = application.car.driving_state
        record = next((r for r in reversed(application.car.frame_history.snapshot())
                       if state is not None and r.camera.frame_id == state.frame_id and r.driving is not None), None)
        overlay = render_overlay(record.camera, record.driving) if record and application.running() else render_calibration_overlay(camera)
        stage_images = {**stages, 'raw': camera.raw_image, 'overlay': render_calibration_overlay(camera)}
        speed = state.speed if state and application.running() else application.car.speed
        angle = state.steering_angle_car if state and application.running() else application.car.steering_angle
        text = f'Frame {camera.frame_id} · Speed-Steuerwert {speed} · Lenkwinkel {angle} · Modus {application.active_mode or "aus"}'
        return (uri(camera.raw_image), uri(overlay),
                *[uri(stage_images.get(key)) for key,_ in STAGES], text,
                f'Frame {camera.frame_id} · Recorder {"aktiv" if application.recorder.is_running() else "inaktiv"}')

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
        return bars, fplot, dplot, age, [html.Div(f'Frame {fid}: {text}') for fid,text in list(events)[-12:]]
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
