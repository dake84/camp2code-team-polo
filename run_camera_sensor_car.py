
import logging
import threading

import Driving
import logging_setup

from CameraCar import CameraCar
from CameraSensor import CameraSensor

if __name__ == '__main__':
    logging_setup.setup_project_logging(logging.INFO)

    car = CameraCar()
    sensor = CameraSensor(car)

    sensor.read_loop(threading.Event())


    
