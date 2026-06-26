import threading
import time
from datetime import datetime
from collections import deque

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html, no_update

import InfraredSensor
import UltrasonicSensor
import BaseCar
import SensorCar
from Driving import DriveController, DrivingMode


app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])

bc = BaseCar.BaseCar()
sc = SensorCar.SensorCar()

ir = InfraredSensor.InfraredSensor(sc)
us = UltrasonicSensor.UltrasonicSensor(sc)

sensor_stop_event = threading.Event()
drive_stop_event = threading.Event()

data_lock = threading.Lock()

max_plot_points = 300

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


plot_time = deque(maxlen=max_plot_points)
plot_distance = deque(maxlen=max_plot_points)
plot_speed = deque(maxlen=max_plot_points)
plot_steering_angle = deque(maxlen=max_plot_points)
plot_p_glied = deque(maxlen=max_plot_points)
plot_i_glied = deque(maxlen=max_plot_points)
plot_d_glied = deque(maxlen=max_plot_points)

measurement_active = False
measurement_start_time = None
measurement_max_time = 0

controller_thread = None
show_distance_plot = False
show_pid_plot = False
active_car = sc


def add_min_max_annotations(figure, values, unit=''):
    """Fügt Min- und Max-Werte als Annotation in den Plot ein.

    Args:
        figure: Plotly-Figure, die erweitert werden soll.
        values: Liste oder Deque mit y-Werten.
        unit: Einheit als Text.
    """
    values = list(values)

    if len(values) > 0:
        max_value = max(values)
        min_value = min(values)
        max_text = f'Max: {max_value:.1f}{unit}'
        min_text = f'Min: {min_value:.1f}{unit}'
    else:
        max_text = 'Max: -'
        min_text = 'Min: -'

    figure.add_annotation(
        x=0.99,
        y=0.98,
        xref='paper',
        yref='paper',
        text=max_text,
        showarrow=False,
        xanchor='right',
        yanchor='top'
    )

    figure.add_annotation(
        x=0.99,
        y=0.02,
        xref='paper',
        yref='paper',
        text=min_text,
        showarrow=False,
        xanchor='right',
        yanchor='bottom'
    )


def create_figure(x_data, y_data, title, y_title, unit=''):
    """Erstellt einen schnellen Liveplot.

    Args:
        x_data: Werte für die x-Achse.
        y_data: Werte für die y-Achse.
        title: Titel des Plots.
        y_title: Beschriftung der y-Achse.
        unit: Einheit für Min-/Max-Anzeige.

    Returns:
        Plotly-Figure.
    """
    figure = go.Figure()

    figure.add_trace(
        go.Scattergl(
            x=list(x_data),
            y=list(y_data),
            mode='lines'
        )
    )

    figure.update_layout(
        title=title,
        xaxis_title='Zeit [s]',
        yaxis_title=y_title,
        margin=dict(l=40, r=20, t=45, b=35),
        height=260,
        uirevision='live'
    )

    add_min_max_annotations(figure, y_data, unit)

    return figure


def create_empty_figure(title):
    """Erstellt einen leeren Plot.

    Args:
        title: Titel des leeren Plots.

    Returns:
        Leere Plotly-Figure.
    """
    figure = go.Figure()

    figure.update_layout(
        title=title,
        margin=dict(l=40, r=20, t=45, b=35),
        height=260
    )

    return figure


def reset_plot_data():
    """Löscht alle Plotdaten."""
    with data_lock:
        plot_time.clear()
        plot_distance.clear()
        plot_speed.clear()
        plot_steering_angle.clear()
        plot_p_glied.clear()
        plot_i_glied.clear()
        plot_d_glied.clear()


def stop_current_drive():
    """Stoppt die aktive Fahrt möglichst zuverlässig."""
    global measurement_active

    drive_stop_event.set()
    measurement_active = False

    if controller_thread is not None and controller_thread.is_alive():
        controller_thread.join(timeout=0.5)

    bc.stop()
    sc.stop()


