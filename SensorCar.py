import sys
from typing import Tuple

import DrivingMode
from SonicCar import SonicCar
from basisklassen import Infrared
from BaseCar import BaseCar
import numpy as np
import time
import threading

class SensorCar(BaseCar):
    """Erstellung Klasse Sensor Car; Grundfunktionalitäten werden aus BaseCar geerbt und um Infrarotsensorik ergänzt, um Linien zu erkennen und entsprechend zu steuern.

    """


    __stop_calibration_event = threading.Event()

    def __init__(self, run_calibration=False):
        """Initialisiert den Infrarotsensor und lädt oder kalibriert Referenzwerte.

        Wenn keine Referenzen übergeben werden, führt das Objekt automatisch eine
        Kalibrierung durch und speichert die ermittelten Referenzwerte intern.

        Args:
            references (list | None, optional): Liste der Referenzmesswerte.
                Wenn None, werden die Referenzen automatisch kalibriert.
        """
        super().__init__()
        self._ir = Infrared()
        # Speichert eine Historie der Messwerte, um Rauschen auszugleichen (Zugriff über Methode sensor_historie)
        self._sensor_historie = []
        # Anzahl der letzten Werte, für die Ermittlung des Durchschnitt-Messwerts
        self._history_length = 5

        # Letzte Abweichung zur schwarzen Linie (für delta KD)
        self.__previous_error = 0
        # Merkt sich die zuletzt eingeschlagene Richtung (Suchmodus) -1 = links, +1 = rechts, 0 = Mitte
        self.__letzte_richtung = 0
        # Merkt sich die Zeit, zu der die Linie verloren wurde (Suchmodus)
        self.__zeit_linie_verloren = 0
        # Wie lange das Auto nach der Linie suchen soll (Suchmodus) in Sekunden
        self.__suchzeit = 5

        if (run_calibration or self.ir_sensor_min_values is None or self.ir_sensor_max_values is None) or self.calibration_line_threshold is None:
            self.kalibriere_sensoren()
            self.kalibriere_linie()
       
    def kalibriere_sensoren(self, loop = 250, output_average = 100, silent_mode=True, update_config = True) -> Tuple[list, list]:
        input("Auto orthogonal knapp vor der Linie platzieren zur Kalibrierung. Achtung: Auto bewegt sich vor- und rückwärts. <Enter> zum starten drücken.")
        messwerte = self._ir.get_average()
        sensorwerte = []
        i = 0
        mode = BaseCar.FORWARD_MODE
        while i < loop:
            if (i>loop/2): mode = BaseCar.BACKWARD_MODE
            self.drive(speed=(mode*self.v_min))
            messwerte = self._ir.get_average()         
            sensorwerte.append(messwerte)
            i+=1
        self.stop()

        sensor_min = np.min(sensorwerte, axis=0).tolist()
        sensor_max = np.max(sensorwerte, axis=0).tolist()
        
        if (not silent_mode):
            print("Kalibrierung abgeschlossen. Ergebnisse:")
            print(f"Sensor-Min-Werte (schwarz): {sensor_min} (Summe: {sum(sensor_min)})")
            print(f"Sensor-Max-Werte (weiß): {sensor_max} (Summe: {sum(sensor_max)})")
            # TODO
            save=input ("Sensorwerte in Config-Speichern? (j/n): ")
            if (save == "j"):
                print("Noch nicht implementiert")
                self._save_config()
        
        if (update_config):
            self.ir_sensor_min_values = sensor_min
            self.ir_sensor_max_values = sensor_max
            self._save_config()

        input("<ENTER> zum Fortfahren")
        return sensor_min, sensor_max

    def sensor_historie(self, history_length: int = 0) -> list:
        if (history_length<1): history_length = self._history_length
        if (len(self._sensor_historie) > self._history_length):
            # Kürze Historie, falls länger als length
            self._sensor_historie = self._sensor_historie[-self._history_length:]
        
        return self._sensor_historie

    def normierte_sensorwerte(self, history_length: int=0) -> list:
        if (history_length<1): history_length = self._history_length
        self._sensor_historie.append(self._ir.read_analog())

        messwerte = np.mean(self.sensor_historie(history_length), axis = 0)

        normierte_werte = []

        for i in range(len(messwerte)):
            roh = messwerte[i]
            s_min = self.ir_sensor_min_values[i]
            s_max = self.ir_sensor_max_values[i]
        
            if s_max == s_min:
                normiert = 0
            else:
                v = (roh-s_min) / (s_max-s_min)
                v = max(0.0, min(1.0, v))
                # 1000 = 100% weiß, 0 = 100% schwarz
                normiert = int(v*1000)
            normierte_werte.append(normiert)

        return normierte_werte

    def kalibriere_linie(self):
        input("Line-Threshold ermitteln. Auto so auf Linie positionieren, dass sie gerade noch registriert wird. <Enter> zum Beginnen")

        calibration_thread = threading.Thread(target=self._line_calibrator)
        calibration_thread.start()
        input()
        self.__stop_calibration_event.set()
        calibration_thread.join()

    def _line_calibrator(self):
        while (not self.__stop_calibration_event.is_set()):
            messwerte = self.normierte_sensorwerte()
            min_mw = min(messwerte)
            max_mw = max(messwerte)
            print(f"\rNormierte Messwerte: {messwerte}, Summe: {sum(messwerte)}, Min: {min_mw}, Max: {max_mw}, Diff: {max_mw-min_mw}\n<ENTER> zum Beenden", end="")
            sys.stdout.flush()
            time.sleep(0.05)

    def lenkwinkel_berechnen(self) -> float:
        """Berechnet den Lenkwinkel basierend auf IR-Messwerten und PD-Korrekturfaktoren.

        Die Funktion nutzt gewichtete Infrarotmesswerte, um einen Fehlerwert zu bestimmen
        und daraus mittels proportionaler und differenzieller Steuerung den Lenkwinkel
        zu berechnen. Der resultierende Winkel wird auf den Bereich von 45 bis 135 Grad
        begrenzt.

        Returns:
            float: Der berechnete Lenkwinkel im Bereich von 45 bis 135 Grad.
        """

        messwerte = self.normierte_sensorwerte()
        sum_messwerte = sum(messwerte)
        
        # Div/0 -> Lenkwinkel geradeaus
        if (sum_messwerte == 0):
            return 90
        
        error = sum(np.multiply(messwerte, self.ir_sensor_gewichte))/sum_messwerte
        dKP = (self.korrektur_proportional * error) 
        dKD = (self.korrektur_differential * (error - self.__previous_error))
        u = dKP + dKD
        lw = max(45, min(135, (90+u)))
        self.__previous_error = error

        # Richtung für Suchmodus merken
        print(f"Current Error: {error}")
        if (error > 0.2):
            self.__letzte_richtung = 1
        elif (error < -0.2):
            self.__letzte_richtung = -1


        #print(f"dKP: {dKP}, dKD: {dKD}, u: {u}, lw: {lw}")
        return lw

    def geschwindigkeit_berechnen(self, lenkwinkel : float, update_config=False):
        """Berechnet Geschwindigkeit in Abhängigkeit von Lenkwinkel

        Args:
            lenkwinkel (float): Lenkwinkel

        Returns:
            int: Die berechnete Geschwindigkeit.
        """
        v = max(0, self.v_max - (self.bremsfaktor * abs((lenkwinkel - 90))))
        return int(max(v, self.v_min))
   
    def is_on_line(self, lost_time: float=0, history_length:int=0) -> bool:
        """Prüft, ob das Auto sich mittig auf der Linie befindet, basierend auf den IR-Messwerten.

        Returns:
            bool: True/False, basierend auf den durchschnittlichen IR-Messwerten des mittleren Sensors (auf Position 2 von 0-4). Messwert < 1 = dunkle Linie erkannt
        """
        hist = self.normierte_sensorwerte(history_length)
             
        min_sensor = min(hist)
        max_sensor = max(hist)

        # Example:
        # (1000 (weiß) - 200 (schwarz) < 800 -> false --> on line
        # (1000 (weiß) - 300 (schwarz) < 800 -> true --> not on line
        if (max_sensor - min_sensor < self.calibration_line_threshold):
