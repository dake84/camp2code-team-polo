import time
import select
from evdev import InputDevice, list_devices, ecodes
from basisklassen import *

print("Verfügbare Geräte:", list_devices())
controller = None

PREFER = "stadia"

for path in list_devices():
    dev = InputDevice(path)
    name = (dev.name or "").lower()
    if PREFER in name:
        controller = dev
        controller.grab()
        break
        
if controller is None:
    raise RuntimeError("Kein Stadia Controller gefunden! Ist er eingeschaltet und verbunden?")

print(f"Benutze Gerät: {controller.path} - {controller.name}")

# Feste Werte für den Stadia Controller über Bluetooth
center_value = 127

steer = 0
speed = 0

steer_min = 0.1
speed_min = 15

# Initialisierung deiner Basisklassen
bw = BackWheels()
fw = FrontWheels()

print("Fahrprogramm gestartet. Drücke STRG+C zum Beenden.")

try:
    while True:
        # Abfrage des Controllers mit einem kleinen Timeout (0.005s)
        select_event, _, _ = select.select([controller.fd], [], [], 0.005)
        
        if select_event:
            for event in controller.read():
                if event.type == ecodes.EV_ABS:
                    
                    # RECHTER STICK: Hoch/Runter (Achse 2) -> BACK WHEELS (Antrieb)
                    if event.code == 2:
                        speed = (event.value - center_value) / 128

                    # LINKER STICK: Links/Rechts (Achse 0) -> FRONT WHEELS (Lenkung)
                    if event.code == 0:
                        steer = (event.value - center_value) / 128

        # =========================================================
        # INTERNE LOGIK 1: FRONT WHEELS (Lenkung via Linker Stick)
        # =========================================================
        if (steer < steer_min) and (steer > -steer_min):
            fw.turn(90)  # Geradeaus, wenn Stick in der Mitte ist
        else:
            steer_angle = int(90 + 45 * steer)
            # Sicherheitsbegrenzung, damit die Servos nicht überdrehen
            steer_angle = max(45, min(135, steer_angle)) 
            fw.turn(steer_angle)

        # =========================================================
        # INTERNE LOGIK 2: BACK WHEELS (Antrieb via Rechter Stick)
        # =========================================================
        speed_0_to_100 = int(abs(speed) * 100)

        # Stick nach unten -> Rückwärts fahren
        if (speed_0_to_100 >= speed_min) and (speed >= 0):
            bw.backward()
            bw.speed = speed_0_to_100
            
        # Stick nach oben -> Vorwärts fahren (da Werte negativ werden beim Drücken nach oben)
        if (speed_0_to_100 >= speed_min) and (speed < 0):
            bw.forward()
            bw.speed = speed_0_to_100
            
        # Stopp-Bedingungen (Deadzone), wenn der Stick nahe der Mitte ist
        if (speed_0_to_100 <= speed_min):
            bw.speed = 0

except KeyboardInterrupt:
    # Sicherheits-Netz: Wenn du das Skript abbrichst, stoppt das Auto sofort
    print("\nProgramm beendet. Stoppe Motoren...")
    bw.speed = 0
    fw.turn(90)