import abc
import curses
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

    NONE=int(0),
    LOST_LINE = int(10)
    OBSTACLE_AHEAD = int(20)
    PROGRAM_STOPPED_BY_USER = int(30)

    STR_REASONS = {
        NONE: "No reason",
        LOST_LINE: "Lost line",
        OBSTACLE_AHEAD: "Obstacle ahead",
        PROGRAM_STOPPED_BY_USER: "Stopped by user"
    }
    
    def __init__(self, reason:int) -> None:
        """_summary_

        Args:
            reason (int): _description_
        """
        self._reason = reason

    def __int__(self) -> int:
        """_summary_

        Returns:
            int: _description_
        """
        return self._reason

    def __str__(self) -> str:
        """_summary_

        Returns:
            str: _description_
        """
        return StopReason.STR_REASONS[self._reason] if self._reason in self.STR_REASONS else "Unknown reason"

    def __eq__(self, value: object) -> bool:
        """_summary_

        Args:
            value (object): _description_

        Returns:
            bool: _description_
        """
        if (isinstance(value, int)):
            return int(value) == self._reason
        return False

class DrivingMode(abc.ABC):

    def __init__(self, name:str, car:BaseCar, logger:logging.Logger, config:ConfigReader.ConfigReader, update_cfg:bool=False, frequency:int=500) -> None:
        super().__init__()
        self._stop_event = None
        self._name = name
        self._car = car
        self._log = logger
        self._cfg = config
        self._frequency = frequency
        self._lock = threading.RLock()
        self._thread = threading.Thread(target=self._drive, daemon=True, args=[update_cfg])

    def start(self):
        with self._lock:
#            if (not self.is_running):
            self._thread.start()

    def _drive(self, update_cfg:bool=False):
        with self._lock:
            if self.is_running():
                raise RuntimeError("Mode is already running")

        try:
            if (update_cfg):
                self._log.warning(f"Reloading config files. This will cost some performance and is not advised in production environments")
            
            with self._lock:
                self._stop_event = threading.Event()

            self._log.info(f"Starte {self._name}")

            if (self._pre_run()):
                self._log.debug("Pre-Run actions absolved")
            else:
                raise RuntimeError("Pre-Run actions failed. Aborting.")

            i = 0
            running = True
            while (not self._stop_event.is_set() and self._run_condition() and running):
                if (update_cfg):
                    self._cfg._load_config_file()
                    self._car._update_config()

                running = self._run()
                time.sleep(1/self._frequency)
                i+=1

                if (i%self._frequency == 5):
                    self._log.debug(f"Running {self._name} in iteration #{i}")
                
        except Exception as e:
            self._log.exception(e)
        finally:
            self._car.stop()
            if (self._post_run()):
                self._log.debug("Post-Run actions absolved")
            else:
                raise RuntimeError("Post-Run actions failed. Aborting.")                
            with self._lock:
                self._stop_event = None

    @abc.abstractmethod
    def _run_condition(self) -> bool:
        pass

    def _pre_run(self) -> bool:
        self._car.drive(0,90)
        return True

    @abc.abstractmethod
    def _run(self) -> bool:
        pass

    def _post_run(self) -> bool:
        self._car.drive(0,90)
        return True

    def is_running(self) -> bool:
        return self._stop_event is not None

    def stop(self, reason:StopReason|int=0) -> bool:
        if (self._stop_event is not None):
            self._stop_event.set()
            self._stop_car(reason)
            return True
        return False

    def _stop_car(self, reason:StopReason|int=0):
        """_summary_

        Args:
            reason (StopReason | int, optional): _description_. Defaults to 0.
        """
        self._log.info(f"Car stopped for {reason}")
        self._car.stop()    