#        if (line > self.calibration_line_threshold):
            if (lost_time == 0): self.__zeit_linie_verloren = time.time()
            #print(f"Auto hat Linie verlassen (line {hist} (delta: {(max_sensor-min_sensor)}) < calibration_line_threshold {self.calibration_line_threshold})")
            return False
        else:
            self.__zeit_linie_verloren = 0
            return True

    def search(self) -> bool:
        if (self.__zeit_linie_verloren > 0):
            while (time.time() - self.__zeit_linie_verloren < self.__suchzeit):
                self.drive(self.v_min, (90+(self.__letzte_richtung*45)))
                if (self.is_on_line(lost_time=self.__zeit_linie_verloren, history_length=100)):
                    return True
            return False
        raise RuntimeError("Keine Suchzeit gesetzt (Lost-Time: {self.__zeit_linie_verloren}, Search-Time: {self.__suchzeit})")
    
    @property
    def korrektur_proportional(self) -> float:
        """Liefert den aktuellen Wert für die proportionale Korrektur, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "korrektur_proportional" in Config.Json nicht gefunden.
        
        Returns:
            float: Korrekturwert für die proportionale Steuerung
        """
        return float(self.get_config()["korrektur_proportional"])

    @property
    def korrektur_differential(self) -> float:
        """Liefert den aktuellen Wert für die differentiale Korrektur, der über Methode get_config() aus der Datei json.config ausgelesen wird.

        Raises:
            KeyError: Wert "korrektur_differential" in Config.Json nicht gefunden.
        
        Returns:
            float: Korrekturwert für die differentiale Steuerung   
        """
        return float(self.get_config()["korrektur_differential"])
    
    @property
    def bremsfaktor(self) -> float: 
        """Liefert den aktuellen Wert für den Bremsfaktor, der über Methode get_config() aus der Datei json.config ausgelesen wird. Wird verwendet, um die Lenkwinkel-abhängige Geschwindigkeit zu berechnen.

        Raises:
            KeyError: Wert "bremsfaktor" in Config.Json nicht gefunden.
        
        Returns:
            float: Bremsfaktor   
        """
        return float(self.get_config()["bremsfaktor"])        

    @property
    def ir_sensor_gewichte(self) -> list:
        """Liefert die Gewichte für die Ermittlung des Lenkwinkel-Fehlers

        Returns:
            np.ndarray: Array mit fünf Gewichts-Werten
        
        """
        return self.get_config()["ir_sensor_weights"]

    @property
    def v_max(self, update_config=False) -> int:
        return int(self.get_config()["v_max"])

    @property
    def v_min(self) -> int:
        return int(self.get_config()["v_min"])

    @property
    def calibration_line_threshold(self) -> float:
        return float(self.get_config()["calibration_line_threshold"])

    @property
    def ir_sensor_min_values(self) -> list:
        return self.get_config()["ir_sensor_min_values"]
    
    @property
    def ir_sensor_max_values(self) -> list:
        return self.get_config()["ir_sensor_max_values"]

    @ir_sensor_min_values.setter
    def ir_sensor_min_values(self, values:list):
        self.set_config("ir_sensor_min_values", values)

    @ir_sensor_max_values.setter
    def ir_sensor_max_values(self, values:list):
        self.set_config("ir_sensor_max_values", values)


