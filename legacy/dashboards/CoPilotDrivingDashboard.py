"""Driving Cockpit: neue Oberflaeche fuer die bestehende Fahrzeugarchitektur.

Startet die Kamera, aber keinen Fahrmodus automatisch. Alle Fahrbefehle
laufen weiterhin ausschliesslich ueber die vorhandenen DrivingMode-Klassen.
"""
from __future__ import annotations
import atexit
import base64
import logging
import threading
import time
from collections import deque
from datetime import datetime
import cv2
import numpy as np
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, dcc, html, ctx, no_update
from CameraCar import CameraCar
from CameraSensor import CameraSensor
from Save_Data_AI import SaveData_4_AI
import Driving
from dashboard_overlay import render_calibration_overlay, render_overlay

log = logging.getLogger(__name__)

class CockpitHistory:
    """Kleine Historie fuer Plots. Speichert keine Bildkopien."""
    def __init__(self, maxlen=180):
        self._rows = deque(maxlen=maxlen)
        self._events = deque(maxlen=30)
        self._lock = threading.RLock()
        self._last_frame = None
        self._missing = False

    def sample(self, car):
        # Nur neue Fahrentscheidungen zaehlen. Bei ausgeschaltetem Controller
        # entstehen keine erfundenen Messwerte.
        state = car.driving_state
        if state is None:
            return
        with self._lock:
            if state.frame_id == self._last_frame:
                return
            self._last_frame = state.frame_id
            count = len(state.lanes) if state.lanes is not None else 0
            missing = count == 0
            if missing != self._missing:
                self._events.appendleft((datetime.now().strftime('%H:%M:%S'),
                                        'Keine Linien erkannt' if missing else 'Linien wieder erkannt',
                                        state.frame_id))
                self._missing = missing
            self._rows.append(dict(t=time.monotonic(), frame=state.frame_id,
                                   steering=float(state.steering_angle), speed=float(state.speed),
                                   line_missing=int(missing), lines=count))

    def snapshot(self):
        with self._lock:
            return list(self._rows), list(self._events)

class Cockpit:
    def __init__(self):
        self.car = CameraCar()
        self.sensor = CameraSensor(self.car)
        self.modes = {
            'camera': Driving.CameraMode(car=self.car),
            'gamepad': Driving.PSControllerMode(car=self.car),
        }
        self.recorder = SaveData_4_AI(car=self.car, frequency=5.0)
        self.history = CockpitHistory()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.active_mode = None
        self.recording = False
        self.thread = threading.Thread(target=self.sensor.read_loop,
                                       args=(self.stop_event,), daemon=True, name='sensor')
        self.thread.start()

    def running(self):
        return any(mode.is_running() for mode in self.modes.values())

    def start_mode(self, key):
        with self.lock:
            if self.running():
                return 'Ein Fahrmodus laeuft bereits. Zuerst stoppen.'
            if key not in self.modes:
                return 'Unbekannter Fahrmodus.'
            self.modes[key].start()
            self.active_mode = key
            return f'{key} gestartet'

    def stop_modes(self):
        with self.lock:
            # Hardware zuerst anhalten, auch falls der Thread blockiert.
            self.car.drive(0, 90)
            for mode in self.modes.values():
                if mode.is_running():
                    mode.stop()
            self.active_mode = None
            return 'Fahrzeug gestoppt'

    def switch_processor(self, name):
        with self.lock:
            if self.running():
                return 'Prozessorwechsel nur bei gestopptem Fahrmodus.'
            self.sensor.set_processor(name)
            return f'Bildprozessor: {name}'

    def toggle_recording(self):
        with self.lock:
            if self.recording:
                self.recorder.stop()
                self.recording = False
                return 'Aufzeichnung gestoppt'
            self.recorder.start()
            self.recording = True
            return 'Aufzeichnung gestartet'

    def shutdown(self):
        try:
            self.stop_modes()
        except Exception:
            log.exception('Fehler beim Fahrzeugstopp')
        try:
            self.recorder.stop()
        except Exception:
            log.exception('Fehler beim Recorder-Stopp')
        self.stop_event.set()
        try:
            self.sensor.stop()
        except Exception:
            log.exception('Fehler beim Kamera-Stopp')
        self.thread.join(timeout=2)


def jpeg_uri(image):
    if image is None or image.size == 0:
        return None
    ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 68])
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii') if ok else None


