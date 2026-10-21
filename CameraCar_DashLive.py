import logging
import threading
import time

import cv2 #Bildverarbeitung
import dash_bootstrap_components as dbc
import numpy as np
from dash import Dash, Input, Output, dcc, html
from flask import Flask, Response

import logging_setup  # noqa: F401  – konfiguriert Logging beim Import

# Unsere Packge für CameraCar
import Driving

from CameraCar import CameraCar, CameraData, CameraDataProcessed
from CameraSensor import CameraSensor

# Objekt: CaemraCar -> cc
cc = CameraCar()

# Opjekt: CameraMode erbt von cc
cs = CameraSensor(cc)

# Stop events für driving und fü rcs
# Hier getrennt, da zB Sensor laufen lassen aber Car soll stehen bleiben
sensor_stop_event = threading.Event()
drive_stop_event = threading.Event()

driving = Driving.CameraMode(car = cc)

camera_sensor_thread = threading.Thread(
    target=cs.read_loop,
    args=(sensor_stop_event,),
    daemon=True
)

camera_sensor_thread.start()


log = logging.getLogger(__name__)

# --- Konfiguration ----------------------------------------------------------
DEFAULT_HSV = {"h": (90, 120), "s": (20, 255), "v": (50, 255)}
SCALE = 0.5
TARGET_FPS = 15
JPEG_QUALITY = 70


class ImageStream:
    """Importiert Kamerabilder in einem eigenen Thread und hält das letzte Ergebnis bereit."""

    def __init__(self, car):
        self.car = car
        self._hsv_lock = threading.Lock()
        self._lower = np.array([DEFAULT_HSV[k][0] for k in "hsv"], dtype=np.uint8)
        self._upper = np.array([DEFAULT_HSV[k][1] for k in "hsv"], dtype=np.uint8)
        self._frame_cond = threading.Condition()
        self.use_raw_data = True
        self._jpeg = None
        self.lines = None  # Koordinaten im skalierten Bild (Faktor SCALE)!

    def set_hsv(self, h, s, v):
        lower = np.array([h[0], s[0], v[0]], dtype=np.uint8)
        upper = np.array([h[1], s[1], v[1]], dtype=np.uint8)
        with self._hsv_lock:  # beide Grenzen zusammen tauschen
            self._lower, self._upper = lower, upper

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
            frame = self.car.camera_data_processed.process_image
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


def create_app(stream: ImageStream, sensor: CameraSensor) -> Dash:
    server = Flask(__name__)
    app = Dash(__name__, server=server, external_stylesheets=[dbc.themes.CERULEAN])

    @server.route("/test")
    def unterseite_test():
        return "Hier ist die Testseite"

    @server.route("/stream")
    def stream():
        return Response(stream.mjpeg_stream(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    def slider(channel, max_value):
        return html.Div([
            html.Label(channel.upper()),
            dcc.RangeSlider(0, max_value, value=list(DEFAULT_HSV[channel]),
                            id=f"{channel}-slider",
                            tooltip={"placement": "bottom"}),
        ])

    app.layout = dbc.Container([
        dbc.Row(html.H1("Projektphase 2")),
        dbc.Row([
            dbc.Col(html.Img(src="/stream", style={"width": "100%"}), md=8),
            dbc.Col([
                slider("h", 179),
                slider("s", 255),
                slider("v", 255),
                html.Div(id="hsv-info", className="mt-3"),
            ], md=4),
        ]),
    ], fluid=True)

    @app.callback(
        Output("hsv-info", "children"),
        Input("h-slider", "value"),
        Input("s-slider", "value"),
        Input("v-slider", "value"),
    )
    def update_filter(h, s, v):
        stream.set_hsv(h, s, v)
        return f"H: {h}, S: {s}, V: {v}"

    return app


def main():
    car = CameraCar()
    sensor = CameraSensor(car)
    mode = Driving.CameraMode(car=car)
    processor = ImageStream(car)

    stop_event = threading.Event()
    threads = [
        threading.Thread(target=sensor.read_loop, args=(stop_event,),
                         daemon=True, name="sensor"),
        threading.Thread(target=processor.run, args=(stop_event,),
                         daemon=True, name="processor"),
    ]
    for t in threads:
        t.start()
    mode.start()

    try:
        app = create_app(processor)
        # Reloader aus, sonst öffnet ein zweiter Prozess die Kamera
        app.run(host="0.0.0.0", debug=False, use_reloader=False)
    finally:
        stop_event.set()
        if hasattr(mode, "stop"):  # an eure Driving-API anpassen
            mode.stop()
        for t in threads:
            t.join(timeout=2)
        log.info("Sauber beendet")


if __name__ == "__main__":
    main()