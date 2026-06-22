
import threading

import CarLogger
import Driving
import InfraredSensor
import SensorCar

import sys
import traceback

# Wenn du das Programm mit Strg+C abbrichst, siehst du alle Threads:
def dump_threads(signum, frame):
    print("\n--- THREAD DUMP ---")
    for thread_id, stack in sys._current_frames().items():
        print(f"\nThread ID: {thread_id}")
        traceback.print_stack(stack)
    sys.exit(1)

import signal
signal.signal(signal.SIGINT, dump_threads)

if __name__ == '__main__':
    sc = SensorCar.SensorCar()
    
    ir = InfraredSensor.InfraredSensor(sc)
    cl = CarLogger.CarLogger(sc)
    dc = Driving.DriveController(sc, Driving.DrivingMode.FORWARD_BACKWARD)
    dl = CarLogger.CarLogger(car=dc, logfile="driving_controller_log.json", log_name="driving_controller_log")


    stop_event = threading.Event()

    sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event])
    controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event, Driving.DrivingMode.FOLLOW_LINE])
    dl_thread=threading.Thread(target=dl.run, args=[stop_event])
    cl_thread=threading.Thread(target=cl.run, args=[stop_event])


    print("Starting sensor thread...", end="")
    sensor_thread.start()
    print("...started!")
    print("Starting logging thread...", end="")
    dl_thread.start()
    cl_thread.start()
    print("...started!")
    print("Starting controller thread...", end="")
    #controller_thread.start()
    print("...started!")

    input("Stop?")

    stop_event.set()

    print("Ending sensor thread...", end="")
    sensor_thread.join()
    print("...ended!")
    print("Ending logging thread...", end="")
    dl_thread.join()
    cl_thread.join()
    print("...ended!")
    print("Ending controller thread...", end="")
    controller_thread.join()
    print("...ended!")
    
    # Just to be surrrrre
    sc.stop()