def line_chart(rows, chosen):
    """Nur wirklich vorhandene Daten anzeigen; Offset wird nicht erfunden."""
    fig = go.Figure()
    if rows:
        base = rows[-1]['t']
        x = [row['t'] - base for row in rows]
        series = {'steering': ('Lenkwinkel (Grad)', '#45c8f5'),
                  'speed': ('Speed (Steuerwert)', '#f0ad4e'),
                  'line_missing': ('Keine Linie (0/1)', '#f66d84')}
        for key in chosen or []:
            if key in series:
                label, color = series[key]
                fig.add_scatter(x=x, y=[row[key] for row in rows],
                                name=label, mode='lines', line={'color':color})
    fig.update_layout(template='plotly_dark', paper_bgcolor='#17243a',
                      plot_bgcolor='#17243a', height=300,
                      margin=dict(l=35,r=15,t=15,b=35),
                      xaxis_title='Sekunden bis jetzt', uirevision='history')
    return fig


def frequency_chart(car, sensor, camera_mode):
    labels=['Kamera','Controller']
    targets=[float(sensor.scan_frequency), float(camera_mode._frequency)]
    metrics=[car.thread_metrics['camera'],car.thread_metrics['controller']]
    actual=[m.hz if m.age_seconds is not None and m.age_seconds < 2 else 0.0
            for m in metrics]
    fig=go.Figure()
    fig.add_bar(x=labels,y=targets,name='Soll',marker_color='#73859f')
    fig.add_bar(x=labels,y=actual,name='Ist',marker_color='#43cfa4')
    fig.update_layout(barmode='group',template='plotly_dark',height=260,
                      paper_bgcolor='#17243a',plot_bgcolor='#17243a',
                      margin=dict(l=35,r=15,t=15,b=25),yaxis_title='Hz',uirevision='frequency')
    return fig


