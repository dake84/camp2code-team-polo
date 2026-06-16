import time

from evdev import InputDevice, list_devices, ecodes
import select

from basisklassen import *
 
print(list_devices())
controller = None

PREFER = "Wireless Controller"

for path in list_devices():
	dev = InputDevice(path)
	dev.grab()
	name = (dev.name or "").lower()
	if name == "wireless controller":
		controller = dev
		break
		
if controller is None:
	raise RuntimeError("No controller found")

print(f"Using device: {controller.path} - {controller.name}")

steer = 0
speed = 0

steer_min = 0.1
speed_min = 15

bw = BackWheels()
fw = FrontWheels()

while True:
	
	event = dev.read_one()
	
	select_event, _, _ = select.select([dev.fd], [], [], 0.005)
	
	if select_event:
		for event in dev.read():
			if event.type == ecodes.EV_ABS:
				
				if event.code == ecodes.ABS_RY:
					speed = (event.value - 127) / 128
					#print(f"Button: {event.code} - {speed}")
				if event.code == ecodes.ABS_X:
					steer = (event.value - 127) / 128
					#print(f"Button: {event.code} - {steer}")	

	if (steer < steer_min) and (steer > -steer_min):
		fw.turn(90)
	else:
		steer_angle = int(90 + 45 * steer)
		fw.turn(steer_angle)

	#print(f"\rSteer = {steer_angle}", end="", flush=True)

	speed_0_to_100 = int(abs(speed) * 100)
	#print(f"\rSpeed = {speed}", end="", flush=True)

	# Rückwärts
	if (speed_0_to_100 >= speed_min) and (speed >= 0):
		bw.backward()
		bw.speed = speed_0_to_100
	# Vorwärts
	if (speed_0_to_100 >= speed_min) and (speed < 0) and (speed < 0):
		bw.forward()
		bw.speed = speed_0_to_100
	if (speed_0_to_100 <= speed_min) and (speed >= 0):
		bw.backward()
		bw.speed = 0
	if (speed_0_to_100 <= speed_min) and (speed < 0):
		bw.forward()
		bw.speed = 0

