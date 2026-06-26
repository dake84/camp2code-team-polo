import curses
import threading
import time
import logging
import sys
from typing import Optional

# Core-Projekt-Importe
import Driving
import InfraredSensor
import UltrasonicSensor
import logging_setup
from Driving import DrivingMode, SensorCarMode, StopReason
from SensorCar import SensorCar
from ConfigReader import ConfigReader

class KeyboardMode(SensorCarMode):
    SPEED_FORWARD = 50
    SPEED_BACKWARD = -50
    ANGLE_STRAIGHT = 90
    ANGLE_LEFT = 45
    ANGLE_RIGHT = 135

    def __init__(self, car):
        super().__init__("AdvancedKeyboardMode")
        self._car = car
        self._stop_event = threading.Event()
        self.__log = logging.getLogger(self.__class__.__name__)
        
        self._autonomous_instance = None
        
        # Interne Variablen für die Stillstands-PID-Prognose
        self._last_error = 0.0
        self._integral = 0.0
        self._last_time = time.time()

    def _run(self) -> bool:
        return False
    
    def _run_condition(self) -> bool:
        return True

    def start(self):
        self._stop_event.clear()
        curses.wrapper(self._main_loop)

    def stop(self, reason:StopReason|int=0) -> bool:
        self._stoppe_autonomen_modus()
        return super().stop(reason)

    def _stoppe_autonomen_modus(self):
        if self._autonomous_instance is not None:
            self._autonomous_instance.stop()
            self._autonomous_instance = None
            self._car.drive(0, self.ANGLE_STRAIGHT)

    def _berechne_prognose_lw(self, ir_display) -> tuple[float, float, float, float, int]:
        """
        Berechnet den virtuellen PID-Stellwert u und den daraus resultierenden
        Lenkwinkel, ohne den echten Servo des Autos anzusteuern.
        """
        # 1. Config laden (Exakt wie im sensorcar_controller / FollowLine)
        self._car._cfg._load_config_file()
        if self._autonomous_instance is not None:
            self._autonomous_instance._cfg._load_config_file() 
        
        sc_cfg = ConfigReader("sensorcar_controller")
        kp = sc_cfg.get_float("korrektur_proportional", 25.0)
        ki = sc_cfg.get_float("korrektur_integral", 0.2)
        kd = sc_cfg.get_float("korrektur_differential", 10.0)
        i_bound = sc_cfg.get_float("korrektur_integral_boundary", 30.0)

        # 2. Gewichtete Abweichung (Error) berechnen (Beispiel für 5 Sensoren)
        # Wenn deine FollowLine-Klasse eine andere Formel nutzt, hier anpassen!
        # Typischer Ansatz: S1=-2, S2=-1, S3=0, S4=1, S5=2 gewichtet mit Helligkeit
        if len(ir_display) == 5:
            # Gewichtung: Links negativ, Rechts positiv
            weights = sc_cfg.get_list("ir_sensor_weights", [2.0, 1.0, 0.0, -1.0, -2.0])

            # Wir invertieren den Wert (1.0 = Weiß = Keine Linie, 0.0 = Schwarz = Linie)
            # Damit zieht die Linie den Fehler an
            inverted_sensors = [1.0 - v for v in ir_display]
            total_brightness = sum(inverted_sensors)
            
            if total_brightness > 0.1:
                error = sum(s * w for s, w in zip(inverted_sensors, weights)) / total_brightness
            else:
                error = 0.0
        else:
            error = 0.0

        # 3. Zeit-Differenz (dt) ermitteln
        now = time.time()
        dt = now - self._last_time
        if dt <= 0: dt = 0.01
        self._last_time = now

        # 4. PID-Glieder berechnen
        p_term = kp * error
        
        self._integral += error * dt
        # Begrenzung des I-Glieds (Anti-Windup)
        self._integral = max(-i_bound, min(i_bound, self._integral))
        i_term = ki * self._integral
        
        derivative = (error - self._last_error) / dt
        d_term = kd * derivative
        self._last_error = error

        # Stellgröße u berechnen
        u = p_term + i_term + d_term

        # 5. Prognostizierten Lenkwinkel berechnen (Basis 90° geradeaus)
        # u positiv -> schlägt nach rechts aus, u negativ -> nach links
        vorausgesagter_lw = int(90 + u)
        vorausgesagter_lw = max(self.ANGLE_LEFT, min(self.ANGLE_RIGHT, vorausgesagter_lw))

        return p_term, i_term, d_term, u, vorausgesagter_lw

    def _draw_stats(self, stdscr):
        """Zeigt die Echtzeitdaten und die PID-Stillstands-Prognose an."""
        speed = getattr(self._car, "speed", 0)
        angle = getattr(self._car, "steering_angle", 90)
        distance = getattr(self._car, "distance", 300)
        
        try:
            is_on_line = "JA 🛣️" if self._car.is_on_line() else "NEIN"
        except Exception:
            is_on_line = "UNKNOWN"

        ir_values = getattr(self._car, "ir_sensor_values", [[1.,1.,1.,1.,1.]])
        if isinstance(ir_values, list) and len(ir_values) > 0 and isinstance(ir_values[0], list):
            ir_display = ir_values[-1]
        else:
            ir_display = ir_values

        s_min = min(ir_display) if ir_display else 0.0
        s_max = max(ir_display) if ir_display else 0.0
        s_delta = s_max - s_min
        ir_str = " ".join([f"S{i+1}:[{v:.2f}]" for i, v in enumerate(ir_display)])

        # UNSE RE NEUE PROGNOSE IM STILLSTAND BERECHNEN:
        p_p, p_i, p_d, p_u, p_lw = self._berechne_prognose_lw(ir_display)

        cfg_pid = ConfigReader("sensorcar_controller")
        car_pid = ConfigReader("car_controller")

        # Curses Render-Bereich ab Zeile 7
        stdscr.move(7, 0)
        stdscr.clrtoeol()
        stdscr.addstr(7, 0, "─" * 76)
        
        stdscr.move(8, 0)
        stdscr.clrtoeol()
        stdscr.addstr(8, 0, f"📊 REAL-STATS -> Tempo: {speed:3d}% | Lenkung: {angle:3d}° | Auf Linie: {is_on_line}")
        
        stdscr.move(9, 0)
        stdscr.clrtoeol()
        stdscr.addstr(9, 0, f"📏 US-Distanz: {distance:3d} cm")
        
        stdscr.move(10, 0)
        stdscr.clrtoeol()
        stdscr.addstr(10, 0, f"📷 IR-Sensoren: {ir_str}")
        
        stdscr.move(11, 0)
        stdscr.clrtoeol()
        stdscr.addstr(11, 0, f"📈 IR-Stats:    Min: [{s_min:.2f}] | Max: [{s_max:.2f}] | Delta: [{s_delta:.2f}]")
        
        # HIER WIRD DIE PROGNOSE ANGEZEIGT (Zeile 13 & 14)
        stdscr.move(13, 0)
        stdscr.clrtoeol()
        stdscr.addstr(13, 0, f"🔮 PID-PROGNOSE -> P:{p_p:5.1f} | I:{p_i:5.1f} | D:{p_d:5.1f} | u (Gesamt): {p_u:6.1f}", curses.A_BOLD)
        stdscr.move(14, 0)
        stdscr.clrtoeol()
        stdscr.addstr(14, 0, f"🔮 Virtueller Lenkwinkel (lw): {p_lw:3d}°  (Soll-Wert bei Autopilot)", curses.A_BOLD)

        stdscr.move(16, 0)
        stdscr.clrtoeol()
        stdscr.addstr(16, 0, f"⚙️  Active Config -> Target-P:{cfg_pid.get_int('korrektur_proportional', 25)} D:{cfg_pid.get_int('korrektur_differential', 10)} | Sonic-V_max: {car_pid.get_int('v_max', 60)}%")

        stdscr.move(17, 0)
        stdscr.clrtoeol()
        stdscr.addstr(17, 0, "─" * 76)
        stdscr.refresh()

    def _main_loop(self, stdscr):
        stdscr.nodelay(True)  
        stdscr.keypad(True)   
        curses.curs_set(0)    

        last_speed = 0
        last_angle = self.ANGLE_STRAIGHT
        step_time = 0.05

        while not self._stop_event.is_set():
            try:
                auto_aktiv = self._autonomous_instance is not None
                status_text = "🤖 AUTOPILOT (FOLLOW LINE) AKTIV" if auto_aktiv else "✋ MANUELLE STEUERUNG"
                status_attr = curses.A_REVERSE if auto_aktiv else curses.A_NORMAL

                stdscr.move(0, 0)
                stdscr.addstr(0, 0, f"⌨️  KOMMANDOZENTRALE (KEYBOARD MODE)  |  [{status_text}]", curses.A_UNDERLINE | curses.A_BOLD | status_attr)
                stdscr.addstr(1, 0, "Steuerung:  [Pfeiltasten] Manuell fahren  |  [LEERTASTE] Not-Stopp")
                stdscr.addstr(2, 0, "Autopilot:  [A] Autopiloten starten       |  [M] Autopiloten beenden")
                stdscr.addstr(3, 0, "Programm:   Beenden mit [ESC]")
                
                self._draw_stats(stdscr)
                key = stdscr.getch()

                if key != -1:
                    speed = last_speed
                    angle = last_angle
                    action_text = "Keine Aktion"
                    valid_key = False

                    if key == ord('a') or key == ord('A'):
                        if not auto_aktiv:
                            self._stoppe_autonomen_modus()
                            self._autonomous_instance = Driving.FollowLine(car=self._car)
                            self._autonomous_instance.start()
                            action_text = "🤖 AUTOPILOT GESTARTET"
                            stdscr.move(5, 0)
                            stdscr.clrtoeol()
                            stdscr.addstr(5, 0, f"Letzte Aktion: {action_text}")
                            stdscr.refresh()
                        continue

                    elif key == ord('m') or key == ord('M'):
                        if auto_aktiv:
                            self._stoppe_autonomen_modus()
                            action_text = "✋ AUTOPILOT GESTOPPT"
                            last_speed, last_angle = 0, self.ANGLE_STRAIGHT
                            stdscr.move(5, 0)
                            stdscr.clrtoeol()
                            stdscr.addstr(5, 0, f"Letzte Aktion: {action_text}")
                            stdscr.refresh()
                        continue

                    elif key == 27: 
                        break

                    elif key == curses.KEY_UP:
                        if auto_aktiv: self._stoppe_autonomen_modus()
                        speed = self.SPEED_FORWARD
                        angle = self.ANGLE_STRAIGHT
                        action_text = "MANUELL VORWÄRTS"
                        valid_key = True
                    elif key == curses.KEY_DOWN:
                        if auto_aktiv: self._stoppe_autonomen_modus()
                        speed = self.SPEED_BACKWARD
                        angle = self.ANGLE_STRAIGHT
                        action_text = "MANUELL RÜCKWÄRTS"
                        valid_key = True
                    elif key == curses.KEY_LEFT:
                        if auto_aktiv: self._stoppe_autonomen_modus()
                        angle = self.ANGLE_LEFT
                        action_text = "MANUELL LINKS"
                        valid_key = True
                    elif key == curses.KEY_RIGHT:
                        if auto_aktiv: self._stoppe_autonomen_modus()
                        angle = self.ANGLE_RIGHT
                        action_text = "MANUELL RECHTS"
                        valid_key = True
                    elif key == ord(' '): 
                        if auto_aktiv: self._stoppe_autonomen_modus()
                        speed = 0
                        angle = self.ANGLE_STRAIGHT
                        action_text = "🛑 NOT-STOPP"
                        valid_key = True

                    if valid_key and (speed != last_speed or angle != last_angle):
                        self._car.drive(speed, angle)
                        
                        stdscr.move(5, 0)
                        stdscr.clrtoeol()
                        stdscr.addstr(5, 0, f"Letzte Aktion: {action_text} -> drive({speed}, {angle})")
                        stdscr.refresh()
                        
                        last_speed = speed
                        last_angle = angle

                time.sleep(step_time)

            except KeyboardInterrupt:
                break

        self._stoppe_autonomen_modus()
        self._car.drive(0, self.ANGLE_STRAIGHT)


if __name__ == '__main__':
    logging_setup.setup_project_logging(logging.CRITICAL)

    print("\n🏎️  Initialisiere SensorCar Hardware und Sensor-Threads...")
    sc = SensorCar()
    sc.stop()

    us = UltrasonicSensor.UltrasonicSensor(sc)
    ir = InfraredSensor.InfraredSensor(sc)
    
    stop_event = threading.Event()
    us_sensor_thread = threading.Thread(target=us.read_loop, args=[stop_event], daemon=True)
    ir_sensor_thread = threading.Thread(target=ir.read_loop, args=[stop_event], daemon=True)
    
    ir_sensor_thread.start()
    us_sensor_thread.start()
    
    time.sleep(0.5) 

    key_mode = KeyboardMode(car=sc)
    try:
        key_mode.start()
    finally:
        print("\n🛑 Räume Hardware-Ressourcen auf... Bitte warten.")
        stop_event.set()
        sc.stop()
        us_sensor_thread.join()
        ir_sensor_thread.join()
        print("✅ Hardware sicher gestoppt. Bis zur nächsten Fahrt!")