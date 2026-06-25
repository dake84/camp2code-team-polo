
import logging
import threading

import Driving
from SonicCar import SonicCar
import UltrasonicSensor
import logging_setup

if __name__ == '__main__':
    logging_setup.setup_project_logging(logging.INFO)
    sc = SonicCar()
    sc.stop()

    us = UltrasonicSensor.UltrasonicSensor(sc)
    dc = Driving.RoomExplorer(car=sc)
    ao = Driving.ApproachObstacle(car=sc)
    
    stop_event = threading.Event()
    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event])

    try:
        us_sensor_thread.start()
        input("Approach obstacle... <ENTER>")
        ao.start()
        input("Room-Explorer... <ENTER>")
        ao.stop()
        dc.start()
        input("<ENTER> zum Beenden")
    except KeyboardInterrupt:
        print("Beende Programm")
    finally:
        dc.stop()
        stop_event.set()

        # Just to be surrrrre
        sc.stop()

        print("Ending sensor thread...", end="")
        us_sensor_thread.join()
        print("...ended!")