app.layout = dbc.Container(
    [
        dbc.Row(
            [
                html.H1('Camp2Code: Car Dashboard'),
            ]
        ),
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H1('Live-Werte'),
                        html.P(id='Time'),
                        html.P(id='Speed'),
                        html.P(id='Steer'),
                        html.P(id='Status'),

                        dcc.Interval(
                            id='interval_values',
                            interval=200,
                            n_intervals=0,
                        ),

                        dcc.Interval(
                            id='interval_graphs',
                            interval=1000,
                            n_intervals=0,
                        ),

                        html.Label('Fahrmodus wählen'),

                        dcc.Dropdown(
                            id='dropdown_fahrmodus',
                            options=[
                                {'label': 'Fahrmodus 1 - Vorwärts und Rückwärts', 'value': 'fahrmodus_1'},
                                {'label': 'Fahrmodus 2 - Kreisfahrt mit maximalem Lenkwinkel', 'value': 'fahrmodus_2'},
                                {'label': 'Fahrmodus 3 - Vorwärtsfahrt bis Hindernis', 'value': 'fahrmodus_3'},
                                {'label': 'Fahrmodus 4 - Erkennungstour', 'value': 'fahrmodus_4'},
                                {'label': 'Fahrmodus 5 - Linienverfolgung', 'value': 'fahrmodus_5'},
                                {'label': 'Fahrmodus 6 - Erweiterte Linienverfolgung', 'value': 'fahrmodus_6'},
                                {'label': 'Fahrmodus 7', 'value': 'fahrmodus_7'},
                            ],
                            value='fahrmodus_1',
                            clearable=False,
                            placeholder='Bitte Fahrmodus wählen',
                        ),

                        dbc.Button(
                            'Start Fahrmodi',
                            id='button_start_fahrmodi',
                            n_clicks=0,
                            className='mt-4'
                        ),

                        dbc.Button(
                            'Stop',
                            id='button_stop_fahrmodi',
                            n_clicks=0,
                            color='danger',
                            className='mt-4 ms-2'
                        )
                    ],
                    width=3,
                ),

                dbc.Col(
                    [
                        dcc.Graph(id='g_speed', figure=create_empty_figure('Geschwindigkeit über Zeit')),
                        dcc.Graph(id='g_angle', figure=create_empty_figure('Lenkwinkel über Zeit')),
                        dcc.Graph(id='g_distance', figure=create_empty_figure('Distanz über Zeit')),
                    ],
                    width=5,
                ),

                dbc.Col(
                    [
                        dcc.Graph(id='g_p_glied', figure=create_empty_figure('P-Glied über Zeit')),
                        dcc.Graph(id='g_i_glied', figure=create_empty_figure('I-Glied über Zeit')),
                        dcc.Graph(id='g_d_glied', figure=create_empty_figure('D-Glied über Zeit')),
                    ],
                    width=4,
                )
            ]
        )
    ],
    fluid=True
)


@app.callback(
    Output('Time', 'children'),
    Output('Speed', 'children'),
    Output('Steer', 'children'),
    Input('interval_values', 'n_intervals'),
)
def update_values(n):
    global measurement_active, measurement_start_time, measurement_max_time
    global controller_thread, active_car

    current_time = datetime.now().strftime('%H:%M:%S')

    speed = active_car.speed * active_car.direction
    steering_angle = active_car.steering_angle

    if measurement_active and measurement_start_time is not None:
        elapsed = time.time() - measurement_start_time

        with data_lock:
            plot_time.append(elapsed)
            plot_speed.append(speed)
            plot_steering_angle.append(steering_angle)

            if show_distance_plot:
                plot_distance.append(sc.distance)

            if show_pid_plot:
                plot_p_glied.append(sc.p_wert)
                plot_i_glied.append(sc.i_wert)
                plot_d_glied.append(sc.d_wert)

        if elapsed >= measurement_max_time:
            stop_current_drive()

    if controller_thread is not None and not controller_thread.is_alive():
        measurement_active = False

    return (
        f'Time: {current_time}',
        f'Speed: {speed}',
        f'Steer: {steering_angle}'
    )


@app.callback(
    Output('g_distance', 'figure'),
    Output('g_speed', 'figure'),
    Output('g_angle', 'figure'),
    Output('g_p_glied', 'figure'),
    Output('g_i_glied', 'figure'),
    Output('g_d_glied', 'figure'),
    Output('g_distance', 'style'),
    Output('g_p_glied', 'style'),
    Output('g_i_glied', 'style'),
    Output('g_d_glied', 'style'),
    Input('interval_graphs', 'n_intervals'),
)
def update_graphs(n):
    if show_distance_plot:
        distance_style = {'display': 'block'}
    else:
        distance_style = {'display': 'none'}

    if show_pid_plot:
        pid_style = {'display': 'block'}
    else:
        pid_style = {'display': 'none'}

    with data_lock:
        time_data = list(plot_time)
        speed_data = list(plot_speed)
        steering_angle_data = list(plot_steering_angle)
        distance_data = list(plot_distance)
        p_data = list(plot_p_glied)
        i_data = list(plot_i_glied)
        d_data = list(plot_d_glied)

    figure_speed = create_figure(
        time_data,
        speed_data,
        'Geschwindigkeit über Zeit',
        'Geschwindigkeit'
    )

    figure_angle = create_figure(
        time_data,
        steering_angle_data,
        'Lenkwinkel über Zeit',
        'Lenkwinkel [°]',
        ' °'
    )

    if show_distance_plot:
        figure_distance = create_figure(
            time_data[-len(distance_data):],
            distance_data,
            'Distanz über Zeit',
            'Distanz [cm]',
            ' cm'
        )
    else:
        figure_distance = no_update

    if show_pid_plot:
        figure_p_glied = create_figure(
            time_data[-len(p_data):],
            p_data,
            'P-Glied über Zeit',
            'P-Glied'
        )

        figure_i_glied = create_figure(
            time_data[-len(i_data):],
            i_data,
            'I-Glied über Zeit',
            'I-Glied'
        )

        figure_d_glied = create_figure(
            time_data[-len(d_data):],
            d_data,
            'D-Glied über Zeit',
            'D-Glied'
        )
    else:
        figure_p_glied = no_update
        figure_i_glied = no_update
        figure_d_glied = no_update

    return (
        figure_distance,
        figure_speed,
        figure_angle,
        figure_p_glied,
        figure_i_glied,
        figure_d_glied,
        distance_style,
        pid_style,
        pid_style,
        pid_style
    )


