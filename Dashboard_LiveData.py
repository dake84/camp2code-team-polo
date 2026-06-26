import threading
import time
from datetime import datetime

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html

import Driving
import InfraredSensor
import UltrasonicSensor
import BaseCar
import SensorCar

app = Dash(external_stylesheets=[dbc.themes.BOOTSTRAP])

bc = BaseCar.BaseCar()
sc = SensorCar.SensorCar()

ir = InfraredSensor.InfraredSensor(sc)
us = UltrasonicSensor.UltrasonicSensor(sc)

sensor_stop_event = threading.Event()
drive_stop_event = threading.Event()

mode_one = Driving.ModeOne(car=sc)
currentMode = mode_one
mode_two = Driving.ModeTwo(car=sc)
re = Driving.RoomExplorer(car=sc)
ao = Driving.ApproachObstacle(car=sc)
fl = Driving.FollowLine(car=sc)

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
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=x_data,
            y=y_data,
            mode='lines+markers'
        )
    )
    figure.update_layout(
        title=title,
        xaxis_title='Zeit [s]',
        yaxis_title=y_title,
    )
    add_min_max_annotations(figure, y_data, unit)
    return figure


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
                        html.P(id='Distance'),
                        html.P(id='Speed'),
                        html.P(id='Steer'),
                        html.P(id='Status'),
                        dcc.Interval(
                            id='interval',
                            interval=200,
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
                            className='mt-4'
                        )
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
                ),
                dbc.Col(
                    [
                        dcc.Graph(id='g_p_glied'),
                        dcc.Graph(id='g_i_glied'),
                        dcc.Graph(id='g_d_glied'),
                    ],
                    width=4,
                )
            ]
        )
    ]
)


@app.callback(
    Output('Time', 'children'),
    Output('Speed', 'children'),
    Output('Steer', 'children'),
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

    Input('interval', 'n_intervals'),
)

