import threading
import time
from typing import Optional
import logging

from BaseCar import BaseCar
from CarLogger import CarLogger, Loggable
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

    def __init__(self, car:Optional[BaseCar]=None, driving_mode:int=DrivingMode.FORWARD_BACKWARD, car_logger:Optional[CarLogger]=None):
        self._dm = driving_mode
        self._car = car if car is not None else BaseCar()
        self._l = car_logger if car_logger is not None else CarLogger(self._car)


        # Im Test Mode wird die cfg laufend nachgeladen (macht's vieeeel langsamer)
        self._test_mode = True

        if (driving_mode not in DrivingMode.SUPPORTED_DRIVING_MODES or not isinstance(car, DrivingMode.SUPPORTED_DRIVING_MODES[driving_mode])): 
            raise ValueError(f"DrivingMode {driving_mode} nicht unterstützt für Fahrzeug vom Typ {type(car)} (bedingt {DrivingMode.SUPPORTED_DRIVING_MODES[driving_mode]}).")
    
        self._lock = threading.Lock()

        # Inititalize var for lenkwinkel calc debug values
        self._lw_debug_values = {}



    def drive_car(self, stop_event:threading.Event, driving_mode:Optional[int]=None):
        dm = driving_mode if driving_mode is not None else self._dm
        if (dm not in DrivingMode.SUPPORTED_DRIVING_MODES or not isinstance(self._car, DrivingMode.SUPPORTED_DRIVING_MODES[dm])): 
            raise ValueError(f"DrivingMode {dm} nicht unterstützt für Fahrzeug vom Typ {type(self._car)}.")

        self.run = True

        if (dm == DrivingMode.FORWARD_BACKWARD):
            self._l.debug('Starte Fahrmodus 1')
            self._car.stop()
            time.sleep(1)

            self._car.drive(30)
            self._l.debug(f"3 Sekunden vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(3)

            self._car.stop()
            self._l.debug(f"1 Sekunde Stopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(1)
            
            self._car.drive(-30)
            self._l.debug(f"3 Sekunden rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(3)

            self._car.stop()
            self._l.debug(f"Fahrmodus 1 beendet. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")            
        elif (dm in (DrivingMode.CIRCULAR, DrivingMode.CIRCULAR_LEFT, DrivingMode.CIRCULAR_RIGHT)):
            direction = (135, "rechts/Uhrzeigersinn") if dm == DrivingMode.CIRCULAR_RIGHT else (45, "links/gegen Uhrzeigersinn")
            
            self._l.debug('Starte Fahrmodus 2 ({direction[1]})')
            self._car.stop()
            time.sleep(1)

            self._car.drive(30)
            self._l.debug(f"1 Sekunde vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(1)

            self._car.drive(30, direction[0])
            self._l.debug(f"8 Sekunden {direction[1]} vorwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(8)

            self._car.stop()
            self._l.debug(f"Kurzer Zwischenstopp. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(2)

            self._car.drive(-30, direction[0])
            self._l.debug(f"8 Sekunden {direction[1]} rückwärts. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(8)

            self._car.drive(-30, 90)
            print(f"1 Sekunde rückwärts zum Startpunkt. Geschwindigkeit: {self._car.speed}, Lenkwinkel: {self._car.steering_angle}")
            time.sleep(1)

            self._car.stop()
            print(f"Fahrmodus 2 {direction[1]} beendet.")              
        elif (dm == DrivingMode.APPROACH_OBSTACLE):
            raise NotImplementedError(f"Fahrmodus {dm} noch nicht implementiert")
        elif (dm == DrivingMode.EXPLORE):
            raise NotImplementedError(f"Fahrmodus {dm} noch nicht implementiert")
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

    def stop_car(self, reason:StopReason|int=0):
        self._stop_reason = StopReason(reason)
        self._run = False
        self._l.debug(f"Car stopped for {reason}")
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

    def _calc_steering_angle_from_ir_sensors(self, messwerte:list[float], korrektur_proportional:float, korrektur_integral:float, summe_integral:float, anti_windup:float, korrektur_differential:float, previous_error:float, last_time:float) -> tuple[float, float, float, int]:
        if not isinstance(self._car, SensorCar): raise ValueError(self.__ve(SensorCar))

        
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

