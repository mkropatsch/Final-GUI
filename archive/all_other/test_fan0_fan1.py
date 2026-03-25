import serial
import time

PORT = "COM3"   # ⚠️ Change this to match your board
BAUD = 115200

def send(cmd, ser):
    ser.write((cmd + "\n").encode("utf-8"))
    print(f"→ {cmd}")
    time.sleep(0.3)
    while ser.in_waiting:
        print(ser.readline().decode(errors="ignore").strip())

def main():
    print("Connecting to board...")
    with serial.Serial(PORT, BAUD, timeout=2) as ser:
        time.sleep(2)  # wait for reset
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # optional: check firmware info
        send("M115", ser)

        print("\n=== Testing fan indices P0–P3 ===")
        for p in range(4):
            print(f"\n--- Testing FAN P{p} ---")
            send(f"M106 P{p} S255", ser)   # turn fan fully ON
            time.sleep(3)
            send(f"M106 P{p} S0", ser)     # turn fan OFF
            time.sleep(1)
        print("\n=== Test complete ===")

if __name__ == "__main__":
    main()
