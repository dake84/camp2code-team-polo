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

import logging_setup  # noqa: F401  – konfiguriert Logging beim Import

# Unsere Packge für CameraCar
import Driving

from CameraCar import CameraCar, CameraData, CameraDataProcessed
from CameraSensor import CameraSensor

# --- Konfiguration ----------------------------------------------------------
DEFAULT_HSV = {"h": (100, 130), "s": (80, 255), "v": (50, 255), "crop": (0.2,0.8), "hough_threshold": 35}
SCALE = 0.5
TARGET_FPS = 15
JPEG_QUALITY = 70

log = logging.getLogger(__name__)
_shutdown_lock = threading.Lock()



class ImageStream:
    """Importiert Kamerabilder in einem eigenen Thread und hält das letzte Ergebnis bereit."""

    def __init__(self, car, sensor, use_raw_data:bool=True):
        self.car = car
        self._hsv_lock = threading.Lock()
        self._lower = np.array([DEFAULT_HSV[k][0] for k in "hsv"], dtype=np.uint8)
        self._upper = np.array([DEFAULT_HSV[k][1] for k in "hsv"], dtype=np.uint8)
        self._frame_cond = threading.Condition()
        self.use_raw_data = use_raw_data
        self._jpeg = None
        self.lines = None  # Koordinaten im skalierten Bild (Faktor SCALE)!
        self.sensor = sensor



    def set_hsv(self, hue, saturation, value, crop, hough_threshold):
        pack_processing_parameters = (hue, saturation, value, crop, hough_threshold)
        self.sensor.processing_parameters = pack_processing_parameters

    def run(self, stop_event):
        period = 1.0 / TARGET_FPS
        while not stop_event.is_set():
            t0 = time.monotonic()
            try:
                self._process_once()
            except Exception:
                log.exception("Fehler in der Bildverarbeitung")
                stop_event.wait(1.0)  # im Fehlerfall nicht im Vollgas loopen
            stop_event.wait(max(0.0, period - (time.monotonic() - t0)))

    def _process_once(self):
        frame = None
        line_data = None
        steering_angle = None
        speed = None

        if self.use_raw_data:
            frame = self.car.camera_data.raw_image
        else:
            frame = self.car.camera_data_processed.process_image_lines
            line_data = self.car.camera_data_processed.line_data
            steering_angle = self.car.camera_data_processed.steering_angle
            speed = self.car.camera_data_processed.speed

        if frame is None or frame.size == 0:
            return

        ok, jpeg = cv2.imencode(".jpg", frame,
                                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                                )
        if ok:
            with self._frame_cond:
                self._jpeg = jpeg.tobytes()
                self._frame_cond.notify_all()

    def mjpeg_stream(self):
        """Liefert nur fertige Frames aus – beliebig viele Clients, eine Verarbeitung."""
        while True:
            with self._frame_cond:
                self._frame_cond.wait(timeout=1.0)
                jpeg = self._jpeg
            if jpeg is None:
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"

    # def shutdown(mode, car, sensor, threads, stop_event):
    #  # Reihenfolge: Driving -> CameraCar -> CameraSensor -> eigene Threads.'''
    #  if not _shutdown_lock.acquire(blocking=False):
    #      return  # Shutdown läuft bereits
    #  for name, obj in (('Driving', mode), ('CameraCar', car), ('CameraSensor', sensor)):
    #      try:
    #          if hasattr(obj, 'stop'): obj.stop()
    #      except Exception:
    #          log.exception('Fehler beim Stoppen von %s', name)
    #  stop_event.set()
    #  for t in threads:
    #      t.join(timeout=2)
    #      if t.is_alive(): log.warning('Thread %s läuft noch', t.name)
    #  log.info('Sauber beendet')


