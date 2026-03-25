from DNX64 import DNX64
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.add_dll_directory(HERE)

dll_path = os.path.join(HERE, "DNX64.dll")

dnx = DNX64(dll_path)

print("Init:", dnx.Init())

count = dnx.GetVideoDeviceCount()
print("Devices:", count)

# for i in range(count):
#     try:
#         print(f"Device {i}:", dnx.GetVideoDeviceName(i))
#     except Exception as e:
#         print(f"Error reading device name for {i}:", e)
        
device_index = 0
dnx.SetVideoDeviceIndex(device_index)

print("Turning LED ON")
dnx.SetLEDState(device_index, 1)
time.sleep(2)

print("Turning LED OFF")
dnx.SetLEDState(device_index, 0)
time.sleep(2)


print("Setting LED brightness levels")

for level in range(6, -1, -1):  # 6 -> 0
    print("LED Level:", level)
    dnx.SetFLCLevel(device_index, level)
    time.sleep(1)