def create_app(cockpit):
    app=Dash(__name__,external_stylesheets=[dbc.themes.DARKLY])
    app.title='Camp2Code | Driving Cockpit'
    panel={'background':'#17243a','border':'1px solid #34465e','borderRadius':'14px','padding':'16px'}
    camera_style={'width':'100%','aspectRatio':'4/3','objectFit':'contain',
                  'background':'#0a1322','borderRadius':'8px'}
    app.layout=dbc.Container([
        dbc.Row([dbc.Col([html.H2('◉ Driving Cockpit',className='fw-bold'),
                 html.P('Camp2Code · Fahrbetrieb und Live-Diagnose',className='text-secondary')],md=8),
                 dbc.Col(html.Div(id='run-status',className='text-end fw-semibold'),md=4)],className='pt-4'),
        dbc.Row([dbc.Col(html.Div([
            html.H5('Fahrzeugsteuerung'),
            dbc.Row([dbc.Col([html.Label('Fahrmodus'),dcc.Dropdown(id='mode',
                options=[{'label':'CameraMode','value':'camera'},
                         {'label':'PSControllerMode','value':'gamepad'}],
                value='camera',clearable=False,style={'color':'#111'})],md=5),
                dbc.Col([html.Label('Bildprozessor'),dcc.Dropdown(id='processor',
                options=[{'label':'LaneProcessor (Legacy)','value':'legacy'},
                         {'label':'CopilotLaneProcessor (Diagnose)','value':'copilot'}],
                value='copilot',clearable=False,style={'color':'#111'})],md=7)]),
            html.Div([dbc.Button('Fahrmodus starten',id='start',color='success',className='me-2'),
                      dbc.Button('STOP',id='stop',color='danger',className='me-2'),
                      dbc.Button('Aufzeichnung an/aus',id='record',color='info',outline=True)],
                     className='mt-3'),
            html.Div('Bereit',id='action-message',className='small text-info mt-2')
        ],style=panel),md=12)],className='mb-3'),
        dbc.Row([dbc.Col(html.Div([html.H5('Live · Rohbild'),html.Img(id='raw',style=camera_style)],style=panel),md=6),
                 dbc.Col(html.Div([html.H5('Live · Fahrspur-Overlay'),html.Img(id='overlay',style=camera_style),
                    dbc.Checklist(id='layers',options=[{'label':'Linien / ROI','value':'overlay'}],
                                  value=['overlay'],switch=True)],style=panel),md=6)],className='g-3 mb-3'),
        dbc.Row([dbc.Col(html.Div([html.H5('Fahrdaten'),html.Div(id='kpis',className='fs-5'),
                  html.P('Offset: nicht verfügbar (noch keine fachlich definierte Messgröße)',
                         className='small text-secondary')],style=panel),md=12)],className='mb-3'),
        dbc.Row([dbc.Col(html.Div([html.H5('Telemetrie · letzte 180 Messpunkte'),
                   dbc.Checklist(id='series',inline=True,value=['steering','speed'],
                      options=[{'label':'Lenkwinkel','value':'steering'},
                               {'label':'Speed','value':'speed'},
                               {'label':'Linie fehlt','value':'line_missing'}]),
                   dcc.Graph(id='telemetry',config={'displayModeBar':True})],style=panel),md=8),
                 dbc.Col(html.Div([html.H5('Ereignisse'),html.Div(id='events'),
                       html.P('„Linie fehlt“ ist eine Beobachtung, kein bestätigtes LOST_LINE-Stoppevent.',
                              className='small text-secondary')],style=panel),md=4)],className='g-3 mb-3'),
        dbc.Row([dbc.Col(html.Div([dbc.Checklist(id='show-frequency',
                  options=[{'label':'Performance / Frequenzen anzeigen','value':'show'}],value=['show'],switch=True),
                  html.Div(dcc.Graph(id='frequencies'),id='frequency-panel')],style=panel),md=12)],className='mb-4'),
        dcc.Interval(id='camera-timer',interval=350,n_intervals=0),
        dcc.Interval(id='telemetry-timer',interval=500,n_intervals=0),
    ],fluid=True,style={'maxWidth':'1600px','background':'#0f192b','minHeight':'100vh'})

    @app.callback(Output('action-message','children'),Input('start','n_clicks'),
                  Input('stop','n_clicks'),Input('record','n_clicks'),
                  Input('processor','value'),State('mode','value'),prevent_initial_call=True)
    def actions(_start,_stop,_record,processor,mode):
        try:
            trigger=ctx.triggered_id
            if trigger=='start':return cockpit.start_mode(mode)
            if trigger=='stop':return cockpit.stop_modes()
            if trigger=='record':return cockpit.toggle_recording()
            if trigger=='processor':return cockpit.switch_processor(processor)
        except Exception as error:
            log.exception('Cockpit-Aktion fehlgeschlagen')
            return f'Aktion fehlgeschlagen: {type(error).__name__}'
        return no_update

    @app.callback(Output('raw','src'),Output('overlay','src'),
                  Input('camera-timer','n_intervals'),Input('layers','value'))
    def images(_tick,layers):
        camera=cockpit.car.camera_data
        if camera is None:return None,None
        raw=jpeg_uri(camera.raw_image)
        if 'overlay' not in (layers or []):return raw,raw
        # Im Fahrbetrieb nur ein Paar mit identischer Frame-ID anzeigen.
        state=cockpit.car.driving_state
        if cockpit.running() and state is not None:
            record=next((r for r in reversed(cockpit.car.frame_history.snapshot())
                         if r.camera.frame_id==state.frame_id and r.driving is not None),None)
            overlay=render_overlay(record.camera,record.driving) if record else None
        else:
            overlay=render_calibration_overlay(camera)
        return raw,jpeg_uri(overlay)

    @app.callback(Output('run-status','children'),Output('kpis','children'),
                  Output('telemetry','figure'),Output('events','children'),
                  Output('frequencies','figure'),Output('frequency-panel','style'),
                  Input('telemetry-timer','n_intervals'),Input('series','value'),
                  Input('show-frequency','value'))
    def metrics(_tick,selected,frequency_enabled):
        cockpit.history.sample(cockpit.car)
        rows,events=cockpit.history.snapshot()
        state=cockpit.car.driving_state
        running=cockpit.running()
        status=('● FAHRT AKTIV' if running else '● GESTOPPT') + (' · REC' if cockpit.recording else '')
        kpis=('Noch keine Fahrentscheidung' if state is None else
              f'Lenkwinkel: {state.steering_angle:.0f}°   |   Speed: {state.speed:.0f}   |   '
              f'Linien: {len(state.lanes) if state.lanes is not None else 0}   |   Frame: {state.frame_id}')
        event_nodes=[html.Div(f'{t} · {text} · Frame {frame}',className='small border-bottom py-2')
                     for t,text,frame in events[:8]] or [html.P('Keine Ereignisse',className='text-secondary')]
        return (status,kpis,line_chart(rows,selected),event_nodes,
                frequency_chart(cockpit.car,cockpit.sensor,cockpit.modes['camera']),
                {} if 'show' in (frequency_enabled or []) else {'display':'none'})
    return app


def main():
    cockpit=Cockpit()
    atexit.register(cockpit.shutdown)
    create_app(cockpit).run(host='0.0.0.0',port=8050,debug=False,use_reloader=False)

if __name__=='__main__':
    main()
