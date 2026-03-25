## Trying direct Dino-Lite python connection

import time
from DNX64 import DNX64  # this should be the wrapper file/class you pasted

# 1) Update this path to where DNX64.dll actually is on your computer
DLL_PATH = r"C:\PATH\TO\DNX64.dll"

def main():
    print("Make sure DinoCapture is CLOSED before running.\n")

    dnx = DNX64(DLL_PATH)

    ok = dnx.Init()
    print(f"Init() -> {ok}")
    if not ok:
        print("DNX64 Init failed. Check DLL path, camera connection, and that DinoCapture is closed.")
        return

    # Enumerate devices DNX64 sees
    count = dnx.GetVideoDeviceCount()
    print(f"\nDNX64 sees {count} video device(s):")
    for i in range(count):
        name = dnx.GetVideoDeviceName(i)
        print(f"  {i}: {name}")

    # Pick the device index that corresponds to your Dino-Lite
    # Change this after you see the printed list
    DINO_DEVICE_INDEX = None
    for i in range(count):
        name = dnx.GetVideoDeviceName(i)
        if "Dino" in name or "DINO" in name:
            DINO_DEVICE_INDEX = i
            break

    if DINO_DEVICE_INDEX is None:
        print("\nCouldn't auto-find a 'Dino' device name. Set DINO_DEVICE_INDEX manually in the script.")
        return

    print(f"\nUsing device index {DINO_DEVICE_INDEX}: {dnx.GetVideoDeviceName(DINO_DEVICE_INDEX)}")
    dnx.SetVideoDeviceIndex(DINO_DEVICE_INDEX)

    # Live AMR readout test
    print("\nAMR test: rotate the zoom ring and watch this value change.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            amr = dnx.GetAMR(DINO_DEVICE_INDEX)  # if your device supports AMR, this should change
            print(f"AMR: {amr:.2f}x", end="\r")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopped AMR test.")

    # Optional: quick FOV check (may require meaningful mag input)
    # Try one value just to see if it returns something non-zero.
    try:
        test_mag = 50.0
        fov_um = dnx.FOVx(DINO_DEVICE_INDEX, test_mag)
        print(f"\nFOVx at {test_mag}x: {fov_um:.2f} um")
    except Exception as e:
        print(f"\nFOVx test failed (not necessarily a problem): {e}")

    # Optional: LED test (comment out if you don't want to mess with illumination)
    # Some models require preview/stream active for LED control.
    try:
        print("\nTrying LED toggle (may or may not work on your model)...")
        dnx.SetLEDState(DINO_DEVICE_INDEX, 0)  # often 0=off
        time.sleep(0.5)
        dnx.SetLEDState(DINO_DEVICE_INDEX, 1)  # often 1=on (model-dependent)
        print("LED toggle commands sent.")
    except Exception as e:
        print(f"LED test failed (common if preview isn't established): {e}")

if __name__ == "__main__":
    main()
