
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

    mode_one = Driving.ModeOne(car=sc)
    us = UltrasonicSensor.UltrasonicSensor(sc)
    dc = Driving.RoomExplorer(car=sc)
    ao = Driving.ApproachObstacle(car=sc)
    
    stop_event = threading.Event()
    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event])

    try:
        input("Mode one... <ENTER>")        
        mode_one.start()
        input("press <Enter> to stop")
        mode_one.stop()
        print("finished mode one")

        us_sensor_thread.start()
        input("Approach obstacle... <ENTER>")
        ao.start()
        input("press <Enter> to stop")
        ao.stop()

        input("Room-Explorer... <ENTER>")
        dc.start()
        input("<ENTER> zum Beenden")
        dc.stop()
    except KeyboardInterrupt:
        print("Beende Programm")
    finally:
        stop_event.set()

        # Just to be surrrrre
        sc.stop()

        print("Ending sensor thread...", end="")
        us_sensor_thread.join()
        print("...ended!")