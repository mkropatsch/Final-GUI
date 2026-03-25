import cv2
import time

DINO_INDEX = 1
WINDOW_NAME = "Dino-Lite Live"

cap = cv2.VideoCapture(DINO_INDEX, cv2.CAP_DSHOW)
if not cap.isOpened():
    raise RuntimeError("Could not open Dino-Lite camera")

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)  # create once

print("Press q to quit, s to save image, or close the window (X)")

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame")
            break

        cv2.imshow(WINDOW_NAME, frame)

        # Important: process window events
        key = cv2.waitKey(1) & 0xFF

        # If the window was closed, OpenCV usually reports a negative property
        try:
            prop = cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE)
        except cv2.error:
            # If the window no longer exists, OpenCV can throw here
            break

        if prop < 1:  # window closed or not visible
            print("Window closed — exiting")
            break

        if key == ord("q"):
            break
        elif key == ord("s"):
            filename = f"dinolite_snapshot_{int(time.time())}.png"
            cv2.imwrite(filename, frame)
            print(f"Saved {filename}")

finally:
    cap.release()
    cv2.destroyAllWindows()