def update_values(n):
    global measurement_active, measurement_start_time, measurement_max_time
    global controller_thread, show_distance_plot, show_pid_plot, active_car

    current_time = datetime.now().strftime('%H:%M:%S')

    speed = active_car.speed * active_car.direction
    steering_angle = active_car.steering_angle

    if show_pid_plot:
        p_glied = sc.p_wert
        i_glied = sc.i_wert
        d_glied = sc.d_wert
    else:
        p_glied = '-'
        i_glied = '-'
        d_glied = '-'

    # Fülle Liste während des Durchlaufs
    if measurement_active and measurement_start_time is not None:
        elapsed = time.time() - measurement_start_time

        plot_time.append(elapsed)
        plot_speed.append(speed)
        plot_steering_angle.append(steering_angle)

        if show_distance_plot:
            plot_distance.append(sc.distance)

        if show_pid_plot:
            plot_p_glied.append(sc.p_wert)
            plot_i_glied.append(sc.i_wert)
            plot_d_glied.append(sc.d_wert)

        # Stoppe wenn Zeit überschritten
        if elapsed >= measurement_max_time:
            drive_stop_event.set()
            measurement_active = False
            active_car.stop()

    # Wenn vorbei, ändere Status
    if controller_thread is not None and not controller_thread.is_alive():
        measurement_active = False

    figure_speed = create_figure(
        plot_time,
        plot_speed,
        'Geschwindigkeit über Zeit',
        'Geschwindigkeit'
    )

    figure_angle = create_figure(
        plot_time,
        plot_steering_angle,
        'Lenkwinkel über Zeit',
        'Lenkwinkel [°]',
        ' °'
    )

    figure_distance = create_figure(
        plot_time[:len(plot_distance)],
        plot_distance,
        'Distanz über Zeit',
        'Distanz [cm]',
        ' cm'
    )
    ### PID Plots
    figure_p_glied = create_figure(
        plot_time[:len(plot_p_glied)],
        plot_p_glied,
        'P-Glied über Zeit',
        'P-Glied [ ]',
        '-'
    )

    figure_i_glied = create_figure(
        plot_time[:len(plot_i_glied)],
        plot_i_glied,
        'I-Glied über Zeit',
        'I-Glied [ ]',
        '-'
    )

    figure_d_glied = create_figure(
        plot_time[:len(plot_d_glied)],
        plot_d_glied,
        'D-Glied über Zeit',
        'D-Glied []',
        '-'
    )

    if show_distance_plot:
        distance_style = {'display': 'block'}
    else:
        distance_style = {'display': 'none'}

    if show_pid_plot:
        pid_style = {'display': 'block'}
    else:
        pid_style = {'display': 'none'}

    return (
        f'Time: {current_time}',
        f'Speed: {speed}',
        f'Steer: {steering_angle}',
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
    Output('Distance', 'children'),
    Input('button_start_fahrmodi', 'n_clicks'),
    State('dropdown_fahrmodus', 'value'),
    prevent_initial_call=True
)
def start_fahrmodus(clicks, fahrmodus):
    global measurement_active, measurement_start_time, measurement_max_time
    global controller_thread, show_distance_plot, show_pid_plot, active_car, currentMode

    plot_time.clear()
    plot_distance.clear()
    plot_speed.clear()
    plot_steering_angle.clear()

    drive_stop_event.clear()

    if fahrmodus == 'fahrmodus_1':
        print('Starte Fahrmodus 1')
        if (currentMode is not None):
            currentMode.stop()
            time.sleep(0.25)
        currentMode = mode_one

        active_car = bc

        measurement_start_time = time.time()
        measurement_max_time = currentMode._cfg.get_int('forward_backward_max_time', 10)
        measurement_active = True
        show_distance_plot = False
        show_pid_plot = False

        currentMode.start()
        return 'Fahrmodus 1 gestartet', f'Distanz: {11111}'

    elif fahrmodus == 'fahrmodus_2':
        print('Starte Fahrmodus 2')
        if (currentMode is not None):
            currentMode.stop()
            time.sleep(0.25)
        currentMode = mode_two
        active_car = bc

        measurement_max_time = currentMode._cfg.get_int('forward_backward_max_time', 10)
        measurement_active = True
        show_distance_plot = False
        show_pid_plot = False

        currentMode.start()
        return 'Fahrmodus 2 gestartet', f'Distanz: {11111}'

    elif fahrmodus == 'fahrmodus_3':
        print('Starte Fahrmodus 3')
        if (currentMode is not None):
            currentMode.stop()
            time.sleep(0.25)
        
        currentMode = ao

        active_car = sc

        measurement_max_time = currentMode._cfg.get_int('explorer_max_time', 30)
        measurement_active = True
        show_distance_plot = True
        show_pid_plot = False

        ao.start()

        return 'Fahrmodus 3 gestartet', f'Distanz: {11111}'

    elif fahrmodus == 'fahrmodus_4':
        print('Starte Fahrmodus 4')
        if (currentMode is not None):
            currentMode.stop()
            time.sleep(0.25)

        currentMode = re

        active_car = sc

        measurement_max_time = currentMode._cfg.get_int('explorer_max_time', 30)
        measurement_active = True
        show_distance_plot = True
        show_pid_plot = False

        re.start()
        return 'Fahrmodus 4 gestartet', f'Distanz: {11111}'

    elif fahrmodus == 'fahrmodus_5':
        print('Starte Fahrmodus 5')
        if (currentMode is not None):
            currentMode.stop()
            time.sleep(0.25)


        currentMode = fl

        active_car = sc

        measurement_active = True
        show_distance_plot = False
        show_pid_plot = True

        currentMode.start()

        return 'Fahrmodus 5 noch nicht umgesetzt', f'Distanz: {11111}'

    elif fahrmodus == 'fahrmodus_6':
        print('Starte Fahrmodus 6')

        measurement_active = True
        show_distance_plot = False
        show_pid_plot = True

        return 'Fahrmodus 6 noch nicht umgesetzt', f'Distanz: {11111}'

    elif fahrmodus == 'fahrmodus_7':
        print('Starte Fahrmodus 7')

        measurement_active = True
        show_distance_plot = False
        show_pid_plot = True

        return 'Fahrmodus 7 noch nicht umgesetzt', f'Distanz: {11111}'

    return 'Nix passiert', f'Distanz: {0}'


@app.callback(
    Output('button_stop_fahrmodi', 'children'),
    Input('button_stop_fahrmodi', 'n_clicks'),
    prevent_initial_call=True
)
def stop_fahrmodus(clicks):
    global measurement_active, active_car

    drive_stop_event.set()
    measurement_active = False
    active_car.stop()

    if (currentMode is not None):
        currentMode.stop()

    return 'Stop'


if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)


    
