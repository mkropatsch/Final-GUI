#### Camera

import cv2
import os
from datetime import datetime

CAMERA_INDEX = 1
SAVE_DIR = "well_plate_snapshots"

os.makedirs(SAVE_DIR, exist_ok=True)


def main():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)

    if not cap.isOpened():
        print(f"Could not open camera {CAMERA_INDEX}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    print("Controls:")
    print("  s = save snapshot")
    print("  q = quit")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame.")
            break

        display = frame.copy()
        cv2.putText(
            display,
            "Press 's' to save snapshot, 'q' to quit",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow("Camera Snapshot Tool", display)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("s"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(SAVE_DIR, f"wellplate_{timestamp}.png")
            cv2.imwrite(filename, frame)
            print(f"Saved: {filename}")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()