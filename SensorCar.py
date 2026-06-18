from basisklassen import Infrared
from BaseCar import BaseCar
import numpy as np
import time
import json
import threading

class SensorCar(BaseCar):
    """Erstellung Klasse Sensor Car; Grundfunktionalitäten werden aus BaseCar geerbt und um Infrarotsensorik ergänzt, um Linien zu erkennen und entsprechend zu steuern.

    """

    def __init__(self, references: list = list()):
        """Initialisiert den Infrarotsensor und lädt oder kalibriert Referenzwerte.

        Wenn keine Referenzen übergeben werden, führt das Objekt automatisch eine
        Kalibrierung durch und speichert die ermittelten Referenzwerte intern.

        Args:
            references (list | None, optional): Liste der Referenzmesswerte.
                Wenn None, werden die Referenzen automatisch kalibriert.
        """

        self._ir = Infrared()

        if (len(references) == 0):
            self._ir.cali_references()
            input("Referenzwerte ermittelt. <Enter> zum fahren.")
        else:
            self._ir.set_references(references)
        self._previous_error = 0
        super().__init__()

    def lenkwinkel_berechnen(self) -> float:
        """Berechnet den Lenkwinkel basierend auf IR-Messwerten und PD-Korrekturfaktoren.

        Die Funktion nutzt gewichtete Infrarotmesswerte, um einen Fehlerwert zu bestimmen
        und daraus mittels proportionaler und differenzieller Steuerung den Lenkwinkel
        zu berechnen. Der resultierende Winkel wird auf den Bereich von 45 bis 135 Grad
        begrenzt.

        Returns:
            float: Der berechnete Lenkwinkel im Bereich von 45 bis 135 Grad.
        """

        messwerte = self._ir.get_average(10)
        
        # Fix: Possible Div/0
        error = sum((messwerte*self.ir_sensor_gewichte))/sum(messwerte)
        #print(f"Messwerte: {messwerte}\nGewichte {gewichte}\nMultiplikator {messwerte*gewichte}\nError {error}")

        u = (self.korrektur_proportional * error) + (self.korrektur_differential * (error - self._previous_error))
        self._previous_error = error
        self._lw = max(45, min(135, u))
        return self._lw

    def geschwindigkeit_berechnen(self, lenkwinkel : float):
        """Berechnet Geschwindigkeit in Abhängigkeit von Lenkwinkel

        Args:
            lenkwinkel (float): Lenkwinkel

        Returns:
            int: Die berechnete Geschwindigkeit.
        """
        v = self.v_max - (self.bremsfaktor * abs((lenkwinkel - 90)))
        # 115 - 25
        # v_max (70) - v_min (30)
        return int(v)

    def messwerte(self) -> list:
        """Ausgabe aktuelle, durchschnittliche IR-Messwerte durch Aufruf der Methode get_average.

        Returns:
            list: Aktuelle, durchschnittliche IR-Messwerte. Als array mit 5 Float-Werten je Zeile.
        """
        return self._ir.get_average(100)
    
    def is_on_line(self) -> bool:
        """Prüft, ob das Auto sich mittig auf der Linie befindet, basierend auf den IR-Messwerten.

        Returns:
            bool: True/False, basierend auf den durchschnittlichen IR-Messwerten des mittleren Sensors (auf Position 2 von 0-4). Messwert < 1 = dunkle Linie erkannt
        """
        line = np.array(self._ir.get_average(10))
        print(line)
    
        for i in line:
            if (i<self.calibration_line_threshold):
                return True
        
        return False

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
    def v_max(self) -> int:
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
        if (sc.is_on_line()):
           # Reload config.json from disk
           sc.get_config(force_update=True)
           lw = sc.lenkwinkel_berechnen()
           v = sc.geschwindigkeit_berechnen(lw)
           sc.drive(v, lw)
        else:
            print("Auto hat Linie verlassen! Bitte zurückstellen")
            sc.stop()
            while not sc.is_on_line() and not stop_event.is_set():
                # Reload config.json from disk
                sc.get_config(force_update=True)
                time.sleep(0.5)

    #    print(f"Lenkwinkel: {lw}, Geschwindigkeit: {v}")
    
    sc.stop()

#losgelöst von SensorCar.py definieren, bspw. in run.py?
if __name__ == '__main__':

        sc = SensorCar()
        thread = threading.Thread(target=auto_fahren, args=[sc])
        thread.start()

        input("Auto fährt, zum Beenden <ENTER> drücken")
        
        stop_event.set()

        thread.join()

        print("Programm vollständig beendet")
