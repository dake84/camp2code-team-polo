"""Interaktiver Tastaturmodus (curses)."""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

import ConfigReader

from .base import DrivingMode

if TYPE_CHECKING:
    from BaseCar import BaseCar

class KeyboardMode(DrivingMode):

    def __init__(self, 
                 car: Optional[BaseCar] = None, 
                 name: str = "Interaktiver Tastaturmodus", 
                 logger: Optional[logging.Logger] = None, 
                 cfg: Optional[ConfigReader.ConfigReader] = None, 
                 update_cfg: bool = False, 
                 frequency: int = 25) -> None:
        
        if car is None:
            # Lazy-Import: Hardware nur bei Bedarf initialisieren.
            from SonicCar import SonicCar
            car = SonicCar()
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
        
        import curses  # Lazy-Import: nur im Terminalbetrieb benoetigt
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
        import curses  # Lazy-Import: nur im Terminalbetrieb benoetigt
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

