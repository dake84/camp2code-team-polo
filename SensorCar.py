from typing import Tuple

from basisklassen import Infrared
from BaseCar import BaseCar
import numpy as np
import time
import threading

class SensorCar(BaseCar):
    """Erstellung Klasse Sensor Car; Grundfunktionalitäten werden aus BaseCar geerbt und um Infrarotsensorik ergänzt, um Linien zu erkennen und entsprechend zu steuern.

    """

    # Speichert eine Historie der Messwerte, um Rauschen auszugleichen (Zugriff über Methode sensor_historie)
    _sensor_historie = []
    # Anzahl der letzten Werte, für die Ermittlung des Durchschnitt-Messwerts
    _history_length = 5

    # Letzte Abweichung zur schwarzen Linie (für delta KD)
    __previous_error = 0
    # Merkt sich die zuletzt eingeschlagene Richtung (Suchmodus) -1 = links, +1 = rechts, 0 = Mitte
    __letzte_richtung = 0
    # Merkt sich die Zeit, zu der die Linie verloren wurde (Suchmodus)
    __zeit_linie_verloren = None
    # Wie lange das Auto nach der Linie suchen soll (Suchmodus) in Sekunden
    __suchzeit = 1.5


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

        if (run_calibration):
            self.sensor_min, self.sensor_max = self.kalibriere_sensoren()
        


    def kalibriere_sensoren(self, loop = 250, output_average = 100) -> Tuple[np.ndarray,np.ndarray]:
        input("Auto orthogonal knapp vor der Linie platzieren zur Kalibrierung. Achtung: Auto bewegt sich vor- und rückwärts. <Enter> zum starten drücken.")
        messwerte = self._ir.get_average()
        sensorwerte = []
        i = 0
        mode = BaseCar.FORWARD_MODE
        while i < loop:
            if (i>loop/2): mode = BaseCar.BACKWARD_MODE
            self.drive(speed=(mode*self.v_min))
            messwerte = self._ir.get_average()
#            if (sum(messwerte) < self.calibration_line_threshold):
#                self.stop()
#                print(f"Linie gefunden! (Messwerte: {messwerte}, Summe: {sum(messwerte)})")
#                   
#                time.sleep(0.1)
#                self.drive(speed=(mode*self.v_min))            
            sensorwerte.append(messwerte)
            i+=1
        self.stop()

        self.sensor_min = np.min(sensorwerte, axis=0)
        self.sensor_max = np.max(sensorwerte, axis=0)
        
        print("Kalibrierung abgeschlossen. Ergebnisse:")
        print(f"Sensor-Min-Werte: {self.sensor_min} (Summe: {sum(self.sensor_min)})")
        print(f"Sensor-Max-Werte: {self.sensor_max} (Summe: {sum(self.sensor_max)})")
        input("<ENTER> zum Fortfahren")
        return self.sensor_min, self.sensor_max

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
            s_min = self.sensor_min[i]
            s_max = self.sensor_max[i]
        
            if s_max == s_min:
                normiert = 0
            else:
                v = (roh-s_min) / (s_max-s_min)
                v = max(0.0, min(1.0, v))
                normiert = int(v*1000)
            normierte_werte.append(normiert)

        return normierte_werte

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
        
        error = sum((messwerte*self.ir_sensor_gewichte))/sum_messwerte
        dKP = (self.korrektur_proportional * error) 
        dKD = (self.korrektur_differential * (error - self.__previous_error))
        u = dKP + dKD
        lw = max(45, min(135, (90+u)))
        self.__previous_error = error

        # Richtung für Suchmodus merken
        if (error > 0.2):
            self.__letzte_richtung = 1
        elif (error < -0.2):
            self.__letzte_richtung = -1
        else:
            self.__letzte_richtung = 0

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
   
    def is_on_line(self) -> bool:
        """Prüft, ob das Auto sich mittig auf der Linie befindet, basierend auf den IR-Messwerten.

        Returns:
            bool: True/False, basierend auf den durchschnittlichen IR-Messwerten des mittleren Sensors (auf Position 2 von 0-4). Messwert < 1 = dunkle Linie erkannt
        """
        hist = self.normierte_sensorwerte()
        line = np.sum(hist)
        if (line > self.calibration_line_threshold):
            self.__zeit_linie_verloren = time.time()
            print(f"Auto hat Linie verlassen (line {hist} (sum: {line}) > calibration_line_threshold {self.calibration_line_threshold})")
            return False
        else:
            self.__zeit_linie_verloren = None
            return True

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
    def ir_sensor_gewichte(self) -> np.ndarray:
        """Liefert die Gewichte für die Ermittlung des Lenkwinkel-Fehlers

        Returns:
            np.ndarray: Array mit fünf Gewichts-Werten
        
        """
        return np.array(self.get_config()["ir_sensor_weights"])

    @property
    def v_max(self, update_config=False) -> int:
        return int(self.get_config()["v_max"])

    @property
    def v_min(self) -> int:
        return int(self.get_config()["v_min"])

    @property
    def calibration_line_threshold(self) -> float:
        return float(self.get_config()["calibration_line_threshold"])



# Müsste untenstehender Code noch in die Class Sensor Car integriert werden, damit die IR-gestützte Funktion auto_fahren() als Methode der Klasse SensorCar aufgerufen werden kann? 
# DKE: ginge, siehe: https://www.w3tutorials.net/blog/run-class-methods-in-threads-python/#2-why-run-class-methods-in-threads
# Gedanke: Funktion auto_fahren in SensorCar integrieren
stop_event = threading.Event()

def auto_fahren(sc : SensorCar):

    print("Auto fährt...")
    
    while not stop_event.is_set():
        line = sc.normierte_sensorwerte()
        print(f"line: {line}, sum(line): {sum(line)}")
        if (sc.is_on_line()):
           # Reload config.json from disk
           sc._update_config()
           lw = sc.lenkwinkel_berechnen()
           v = sc.geschwindigkeit_berechnen(lw)
           sc.drive(v, lw)
           print(f"Driving (v: {v}, lw: {lw})")
        else:
            sc.stop()
            while not sc.is_on_line() and not stop_event.is_set():
                # Reload config.json from disk
                sc._update_config()
                time.sleep(0.5)

    #    print(f"Lenkwinkel: {lw}, Geschwindigkeit: {v}")
    
    sc.stop()

#losgelöst von SensorCar.py definieren, bspw. in run.py?
if __name__ == '__main__':

        sc = SensorCar(run_calibration=True)

        thread = threading.Thread(target=auto_fahren, args=[sc])
        thread.start()
        input("Auto fährt, zum Beenden <ENTER> drücken")
        
        stop_event.set()

        thread.join()

        print("Programm vollständig beendet")
