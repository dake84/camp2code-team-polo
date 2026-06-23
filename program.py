
import threading

import CarLogger
import Driving
import InfraredSensor
import UltrasonicSensor
import SensorCar
import signal
import sys
import traceback

# Wenn du das Programm mit Strg+C abbrichst, siehst du alle Threads:
def dump_threads(signum, frame):
    print("\n--- THREAD DUMP ---")
    for thread_id, stack in sys._current_frames().items():
        print(f"\nThread ID: {thread_id}")
        traceback.print_stack(stack)
    sys.exit(1)


signal.signal(signal.SIGINT, dump_threads)

if __name__ == '__main__':
    sc = SensorCar.SensorCar()
    
    # Liest IR-Sensor und schreibt Werte ins Auto
    ir = InfraredSensor.InfraredSensor(sc)
    us = UltrasonicSensor.UltrasonicSensor(sc)
    
    # Liest Werte aus dem Auto und schreibt sie in ein Log-File
    cl = CarLogger.CarLogger(sc)
    # Liest Werte aus dem Auto und steuert das Auto
    dc = Driving.DriveController(sc, Driving.DrivingMode.APPROACH_OBSTACLE)
    # Liest Werte aus dem Controller und schreibt sie in ein Log-File
    # dl = CarLogger.CarLogger(log_object=dc, logfile="driving_controller_log.json", log_name="driving_controller_log")


    stop_event = threading.Event()

    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event])
    ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event])
    controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event, Driving.DrivingMode.EXPLORE])
    #dl_thread=threading.Thread(target=dl.run, args=[stop_event])
    cl_thread=threading.Thread(target=cl.run, args=[stop_event])


    print("Starting sensor thread...", end="")
    us_sensor_thread.start()
    ir_sensor_thread.start()
    print("...started!")
    print("Starting logging thread...", end="")
    #dl_thread.start()
    cl_thread.start()
    print("...started!")
    print("Starting controller thread...", end="")
    controller_thread.start()
    print("...started!")

    input("Stop?")

    stop_event.set()

    print("Ending sensor thread...", end="")
    ir_sensor_thread.join()
    us_sensor_thread.join()
    print("...ended!")
    print("Ending logging thread...", end="")
    # dl_thread.join()
    cl_thread.join()
    print("...ended!")
    print("Ending controller thread...", end="")
    controller_thread.join()
    print("...ended!")
    
    # Just to be surrrrre
    sc.stop()
