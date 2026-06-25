
import logging
import signal
import threading

import CarLogger
import ConfigReader
import Driving
import InfraredSensor
import UltrasonicSensor
import SensorCar
import SonicCar
import os
from logging import handlers
import logging_setup

import sys
import traceback


car_select = SensorCar.SensorCar() # SonicCar.SonicCar()
driving_mode_select = Driving.DrivingMode.APPROACH_OBSTACLE # FORWARD_BACKWARD = 10, CIRCULAR = 20, CIRCULAR_LEFT = CIRCULAR, CIRCULAR_RIGHT = 25, APPROACH_OBSTACLE = 30, EXPLORE = 40FOLLOW_LINE = 50ADVANCED_FOLLOW_LINE = 60ADVANCED_FOLLOW_LINE_WITH_OBSTACLE_DETECTION = 70STADIA_CONTROLLER = 100,KALIBRIERUNGSFAHRT = 500

# Wenn du das Programm mit Strg+C abbrichst, siehst du alle Threads:
def dump_threads(signum, frame):
    print("\n--- THREAD DUMP ---")
    for thread_id, stack in sys._current_frames().items():
        print(f"\nThread ID: {thread_id}")
        traceback.print_stack(stack)
    sys.exit(1)


signal.signal(signal.SIGINT, dump_threads)

if __name__ == '__main__':
    logging_setup.setup_project_logging()
    sc = car_select
    sc.speed
    
    
    # Liest IR-Sensor und schreibt Werte ins Auto
    ir = InfraredSensor.InfraredSensor(sc)
    us = UltrasonicSensor.UltrasonicSensor(sc)
    
    # Liest Werte aus dem Auto und schreibt sie in ein Log-File
    cl = CarLogger.CarLogger(sc)
    # Liest Werte aus dem Auto und steuert das Auto
    dc = Driving.DriveController(sc)
    # Liest Werte aus dem Controller und schreibt sie in ein Log-File
    # dl = CarLogger.CarLogger(log_object=dc, logfile="driving_controller_log.json", log_name="driving_controller_log")


    stop_event = threading.Event()

    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event])
    ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event])
    controller_thread = threading.Thread(target=dc.drive_car, args=[stop_event, driving_mode_select])
    #dl_thread=threading.Thread(target=dl.run, args=[stop_event])
    cl_thread=threading.Thread(target=cl.run, args=[stop_event])


    try:


        sc.stop()
    
        print("Starting sensor thread...", end="")
        us_sensor_thread.start()
        ir_sensor_thread.start()
        print("...started!")

        while True:
            user_input = input("Kalibrierungsfahrt (1) oder reguläres Fahrprogramm(2) ?")
            if (user_input == "1"):

                kf = Driving.Kalibrierungsfahrt(dc)
                kf.fahre_kalibrierungsfahrt(threading.Event())
                if ("j" == input("Soll das Ergebnis vom Fahrzeug in die Sensor-Konfig geschrieben werden? (j/n)")):
                    ir.save_calibration()
                    stop_event.set()
                    break
                stop_event.set()
                break

            elif (user_input == "2"):
     
                print("Starting logging thread...", end="")
                #dl_thread.start()
                cl_thread.start()
                print("...started!")
                print(f"cl_thread alive? {cl_thread.is_alive()}")
                print("Starting controller thread...", end="")
                controller_thread.start()
                print(f"controller_thread alive? {controller_thread.is_alive()}")
                print("...started!")
                input("Drücke Enter zum Stoppen…")
                stop_event.set()
                break

            else:
                print("Nur 1 oder 2 zulässig")
                continue
                



    except KeyboardInterrupt:
        stop_event.set()

    finally:
        # Just to be surrrrre
        sc.stop()

        print("Ending sensor thread...", end="")
        ir_sensor_thread.join()
        us_sensor_thread.join()
        print("...ended!")
        print("Ending logging thread...", end="")
        # dl_thread.join()
        if cl_thread.is_alive():
            cl_thread.join()
        print("...ended!")
        print("Ending controller thread...", end="")
        if controller_thread.is_alive():
            controller_thread.join()
        print("...ended!")
        
        # if ("j" == input("Sollen die geänderten Kalibrierungswerte des IR-Sensors gespeichert werden (j)?")):
        #     ir.save_calibration()
