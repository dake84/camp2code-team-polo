import select
from evdev import InputDevice, ecodes

from basisklassen import *

# ========================================
# Controller direkt auswählen
# ========================================

# mit sudo evtest schauen, was der richtige Controller ist xD
controller = InputDevice('/dev/input/event4')

print(f'Using device: {controller.path} - {controller.name}')

controller.grab()

# ========================================
# Variablen
# ========================================

steer = 0.0
speed = 0.0
steer_angle = 90

steer_min = 0.1
speed_min = 15

bw = BackWheels()
fw = FrontWheels()

# ========================================
# Hauptschleife
# ========================================

try:

    while True:

        ready, _, _ = select.select([controller.fd], [], [], 0.005)

        if ready:

            try:

                events = list(controller.read())
                print(f'Events empfangen: {len(events)}')

                for event in events:

                    print(
                        f'type={event.type}, '
                        f'code={event.code}, '
                        f'value={event.value}'
                    )

                    if event.type != ecodes.EV_ABS:
                        continue

                    if event.code == ecodes.ABS_RY:
                        speed = (event.value - 127) / 128
                        print(f'Speed = {speed:.3f}')

                    elif event.code == ecodes.ABS_X:
                        steer = (event.value - 127) / 128
                        print(f'Steer = {steer:.3f}')

            except Exception as e:
                print(f'Fehler beim Lesen der Events: {e}')

        # ========================================
        # Lenkung
        # ========================================

        if -steer_min < steer < steer_min:
            steer_angle = 90
        else:
            steer_angle = int(90 + 45 * steer)

        fw.turn(steer_angle)

        # ========================================
        # Geschwindigkeit
        # ========================================

        speed_0_to_100 = int(abs(speed) * 100)

        if speed_0_to_100 >= speed_min and speed >= 0:
            bw.backward()
            bw.speed = speed_0_to_100

        elif speed_0_to_100 >= speed_min and speed < 0:
            bw.forward()
            bw.speed = speed_0_to_100

        else:
            bw.speed = 0

except KeyboardInterrupt:
    print('\nProgramm beendet')

except Exception as e:
    print(f'Unbehandelter Fehler: {e}')

finally:
    controller.ungrab()