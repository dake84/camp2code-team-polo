import random
import threading
import time
from datetime import datetime
from typing import Optional, Tuple
import logging
import numpy as np

from BaseCar import BaseCar
from CarLogger import Loggable
import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar

class StopReason(int):
    
    NONE=0,
    LOST_LINE = 10
    OBSTACLE_AHEAD = 20
    PROGRAM_STOPPED_BY_USER = 30

    STR_REASONS = {
        NONE: "No reason",
        LOST_LINE: "Lost line",
        OBSTACLE_AHEAD: "Obstacle ahead",
        PROGRAM_STOPPED_BY_USER: "Stopped by user"
    }
    
    def __init__(self, reason:int) -> None:
        self._reason = reason

    def __int__(self) -> int:
        return self._reason

    def __str__(self) -> str:
        return StopReason.STR_REASONS[self._reason] if self._reason in self.STR_REASONS else "Unknown reason"

    def __eq__(self, value: object) -> bool:
        if (isinstance(value, int)):
            return int(value) == self._reason
        return False

class RoomExplorer(Loggable):

    def __init__(self, car:Optional[SonicCar]=None, sensor_config:Optional[ConfigReader.ConfigReader]=None):
        self._car = car if car is not None else SonicCar()
        self.__log = logging.getLogger(self.__class__.__name__)
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("room_explorer")

        # Im Test Mode wird die cfg laufend nachgeladen (macht's vieeeel langsamer)
        self._test_mode = True

        self._lock = threading.Lock()


    def drive_car(self, stop_event:threading.Event):
        self.__log.info("Starte RoomExplorer-Modus (Fahrmodus 4)")

        # Fahrmodus 4
        try:
            self._car._update_config()
            self.run = True
            self._room_explorer(self._car, stop_event)
        except Exception as e:
            self.__log.exception(e)
 
    @property
    def car(self) -> SonicCar:
        return self._car

    @property
    def run(self) -> bool:
        return self._run

    @run.setter
    def run(self, run:bool):
        if (run): self._stop_reason = None
        self._run = run

    def _room_explorer(self, car:SonicCar, stop_event:threading.Event):
        explorer_max_time = self._cfg.get_int("explorer_max_time", 30)
        ultrasonic_max_distance_to_stop = self._cfg.get_int("ultrasonic_max_distance_to_stop", 30)

        t_start = time.time()
        t_end = time.time() + explorer_max_time
        print("Los gehts....")
        print(datetime.fromtimestamp(t_end).strftime("%Y-%m-%d %H:%M:%S"))
        self.__log.debug(f"Erkunde den Raum für {explorer_max_time}s")

        actual_speed_drive_explore = self._cfg.get_int("start_speed", 60)
        steering_angle_drive_explore = self._cfg.get_int("start_angle", 90)
        slow_down_window = self._cfg.get_float("slow_down_window", 0.3)

        speed_direction = random.choice([-1, 1]) # Vorwärts / Rückwärts
        steer_direction = random.choice([-1, 1]) # Links / Rechts

        self.__log.debug(f"Parameter: speed_direction ({speed_direction}, steer_direction:{steer_direction})")

        counter = 0        

        while (not stop_event.is_set() and (time.time()-t_start < explorer_max_time)):
            car.drive(actual_speed_drive_explore, steering_angle_drive_explore)

            actual_distance = car.distance
            self.__log.debug(f"Sensor-Wert ausgelesen: {actual_distance}")
            
            if (actual_distance < ((1+slow_down_window)*ultrasonic_max_distance_to_stop)):
                self.__log.debug("Obstacle ahead, slowing down...")
                car.speed = self._calculate_speed_from_distance(car, actual_distance)

            if (actual_distance <= ultrasonic_max_distance_to_stop):
                self.__log.debug("Obstacle ahead, overcoming...")
                self._overcome_obstacle(car)
            else:
                actual_speed_drive_explore, steering_angle_drive_explore, speed_direction, steer_direction, counter = self.drive_explore(car, actual_speed_drive_explore,
                                                                                 steering_angle_drive_explore,
                                                                                 speed_direction,
                                                                                 steer_direction,
                                                                                 counter
                                                                                )                

    def drive_explore(self, car:SonicCar, actual_speed: int, steering_angle: int, speed_dir: int, steer_dir: int, counter: int) -> Tuple[int, int, int, int, int]:
        # TODO Ausgabe in log nur dann, wenn vorher keine freie Fahrt war (letzter log eintrag)
        self.__log.info("Freie Fahrt voraus, kein Hindernis in Sicht")
        self.__log.debug(actual_speed, steering_angle, speed_dir, steer_dir, counter)
        counter += 1

        # Richtung nur alle x Zyklen ändern
        if counter >= 25:
            counter = 0
            speed_dir = random.choice([-1, 1])
            steer_dir = random.choice([-1, 1])

        # kleine Schritte -> smooth
        actual_speed += speed_dir * 1
        steering_angle += int(steer_dir * 1.5)

        # Grenzen
        actual_speed = max(30, min(actual_speed, 100))
        steering_angle = max(45, min(steering_angle, 135))

        self.__log.debug(f"Exploriere den Raum..... (Geschwindigkeit: {actual_speed}, Lenkwinkel: {steering_angle})")
        car.drive(actual_speed, steering_angle)

        return actual_speed, steering_angle, speed_dir, steer_dir, counter

    def _overcome_obstacle(self, car:SonicCar):        
      
        car.stop()

        # Lenkt links oder rechts ein
        ausweich_lenkung = random.choice(
            (
                (45, "links"), 
                (135, "rechts")
            )
        )

        zufalls_zeit = random.randint(1, 4)
        self.__log.info(f"Overcoming obstacle..... driving to the {ausweich_lenkung[1]}")

        car.drive(-30, ausweich_lenkung[0])
        time.sleep(zufalls_zeit) # Hier die Rückwärtsfahrzeit zufällig setzen

    def _approach_obstacle(self, car:SonicCar, stop_event:threading.Event):
            
            ultrasonic_max_distance_to_stop = self._cfg.get_int("ultrasonic_max_distance_to_stop", 30)
            actual_distance = car.distance
            self.__log.debug(f"Max-Distance to stop: {ultrasonic_max_distance_to_stop}, actual_distance: {actual_distance}")
            while (not stop_event.is_set() and actual_distance > ultrasonic_max_distance_to_stop):
                self.__log.debug(f"Driving towards obstacle..... actual_distance: {actual_distance}")

