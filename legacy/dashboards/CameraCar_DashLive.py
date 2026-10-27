import logging
import threading
import time

import os
import signal

import cv2 #Bildverarbeitung
import dash_bootstrap_components as dbc
import numpy as np
from dash import Dash, Input, Output, dcc, html, State
from flask import Flask, Response
import plotly.graph_objects as go

import LenkwinkelKalkulator
import logging_setup  # noqa: F401  – konfiguriert Logging beim Import

# Unsere Packge für CameraCar
import Driving
from Save_Data_AI import SaveData_4_AI

from CameraCar import CameraCar, CameraData, CameraDataProcessed
from CameraSensor import CameraSensor
from telemetry import FrequencyCounter
from dashboard_overlay import render_calibration_overlay


# Config
DEFAULT_HSV = {'h': (100, 130), 's': (80, 255), 'v': (50, 255), 'crop': (0.2, 0.8), 'blur': 3, 'morph_kernel': 5}

SCALE = 0.5
TARGET_FPS = 15
JPEG_QUALITY = 70

log = logging.getLogger(__name__)
_shutdown_lock = threading.Lock()


class ImageStream:
    '''Importiert Kamerabilder in einem eigenen Thread und hält das letzte Ergebnis bereit.'''

    def __init__(self, car, sensor, driving, use_raw_data:bool=True):
        self.car = car
        self._hsv_lock = threading.Lock()
        self._lower = np.array([DEFAULT_HSV[k][0] for k in 'hsv'], dtype=np.uint8)
        self._upper = np.array([DEFAULT_HSV[k][1] for k in 'hsv'], dtype=np.uint8)
        self._frame_cond = threading.Condition()
        self.use_raw_data = use_raw_data
        self._jpeg = None
        self.lines = None  # Koordinaten im skalierten Bild (Faktor SCALE)!
        self.sensor = sensor
        self.driving = driving
        self.metrics = FrequencyCounter()

    def set_hsv(self, hue, saturation, value, crop, blur, morph_kernel):
        self.sensor.processing_parameters = (hue, saturation, value, crop, blur, morph_kernel)

    def run(self, stop_event):
        period = 1.0 / TARGET_FPS
        while not stop_event.is_set():
            t0 = time.monotonic()
            try:
                self._process_once()
            except Exception:
                log.exception('Fehler in der Bildverarbeitung')
                stop_event.wait(1.0)  # im Fehlerfall nicht im Vollgas loopen
            stop_event.wait(max(0.0, period - (time.monotonic() - t0)))

    def _process_once(self):
        """Ein Bild erzeugen, ohne das Original im CameraCar zu veraendern."""
        from dashboard_overlay import render_overlay

        # Beide Streams zeichnen dasselbe Overlay, aber auf verschiedenen
        # Originalbildern: links Rohbild, rechts vorverarbeitetes ROI-Bild.
        # Die Linienerkennung selbst wird NICHT erneut ausgefuehrt.
        processed = not self.use_raw_data
        mode_running = self.driving is not None and self.driving.is_running()
        if not mode_running:
            camera = self.car.camera_data
            frame = render_calibration_overlay(camera, processed=processed)
        else:
            state = self.car.driving_state
            if state is None:
                return
            record = next((r for r in reversed(self.car.frame_history.snapshot())
                           if r.camera.frame_id == state.frame_id and r.driving is not None), None)
            if record is None:
                return
            frame = render_overlay(record.camera, record.driving, processed=processed)

        if frame is None or frame.size == 0:
            return

        ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            self.metrics.tick()
            with self._frame_cond:
                self._jpeg = jpeg.tobytes()
                self._frame_cond.notify_all()

    def mjpeg_stream(self):
        '''Liefert nur fertige Frames aus – beliebig viele Clients, eine Verarbeitung.'''
        while True:
            with self._frame_cond:
                self._frame_cond.wait(timeout=1.0)
                jpeg = self._jpeg

            if jpeg is None:
                continue

            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n'