# Müsste untenstehender Code noch in die Class Sensor Car integriert werden, damit die IR-gestützte Funktion auto_fahren() als Methode der Klasse SensorCar aufgerufen werden kann? 
# DKE: ginge, siehe: https://www.w3tutorials.net/blog/run-class-methods-in-threads-python/#2-why-run-class-methods-in-threads
# Gedanke: Funktion auto_fahren in SensorCar integrieren
stop_event = threading.Event()

def auto_fahren(car : BaseCar, dm : int=DrivingMode.FOLLOW_LINE):

    print(f"Auto fährt (Fahrmodus {dm})...")
    
    if (dm in (DrivingMode.FOLLOW_LINE, DrivingMode.ADVANCED_FOLLOW_LINE)):
        if isinstance(car, SensorCar):
            while not stop_event.is_set():
                line = car.normierte_sensorwerte()
                if (car.is_on_line()):

                    # Reload config.json from disk
                    car._update_config()

                    lw = car.lenkwinkel_berechnen()
                    v = car.geschwindigkeit_berechnen(lw)

                    car.drive(v, lw)

                    print(f"Driving (v: {v}, lw: {lw})")
                elif (dm == DrivingMode.FOLLOW_LINE):
                    # Stoppen sobald Linie verloren
                    car.stop()
                    while not car.is_on_line() and not stop_event.is_set():
                        # Reload config.json from disk
                        car._update_config()
                        time.sleep(0.5)
                elif (dm == DrivingMode.ADVANCED_FOLLOW_LINE):
                    # Suchen sobald Linie verloren
                    print("Suche Weg...")
                    if (not car.search()):
                        print("Weg nicht gefunden")
                        #break
                    else:
                        print("Weg gefunden")
                #    print(f"Lenkwinkel: {lw}, Geschwindigkeit: {v}")
            car.stop()
            return
        else:
            raise TypeError(f"Für Fahrmodus {dm} muss sein SensorCar übergeben werden.")
    elif (dm == DrivingMode.ADVANCED_FOLLOW_LINE_WITH_OBSTACLE_DETECTION):
        if (isinstance(car, SensorCar) and isinstance(car, SonicCar)):
            # Fahrmodus noch nicht implementiert
            pass
        else:
            raise TypeError(f"Für Fahrmodus {dm} muss sein Car vom Typ SensorCar und SonicCar übergeben werden.")
    
    raise NotImplementedError(f"Fahrmodus {dm} nicht unterstützt")


#losgelöst von SensorCar.py definieren, bspw. in run.py?
if __name__ == '__main__':

        sc = SensorCar(run_calibration=False)
        input("<Enter> to go")
        #sc.kalibriere_sensoren(update_config=False)
        # sc.kalibriere_linie()
        #while(input("Nochmal (j/n)") != "n"):
        #    s = sc.kalibriere_sensoren()
        #   
        #    print(f"s_min: {s[0]}, s_max: {s[1]}")
        #exit()

        # ToDo: Futures nutzen anstatt Threads?!
        # https://coderivers.org/blog/python-thread-vs-concurrent/
        thread = threading.Thread(target=auto_fahren, args=[sc, DrivingMode.ADVANCED_FOLLOW_LINE])
        thread.start()
        input("Auto fährt, zum Beenden <ENTER> drücken")
        
        stop_event.set()

        thread.join()
        sc.stop()
        print("Programm vollständig beendet")
