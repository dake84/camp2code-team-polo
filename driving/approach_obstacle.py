"""Fahrmodus 3: Fahrt bis zu einem Hindernis (Ultraschall)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .sonic_car_mode import SonicCarMode
from .stop_reason import StopReason

if TYPE_CHECKING:  # nur fuer Typpruefung, kein Hardwareimport zur Laufzeit
    from SonicCar import SonicCar

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
            self._soniccar.speed =  int(self._soniccar.speed * (self._soniccar.distance/ultrasonic_max_distance_to_stop))
            self._log.info(f"Fahrzeug nähert sich dem Hindernis (d={actual_distance}), verlangsame Fahrt ({old_speed} -> {self._soniccar.speed})")
            return True
        elif not self._free_logged:
            self._log.info("Freie Fahrt voraus, kein Hindernis in Sicht")
            self._free_logged = True
            return False
        
        return False

