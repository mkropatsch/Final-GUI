import serial
import time

# Change this to your COM port (Windows example: "COM4", macOS/Linux example: "/dev/ttyUSB0")
PORT = "COM3"
BAUD = 115200

def send(cmd, ser):
    ser.write((cmd + "\n").encode("utf-8"))
    print(f"→ {cmd}")
    time.sleep(0.5)
    while ser.in_waiting:
        print(ser.readline().decode(errors="ignore").strip())

def main():
    print("Connecting to Marlin...")
    with serial.Serial(PORT, BAUD, timeout=2) as ser:
        time.sleep(2)  # give time for reset

        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # optional: wake up
        send("M115", ser)  # firmware info

        # Test Fan0
        print("\nTesting FAN0 (motor)...")
        send("M106 PC7 S128", ser)  # full power ON
        time.sleep(10)
        send("M106 PC7 S128", ser)  # half speed
        time.sleep(2)
        send("M107 PC7", ser)       # OFF
        print("Done.")

if __name__ == "__main__":
    main()
