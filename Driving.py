import random
import threading
import time
from typing import Optional, Tuple
import logging
import numpy as np

from BaseCar import BaseCar
from CarLogger import Loggable
import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar

class DrivingMode():
    FORWARD_BACKWARD = 10
    CIRCULAR = 20
    CIRCULAR_LEFT = CIRCULAR
    CIRCULAR_RIGHT = 25
    APPROACH_OBSTACLE = 30
    EXPLORE = 40
    FOLLOW_LINE = 50
    ADVANCED_FOLLOW_LINE = 60
    ADVANCED_FOLLOW_LINE_WITH_OBSTACLE_DETECTION = 70
    STADIA_CONTROLLER = 100

    SUPPORTED_DRIVING_MODES = {
        FORWARD_BACKWARD: BaseCar,
        CIRCULAR: BaseCar,
        CIRCULAR_LEFT: BaseCar,
        CIRCULAR_RIGHT: BaseCar,
        APPROACH_OBSTACLE: SonicCar,
        EXPLORE: SonicCar,
        FOLLOW_LINE: SensorCar,
        ADVANCED_FOLLOW_LINE: SensorCar,
        ADVANCED_FOLLOW_LINE_WITH_OBSTACLE_DETECTION: SensorCar,
        STADIA_CONTROLLER: BaseCar
    }

    @staticmethod
    def is_supported_driving_mode(dm:int, car:BaseCar):
        return isinstance(DrivingMode.SUPPORTED_DRIVING_MODES[dm], type(car))

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

