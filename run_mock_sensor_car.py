
import threading

import Driving
import logging_setup
from SensorCar import MockSensorCar


if __name__ == '__main__':
    logging_setup.setup_project_logging()
    sc = MockSensorCar() #feste_werte=[[0.,1.,1.,1.,1.]]
    dc = Driving.DriveController(car=sc, driving_mode=Driving.DrivingMode.ADVANCED_FOLLOW_LINE)

    se = threading.Event()
    dc.drive_car(stop_event=se)

