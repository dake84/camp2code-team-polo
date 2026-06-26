
import logging
import threading

import Driving
import InfraredSensor
from SensorCar import MockSensorCar, SensorCar
import UltrasonicSensor
import logging_setup

if __name__ == '__main__':
    logging_setup.setup_project_logging(logging.INFO)
    sc = MockSensorCar(mockSpeed=True)
    sc.stop()

    us = UltrasonicSensor.UltrasonicSensor(sc)
    ir = InfraredSensor.InfraredSensor(sc)
    
    mode_one = Driving.ModeOne(car=sc)
    mode_two = Driving.ModeTwo(car=sc)
    dc = Driving.RoomExplorer(car=sc)
    ao = Driving.ApproachObstacle(car=sc)
    fl = Driving.FollowLine(car=sc)
    key = Driving.KeyboardMode(car=sc)

    stop_event = threading.Event()
    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event], daemon=True)
    ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event], daemon=True)


    try:
        
        key.start()

        ir_sensor_thread.start()
        us_sensor_thread.start()


        input("(1) Forward/Backward... <ENTER>")        
        mode_one.start()
        input("press <Enter> to stop")
        mode_one.stop()
        print("finished mode one")

        input("(2) Circles... <ENTER>")        
        mode_two.start()
        input("press <Enter> to stop")
        mode_two.stop()
        print("finished mode one")

        input("(3) Approach obstacle... <ENTER>")
        ao.start()
        input("press <Enter> to stop")
        ao.stop()        

        input("(4) Room-Explorer... <ENTER>")
        dc.start()
        input("press <Enter> to stop")
        dc.stop()

        input("(5) Follow-Line... <ENTER>")
        fl.start()
        input("press <Enter> to stop")
        fl.stop()


        input("<ENTER> zum Beenden")
    except KeyboardInterrupt:
        print("Beende Programm")
    except Exception as e:
        print(e)
    finally:
        stop_event.set()

        # Just to be surrrrre
        sc.stop()

        print("Ending sensor thread...", end="")
        us_sensor_thread.join()
        ir_sensor_thread.join()
        print("...ended!")