class DriveController(Loggable):

    def __init__(self, car:Optional[BaseCar]=None, driving_mode:int=DrivingMode.FORWARD_BACKWARD, sensor_config:Optional[ConfigReader.ConfigReader]=None):
        print("Check, bin da!!!")
        self._dm = driving_mode
        self._car = car if car is not None else BaseCar()
        
        self._log = logging.getLogger(self.__class__.__name__)

        # Anzahl der letzten Werte, für die Ermittlung des Durchschnitt-Messwerts
        self._history_length = 1
        # Initialisiert Integral für Fehler
        self._integral = 0
        # Initialisiert Gewichtung für KD
        self._kd_weight = 0
        # Letzte Abweichung zur schwarzen Linie (für delta KD)
        self._last_derivative = 0
        # Initialisierung Slew Rate
        self._slew_rate = 0
        # Initialisierung Error Tolerance
        self._error_tolerance = 0
        #Initialisierung last_time_stamp
        self._last_time_stamp = 0


        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader("drive_controller")


        # Im Test Mode wird die cfg laufend nachgeladen (macht's vieeeel langsamer)
        self._test_mode = True

        if (driving_mode not in DrivingMode.SUPPORTED_DRIVING_MODES or not isinstance(car, DrivingMode.SUPPORTED_DRIVING_MODES[driving_mode])): 
            raise ValueError(f"DrivingMode {driving_mode} nicht unterstützt für Fahrzeug vom Typ {type(car)} (bedingt {DrivingMode.SUPPORTED_DRIVING_MODES[driving_mode]}).")
        self._lock = threading.Lock()

        # Inititalize var for lenkwinkel calc debug values
        self._lw_debug_values = {}


    def drive_car(self, stop_event:threading.Event, driving_mode:Optional[int]=None):
        dm = driving_mode if driving_mode is not None else self._dm
        self._car._update_config()

        if (dm not in DrivingMode.SUPPORTED_DRIVING_MODES or not isinstance(self._car, DrivingMode.SUPPORTED_DRIVING_MODES[dm])): 
            error = ValueError(f"DrivingMode {dm} nicht unterstützt für Fahrzeug vom Typ {type(self._car)}.")
            self._log.error(error)
            raise error

        self.run = True

        if (dm == DrivingMode.FORWARD_BACKWARD):
            self._log.debug('Starte Fahrmodus 1')
            self._car.stop()
            time.sleep(1)

            self._car.drive(30)
            self._log.debug(f"3 Sekunden vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(3)

            self._car.stop()
            self._log.debug(f"1 Sekunde Stopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(1)
            
            self._car.drive(-30)
            self._log.debug(f"3 Sekunden rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(3)

            self._car.stop()
            self._log.debug(f"Fahrmodus 1 beendet. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")            
        elif (dm in (DrivingMode.CIRCULAR, DrivingMode.CIRCULAR_LEFT, DrivingMode.CIRCULAR_RIGHT)):
            direction = (135, "rechts/Uhrzeigersinn") if dm == DrivingMode.CIRCULAR_RIGHT else (45, "links/gegen Uhrzeigersinn")
            
            self._log.debug('Starte Fahrmodus 2 ({direction[1]})')
            self._car.stop()
            time.sleep(1)

            self._car.drive(30)
            self._log.debug(f"1 Sekunde vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(1)

            self._car.drive(30, direction[0])
            self._log.debug(f"8 Sekunden {direction[1]} vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(8)

            self._car.stop()
            self._log.debug(f"Kurzer Zwischenstopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(2)

            self._car.drive(-30, direction[0])
            self._log.debug(f"8 Sekunden {direction[1]} rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(8)

            self._car.drive(-30, 90)
            print(f"1 Sekunde rückwärts zum Startpunkt. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(1)

            self._car.stop()
            print(f"Fahrmodus 2 {direction[1]} beendet.")              
        elif (dm == DrivingMode.APPROACH_OBSTACLE):
            try:
                if isinstance(self._car, SensorCar):
                    self._approach_obstacle(self._car, stop_event)
            except Exception as e:
                self._log.error(e)
        elif (dm == DrivingMode.EXPLORE):
            # Fahrmodus 4
            try:
                if isinstance(self._car, SensorCar):
                    self._room_explorer(self._car, stop_event)
            except Exception as e:
                self._log.error(e)
        elif (dm in (DrivingMode.FOLLOW_LINE, DrivingMode.ADVANCED_FOLLOW_LINE)):
            self._follow_line(dm, stop_event)
        elif (dm == DrivingMode.ADVANCED_FOLLOW_LINE_WITH_OBSTACLE_DETECTION):
            raise NotImplementedError(f"Fahrmodus {dm} noch nicht implementiert")

        # just to be surrrre...
        # self.stop(self._stop_reason)
 
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

        actual_speed_drive_explore = 60
        steering_angle_drive_explore = 90
        speed_direction = random.choice([-1, 1])
        steer_direction = random.choice([-1, 1])
        counter = 0


        while (not stop_event.is_set() and (time.time()-t_start > explorer_max_time)):
            car.drive(actual_speed_drive_explore, steering_angle_drive_explore)

            actual_distance = car.distance
            
            if (actual_distance < (1.33*ultrasonic_max_distance_to_stop)):
                car.speed = self._calculate_speed_from_distance(car, actual_distance)

            if (actual_distance <= ultrasonic_max_distance_to_stop):
                self._overcome_obstacle(car)
            else:
                actual_speed_drive_explore, steering_angle_drive_explore, speed_direction, steer_direction, counter = self.drive_explore(car, actual_speed_drive_explore,
                                                                                 steering_angle_drive_explore,
                                                                                 speed_direction,
                                                                                 steer_direction,
                                                                                 counter
                                                                                )                

    def drive_explore(self, car:SonicCar, actual_speed: int, steering_angle: int, speed_dir: int, steer_dir: int, counter: int) -> Tuple[int, int, int, int, int]:

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

        self._log.debug(f"Exploriere den Raum..... (Geschwindigkeit: {actual_speed}, Lenkwinkel: {steering_angle})")
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
        self._log.debug(f"Overcoming obstacle..... driving to the {ausweich_lenkung[1]}")

        car.drive(-30, ausweich_lenkung[0])
        time.sleep(zufalls_zeit) # Hier die Rückwärtsfahrzeit zufällig setzen

    def _approach_obstacle(self, car:SonicCar, stop_event:threading.Event):
            
            ultrasonic_max_distance_to_stop = self._cfg.get_int("ultrasonic_max_distance_to_stop", 30)
            actual_distance = car.distance
            self._log.debug(f"Max-Distance to stop: {ultrasonic_max_distance_to_stop}, actual_distance: {actual_distance}")
            while (not stop_event.is_set() and actual_distance > ultrasonic_max_distance_to_stop):
                
                if (actual_distance < (1.33*ultrasonic_max_distance_to_stop)):
                    car.speed = self._calculate_speed_from_distance(car, actual_distance)
                else:
                    car.drive(car.v_max, 90)
    
                actual_distance = car.distance

            self.stop_car(StopReason.OBSTACLE_AHEAD)
        
    def _calculate_speed_from_distance(self, car:SonicCar, distance:int):
            car.speed = distance+car.v_min
            self._log.debug(f"Speed set because we are approaching an obstacle :-o (Speed: {car.speed}, Distance: {distance})")

    def stop_car(self, reason:StopReason|int=0):
        self._stop_reason = StopReason(reason)
        self._run = False
        self._log.debug(f"Car stopped for {reason}")
        self._car.stop()

    def obstacle_ahead(self):
        self.stop_car(StopReason.OBSTACLE_AHEAD)

    def _follow_line(self, driving_mode:int, stop_event:threading.Event):    
        if not isinstance(self._car, SensorCar): raise ValueError(self.__ve(SensorCar))
        last_time = time.time()
        
        # These values will be set in the driving loooooop
        last_integral = 0
        last_error = 0
        last_direction = 0
        
        while (self.run or not stop_event.is_set()):

            # Cfg nachladen (kostet Performance!!!!)
            if (self._test_mode): self._car._update_config()

            # These values must come from the Car-Configuration-File
            korrektur_proportional = self._car.get_config().get("korrektur_proportional", 5)
            korrektur_integral = self._car.get_config().get("korrektur_integral", 0)
            anti_windup = self._car.get_config().get("anti_windup", 0.3)
            korrektur_differential = self._car.get_config().get("korrektur_differential", 0)
            v_min = self._car.get_config().get("v_min", 20)
            v_max = self._car.get_config().get("v_max", 70)
            
            # Erforderlicher Kontrast zur Linienerkennung (in Prozent)
            minimum_line_contrast = self._car.get_config().get("minimum_line_contrast", 0.5)
            
            # Offset vom Lenkwinkel zur Erkennung der letzten Richtung (90 +- lw_threshold)
            steering_direction_offset = self._car.get_config().get("steering_direction_offset", 0.2)

            if (self._is_on_line(self.ir_sensor_values, minimum_line_contrast)):
                
                # Lenkwinkel berechnen und zeitpersistente Werte merken
                last_time, last_integral, last_error, lw = self._calc_steering_angle_from_ir_sensors(self.ir_sensor_values, korrektur_proportional, korrektur_integral, last_integral, anti_windup, korrektur_differential, last_error, last_time)

                # Letzte Lenkrichtung merken
                if (lw > (90+steering_direction_offset)):
                    last_direction = 1
                elif (lw < (90-steering_direction_offset)):
                    last_direction = -1

                v = self._calc_speed_from_steering_angle(lw, v_min, v_max)

                self._car.drive(v, lw)
                time.sleep(0.1)
            elif (driving_mode in (DrivingMode.ADVANCED_FOLLOW_LINE, DrivingMode.ADVANCED_FOLLOW_LINE_WITH_OBSTACLE_DETECTION)):
                lost_line_time = time.time()
                search_time = 0
                while ((lost_line_time+search_time) > time.time() and not self._is_on_line(self.ir_sensor_values, minimum_line_contrast)):
                    self._search(last_direction, v_min)
                    time.sleep(0.1)
            else:
                self.stop_car(StopReason.LOST_LINE)

    @property
    def ir_sensor_values(self) -> list[float]:
        if (isinstance(self._car, SensorCar)):
            return self._car.ir_sensor_values # oder alternativ self._car.weighted_ir_sensor_values
        raise ValueError(self.__ve(SensorCar))

    def _search(self, direction:int, v_min:int):
        angle = 90 + (direction*45)
        self._car.drive(v_min, angle)

    def _is_on_line(self, messwerte:list[float], line_threshold:float) -> bool:
        min_sensor = min(messwerte)
        max_sensor = max(messwerte)

        return (min_sensor/max_sensor < line_threshold)

  
    @property
    def korrektur_proportional(self) -> float:
        """Liefert den aktuellen Wert für die proportionale Korrektur, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "korrektur_proportional" in Config.Json nicht gefunden.
        
        Returns:
            float: Korrekturwert für die proportionale Steuerung
        """
        return self._cfg.get_float("korrektur_proportional", 50)

    @property
    def korrektur_integral (self) -> float:
        """Liefert den aktuellen Wert für die integrale Korrektur, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "korrektur_integral" in Config.Json nicht gefunden.
        
        Returns:
            float: Korrekturwert für die integrale Steuerung   
        """
        return self._cfg.get_float("korrektur_integral", 10)

    @property
    def korrektur_integral_min_boundary (self) -> float:
        """Liefert den aktuellen Min-Wert für die integrale Korrektur, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "korrektur_integral_min_boundary" in Config.Json nicht gefunden.
        
        Returns:
            float: Min. Korrekturwert für die integrale Steuerung   
        """
        return self._cfg.get_float("korrektur_integral_min_boundary", -30)
        

    @property
    def korrektur_integral_max_boundary (self) -> float:
        """Liefert den aktuellen Wert für die integrale Korrektur, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "korrektur_integral_max_boundary" in Config.Json nicht gefunden.
        
        Returns:
            float: Max. Korrekturwert für die integrale Steuerung   
        """
        return self._cfg.get_float("korrektur_integral_max_boundary", 30)
    
    @property
    def korrektur_differential(self) -> float:
        """Liefert den aktuellen Wert für die differentiale Korrektur, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "korrektur_differential" in Config.Json nicht gefunden.
        
        Returns:
            float: Korrekturwert für die differentiale Steuerung   
        """
        return self._cfg.get_float("korrektur_differential", 150)
    
    @property
    def kd_weight(self) -> float:
        """Liefert den aktuellen Wert für kd_weight, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "kd_weight" in Config.Json nicht gefunden.
        
        Returns:
            float: kd_weight   
        """
        return self._cfg.get_float("kd_weight", 0.5)
    
    @property
    def slew_rate(self) -> float:
        """Liefert slew rate für Lenkwinkel, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "slew_rate" in Config.Json nicht gefunden.
        
        Returns:
            float: slew_rate  
        """
        return self._cfg.get_float("slew_rate", 3)
     
    @property
    def error_tolerance(self) -> float:
        """Liefert Fehlertoleranz für Lenkwinkel, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "error_tolerance" in Config.Json nicht gefunden.
        
        Returns:
            float: error_tolerance  
        """
        return self._cfg.get_float("error_tolerance", 0)


    def _calc_steering_angle_from_ir_sensors(self, messwerte:list[float], korrektur_proportional:float, korrektur_integral:float, korrektur_differential:float):
        
        """Berechnet den Lenkwinkel basierend auf IR-Messwerten und PID-Korrekturfaktoren.

        Die Funktion nutzt gewichtete Infrarotmesswerte, um einen Fehlerwert zu bestimmen
        und daraus mittels proportionaler und differenzieller Steuerung den Lenkwinkel
        zu berechnen. Der resultierende Winkel wird auf den Bereich von 45 bis 135 Grad
        begrenzt.

        Returns:
            float: Der berechnete Lenkwinkel im Bereich von 45 bis 135 Grad.
        """
        if not isinstance(self._car, SensorCar): raise ValueError(self.__ve(SensorCar))
        
        last_time_stamp = self._last_time_stamp if (self._last_time_stamp > 0) else time.time()
        current_time_stamp = time.time()

        if korrektur_differential is None:
            korrektur_differential = self.korrektur_differential
        if korrektur_integral is None:
            korrektur_integral = self.korrektur_integral
        if korrektur_proportional is None:
            korrektur_proportional = self.korrektur_proportional            

        messwerte = self._car.ir_sensor_value_history(length = self._history_length, clear_history=True)
        sum_messwerte = sum(messwerte)
        
        # Div/0 -> Lenkwinkel geradeaus
        if (sum_messwerte == 0):
            return 90
        
        error = sum(np.multiply(messwerte, self._cfg.get_list("ir_sensor_weights")))/sum_messwerte
        print(error)
        # if abs(error) < self._error_tolerance:
        #     error = 0

        # P
        dKP = (self.korrektur_proportional * error) 
        
        # I
        self._integral += error * (current_time_stamp-last_time_stamp)
        self._integral = max(min(self._integral, self.korrektur_integral_max_boundary), self.korrektur_integral_min_boundary)   # Anti-Windup
        dKI = self.korrektur_integral * self._integral 
        
        # D
        # derivative = self._kd_weight * (error - self.__previous_error) + (1-self._kd_weight)*self._last_derivative
        # self._last_derivative = derivative
        # dKD = (self.korrektur_differential * derivative)
        dKD = 0
        if (current_time_stamp> last_time_stamp):
            dKD = (self.korrektur_differential * ((error - self.__previous_error))/(current_time_stamp - last_time_stamp))


        u = dKP + dKI + dKD

        lw = max(45, min(135, 90 + u))

        # Slew-Rate-Limit in beide Richtungen
        max_step = self._slew_rate
        delta = lw - self._lw_previous

        if delta > max_step:
            lenkwinkel = self._lw_previous + max_step
        elif delta < -max_step:
            lenkwinkel = self._lw_previous - max_step
        else:
            lenkwinkel = lw

        self._lw_previous = lenkwinkel
        self.__previous_error = error




        print(f"dKP: {dKP}, dKD: {dKD}, u: {u}, lw: {lw}")
        return lenkwinkel

        
        # Hier Werte berechnen und für Logging in Methode get_logging_payload in Klasse speichern
        with self._lock:
            self._lw_debug_values["integral"] = 0
            self._lw_debug_values["sum_messwerte"] = 0


        raise NotImplementedError(f"Fahrmodus noch nicht implementiert")

    def _calc_speed_from_steering_angle(self, lenkwinkel:float, v_min:int, v_max:int) -> int:
        #assert (45 < lenkwinkel < 135), "Lenkwinkel muss zwischen 45 und 135° sein"
        if (45 > lenkwinkel or 135 < lenkwinkel):
            raise ValueError(f"Lenkwinkel muss zwischen 45 und 135° sein (war: {lenkwinkel})")
        self._speed = int(v_min + (v_max - v_min) * (1 - ((lenkwinkel-90)/45)**2))
        return self._speed

    def __ve(self, typ:type) -> str:
        return f"{type(self._car)} nicht kompatibel mit dieser Funktion. Mindestens Fahrzeug vom Typ {typ} notwendig."

    # TODO LOG COMPUTED VALUES
    def get_logging_payload(self, log_level:int=logging.INFO) -> dict:
        with self._lock:
            payload = self._car.get_logging_payload()
            payload["lenkwinkel"] = "yet to be implemented"
            if (log_level == logging.DEBUG):
                payload["lenkwinkel_calculus"] = self._lw_debug_values
            return payload