class ModeOne(DrivingMode):

    def __init__(self, car: Optional[BaseCar], name:str="Fahrmodus 1", logger: Optional[logging.Logger]=None, cfg: Optional[ConfigReader.ConfigReader]=None, update_cfg: bool = False, frequency: int = 500) -> None:
        car = car if car is not None else SonicCar()
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("soniccar_controller")
        super().__init__(name, car, logger, cfg)

    def _run_condition(self) -> bool:
        return True
    
    def _run(self) -> bool:
        self._log.info('Starte Fahrmodus 1')
        self._sleep_with_stop(1, stop=True)  
                     
        self._car.drive(30)
        self._log.debug(f"3 Sekunden vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(3, stop=False)

        self._log.debug(f"1 Sekunde Stopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(1, stop=True)
            
        self._car.drive(-30)
        self._log.debug(f"3 Sekunden rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(3, stop = False)

        self._car.stop()
        self._log.info(f"Fahrmodus 1 beendet. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")            

        return False


    def _sleep_with_stop(self, duration: float, stop:bool) -> bool:
        if (self._stop_event is None):
            raise RuntimeError("Stop_event not availabel")
        end_time = time.time() + duration
        step_time = 1/self._frequency 
        if (stop):
            self._car.stop()

        while time.time() < end_time and not self._stop_event.is_set():
            time.sleep(step_time)

        return False

class ModeTwo(ModeOne):

    def __init__(self, car: Optional[BaseCar], name: str = "Fahrmodus 2", logger: Optional[logging.Logger] = None, cfg: Optional[ConfigReader.ConfigReader] = None, update_cfg: bool = False, frequency: int = 500) -> None:
        super().__init__(car=car, name=name, logger=logger, cfg=cfg, update_cfg=update_cfg, frequency=frequency)

    def _run_condition(self) -> bool:
        return True
    
    def _run(self) -> bool:
        self._circle((135, "rechts/Uhrzeigersinn"))
        self._circle((45, "links/gegen Uhrzeigersinn"))
        return False
    
    def _circle(self, direction: Tuple[int, str]) -> bool:
        self._log.info(f'Starte Fahrmodus 2 ({direction[1]})')
        
        self._sleep_with_stop(1, stop=True)

        self._car.drive(30)
        self._log.debug(f"1 Sekunde vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(1, stop=False)

        self._car.drive(30, direction[0])
        self._log.debug(f"8 Sekunden {direction[1]} vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(8, stop=False)

        self._log.debug(f"Kurzer Zwischenstopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(2, stop=True)

        self._car.drive(-30, direction[0])
        self._log.debug(f"8 Sekunden {direction[1]} rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(8, stop=False)

        self._car.drive(-30, 90)
        self._log.debug(f"1 Sekunde rückwärts zum Startpunkt. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
        self._sleep_with_stop(1, stop=False)

        self._car.stop()
        self._log.info(f"Fahrmodus 2 {direction[1]} beendet. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}") 

        return False
    
class SonicCarMode(DrivingMode):

    def __init__(self, name:str, car:Optional[SonicCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None):
        car = car if car is not None else SonicCar()
        # just a pointer to remove linter-errors
        self._soniccar = car
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("soniccar_controller")
        super().__init__(name, car, logger, cfg)

# Mode 3
class ApproachObstacle(SonicCarMode):
        
    def __init__(self, name="Fahrmodus 3 (ApproachObstacle)", car:Optional[SonicCar]=None):
        super().__init__(name=name, car=car)
        
        # Dein Flag für einmaliges Logging
        self._free_logged = False

    def _pre_run(self) -> bool:
        ultrasonic_max_distance_to_stop = self._cfg.get_int("ultrasonic_max_distance_to_stop", 30)
        actual_distance = self._soniccar.distance

        self._log.debug(f"Max-Distance to stop: {ultrasonic_max_distance_to_stop}, actual_distance: {actual_distance}")        
        self._soniccar.speed = self._soniccar.v_min

        return True

    def _run_condition(self) -> bool:
        ultrasonic_max_distance_to_stop = self._cfg.get_int("ultrasonic_max_distance_to_stop", 30)
        d = self._soniccar.distance
        if (d < ultrasonic_max_distance_to_stop):
            self._log.info(f"Reached obstacle (distance: {d}), stopping")
            return False
        return True

    def _run(self) -> bool:

        freie_fahrt = self._set_speed_based_on_distance()

        ultrasonic_max_distance_to_stop = self._cfg.get_int("ultrasonic_max_distance_to_stop", 30)
        d = self._soniccar.distance

        if freie_fahrt:
            self._car.drive(self._car.speed + 5, 90)
        elif (d < ultrasonic_max_distance_to_stop):
            self.stop(StopReason(StopReason.OBSTACLE_AHEAD))
            return False

        return True

    def _set_speed_based_on_distance(self) -> bool:
        # False, wenn freie Fahrt, sonst true

        ultrasonic_max_distance_to_stop = self._cfg.get_int("ultrasonic_max_distance_to_stop", 30)
        slow_down_window = self._cfg.get_float("slow_down_window", 0.3)
        
        actual_distance = self._soniccar.distance
        self._log.debug(f"Sensor-Wert ausgelesen: {actual_distance}")

        old_speed = self._soniccar.speed
        new_speed = old_speed
            
        if (actual_distance <= ultrasonic_max_distance_to_stop):
            self._log.info("Obstacle ahead, stopping...")
            self._soniccar.stop()
            self._free_logged = False
            return True
        elif (actual_distance <= ((1+slow_down_window)*ultrasonic_max_distance_to_stop)):
            self._free_logged = False
            self._soniccar.speed = int(self._soniccar.speed * (self._soniccar.distance/ultrasonic_max_distance_to_stop))
            self._log.info(f"Fahrzeug nähert sich dem Hindernis (d={actual_distance}), verlangsame Fahrt ({old_speed} -> {self._soniccar.speed})")
            return True
        elif not self._free_logged:
            self._log.info("Freie Fahrt voraus, kein Hindernis in Sicht")
            self._free_logged = True
            return False
        
        return False

# Mode 4
class RoomExplorer(ApproachObstacle):
    def __init__(self, name="Fahrmodus 4 (RoomExplorer)", car:Optional[SonicCar]=None):
        super().__init__(name=name, car=car)

        self._explore_logged = False

    def _pre_run(self):
        explorer_max_time = self._cfg.get_int("explorer_max_time", 30)

        self._t_end = time.time() + explorer_max_time
        self._log.debug(f"Erkunde den Raum für {explorer_max_time}s bis {datetime.fromtimestamp(self._t_end).strftime("%Y-%m-%d %H:%M:%S")}")

        actual_speed_drive_explore = self._cfg.get_int("start_speed", 60)
        steering_angle_drive_explore = self._cfg.get_int("start_angle", 90)
        self._car.drive(actual_speed_drive_explore, steering_angle_drive_explore)

        self._counter = 0
        self._log.debug(f"Parameter: speed_direction ({actual_speed_drive_explore}, steer_direction:{steering_angle_drive_explore})")

        return True

    def _run_condition(self) -> bool:
        if (self._t_end is None):
            raise RuntimeError("Start- und/oder Endzeit nicht gesetzt (t_start: {self._t_start}, t_end: {self._t_end})")
        return (time.time() < self._t_end)

    def _run(self) -> bool:
            
        if (not self._set_speed_based_on_distance()):
            # Freie Fahrt
            self._log.debug("Freie Fahrt")
            self._explore()
        else:
            # Ausweichmanöver einleiten
            self._explore_logged = False
            suchzeit_sekunden = self._overcome_obstacle()
            self._log.info(f"Fahre rückwärts für {suchzeit_sekunden}")
            start_suchzeit = time.time()
            ende_suchzeit = start_suchzeit + suchzeit_sekunden
            if (self._stop_event is None):
                raise RuntimeError("Stop_Event not available")
            while (not self._stop_event.is_set() and (time.time() < ende_suchzeit)):
                time.sleep(1/self._frequency)
            self._log.info(f"Rückwärtsfahrt beendet")

        return True

    def _post_run(self):
        self._t_end = None
        self._counter = 0

        return True

    def _explore(self):
        if not self._explore_logged:
            self._log.info(f"Fahre auf Erkundungsfahrt bis zum nächsten Hindernis...")
            self._explore_logged = True
        actual_speed = self._soniccar.speed
        steering_angle = self._soniccar.steering_angle
        steer_dir = -1 if steering_angle < 90 else 1
        speed_dir = self._soniccar.direction

        self._log.debug(f"speed: {actual_speed},, angle: {steering_angle}, speed_dir: {speed_dir}, steer_dir: {steer_dir}, counter: {self._counter}")
        self._counter += 1

        # Richtung nur alle x Zyklen ändern
        if self._counter >= 25:
            counter = 0
            speed_dir = random.choice([-1, 1])
            steer_dir = random.choice([-1, 1])

        # kleine Schritte -> smooth
        actual_speed += speed_dir * 1
        steering_angle += int(steer_dir * 1.5)

        # Grenzen
        actual_speed = max(30, min(actual_speed, 100))
        steering_angle = max(45, min(steering_angle, 135))

        self._log.debug(f"Exploriere den Raum..... (Geschwindigkeit: {actual_speed}, Lenkwinkel: {steering_angle})")
        self._soniccar.drive(actual_speed, steering_angle)

    def _overcome_obstacle(self) -> int:
        # Gibt die Rückwärtsfahrzeit in Sekunden aus
                
        self._soniccar.stop()

        # Lenkt links oder rechts ein
        ausweich_lenkung = random.choice(
            (
                (45, "links"), 
                (135, "rechts")
            )
        )

        self._log.info(f"Overcoming obstacle..... driving to the {ausweich_lenkung[1]}")

        self._soniccar.drive(-30, ausweich_lenkung[0])
        return random.randint(1, 4)

    def get_logging_payload(self, log_level:int=logging.INFO) -> dict:
        """_summary_

        Args:
            log_level (int, optional): _description_. Defaults to logging.INFO.

        Returns:
            dict: _description_
        """
        with self._lock:
            payload = self._car.get_logging_payload()
            payload["lenkwinkel"] = "yet to be implemented"
            #if (log_level == logging.DEBUG):
                #payload["lenkwinkel_calculus"] = self._lw_debug_values
            return payload

class SensorCarMode(SonicCarMode):
    def __init__(self, name:str, car:Optional[SensorCar]=None, cfg:Optional[ConfigReader.ConfigReader]=None):
        car = car if car is not None else SensorCar()
        # just a pointer to remove linter-errors
        self._sensorcar = car
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("sensorcar_controller")
        super().__init__(name, car, cfg)

# Mode 5
class FollowLine(SensorCarMode):
    def __init__(self, name="Fahrmodus 5", car:Optional[SensorCar]=None):
        super().__init__(name=name, car=car)

        self._summe_integral = 0
        self._previous_error = 0
        self._last_direction = 0
        self._last_lenkwinkel = 90
        self._last_time = time.time()
    
    def _run_condition(self) -> bool:
        return self._is_on_line()
    
    def _pre_run(self) -> bool:
        self._last_time = time.time()      
        return super()._pre_run()

    def _run(self) -> bool:

        # Lenkwinkel berechnen und zeitpersistente Werte merken
        lw = self._calc_steering_angle_from_ir_sensors()
        v = self._calc_speed_from_steering_angle(lw)

        self._log.info(f"Folge Linie mit Lenkwinkel {lw} und Geschwindigkeit {v}")

        self._sensorcar.drive(v, lw)

        return True    
    
    def _post_run(self) -> bool:
        return super()._post_run()    

    def _is_on_line(self) -> bool:
        minimum_line_contrast = self._cfg.get_float("minimum_line_contrast", 0.5)
        messwerte = self._sensorcar.ir_sensor_values
        
        min_sensor = min(messwerte)
        max_sensor = max(messwerte)

        if (max_sensor == 0):
            return True

        self._log.debug(f"Prüfung is_on_line mit min_sensor: {min_sensor:.2f}, max_sensor: {max_sensor:.2f}, line_threshold: {minimum_line_contrast}, min/max: {min_sensor/max_sensor:.2f}")
        return (max_sensor-min_sensor > minimum_line_contrast)
    
    def _calc_steering_angle_from_ir_sensors(self) -> int:
        """Berechnet den Lenkwinkel basierend auf IR-Messwerten und PID-Korrekturfaktoren.

        Die Funktion nutzt gewichtete Infrarotmesswerte, um einen Fehlerwert zu bestimmen
        und daraus mittels proportionaler und differenzieller Steuerung den Lenkwinkel
        zu berechnen. Der resultierende Winkel wird auf den Bereich von 45 bis 135 Grad
        begrenzt.

        Returns:
            float: Der berechnete Lenkwinkel im Bereich von 45 bis 135 Grad.
        """

        korrektur_proportional = self._cfg.get_float("korrektur_proportional", 5)
        korrektur_integral = self._cfg.get_float("korrektur_integral", 0)
        korrektur_integral_boundary = self._cfg.get_float("korrektur_integral_boundary", 30)
        korrektur_differential = self._cfg.get_float("korrektur_differential", 0)

        sensor_gewichte = self._cfg.get_list("ir_sensor_weights", [2,1,0,-1,-2])
        slew_rate = self._cfg.get_int("slew_rate", 3)

        messwerte = self._sensorcar.ir_sensor_values

        self._log.debug(f"Starte Lenkwinkel-Berechnung mit Eingangsdaten: messwerte:{messwerte}, sensor_gewichtung:{sensor_gewichte}, slew_rate:{slew_rate}, korrektur_proportional:{korrektur_proportional}, korrektur_integral:{korrektur_integral}, summe_integral:{self._summe_integral}, korrektur_integral_boundary:{korrektur_integral_boundary}, korrektur_differential:{korrektur_differential}, previous_error:{self._previous_error}, last_time:{self._last_time}, last_lenkwinkel:{self._last_lenkwinkel}")

        current_time_stamp = time.time()

        sum_messwerte = sum(messwerte)
        
        # Div/0 -> Lenkwinkel geradeaus
        if (sum_messwerte == 0):
            self._last_time = time.time()
            return 90
        
        error = sum(np.multiply(messwerte, sensor_gewichte))/sum_messwerte
        self._log.debug(f"Aktueller Messwerte-Fehler: {error}")

        # P
        dKP = (korrektur_proportional * error)
        self._sensorcar.p_wert = dKP
        

        # I
        self._summe_integral += error * (current_time_stamp-self._last_time)
        self._summe_integral = max(min(self._summe_integral, korrektur_integral_boundary), -korrektur_integral_boundary)   # Anti-Windup
        dKI = korrektur_integral * self._summe_integral
        self._sensorcar.i_wert = dKI

        # D
        dKD = 0
        if (current_time_stamp> self._last_time):
            dKD = (korrektur_differential * ((error - self._previous_error))/(current_time_stamp - self._last_time))
        self._sensorcar.d_wert = dKD

        u = dKP + dKI + dKD

        self._log.info(f"Lenkwinkel berechnet u:{dKP:.2f}, P:{dKP:.2f}, I:{dKI:.2f}, D:{dKD:.2f}")

        # 1. Normierung
        lw = int(max(45, min(135, 90 + u)))
        self._log.debug
        (f"Errechneter Lenkwinkel (u): {u} --> normiert: {lw}")
        # 2. Anti-Slew
        delta = lw - self._last_lenkwinkel
        delta = max(-slew_rate, min(delta, slew_rate))
        lw = self._last_lenkwinkel + delta

        # 3. Finales Clamping
        lw = max(45, min(135, lw))
        self._log.debug(f"Lenkwinkel nach anti-slew (lw): {lw}")

        self._log.debug(f"Ergebnis der Lenkwinkel-Berechung: \ndKP: {dKP}, \ndKI: {dKI}, \ndKD: {dKD}, \nu: {u}, \nlw: {lw}, \ndelta t:{current_time_stamp-self._last_time}, \nsumme integral:{self._summe_integral}")
        self._log.debug(f"Ergebnis der dKI-Berechung: \nerror: {error}, \ndelta t:{current_time_stamp-self._last_time}, \nsumme integral:{self._summe_integral}, \nkorr integral:{korrektur_integral}")

        # # Hier Werte berechnen und für Logging in Methode get_logging_payload in Klasse speichern
        # # Diese Werte kommen dann in das JSON-DataLog
        # with self._lock:
        #     self._lw_debug_values["integral"] = 0
        #     self._lw_debug_values["sum_messwerte"] = 0
        #     self._lw_debug_values["previous_error"] = previous_error


        # last_time, summe_integral, last_error, lw
        return lw

    def _calc_speed_from_steering_angle(self, lenkwinkel:float) -> int:
        v_min = self._sensorcar.v_min
        v_max = self._sensorcar.v_max
        self._log.info(f"Berechne Geschwindigkeit für Lenkwinkel: {lenkwinkel}°")
        if (45 > lenkwinkel or 135 < lenkwinkel):
            raise ValueError(f"Lenkwinkel muss zwischen 45 und 135° sein (war: {lenkwinkel})")
        
        self._speed = int(v_min + (v_max - v_min) * (1 - ((lenkwinkel-90)/45)**2))
        self._log.info(f"Kalkulierte Speed: {self._speed}")
        return self._speed

class KeyboardMode(DrivingMode):

    def __init__(self, 
                 car: Optional[BaseCar] = None, 
                 name: str = "Interaktiver Tastaturmodus", 
                 logger: Optional[logging.Logger] = None, 
                 cfg: Optional[ConfigReader.ConfigReader] = None, 
                 update_cfg: bool = False, 
                 frequency: int = 25) -> None:
        
        car = car if car is not None else SonicCar()
        logger = logging.getLogger(self.__class__.__name__)
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("soniccar_controller")
        
        super().__init__(name, car, logger, cfg, update_cfg, frequency)
        
        self.SPEED_FORWARD = 40
        self.SPEED_BACKWARD = -40
        self.ANGLE_STRAIGHT = 90
        self.ANGLE_LEFT = 60
        self.ANGLE_RIGHT = 120

    def start(self) -> None:
        self._log.info(f"Starte {self._name} direkt via start().")
        
        with self._lock:
            self._stop_event = threading.Event()
        
        try:
            curses.wrapper(self._interactive_loop)
        except Exception as e:
            self._log.exception(f"Fehler im curses-Wrapper von {self._name}: {e}")
        finally:
            self._car.stop()
            with self._lock:
                self._stop_event = None
            self._log.info(f"{self._name} beendet und Fahrzeug gestoppt.")

    def _run_condition(self) -> bool:
        return False
    
    def _run(self) -> bool:
        return False

    def _interactive_loop(self, stdscr) -> None:
        curses.cbreak()          
        stdscr.keypad(True)      
        stdscr.nodelay(True)     
        
        stdscr.clear()
        stdscr.addstr(1, 0, "Q = Vorwärts-Links | W = Vorwärts  | E = Vorwärts-Rechts")
        stdscr.addstr(2, 0, "A = Rückwärts-Links | S = Rückwärts | D = Rückwärts-Rechts")
        stdscr.addstr(3, 0, "Drücke 'Space' oder 'Esc' zum Beenden.\n")
        stdscr.refresh()
        
        last_speed = 0
        last_angle = self.ANGLE_STRAIGHT
        step_time = 1 / self._frequency

        if (self._stop_event is None):
            raise RuntimeError("Stop_event not available")
        
        while not self._stop_event.is_set():
            try:
                key = stdscr.getch()
                if key != -1:
                    speed = 0
                    angle = self.ANGLE_STRAIGHT
                    action_text = ""
                    valid_key = False

                    if key == ord('w') or key == curses.KEY_UP:
                        speed = self.SPEED_FORWARD
                        angle = self.ANGLE_STRAIGHT
                        action_text = "VORWÄRTS"
                        valid_key = True
                    elif key == ord('q'):
                        speed = self.SPEED_FORWARD
                        angle = self.ANGLE_LEFT
                        action_text = "VORWÄRTS LINKS"
                        valid_key = True
                    elif key == ord('e'):
                        speed = self.SPEED_FORWARD
                        angle = self.ANGLE_RIGHT
                        action_text = "VORWÄRTS RECHTS"
                        valid_key = True

                    elif key == ord('s') or key == curses.KEY_DOWN:
                        speed = self.SPEED_BACKWARD
                        angle = self.ANGLE_STRAIGHT
                        action_text = "RÜCKWÄRTS"
                        valid_key = True
                    elif key == ord('a') or key == curses.KEY_LEFT:
                        speed = self.SPEED_BACKWARD
                        angle = self.ANGLE_LEFT
                        action_text = "RÜCKWÄRTS LINKS"
                        valid_key = True
                    elif key == ord('d') or key == curses.KEY_RIGHT:
                        speed = self.SPEED_BACKWARD
                        angle = self.ANGLE_RIGHT
                        action_text = "RÜCKWÄRTS RECHTS"
                        valid_key = True

                    elif key == 27 or key == ord(' '):
                        self._stop_event.set()
                        break

                    if valid_key and (speed != last_speed or angle != last_angle):
                        self._car.drive(speed, angle)
                        
                        stdscr.move(5, 0)
                        stdscr.clrtoeol()
                        stdscr.addstr(5, 0, f"Aktion: {action_text} -> car.drive({speed}, {angle})")
                        stdscr.refresh()
                        
                        last_speed = speed
                        last_angle = angle

                else:
                    if last_speed != 0 or last_angle != self.ANGLE_STRAIGHT:
                        self._car.drive(0, self.ANGLE_STRAIGHT)
                        
                        stdscr.move(5, 0)
                        stdscr.clrtoeol()
                        stdscr.addstr(5, 0, "Aktion: STOPP (Keine Taste gedrückt)")
                        stdscr.refresh()
                        
                        last_speed = 0
                        last_angle = self.ANGLE_STRAIGHT

                time.sleep(step_time)

            except KeyboardInterrupt:
                self._stop_event.set()
                break