#                us_error = car.last_error
#                if (us != 0):
#                    self._log.info("Auto meldet Fehler: {us_error}")


                if (actual_distance < (1.33*ultrasonic_max_distance_to_stop)):
                    self.__log.info(f"Approaching obstacle... actual_distance: {actual_distance}")
                    car.speed = self._calculate_speed_from_distance(car, actual_distance)
                else:
                    car.drive(car.v_max, 90)
    
                actual_distance = car.distance

            self.__log.debug(f"Stopped car because we are too close to an obstacle! actual_distance: {actual_distance}")

            self.stop_car(StopReason.OBSTACLE_AHEAD)
        
    def _calculate_speed_from_distance(self, car:SonicCar, distance:int) -> int:
            old_speed = car.speed
            speed = distance+car.v_min
            self.__log.info(f"Fahrzeug nähert sich dem Hindernis, verlangsame Fahrt ({old_speed} -> {speed})")
            return speed

    def stop_car(self, reason:StopReason|int=0):
        self._stop_reason = StopReason(reason)
        self._run = False
        self.__log.info(f"Car stopped for {self._stop_reason}")
        self._car.stop()

    def obstacle_ahead(self):
        self.stop_car(StopReason.OBSTACLE_AHEAD)

     # TODO LOG COMPUTED VALUES
    def get_logging_payload(self, log_level:int=logging.INFO) -> dict:
        with self._lock:
            payload = self._car.get_logging_payload()
            payload["lenkwinkel"] = "yet to be implemented"
            #if (log_level == logging.DEBUG):
                #payload["lenkwinkel_calculus"] = self._lw_debug_values
            return payload





