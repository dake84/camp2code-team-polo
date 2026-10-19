import cv2
import dash_bootstrap_components as dbc
import numpy as np
from basisklassen_cam import Camera
from dash import Dash, Input, Output, dcc, html
from flask import Flask, Response

external_stylesheets = [dbc.themes.CERULEAN]
server = Flask(__name__)
app = Dash(external_stylesheets=external_stylesheets, server=server)


@server.route("/test")
def unterseite_test():
    return "Hier ist die Testseite"


cam = Camera()

hsv_range = {
    "lower": (90, 20, 50),
    "upper": (120, 255, 255),
}


def generate_stream(cam):
    while True:
        frame = cam.get_frame()
        resized = cv2.resize(frame, None, fx=0.5, fy=0.5)

        # 1. HSV-Farbfilter
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)

        lower = np.array(hsv_range["lower"], dtype=np.uint8)
        upper = np.array(hsv_range["upper"], dtype=np.uint8)
        filtered = cv2.inRange(hsv, lower, upper)

        # 2. Canny Edge Detection
        edges = cv2.Canny(filtered, 50, 150)

        # 3. Hough Lines P
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=50,
            minLineLength=40,
            maxLineGap=10,
        )

        # 4. Erkannte Linien einzeichnen
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line
                cv2.line(resized, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # 5. Stream erzeugen
        _, frame_as_jpeg = cv2.imencode(".jpeg", resized)
        frame_in_bytes = frame_as_jpeg.tobytes()
        frame_as_string = (
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
            + frame_in_bytes
            + b"\r\n\r\n"
        )
        yield frame_as_string


@server.route("/stream")
def stream():
    return Response(
        generate_stream(cam),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


app.layout = dbc.Container(
    [
        dbc.Row([html.H1("Projektphase 2", id="title")]),
        dbc.Row(
            [
                dbc.Col([html.Img(src="/stream")]),
                dbc.Col(
                    [
                        dcc.RangeSlider(
                            0, 179, value=[90, 120], id="h-slider"
                        ),
                        dcc.RangeSlider(
                            0, 255, value=[20, 255], id="s-slider"
                        ),
                        dcc.RangeSlider(
                            0, 255, value=[50, 255], id="v-slider"
                        ),
                    ]
                ),
            ]
        ),
    ]
)


@app.callback(
    Output("title", "children"),
    Input("h-slider", "value"),
    Input("s-slider", "value"),
    Input("v-slider", "value"),
)
def update_filter(h, s, v):
    hsv_range["lower"] = (h[0], s[0], v[0])
    hsv_range["upper"] = (h[1], s[1], v[1])

    return f"Projektphase 2 - H: {h}, S: {s}, V: {v}"


if __name__ == "__main__":
    app.run(host="0.0.0.0")