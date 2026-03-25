## Testing controller connection

import pygame
pygame.init()
pygame.joystick.init()
print("Joysticks:", pygame.joystick.get_count())
for i in range(pygame.joystick.get_count()):
    js = pygame.joystick.Joystick(i)
    js.init()
    print(f"{i}: {js.get_name()}")



#%% Testing microcontroller connection
import serial
import time

# replace baudrate with the one your firmware uses (common: 115200 or 9600)
ser = serial.Serial("COM4", baudrate=115200, timeout=1)

time.sleep(2)  # wait for microcontroller reset if it's Arduino-like
ser.write(b"M115\n")  # or any known command your firmware accepts
print("Wrote a test command")

# try to read back response
resp = ser.readline().decode(errors="ignore").strip()
print("Response:", resp)

ser.close()

#region Figuring out drivers
import serial
ser = serial.Serial("COM4", 115200, timeout=1)
ser.write(b"M115\n")
print(ser.readline().decode(errors="ignore"))
#endregion


