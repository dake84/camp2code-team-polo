from basisklassen import BackWheels, FrontWheels
import time


fw = FrontWheels(turning_offset=25)
bw = BackWheels(forward_A=0, forward_B=0 )

# Lenkung auf geradeaus
fw.turn(90)
time.sleep(1)


print("Fahre los... Drücke ENTER zum Richtungswechsel.")
bw.speed = 0

bw.backward()


input()  # wartet auf ENTER


print("Fahre los... Drücke ENTER zum Stoppen.")
bw.forward()

input()  # wartet auf ENTER

bw.stop()
print("Gestoppt.")

