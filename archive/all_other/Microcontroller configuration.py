import serial
import time

# Adjust COM port if needed
PORT = "COM3"
BAUD = 115200

with serial.Serial(PORT, BAUD, timeout=2) as ser:
    print(f"Opened {PORT} at {BAUD} baud")

    # Clear out startup messages
    time.sleep(2)  # give board time to reset
    ser.reset_input_buffer()

    # Put in relative mode
    ser.write(b"G91\n")
    ser.flush()
    time.sleep(0.2)

    # Command an X move
    print("Sending G1 X10 F3000...")
    ser.write(b"G1 X10 F3000\n")
    ser.flush()

    # Read back some lines (status/echo)
    time.sleep(0.5)
    while ser.in_waiting:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            print("Board:", line)

print("Done. Watch the motors: did both move?")
