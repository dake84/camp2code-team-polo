import logging
import threading
import time

import Driving
import logging_setup

from CameraSensor import CameraSensor
from DeepCar import DeepCar

if __name__ == '__main__':
    logging_setup.setup_project_logging(logging.INFO)

    car = DeepCar()
    sensor = CameraSensor(car)
    mode = Driving.DeepCarMode(car=car)

    stop_event = threading.Event()
    sensor_thread = threading.Thread(target=sensor.read_loop, args=[stop_event], daemon=True)

    try:
        sensor_thread.start()
        time.sleep(1)  # warten, bis das erste Kamerabild in car.camera_data liegt
        mode.start()
        input('<ENTER> zum Beenden')
    except KeyboardInterrupt:
        print('Beende Programm')
    except Exception as e:
        print(e)
    finally:
        stop_event.set()

        # Just to be surrrrre
        mode.stop()
        car.stop()
        print('Ending sensor thread...', end='')
        sensor_thread.join()
        print('...ended!')