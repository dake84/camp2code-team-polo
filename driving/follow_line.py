from __future__ import annotations

# Gemeinsame Bibliotheken aus der bisherigen Driving.py.
# Die Fahrlogik bleibt bei dieser reinen Modulaufteilung unveraendert.
import abc
import curses
import random
import threading
import time
from datetime import datetime
from typing import Optional, Tuple
import logging
import cv2
import numpy as np
from BaseCar import BaseCar
from CameraCar import CameraCar, CameraDataProcessed, CameraData
from CarLogger import Loggable
import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar
from LenkwinkelKalkulator import lenkwinkel_from_image
import select
from evdev import InputDevice, ecodes

from .sensor_car_mode import SensorCarMode

class FollowLine(SensorCarMode):
    def __init__(self, name="Fahrmodus 5", car:Optional[SensorCar]=None):
        super().__init__(name=name, car=car)

        self._summe_integral = 0
        self._previous_error = 0
        self._last_direction = 0
        self._last_lenkwinkel = 90
        self._last_time = time.monotonic()
    
    def _run_condition(self) -> bool:
        return self._is_on_line()
    
    def _pre_run(self) -> bool:
        self._last_time = time.monotonic()
        self._summe_integral = 0.0
        self._previous_error = 0.0
        self._last_lenkwinkel = 90
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
        if (not self._sensorcar.is_on_line()):
            self._log.info("Lost line")
            return False
        return True
    
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

        sensor_gewichte = self._cfg.get_list("ir_sensor_weights", [-2,-1,0,1,2])
        slew_rate = self._cfg.get_int("slew_rate", 3)

        messwerte = self._sensorcar.ir_sensor_values

        self._log.debug(f"Starte Lenkwinkel-Berechnung mit Eingangsdaten: messwerte:{messwerte}, sensor_gewichtung:{sensor_gewichte}, slew_rate:{slew_rate}, korrektur_proportional:{korrektur_proportional}, korrektur_integral:{korrektur_integral}, summe_integral:{self._summe_integral}, korrektur_integral_boundary:{korrektur_integral_boundary}, korrektur_differential:{korrektur_differential}, previous_error:{self._previous_error}, last_time:{self._last_time}, last_lenkwinkel:{self._last_lenkwinkel}")

        current_time_stamp = time.monotonic()

        sum_messwerte = sum(messwerte)
        
        # Div/0 -> Lenkwinkel geradeaus
        if (sum_messwerte == 0):
            self._last_time = current_time_stamp
            self._previous_error = 0.0
            self._last_lenkwinkel = 90
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
        self._previous_error = error
        self._last_time = current_time_stamp
        self._last_lenkwinkel = lw
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

