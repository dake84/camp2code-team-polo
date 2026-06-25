import dash_bootstrap_components as dbc
import pandas as pd

import time

from dash import Dash, Input, Output, State, dcc, html

import CarLogger
import ConfigReader
import Driving
import InfraredSensor
import UltrasonicSensor
import SensorCar
import SonicCar

import threading
app = Dash(external_stylesheets=[dbc.themes.BOOTSTRAP])

sc = SensorCar.SensorCar()
son_c = SonicCar.SonicCar()
    
# Liest IR-Sensor und schreibt Werte ins Auto
ir = InfraredSensor.InfraredSensor(sc)
# Liest US-Sensor und schreibt Werte ins Auto
us = UltrasonicSensor.UltrasonicSensor(son_c)
# Liest Werte aus dem Auto und schreibt sie in ein Log-File
cl = CarLogger.CarLogger(sc)
# Liest Werte aus dem Auto und steuert das Auto
dc = Driving.DriveController(sc)

stop_event = threading.Event()

us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event], daemon=True)
ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event], daemon=True)
#controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event, Driving.DrivingMode.APPROACH_OBSTACLE], daemon=True)
#dl_thread=threading.Thread(target=dl.run, args=[stop_event], daemon=True)
#cl_thread=threading.Thread(target=cl.run, args=[stop_event], daemon=True)


us_sensor_thread.start()
ir_sensor_thread.start()

start_time = time.time()

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
                        html.Label('Fahrmodus wählen'),
                        dcc.Dropdown(
                            id='dropdown_fahrmodus',
                            options=[
                                {'label': 'Fahrmodus 1 - Vorwärts und Rückwärts', 'value': 'fahrmodus_1'},
                                {'label': 'Fahrmodus 2 - Kreisfahrt mit maximalem Lenkwinkel', 'value': 'fahrmodus_2'},
                                {'label': 'Fahrmodus 3 - Vorwärtsfahrt bis Hindernis', 'value': 'fahrmodus_3'},
                                {'label': 'Fahrmodus 4 - Erkennungstour', 'value': 'fahrmodus_4'},
                                {'label': 'Fahrmodus 5 - Linienverfolgung', 'value': 'fahrmodus_5'},
                                {'label': 'Fahrmodus 6 - Erweiterte Linienverfolgung:', 'value': 'fahrmodus_6'},
                                {'label': 'Fahrmodus 7', 'value': 'fahrmodus_7'},
                            ],
                            value='fahrmodus_1',
                            clearable=False,
                            placeholder='Bitte Fahrmodus wählen',
                        ),
                        dbc.Button("Start Fahrmodi",
                            id = "button_start_fahrmodi",
                            n_clicks = 0,
                            className='mt-4',   # größerer Abstand
                        )
                    ],
                    width = 4,
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

    current_time = time.time() - start_time
    distance = son_c.distance
    speed = son_c.speed
    steering_angle = son_c.steering_angle

    return f'Time {current_time:.1f} s', f'Distance: {distance} cm', f'Speed: {speed}', f'Steer: {steering_angle}'


# Button, bei n_clicks wird start_fahrmodus gestartet
@app.callback(
    Input('button_start_fahrmodi', 'n_clicks'),
    State('dropdown_fahrmodus', 'value'),
    prevent_initial_call=True
)
def start_fahrmodus(clicks, fahrmodus):
    print(f'Ausgewählter Fahrmodus: {fahrmodus}')

    if fahrmodus == 'fahrmodus_1':
        print('Starte Fahrmodus 1')
    elif fahrmodus == 'fahrmodus_2':
        print('Starte Fahrmodus 2')
    elif fahrmodus == 'fahrmodus_3':
        print('Starte Fahrmodus 3')
        controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event, Driving.DriveController.APPROACH_OBSTACLE])
        controller_thread.start()
        input("Drücke Enter zum Stoppen…")
        stop_event.set()
    elif fahrmodus == 'fahrmodus_4':
        print('Starte Fahrmodus 4')
    elif fahrmodus == 'fahrmodus_5':
        print('Starte Fahrmodus 5')
    elif fahrmodus == 'fahrmodus_6':
        print('Starte Fahrmodus 6')
    elif fahrmodus == 'fahrmodus_7':
        print('Starte Fahrmodus 7')

if __name__ == "__main__":
    app.run(host = "0.0.0.0", debug = True)