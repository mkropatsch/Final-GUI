## Well test

import cv2
import numpy as np

IMAGE_PATH = "well_plate_snapshots/wellplate_20260320_172923.png"


def main():
    img = cv2.imread(IMAGE_PATH)
    if img is None:
        print("Failed to load image")
        return

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # reduce noise
    blur = cv2.GaussianBlur(gray, (15, 15), 3)

    _, thresh = cv2.threshold(blur, 200, 255, cv2.THRESH_BINARY)

    # edge detection
    edges = cv2.Canny(thresh, 50, 150)

    # try circle detection
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=300,
        param1=100,
        param2=60,
        minRadius=200,
        maxRadius=500
    )

    output = img.copy()

    if circles is not None:
        circles = np.uint16(np.around(circles))

        for (x, y, r) in circles[0]:
            # draw circle
            cv2.circle(output, (x, y), r, (0, 255, 0), 3)
            # draw center
            cv2.circle(output, (x, y), 3, (0, 0, 255), -1)

    cv2.imshow("Original", img)
    cv2.imshow("Edges", edges)
    cv2.imshow("Detected Circles", output)

    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()