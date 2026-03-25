## Testing image tracking

import cv2
import numpy as np
import os
import threading # basically so i can do stuff in terminal without freezing the video
from datetime import datetime

INDEX = 1 # camera index
output_dir = r"C:\Users\macke\Desktop\camera_mp4_test" # save location
#os.makedirs(output_dir, exist_ok=True) # would make the folder if it didn't exist

cap = cv2.VideoCapture(INDEX, cv2.CAP_DSHOW) #opens the camera
if not cap.isOpened():
    raise RuntimeError("Couldn't open camera.")

ret, frame0 = cap.read() #reads the first frame (ret is true/false)
if not ret or frame0 is None: #ret receives the frame
    cap.release() #unlocks the camera
    raise RuntimeError("Couldn't read initial frame.")

h, w = frame0.shape[:2] #determines camera dimensions
fps = cap.get(cv2.CAP_PROP_FPS) # get the frame shape and camera rate
if fps is None or fps <= 1 or fps > 240:
    fps = 30.0

print("Camera preview running.")
print("Press ENTER in this terminal to start recording.")
print("Press Q in the video window to stop.")

recording = False
writer = None #writes the video

# Thread to wait for ENTER without blocking video
def wait_for_enter():
    global recording, writer
    input()  # wait for ENTER

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(output_dir, f"dino_detection_{timestamp}.mp4")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (w*2, h))

    if not writer.isOpened():
        print("Failed to start recording.")
        return

    recording = True
    print(f"Recording started → {out_path}")

threading.Thread(target=wait_for_enter, daemon=True).start() #starts the thread

while True: #infinite loop until breaks with q command
    ret, frame = cap.read()
    if not ret or frame is None:
        break #would exit the loop

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) # grayscale for thresholding
    gray = cv2.GaussianBlur(gray, (7, 7), 0) # blurs the image to reduce noise

    _, bw = cv2.threshold(gray, 0, 255,
                          cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU) #binary mask

    kernel = np.ones((5, 5), np.uint8) #cleans the mask, element for morphology
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel, iterations=1)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
# finds outlines against background
    if contours:
        c = max(contours, key=cv2.contourArea) #contour with max object
        area = cv2.contourArea(c) #area to pixels

        if area > 2000: #ignores tiny blobs
            cv2.drawContours(frame, [c], -1, (0, 255, 0), 2)
# moments (below) basically looks at weighted averages of pixel intensities
            M = cv2.moments(c) #draws the contour on the live frame 
            if M["m00"] != 0: # m00 is area
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"]) #centroid formula
                cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1) # red dot
                # cv2.putText(frame,
                #             f"Area={int(area)} Center=({cx},{cy})",
                #             (10, 30),
                #             cv2.FONT_HERSHEY_SIMPLEX,
                #             0.8,
                #             (0, 255, 255),
                #             2)

    # Show recording indicator
    # if recording:
    #     cv2.putText(frame, "REC ●",
    #                 (w - 120, 40),
    #                 cv2.FONT_HERSHEY_SIMPLEX,
    #                 1,
    #                 (0, 0, 255),
    #                 3)

    bw_bgr = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR) #combines both feeds
    combined = np.hstack([frame, bw_bgr]) #stacks feeds

    if recording and writer is not None:
        writer.write(combined)

    cv2.imshow("Detection (Q to quit)", frame) #show windows
    cv2.imshow("Mask", bw)

    if cv2.waitKey(1) & 0xFF == ord('q'): #closes window
        break

cap.release() #releases the camera

if writer is not None: #closes the video file
    writer.release()

cv2.destroyAllWindows() #closes and prints message
print("Finished.")
