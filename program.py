
import threading

from CarLogger import CarLogger
import Driving
from InfraredSensor import InfraredSensors
from SensorCar import SensorCar


if __name__ == '__main__':
    sc = SensorCar()
    
    ir = InfraredSensors(sc)
    cl = CarLogger(sc)
    dc = Driving.DriveController(sc, Driving.DrivingMode.FORWARD_BACKWARD)


    stop_event = threading.Event()
    sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event])
    controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event, Driving.DrivingMode.FORWARD_BACKWARD])
    logging_thread =threading.Thread(target=cl.run, args=[stop_event])


    sensor_thread.start()
    logging_thread.start
    controller_thread.start()

    input("Stop?")

    stop_event.set()

    sensor_thread.join()
    logging_thread.join()
    controller_thread.join()
    
    # Just to be surrrrre
    sc.stop
