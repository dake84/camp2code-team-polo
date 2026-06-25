import dash_bootstrap_components as dbc
import pandas as pd

from dash import Dash, Input, Output, State, dcc, html

from SonicCar import SonicCar

import threading
app = Dash(external_stylesheets=[dbc.themes.BOOTSTRAP])

car = SonicCar()

app.layout = dbc.Container(
    [
        dbc.Row(
            [
                html.H1("Camp2Code: Car Dashboard"),
            ]
        ),
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H1('Live-Werte'),
                        html.P(id = 'Time'),
                        html.P(id = 'Distance'),
                        html.P(id = 'Speed'),
                        html.P(id = 'Steer'),
                        dcc.Interval(
                            id = 'interval',
                            interval = 200,   # alle 500 ms
                            n_intervals = 0,
                        ),
                        dbc.Button("Start Fahrmodi",
                            id = "button_start_fahrmodi",
                            n_clicks = 0,
                        )
                    ],
                    width = 3,
                ),
                dbc.Col(
                    [
                        dcc.Graph(id = "g1"),
                    ]
                ),
            ]
        ),
    ]
)

# Refresh die Daten auf dem Dashboard, zieht die Daten aus __init__ von SonicCar
@app.callback(
    Output('Time', 'children'),
    Output('Distance', 'children'),
    Output('Speed', 'children'),
    Output('Steer', 'children'),
    Input('interval', 'n_intervals'),
)
def update_values(n):
    with car._sensor_lock:
        time = car._latest_distance_time
        distance = car._latest_distance
        speed = car._latest_speed
        steering_angle = car._latest_steering_angle
    return f'Time {time}', f'Distance: {distance}', f'Speed: {speed}', f'Steer: {steering_angle}'


# Button, bei n_clicks wird start_fahrmodus gestartet
@app.callback(
    Input('button_start_fahrmodi', 'n_clicks'),
    prevent_initial_call=True
)
def start_fahrmodus(clicks): # Für jeden Input brauch tdie Funktion eine Variable
    threading.Thread(target = car.fahrmodus_4(Fahrdauer = 5), daemon = True).start()




if __name__ == "__main__":
    app.run(host = "0.0.0.0", debug = True)