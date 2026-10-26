"""Eigenstaendiges Kalibrierungs-Dashboard, ohne Fahrbefehle."""
import atexit
import base64
import logging
import threading
import cv2
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html
from CameraCar import CameraCar
from CameraSensor import CameraSensor
from dashboard_overlay import render_calibration_overlay

log = logging.getLogger(__name__)

STAGES = [
    ('raw', '01 · Rohbild', 'Kameraaufnahme', None),
    ('roi', '02 · Bildausschnitt', 'Nur der relevante Bildbereich', 'crop'),
    ('hsv', '03 · HSV-Farbraum', 'Farbwerte zur Filterung', None),
    ('mask', '04 · Farbmaske', 'Hue, Saturation und Value', 'hsv'),
    ('blur', '05 · Glättung', 'Störpixel reduzieren', 'blur'),
    ('morph', '06 · Morphologie', 'Lücken schließen', 'morph'),
    ('edges', '07 · Kanten', 'Canny-Kantenerkennung', 'canny'),
    ('overlay', '08 · Erkannte Linien', 'Linien im Rohbild, ohne Fahrbetrieb', 'lanes'),
]

def image_uri(image):
    if image is None or image.size == 0:
        return None
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 68])
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii') if ok else None

def slider(label, name, min_v, max_v, value, step=1, range_slider=False):
    control = dcc.RangeSlider if range_slider else dcc.Slider
    return html.Div([html.Label(label, className='small fw-semibold'),
                     control(id=name, min=min_v, max=max_v, step=step, value=value,
                             tooltip={'placement':'bottom','always_visible':False})], className='mb-3')

def controls(kind):
    if kind == 'crop':
        return slider('ROI: oberer / unterer Anteil', 'crop', 0, 1, [0.1,0.9], 0.01, True)
    if kind == 'hsv':
        return html.Div([slider('Hue (0–179)', 'hue', 0,179,[110,179],1,True),
                         slider('Saturation (0–255)', 'sat', 0,255,[0,255],1,True),
                         slider('Value (0–255)', 'val', 0,255,[0,255],1,True)])
    if kind == 'blur':
        return slider('Blur-Kernel', 'blur', 1,15,3,1)
    if kind == 'morph':
        return slider('Morph-Kernel', 'morph', 1,15,5,1)
    if kind == 'canny':
        return slider('Canny: untere / obere Schwelle', 'canny', 0,255,[50,100],1,True)
    return html.Div('Keine eigenen Parameter', className='text-muted small')

def card(key,title,desc,kind):
    return dbc.Col(dbc.Card([
        dbc.CardHeader([html.Strong(title), html.Div(desc,className='small text-muted')]),
        dbc.CardBody([html.Div(html.Img(id=f'image-{key}',style={'width':'100%','aspectRatio':'4/3',
                  'objectFit':'contain','background':'#101827','borderRadius':'8px'})),
                  html.Div([dcc.Checklist(id=f'enabled-{key}',
                         options=[{'label':' Verarbeitung aktiv', 'value':'on'}],
                         value=['on'], inline=True)], className='pt-2')
                  if key in ('roi','mask','blur','morph','edges','overlay') else html.Div(),
                  html.Div(controls(kind),className='pt-3')])
    ],className='h-100 shadow-sm'),xs=12,md=6,xl=4,className='mb-3')

