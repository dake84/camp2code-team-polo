import curses
import time
from BaseCar import BaseCar
from basisklassen import FrontWheels, BackWheels

class DirectHardwareCar(BaseCar):
    def __init__(self):
        self._steering_angle = 90
        self._speed = 0
        self._mode = self.FORWARD_MODE
        
        self._fw = FrontWheels(turning_offset=0)
        self._bw = BackWheels(forward_A=0, forward_B=0)
        
        import logging
        self._log = logging.getLogger(self.__class__.__name__)
        from threading import RLock
        self._lock = RLock()

def main(stdscr):
    car = DirectHardwareCar()
    
    SPEED_FORWARD = 40
    SPEED_BACKWARD = -40
    ANGLE_STRAIGHT = 90
    ANGLE_LEFT = 60
    ANGLE_RIGHT = 120

    curses.cbreak()          
    stdscr.keypad(True)      
    stdscr.nodelay(True)     
    
    stdscr.clear()
    stdscr.addstr(1, 0, "Q = Vorwärts-Links | W = Vorwärts  | E = Vorwärts-Rechts")
    stdscr.addstr(2, 0, "A = Rückwärts-Links | S = Rückwärts | D = Rückwärts-Rechts")
    stdscr.addstr(3, 0, "Drücke 'Space' oder 'Esc' zum Beenden.\n")
    stdscr.refresh()
    last_speed = 0
    last_angle = 90

    while True:
        try:
            key = stdscr.getch()
            if key != -1:
                speed = 0
                angle = ANGLE_STRAIGHT
                action_text = ""
                valid_key = False

                if key == ord('w') or key == curses.KEY_UP:
                    speed = SPEED_FORWARD
                    angle = ANGLE_STRAIGHT
                    action_text = "VORWÄRTS"
                    valid_key = True
                elif key == ord('q'):
                    speed = SPEED_FORWARD
                    angle = ANGLE_LEFT
                    action_text = "VORWÄRTS LINKS"
                    valid_key = True
                elif key == ord('e'):
                    speed = SPEED_FORWARD
                    angle = ANGLE_RIGHT
                    action_text = "VORWÄRTS RECHTS"
                    valid_key = True

                elif key == ord('s') or key == curses.KEY_DOWN:
                    speed = SPEED_BACKWARD
                    angle = ANGLE_STRAIGHT
                    action_text = "RÜCKWÄRTS"
                    valid_key = True
                elif key == ord('a') or key == curses.KEY_LEFT:
                    speed = SPEED_BACKWARD
                    angle = ANGLE_LEFT
                    action_text = "RÜCKWÄRTS LINKS"
                    valid_key = True
                elif key == ord('d') or key == curses.KEY_RIGHT:
                    speed = SPEED_BACKWARD
                    angle = ANGLE_RIGHT
                    action_text = "RÜCKWÄRTS RECHTS"
                    valid_key = True

                elif key == 27 or key == ord(' '):
                    car.drive(0, ANGLE_STRAIGHT)
                    break

                if valid_key and (speed != last_speed or angle != last_angle):
                    car.drive(speed, angle)
                    
                    stdscr.move(5, 0)
                    stdscr.clrtoeol()
                    stdscr.addstr(5, 0, f"Aktion: {action_text} -> car.drive({speed}, {angle})")
                    stdscr.refresh()
                    
                    last_speed = speed
                    last_angle = angle

            else:
                if last_speed != 0 or last_angle != ANGLE_STRAIGHT:
                    car.drive(0, ANGLE_STRAIGHT)
                    
                    stdscr.move(5, 0)
                    stdscr.clrtoeol()
                    stdscr.addstr(5, 0, "Aktion: STOPP (Keine Taste gedrückt)")
                    stdscr.refresh()
                    
                    last_speed = 0
                    last_angle = ANGLE_STRAIGHT

            time.sleep(0.04)

        except KeyboardInterrupt:
            car.drive(0, ANGLE_STRAIGHT)
            break

curses.wrapper(main)