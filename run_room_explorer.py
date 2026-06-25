
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
    dc = Driving.RoomExplorer(sc)

        

    stop_event = threading.Event()

    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event])
    controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event])

    try:

        # Sensor starten (liest Werte aus und fütter das Auto)
        print("Starting sensor thread...", end="")
        us_sensor_thread.start()
        print("...started!")

        if ("4" == input("Fahrmodus")):
            # Controller starten (liest Werte vom Auto und steuert es)
            print("Starting controller thread...", end="")
            controller_thread.start()
            print("...started!")

        input("Stop?")

        stop_event.set()

    except KeyboardInterrupt:
        stop_event.set()

    finally:
        # Just to be surrrrre
        sc.stop()

        print("Ending sensor thread...", end="")
        us_sensor_thread.join()
        print("...ended!")

        print("Ending controller thread...", end="")
        controller_thread.join()
        print("...ended!")