def create_app(modes, car, stream_pre_processing: ImageStream, stream_post_processing: ImageStream, sensor: CameraSensor, save_data: SaveData_4_AI, on_stop) -> Dash:

    server = Flask(__name__)
    app = Dash(__name__, server=server, external_stylesheets=[dbc.themes.CERULEAN])

    @server.route('/test')
    def unterseite_test():
        return 'Hier ist die Testseite'

    @server.route('/stream')
    def stream():
        return Response(stream_pre_processing.mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

    @server.route('/stream2')
    def stream2():
        return Response(stream_post_processing.mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

    def slider(channel, max_value, increment=1.0):
        default = DEFAULT_HSV[channel]

        if isinstance(default, (tuple, list)):
            component = dcc.RangeSlider(0, max_value, value=list(default), step=increment, id=f'{channel}-slider', tooltip={'placement': 'bottom'})
        else:
            component = dcc.Slider(0, max_value, value=default, step=increment, id=f'{channel}-slider', tooltip={'placement': 'bottom'})

        return html.Div([html.Label(channel.upper()), component])

    app.layout = dbc.Container([

        dbc.Row(html.H1('Projektphase 2')),
        dbc.Row([
            dbc.Col([
                html.H5('Thread-Frequenzen: Soll / Ist'),
                html.Div(id='thread-metrics', className='mb-2'),
                dcc.Graph(id='frequency-plot', config={'displayModeBar': False},
                          style={'height': '310px'}),
            ]),
            dcc.Interval(id='metrics-interval', interval=1000, n_intervals=0),
        ]),

        dbc.Row([
            dbc.Col([html.H6('Rohbild + Overlay'), html.Img(src='/stream', style={'width': '100%'})], md=4),
            dbc.Col([html.H6('Vorverarbeitetes Bild + Overlay'), html.Img(src='/stream2', style={'width': '100%'})], md=4),
            dbc.Col([
                slider('h', 179),
                slider('s', 255),
                slider('v', 255),
                slider('crop', 1, 0.05),
                slider('blur', 20, 1),
                slider('morph_kernel', 20, 1),
                html.Div(id='hsv-info', className='mt-3')
            ], md=4),
        ]),

        dbc.Row([
            dbc.Col([
                html.Label('Fahrmodus wählen'),
                dcc.Dropdown(
                    id='dropdown_fahrmodus',
                    options=[
                        {'label': 'Fahrmodus 1 - Bilderkennung', 'value': 'fahrmodus_1'},
                        {'label': 'Fahrmodus 2 - AI', 'value': 'fahrmodus_2'},
                        {'label': 'Fahrmodus 3 - PS Controller', 'value': 'fahrmodus_ps'}
                    ],
                    value='fahrmodus_1',
                    clearable=False,
                    placeholder='Bitte Fahrmodus wählen'
                ),
                dbc.Button('Fahrmodus starten', id='button_fahrmodi', n_clicks=0, color='success', className='mt-4'),
                dcc.Store(id='fahrmodus_state', data=False),
                dbc.Button('Datenaufnahme starten', id='button_save_data', n_clicks=0, color='primary', className='mt-4'),
                dcc.Store(id='save_data_state', data=False)
            ], md=4)
        ]),

        dbc.Row([
            dbc.Col([
                dbc.Button('Close App', id='stop-button', color='danger', className='mt-4', n_clicks=0),
                html.Div(id='stop-info', className='mt-3')
            ], md=4),
        ]),

    ], fluid=True)

    @app.callback(
        Output('thread-metrics', 'children'),
        Output('frequency-plot', 'figure'),
        Input('metrics-interval', 'n_intervals'),
    )
    def show_thread_metrics(_):
        """Soll-/Ist-Frequenzen anzeigen und in einem eigenen Plot vergleichen.

        Bei einem inaktiven Thread zeigen wir 0 Hz statt einer alten Messung.
        Die Sollwerte stammen aus der jeweiligen Konfiguration.
        """
        camera_mode = modes.get('fahrmodus_1')
        controller_target = float(camera_mode._frequency) if camera_mode else 0.0
        entries = [
            ('Kamera', float(sensor.scan_frequency), car.thread_metrics['camera']),
            ('Controller', controller_target, car.thread_metrics['controller']),
            ('Dashboard Rohbild', float(TARGET_FPS), stream_pre_processing.metrics),
            ('Dashboard verarbeitet', float(TARGET_FPS), stream_post_processing.metrics),
        ]
        names, targets, actuals, parts = [], [], [], []
        for name, target, metric in entries:
            age = metric.age_seconds
            active = age is not None and age <= 2.0
            actual = metric.hz if active else 0.0
            status = 'aktiv' if active else 'inaktiv / keine Daten'
            names.append(name)
            targets.append(target)
            actuals.append(actual)
            parts.append(html.Span(
                f'{name}: {target:.1f} / {actual:.1f} Hz ({status})',
                className='badge bg-secondary me-2 mb-2',
            ))

        figure = go.Figure()
        figure.add_bar(name='Soll', x=names, y=targets, marker_color='#7b8ca3')
        figure.add_bar(name='Ist', x=names, y=actuals, marker_color='#20a080')
        figure.update_layout(
            barmode='group',
            margin=dict(l=30, r=15, t=15, b=65),
            yaxis_title='Frequenz (Hz)',
            yaxis=dict(rangemode='tozero'),
            legend=dict(orientation='h', y=1.15),
            uirevision='frequenzen',
        )
        return parts, figure

    @app.callback(
        Output('hsv-info', 'children'),
        Input('h-slider', 'value'),
        Input('s-slider', 'value'),
        Input('v-slider', 'value'),
        Input('crop-slider', 'value'),
        Input('blur-slider', 'value'),
        Input('morph_kernel-slider', 'value')
    )
    def update_filter(h, s, v, crop, blur, morph_kernel):
        stream_pre_processing.set_hsv(h, s, v, crop, blur, morph_kernel)
        return f'H: {h}, S: {s}, V: {v}, crop: {crop}, blur: {blur}, morph: {morph_kernel}'

    @app.callback(
        Output('stop-info', 'children'),
        Input('stop-button', 'n_clicks'),
        prevent_initial_call=True
    )
    def stop_app(n_clicks):
        on_stop()
        threading.Timer(0.5, os.kill, args=(os.getpid(), signal.SIGINT)).start()
        return 'App beendet'

    @app.callback(
        Output('fahrmodus_state', 'data'),
        Output('button_fahrmodi', 'children'),
        Output('button_fahrmodi', 'color'),
        Output('dropdown_fahrmodus', 'disabled'),
        Input('button_fahrmodi', 'n_clicks'),
        State('fahrmodus_state', 'data'),
        State('dropdown_fahrmodus', 'value'),
        prevent_initial_call=True
    )
    def toggle_fahrmodus(n_clicks, running, selected):
        if running:
            for m in modes.values():
                m.stop()

            return False, 'Fahrmodus starten', 'success', False

        mode = modes.get(selected)

        if mode is None:
            log.warning(f'{selected} ist nicht implementiert')
            return False, 'Fahrmodus starten', 'success', False

        mode.start()

        return True, 'Fahrmodus stoppen', 'danger', True

    @app.callback(
        Output('save_data_state', 'data'),
        Output('button_save_data', 'children'),
        Output('button_save_data', 'color'),
        Input('button_save_data', 'n_clicks'),
        State('save_data_state', 'data'),
        prevent_initial_call=True
    )
    def toggle_save_data(n_clicks, running):
        if running:
            save_data.stop()
            return False, 'Datenaufnahme starten', 'primary'

        save_data.start()

        return True, 'Datenaufnahme stoppen', 'danger'

    return app


def shutdown(modes, car, sensor, save_data, threads, stop_event):
    # Reihenfolge:
    # SaveData_4_AI -> Driving -> CameraCar -> CameraSensor -> eigene Threads

    # Speichere Sliderdaten in config.json

    if not _shutdown_lock.acquire(blocking=False):
        return

    try:
        save_data.stop()
    except Exception:
        log.exception('Fehler beim Stoppen von SaveData_4_AI')

    for name, obj in [*modes.items(), ('CameraCar', car), ('CameraSensor', sensor)]:
        try:
            if hasattr(obj, 'stop'):
                obj.stop()
        except Exception:
            log.exception('Fehler beim Stoppen von %s', name)

    stop_event.set()

    for t in threads:
        t.join(timeout=2)

        if t.is_alive():
            log.warning('Thread %s läuft noch', t.name)

    log.info('Sauber beendet')


def main():

    car = CameraCar()
    sensor = CameraSensor(car)
    save_data = SaveData_4_AI(car=car, frequency=5.0)

    modes = {
        'fahrmodus_1': Driving.CameraMode(car=car),
        'fahrmodus_ps': Driving.PSControllerMode(car=car)
    }

    stream_pre_processing = ImageStream(car, sensor, modes.get('fahrmodus_1'), use_raw_data=True)
    stream_post_processing = ImageStream(car, sensor, modes.get('fahrmodus_1'), use_raw_data=False)

    stop_event = threading.Event()

    threads = [
        threading.Thread(target=sensor.read_loop, args=(stop_event,), daemon=True, name='sensor'),
        threading.Thread(target=stream_pre_processing.run, args=(stop_event,), daemon=True, name='pre_processes_stream'),
        threading.Thread(target=stream_post_processing.run, args=(stop_event,), daemon=True, name='post_processed_stream'),
    ]

    for t in threads:
        t.start()

    try:
        app = create_app(
            modes,
            car,
            stream_pre_processing,
            stream_post_processing,
            sensor,
            save_data,
            on_stop=lambda: shutdown(modes, car, sensor, save_data, threads, stop_event)
        )

        # Reloader aus, sonst öffnet ein zweiter Prozess die Kamera
        app.run(host='0.0.0.0', debug=False, use_reloader=False)

    finally:
        shutdown(modes, car, sensor, save_data, threads, stop_event)


if __name__ == '__main__':
    main()


    # 1: [X] Sensor-Funktion bauen (Thread-sicher, vgl. InfraredSensor:43) zum Aufnehmen der Slider-Einstellungen
    # 2: [X] Sensor an App übergeben ('update_filter') zur Übergabe der Filterwerte
    # 3: [HOLD] inkl. Switch RGBBHRGREYHSV...
    # 4: [X] Im Sensor die Bildbearbeitung durchführen und img_processed setzen in datapacket
    # 5: inkl. Linienerkennung einmalen in img_processed
    #    --- Wenn nur eine Linie zu sehen ist - Linie rechts im Bild -> Lenke rechts und fahre langsam bis wieder zwei linien zu sehen sind - Analog ander seite
    # 6: [X] Start Drive/Stop Drive Button (Analog Dashboard_Live_Data.py) Zeile: 460 - 475, 331ff
    # 7: [X] Start Button: Fange an Bilder zu speichern
    #    --- Unterordner (fig) erstellen, dann Timestamp (Ordnername), in einem eigenen Thread laufen lassen, Speicherfrequenz, time.sleep(frequenz - Delta t) Delta t ist speicherdauer
    #    --- Bild braucht eindeutigen Namen - Parameter in Tabelle (Spalte 1 dann Bildname (eindeutig), Parameter)
    #    --- Bild nur speicher, wenn bedingung erfüllt ist zB 2 Fahrbahnlinien sind da
    # 8: [X] Button: Manuel Fahren, Driving Mode umschalten

    '''
    slider für blur, morph kernel
    '''
