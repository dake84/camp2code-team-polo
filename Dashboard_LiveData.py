import threading
import time
from datetime import datetime

import numpy as np  # ✅ NEU

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html

import InfraredSensor
import UltrasonicSensor
import BaseCar
import SensorCar
from Driving import DriveController, DrivingMode

app = Dash(external_stylesheets=[dbc.themes.BOOTSTRAP])

bc = BaseCar.BaseCar()
sc = SensorCar.SensorCar()

ir = InfraredSensor.InfraredSensor(sc)
us = UltrasonicSensor.UltrasonicSensor(sc)

sensor_stop_event = threading.Event()
drive_stop_event = threading.Event()

us_sensor_thread = threading.Thread(
    target=us.read_loop,
    args=(sensor_stop_event,),
    daemon=True
)
ir_sensor_thread = threading.Thread(
    target=ir.read_loop,
    args=(sensor_stop_event,),
    daemon=True
)

us_sensor_thread.start()
ir_sensor_thread.start()

# Listen für Liveplot
plot_time = []
plot_distance = []
plot_speed = []
plot_steering_angle = []
plot_p_glied = []
plot_i_glied = []
plot_d_glied = []

measurement_active = False
measurement_start_time = None
measurement_max_time = 0
controller_thread = None
show_distance_plot = False
show_pid_plot = False
active_car = sc


def add_min_max_annotations(figure, values, unit=''):
    if len(values) > 0:
        max_value = max(values)
        min_value = min(values)
        max_text = f'Max: {max_value:.1f}{unit}'
        min_text = f'Min: {min_value:.1f}{unit}'
    else:
        max_text = 'Max: -'
        min_text = 'Min: -'

    figure.add_annotation(x=0.99, y=0.98, xref='paper', yref='paper',
                          text=max_text, showarrow=False, xanchor='right', yanchor='top')

    figure.add_annotation(x=0.99, y=0.02, xref='paper', yref='paper',
                          text=min_text, showarrow=False, xanchor='right', yanchor='bottom')


def create_figure(x_data, y_data, title, y_title, unit=''):
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=x_data, y=y_data, mode='lines+markers'))
    figure.update_layout(title=title, xaxis_title='Zeit [s]', yaxis_title=y_title)
    add_min_max_annotations(figure, y_data, unit)
    return figure


app.layout = dbc.Container(
    [
        dbc.Row([html.H1('Camp2Code: Car Dashboard')]),
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H1('Live-Werte'),
                        html.P(id='Time'),
                        html.P(id='Distance'),  # ✅ wird jetzt live berechnet
                        html.P(id='Speed'),
                        html.P(id='Steer'),
                        html.P(id='Status'),

                        dcc.Interval(id='interval', interval=200, n_intervals=0),

                        html.Label('Fahrmodus wählen'),
                        dcc.Dropdown(
                            id='dropdown_fahrmodus',
                            options=[
                                {'label': 'Fahrmodus 1', 'value': 'fahrmodus_1'},
                                {'label': 'Fahrmodus 2', 'value': 'fahrmodus_2'},
                                {'label': 'Fahrmodus 3', 'value': 'fahrmodus_3'},
                                {'label': 'Fahrmodus 4', 'value': 'fahrmodus_4'},
                                {'label': 'Fahrmodus 5', 'value': 'fahrmodus_5'},
                            ],
                            value='fahrmodus_1',
                            clearable=False,
                        ),

                        dbc.Button('Start', id='button_start_fahrmodi', n_clicks=0, className='mt-4'),
                        dbc.Button('Stop', id='button_stop_fahrmodi', n_clicks=0, color='danger', className='mt-4')
                    ],
                    width=3,
                ),
                dbc.Col(
                    [
                        dcc.Graph(id='g_speed'),
                        dcc.Graph(id='g_angle'),
                        dcc.Graph(id='g_distance'),
                    ],
                    width=5,
                )
            ]
        )
    ]
)


@app.callback(
    Output('Time', 'children'),
    Output('Distance', 'children'), 
    Output('Speed', 'children'),
    Output('Steer', 'children'),
    Output('g_distance', 'figure'),
    Output('g_speed', 'figure'),
    Output('g_angle', 'figure'),
    Input('interval', 'n_intervals'),
)
def update_values(n):
    global measurement_active, measurement_start_time

    current_time = datetime.now().strftime('%H:%M:%S')

    speed = active_car.speed * active_car.direction
    steering_angle = active_car.steering_angle

    # Daten sammeln
    if measurement_active and measurement_start_time is not None:
        elapsed = time.time() - measurement_start_time

        plot_time.append(elapsed)
        plot_speed.append(speed)
        plot_steering_angle.append(steering_angle)


    distance_total = 0
    if len(plot_time) > 1:
        t = np.array(plot_time)
        v = np.array(plot_speed)
        dt = np.diff(t)
        distance_total = np.sum((v[:-1] + v[1:]) / 2 * dt)

    figure_speed = create_figure(plot_time, plot_speed, 'Geschwindigkeit', 'v')
    figure_angle = create_figure(plot_time, plot_steering_angle, 'Lenkwinkel', '°')
    figure_distance = create_figure(plot_time, plot_speed, 'Distanz (Proxy)', 'cm')  # optional

    return (
        f'Time: {current_time}',
        f'Distance: {distance_total:.2f} cm',  # ✅ LIVE
        f'Speed: {speed}',
        f'Steer: {steering_angle}',
        figure_distance,
        figure_speed,
        figure_angle
    )


@app.callback(
    Output('Status', 'children'),
    Input('button_start_fahrmodi', 'n_clicks'),
    State('dropdown_fahrmodus', 'value'),
    prevent_initial_call=True
)
def start_fahrmodus(clicks, fahrmodus):
    global measurement_active, measurement_start_time

    plot_time.clear()
    plot_speed.clear()

    drive_stop_event.clear()

    active_car = bc
    drive_con = DriveController(bc, driving_mode=DrivingMode.FORWARD_BACKWARD)

    threading.Thread(
        target=drive_con.drive_car,
        args=(drive_stop_event,),
        daemon=True
    ).start()

    measurement_start_time = time.time()
    measurement_active = True

    return 'Gestartet'


@app.callback(
    Output('button_stop_fahrmodi', 'children'),
    Input('button_stop_fahrmodi', 'n_clicks'),
    prevent_initial_call=True
)
def stop_fahrmodus(clicks):
    global measurement_active
    drive_stop_event.set()
    measurement_active = False
    active_car.stop()
    return 'Stop'


if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)