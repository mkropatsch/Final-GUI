### PIN 1, 22, PIN 2 23, PIN 7 24

import serial
import time

# ------------------- CONFIG -------------------
PORT = "COM4"          # <— change this to your Arduino port (e.g. COM3, /dev/ttyACM0)
BAUD = 115200
# ----------------------------------------------

def main():
    print(f"Connecting to {PORT} at {BAUD} baud...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as e:
        print(f"❌ Could not open port {PORT}: {e}")
        return

    time.sleep(2)  # allow Arduino to reset
    ser.reset_input_buffer()
    print("✅ Connected! Type 'help' for commands. Ctrl+C or 'exit' to quit.\n")

    while True:
        try:
            cmd = input("pump> ").strip()
            if not cmd:
                continue
            if cmd.lower() in ["exit", "quit", "q"]:
                print("Closing serial port.")
                break
            if cmd.lower() == "help":
                print("""
Available commands:
  fwd            → run forward
  rev            → run reverse
  stop           → brake to GND
  brakeh         → brake to V+
  coast          → disable outputs (freewheel)
  run fwd 2000   → run forward for 2000 ms
  run rev 1500   → run reverse for 1.5 s
  speed 180      → set PWM speed (requires PWM-capable EN pin)
  help           → show this help
  exit / quit    → leave program
""")
                continue

            # Send command + newline to Arduino
            ser.write((cmd + "\n").encode("ascii"))
            ser.flush()

            # Read any reply
            time.sleep(0.05)
            while ser.in_waiting:
                reply = ser.readline().decode(errors="ignore").strip()
                if reply:
                    print("←", reply)
            # Small pause to avoid spam
            time.sleep(0.05)

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}")
            break

    ser.close()
    print("Serial port closed.")

if __name__ == "__main__":
    main()