@app.callback(
    Output('Status', 'children'),
    Input('button_start_fahrmodi', 'n_clicks'),
    State('dropdown_fahrmodus', 'value'),
    prevent_initial_call=True
)
def start_fahrmodus(clicks, fahrmodus):
    global measurement_active, measurement_start_time, measurement_max_time
    global controller_thread, show_distance_plot, show_pid_plot, active_car

    stop_current_drive()
    reset_plot_data()

    drive_stop_event.clear()

    measurement_start_time = time.time()
    measurement_active = True
    show_distance_plot = False
    show_pid_plot = False

    if fahrmodus == 'fahrmodus_1':
        print('Starte Fahrmodus 1')

        active_car = bc
        drive_con = DriveController(bc, driving_mode=DrivingMode.FORWARD_BACKWARD)

        measurement_max_time = drive_con._cfg.get_int('forward_backward_max_time', 10)

        controller_thread = threading.Thread(
            target=drive_con.drive_car,
            args=(drive_stop_event,),
            daemon=True
        )

        controller_thread.start()

        return 'Fahrmodus 1 gestartet'

    elif fahrmodus == 'fahrmodus_2':
        print('Starte Fahrmodus 2')

        active_car = bc
        drive_con = DriveController(bc, driving_mode=DrivingMode.CIRCULAR)

        measurement_max_time = drive_con._cfg.get_int('circular_max_time', 30)

        controller_thread = threading.Thread(
            target=drive_con.drive_car,
            args=(drive_stop_event,),
            daemon=True
        )

        controller_thread.start()

        return 'Fahrmodus 2 gestartet'

    elif fahrmodus == 'fahrmodus_3':
        print('Starte Fahrmodus 3')

        active_car = sc
        drive_con = DriveController(sc, driving_mode=DrivingMode.APPROACH_OBSTACLE)

        measurement_max_time = drive_con._cfg.get_int('explorer_max_time', 30)
        show_distance_plot = True

        controller_thread = threading.Thread(
            target=drive_con.drive_car,
            args=(drive_stop_event,),
            daemon=True
        )

        controller_thread.start()

        return 'Fahrmodus 3 gestartet'

    elif fahrmodus == 'fahrmodus_4':
        print('Starte Fahrmodus 4')

        active_car = sc
        drive_con = DriveController(sc, driving_mode=DrivingMode.EXPLORE)

        measurement_max_time = drive_con._cfg.get_int('explorer_max_time', 30)
        show_distance_plot = True

        controller_thread = threading.Thread(
            target=drive_con.drive_car,
            args=(drive_stop_event,),
            daemon=True
        )

        controller_thread.start()

        return 'Fahrmodus 4 gestartet'

    elif fahrmodus == 'fahrmodus_5':
        print('Starte Fahrmodus 5')

        active_car = sc
        drive_con = DriveController(sc, driving_mode=DrivingMode.FOLLOW_LINE)

        measurement_max_time = drive_con._cfg.get_int('line_following_max_time', 30)
        show_pid_plot = True

        controller_thread = threading.Thread(
            target=drive_con.drive_car,
            args=(drive_stop_event,),
            daemon=True
        )

        controller_thread.start()

        return 'Fahrmodus 5 gestartet'

    elif fahrmodus == 'fahrmodus_6':
        print('Starte Fahrmodus 6')

        active_car = sc
        measurement_max_time = 30
        show_pid_plot = True

        return 'Fahrmodus 6 noch nicht umgesetzt'

    elif fahrmodus == 'fahrmodus_7':
        print('Starte Fahrmodus 7')

        active_car = sc
        measurement_max_time = 30
        show_pid_plot = True

        return 'Fahrmodus 7 noch nicht umgesetzt'

    measurement_active = False

    return 'Nix passiert'


@app.callback(
    Output('button_stop_fahrmodi', 'children'),
    Input('button_stop_fahrmodi', 'n_clicks'),
    prevent_initial_call=True
)
def stop_fahrmodus(clicks):
    stop_current_drive()

    return 'Stop'


if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=False)