def make_app(car,sensor):
    app=Dash(__name__,external_stylesheets=[dbc.themes.BOOTSTRAP])
    app.title='Camp2Code | Pipeline Studio'
    app.layout=dbc.Container([
        dbc.Row([dbc.Col([html.H2('Pipeline Studio',className='fw-bold mb-1'),
              html.P('Vom Rohbild zur erkannten Fahrspur · Live-Kalibrierung ohne Fahrbetrieb',
                     className='text-muted')],md=9),
              dbc.Col([html.Label('Bildverarbeitung',className='small fw-semibold'),
                       dcc.Dropdown(id='processor',options=[
                           {'label':'CopilotLaneProcessor · Diagnose','value':'copilot'},
                           {'label':'LaneProcessor · Legacy','value':'legacy'}],
                           value='copilot',clearable=False)],md=3)],className='pt-4 mb-3'),
        dbc.Alert('Kalibrierungsmodus: Dieses Dashboard startet keinen Fahrmodus und sendet keine Fahrbefehle.',
                  color='info'),
        dbc.Row([card(*stage) for stage in STAGES]),
        dbc.Card([dbc.CardHeader('Performance · Soll / Ist'),dbc.CardBody([
            html.Div(id='status',className='mb-2'),
            dcc.Graph(id='frequency-chart',config={'displayModeBar':False})])],className='mb-4'),
        dcc.Interval(id='refresh',interval=600,n_intervals=0),
        html.Div(id='settings-status',className='visually-hidden')
    ],fluid=True,style={'maxWidth':'1560px'})

    @app.callback(Output('settings-status','children'),
                  Input('processor','value'),Input('crop','value'),Input('hue','value'),
                  Input('sat','value'),Input('val','value'),Input('blur','value'),Input('morph','value'),Input('canny','value'),
                  *[Input(f'enabled-{name}','value') for name in ('roi','mask','blur','morph','edges','overlay')])
    def update_settings(processor,crop,hue,sat,val,blur,morph,canny,*switches):
        # Einmalige atomare Parameteruebergabe; keine Fahrentscheidung.
        if crop[0] >= crop[1]:
            return 'ROI ungueltig'
        names=('roi','mask','blur','morph','canny','lanes')
        enabled={name: 'on' in values for name, values in zip(names,switches)}
        sensor.processing_parameters=(tuple(hue),tuple(sat),tuple(val),tuple(crop),
                                      int(blur),int(morph),tuple(canny),enabled)
        sensor.set_processor(processor)
        return 'Parameter uebernommen'

    outputs=[Output(f'image-{key}','src') for key,_,_,_ in STAGES]
    @app.callback(outputs+[Output('status','children'),Output('frequency-chart','figure')],Input('refresh','n_intervals'))
    def refresh(_):
        data=car.camera_data
        images={}
        if data is not None:
            images={'raw':data.raw_image,**getattr(data,'stages',{})}
            if data.raw_image is not None and data.raw_image.size:
                images['overlay']=render_calibration_overlay(data)
        encoded=[image_uri(images.get(key)) for key,_,_,_ in STAGES]
        age=car.thread_metrics['camera'].age_seconds
        actual=car.thread_metrics['camera'].hz if age is not None and age<2 else 0.0
        target=float(sensor.scan_frequency)
        fig=go.Figure()
        fig.add_bar(name='Soll',x=['Kamera + Pipeline'],y=[target],marker_color='#7b8ca3')
        fig.add_bar(name='Ist',x=['Kamera + Pipeline'],y=[actual],marker_color='#14a78c')
        fig.update_layout(barmode='group',height=230,margin=dict(l=25,r=15,t=20,b=30),
                          yaxis_title='Hz',uirevision='freq')
        status=f'Frame {getattr(data,"frame_id",-1)} · {len(getattr(data,"lanes",()))} Linien · Soll {target:.1f} Hz / Ist {actual:.1f} Hz'
        return encoded+[status,fig]
    return app

def main():
    car=CameraCar()
    sensor=CameraSensor(car)
    stop=threading.Event()
    thread=threading.Thread(target=sensor.read_loop,args=(stop,),name='camera-pipeline',daemon=True)
    thread.start()
    def shutdown():
        stop.set()
        thread.join(timeout=2)
        # Kameraressourcen werden ueber die bestehende Camera-Klasse verwaltet.
        if hasattr(sensor,'close'):
            sensor.close()
    atexit.register(shutdown)
    make_app(car,sensor).run(host='0.0.0.0',port=8050,debug=False,use_reloader=False)

if __name__=='__main__':
    main()
