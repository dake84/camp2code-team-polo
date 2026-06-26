
import logging
import threading

import Driving
import InfraredSensor
from SensorCar import SensorCar
import UltrasonicSensor
import logging_setup

if __name__ == '__main__':
    logging_setup.setup_project_logging(logging.DEBUG)
    sc = SensorCar()
    sc.stop()

    us = UltrasonicSensor.UltrasonicSensor(sc)
    ir = InfraredSensor.InfraredSensor(sc)
    
    mode_one = Driving.ModeOne(car=sc)
    dc = Driving.RoomExplorer(car=sc)
    ao = Driving.ApproachObstacle(car=sc)
    fl = Driving.FollowLine(car=sc)
    
    stop_event = threading.Event()
    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event])
    ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event])


    try:
        ir_sensor_thread.start()
        input("Follow-Line... <ENTER>")
        fl.start()
        input("press <Enter> to stop")
        fl.stop()

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
        input("press <Enter> to stop")
        dc.stop()



        input("<ENTER> zum Beenden")
    except KeyboardInterrupt:
        print("Beende Programm")
    finally:
        stop_event.set()

        # Just to be surrrrre
        sc.stop()

        print("Ending sensor thread...", end="")
        us_sensor_thread.join()
        ir_sensor_thread.join()
        print("...ended!")