def create_app(mode, car, stream_pre_processing: ImageStream, stream_post_processing: ImageStream, sensor: CameraSensor, on_stop) -> Dash:
    server = Flask(__name__)
    app = Dash(__name__, server=server, external_stylesheets=[dbc.themes.CERULEAN])

    @server.route("/test")
    def unterseite_test():
        return "Hier ist die Testseite"

    @server.route("/stream")
    def stream():
        return Response(stream_pre_processing.mjpeg_stream(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    @server.route("/stream2")
    def stream2():
        return Response(stream_post_processing.mjpeg_stream(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    def slider(channel, max_value, increment=1.0):
        default = DEFAULT_HSV[channel]
        if isinstance(default, (tuple, list)):
            component = dcc.RangeSlider(0, max_value, value=list(default), step=increment,
                                        id=f'{channel}-slider',
                                        tooltip={'placement': 'bottom'})
        else:
            component = dcc.Slider(0, max_value, value=default, step=increment,
                                   id=f'{channel}-slider',
                                   tooltip={'placement': 'bottom'})
        return html.Div([
            html.Label(channel.upper()),
            component,
        ])

    app.layout = dbc.Container([
        dbc.Row(html.H1("Projektphase 2")),
        dbc.Row([
            dbc.Col(html.Img(src="/stream", style={"width": "100%"}), md=4),
            dbc.Col(html.Img(src="/stream2", style={"width": "100%"}), md=4),
            dbc.Col([
                slider("h", 179),
                slider("s", 255),
                slider("v", 255),
                slider("crop", 1, 0.05),
                slider("hough_threshold", 100, 1),
                html.Div(id="hsv-info", className="mt-3")
            ], md=4),
        ]),
        dbc.Row([
            dbc.Col([             
                html.Label('Fahrmodus wählen'),
                dcc.Dropdown(
                    id='dropdown_fahrmodus',
                    options=[
                        {'label': 'Fahrmodus 1 - Bilderkennung', 'value': 'fahrmodus_1'},
                        {'label': 'Fahrmodus 2 - AI', 'value': 'fahrmodus_2'}
                    ],
                    value='fahrmodus_1',
                    clearable=False,
                    placeholder='Bitte Fahrmodus wählen',
                ),
                dbc.Button(
                    'Fahrmodus starten',
                    id='button_fahrmodi',
                    n_clicks=0,
                    color='success',
                    className='mt-4'
                ),
                dcc.Store(
                    id='fahrmodus_state',
                    data=False
                )
            ])
        ]),
        dbc.Row([
        dbc.Col([
            dbc.Button('Close App', id='stop-button', color='danger',
                        className='mt-4', n_clicks=0),
            html.Div(id='stop-info', className='mt-3')
        ], md=1),
        ]),
    ], fluid=True)
    
    

    @app.callback(
        Output('hsv-info', 'children'),
        Input('h-slider', 'value'),
        Input('s-slider', 'value'),
        Input('v-slider', 'value'),
        Input('crop-slider', 'value'),
        Input('hough_threshold-slider', 'value')
    )
    def update_filter(h, s, v, crop, hough_threshold):
        stream_pre_processing.set_hsv(h, s, v, crop, hough_threshold)
        stream_post_processing.set_hsv(h, s, v, crop, hough_threshold)

        return f'H: {h}, S: {s}, V: {v}, crop: {crop}, hough-thresh: {hough_threshold}'

    @app.callback(
        Output("stop-info", "children"),
        Input("stop-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def stop_app(n_clicks):
        on_stop()  # Driving -> CameraCar -> CameraSensor -> Threads
        threading.Timer(0.5, os.kill, args=(os.getpid(), signal.SIGINT)).start()  # danach App beenden
        return 'App beendet'

    @app.callback(
        Output('fahrmodus_state', 'data'),
        Output('button_fahrmodi', 'children'),
        Output('button_fahrmodi', 'color'),
        Input('button_fahrmodi', 'n_clicks'),
        State('fahrmodus_state', 'data'),
        prevent_initial_call=True
    )
    def toggle_fahrmodus(n_clicks, running):
        if running:
            mode.stop()
            return False, 'Fahrmodus starten', 'success'
        mode.start()
        return True, 'Fahrmodus stoppen', 'danger'


    return app

    @app.callback(
        Output("stop-info", "children"),
        Input("stop-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def stop_app(n_clicks):
        os._exit(0)

def shutdown(mode, car, sensor, threads, stop_event):
    # Reihenfolge: Driving -> CameraCar -> CameraSensor -> eigene Threads.

    # Speichere Sliderdaten in config.json

    if not _shutdown_lock.acquire(blocking=False):
        return  # Shutdown läuft bereits
    for name, obj in (('Driving', mode), ('CameraCar', car), ('CameraSensor', sensor)):
        try:
            if hasattr(obj, 'stop'): obj.stop()
        except Exception:
            log.exception('Fehler beim Stoppen von %s', name)
    stop_event.set()
    for t in threads:
        t.join(timeout=2)
        if t.is_alive(): log.warning('Thread %s läuft noch', t.name)
    log.info('Sauber beendet')

def main():

    car = CameraCar()
    sensor = CameraSensor(car)
    mode = Driving.CameraMode(car=car)
    stream_pre_processing = ImageStream(car, sensor, use_raw_data=True)
    stream_post_processing = ImageStream(car, sensor, use_raw_data=False)

    stop_event = threading.Event()
    threads = [
        threading.Thread(target=sensor.read_loop, args=(stop_event,),
                         daemon=True, name="sensor"),
        threading.Thread(target=stream_pre_processing.run, args=(stop_event,),
                         daemon=True, name="pre_processes_stream"),
        threading.Thread(target=stream_post_processing.run, args=(stop_event,),
                         daemon=True, name="post_processed_stream"),
    ]
    for t in threads:
        t.start()
    
    #mode.start()

    try:
        app = create_app(mode, car, stream_pre_processing, stream_post_processing, sensor,
                         on_stop=lambda: shutdown(mode, car, sensor, threads, stop_event))
        # Reloader aus, sonst öffnet ein zweiter Prozess die Kamera
        app.run(host="0.0.0.0", debug=False, use_reloader=False)

    finally:
        shutdown(mode, car, sensor, threads, stop_event)











def register_callbacks(app, driving):

    @app.callback(
        Output('fahrmodus_state', 'data'),
        Output('button_fahrmodi', 'children'),
        Output('button_fahrmodi', 'color'),
        Input('button_fahrmodi', 'n_clicks'),
        State('fahrmodus_state', 'data'),
        prevent_initial_call=True
    )
    def toggle_fahrmodus(n_clicks, running):

        if running:
            driving.stop()

            return (
                False,
                'Fahrmodus starten',
                'success'
            )

        else:
            threading.Thread(
                target=driving.start,
                daemon=True
            ).start()

            return (
                True,
                'Fahrmodus stoppen',
                'danger'
            )









if __name__ == "__main__":
    main()


    # 1: [X] Sensor-Funktion bauen (Thread-sicher, vgl. InfraredSensor:43) zum Aufnehmen der Slider-Einstellungen 
    # 2: [X] Sensor an App übergeben ("update_filter") zur Übergabe der Filterwerte
    # 3: [HOLD] inkl. Switch RGBBHRGREYHSV...
    # 4: [X] Im Sensor die Bildbearbeitung durchführen und img_processed setzen in datapacket
    # 5: inkl. Linienerkennung einmalen in img_processed
    #    --- Wenn nur eine Linie zu sehen ist - Linie rechts im Bild -> Lenke rechts und fahre langsam bis wieder zwei linien zu sehen sind - Analog ander seite
    # 6: Start Drive/Stop Drive Button (Analog Dashboard_Live_Data.py) Zeile: 460 - 475, 331ff
    # 7: Start Button: Fange an Bilder zu speichern
    #    --- Unterordner (fig) erstellen, dann Timestamp (Ordnername), in einem eigenen Thread laufen lassen, Speicherfrequenz, time.sleep(frequenz - Delta t) Delta t ist speicherdauer
    #    --- Bild braucht eindeutigen Namen - Parameter in Tabelle (Spalte 1 dann Bildname (eindeutig), Parameter)
    #    --- Bild nur speicher, wenn bedingung erfüllt ist zB 2 Fahrbahnlinien sind da
    # 8: Button: Manuel Fahren, Driving Mode umschalten

    # Fragen: 
    # 1: Hat CameraSensor, Driving und CameraCar ein Stop event, dass wir für einen Stop Button nutzen können um alles sauber zu schließen?
    """
    slider für blur, morph kernel
    """