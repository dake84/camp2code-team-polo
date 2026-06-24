import keyboard
import time
from BaseCar import BaseCar

print("Keyboard Steuerung aktiv! Drücke ESC zum Beenden.")

while True:
    try:
        if keyboard.is_pressed('w') or keyboard.is_pressed('up'):
            print("FAHRE VORWÄRTS (W / ↑)")
        elif keyboard.is_pressed('s') or keyboard.is_pressed('down'):
            print("FAHRE RÜCKWÄRTS (S / ↓)")
        elif keyboard.is_pressed('a') or keyboard.is_pressed('left'):
            print("LENKE LINKS (A / ←)")
        elif keyboard.is_pressed('d') or keyboard.is_pressed('right'):
            print("LENKE RECHTS (D / →)")
        if keyboard.is_pressed('esc'):
            print("Steuerung wird beendet...")
            break

        time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nKeyboard Steuerung beendet.")
        break