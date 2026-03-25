## Test firmware change


import serial
import time

PORT = "COM3"       # <-- change to your Marlin board COM port
BAUD = 250000       # Marlin default for many BTT boards

def send(cmd: str):
    ser.write((cmd.strip() + "\n").encode())
    time.sleep(0.2)
    lines = []
    while ser.in_waiting:
        lines.append(ser.readline().decode(errors="replace").strip())
    return lines

with serial.Serial(PORT, BAUD, timeout=1) as ser:
    # Give Marlin a moment after opening the port
    time.sleep(2)

    # (Optional) flush startup text
    ser.reset_input_buffer()

    for cmd in ["M115", "M265"]:
        print(f">>> {cmd}")
        out = send(cmd)
        for line in out:
            print(line)
        print()
