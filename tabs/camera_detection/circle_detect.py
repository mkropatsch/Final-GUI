# circle_detect.py
#
# Step 1: Detect well circle edge from Dino-Lite camera feed
# - CLAHE + bilateral filter for glass edge contrast
# - Canny edge detection
# - RANSAC circle fitting (robust to partial arcs / noisy edges)
# - Draws estimated circle, center, image center, and offset
#
# Controls:
#   q        - quit
#   s        - save snapshot
#   +/-      - increase/decrease Canny threshold
#   [/]      - increase/decrease RANSAC inlier tolerance
#   e        - toggle edge view overlay

import cv2
import numpy as np
import os
from datetime import datetime

# ── Camera ────────────────────────────────────────────────────────────────────
CAMERA_INDEX = 1          # change to your Dino-Lite index
FRAME_W      = 1280
FRAME_H      = 720
SAVE_DIR     = "circle_detect_snapshots"
os.makedirs(SAVE_DIR, exist_ok=True)

# ── Tunable parameters (adjusted at runtime with keyboard) ────────────────────
canny_low    = 40         # lower Canny threshold  (raise if too many false edges)
canny_high   = 120        # upper Canny threshold  (raise if too many false edges)
ransac_tol   = 3.0        # how many pixels away from circle still counts as inlier
RANSAC_ITERS = 300        # number of RANSAC trials (more = more accurate, slower)
MIN_INLIERS  = 20         # minimum inliers to accept a circle as valid


# ── Preprocessing ─────────────────────────────────────────────────────────────
def preprocess(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (edges, debug_gray) where edges is a binary edge map.
    Uses CLAHE for local contrast boost (good for glass) then
    bilateral filter to smooth noise while keeping edges sharp.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # CLAHE: local contrast enhancement — much better than equalizeHist for glass
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Bilateral filter: smooths flat regions, preserves edges
    smoothed = cv2.bilateralFilter(enhanced, d=9, sigmaColor=75, sigmaSpace=75)

    edges = cv2.Canny(smoothed, canny_low, canny_high)

    # Central ROI — ignore frame borders (less junk)
    h, w = edges.shape
    mask = np.zeros_like(edges)
    x1, y1 = int(0.05 * w), int(0.05 * h)
    x2, y2 = int(0.95 * w), int(0.95 * h)
    mask[y1:y2, x1:x2] = 255
    edges = cv2.bitwise_and(edges, mask)

    return edges, smoothed


# ── RANSAC circle fit ──────────────────────────────────────────────────────────
def circle_from_3_points(p1, p2, p3):
    """
    Compute the unique circle passing through three 2-D points.
    Returns (cx, cy, r) or None if the points are collinear.
    """
    ax, ay = p1
    bx, by = p2
    cx, cy = p3

    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-6:
        return None  # collinear

    ux = ((ax**2 + ay**2) * (by - cy) +
          (bx**2 + by**2) * (cy - ay) +
          (cx**2 + cy**2) * (ay - by)) / d

    uy = ((ax**2 + ay**2) * (cx - bx) +
          (bx**2 + by**2) * (ax - cx) +
          (cx**2 + cy**2) * (bx - ax)) / d

    r = np.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def ransac_circle(edge_img: np.ndarray,
                  n_iters: int = RANSAC_ITERS,
                  tol: float   = ransac_tol,
                  min_inliers: int = MIN_INLIERS):
    """
    RANSAC circle fit on edge points.
    Returns (cx, cy, r, inlier_pts) or None if no good circle found.
    """
    ys, xs = np.where(edge_img > 0)
    if len(xs) < 3:
        return None

    pts = np.column_stack((xs.astype(np.float32),
                           ys.astype(np.float32)))

    best_circle  = None
    best_inliers = 0

    for _ in range(n_iters):
        # Sample 3 random points
        idx = np.random.choice(len(pts), 3, replace=False)
        result = circle_from_3_points(pts[idx[0]], pts[idx[1]], pts[idx[2]])
        if result is None:
            continue

        cx, cy, r = result

        # Skip degenerate circles
        if r < 10 or r > max(edge_img.shape):
            continue

        # Count inliers: edge points within tol pixels of the circle
        dists    = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r)
        inlier_mask = dists < tol
        n_inliers   = np.sum(inlier_mask)

        if n_inliers > best_inliers:
            best_inliers = n_inliers
            best_circle  = (cx, cy, r, pts[inlier_mask])

    if best_circle is None or best_inliers < min_inliers:
        return None

    # Refine: refit circle using all inliers (least-squares)
    cx, cy, r, inliers = best_circle
    refined = _refine_circle(inliers)
    if refined is not None:
        cx, cy, r = refined

    return (cx, cy, r, inliers)


def _refine_circle(pts: np.ndarray):
    """
    Algebraic least-squares circle fit on a set of 2-D points.
    Returns (cx, cy, r) or None.
    """
    if len(pts) < 3:
        return None

    x, y = pts[:, 0], pts[:, 1]

    # Build the linear system: x^2 + y^2 = 2*cx*x + 2*cy*y + (r^2 - cx^2 - cy^2)
    A = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    b = x**2 + y**2

    try:
        result, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None

    cx, cy = result[0], result[1]
    r = np.sqrt(result[2] + cx**2 + cy**2)

    if r < 1:
        return None

    return (cx, cy, r)


# ── Drawing helpers ────────────────────────────────────────────────────────────
def draw_overlay(display: np.ndarray,
                 circle_result,
                 img_center: tuple[int, int],
                 show_edges: bool,
                 edges: np.ndarray):

    h, w = display.shape[:2]
    cx_img, cy_img = img_center

    # Image center crosshair (yellow)
    cv2.drawMarker(display, img_center, (0, 255, 255),
                   cv2.MARKER_CROSS, markerSize=24, thickness=2)

    if show_edges:
        # Tint edge pixels red on the display
        edge_mask = edges > 0
        display[edge_mask] = (display[edge_mask] * 0.4 +
                              np.array([0, 0, 200]) * 0.6).astype(np.uint8)

    if circle_result is None:
        cv2.putText(display, "No circle detected", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 80, 255), 2, cv2.LINE_AA)
        return

    cx, cy, r, inliers = circle_result
    cxi, cyi = int(round(cx)), int(round(cy))

    # Detected circle (green)
    cv2.circle(display, (cxi, cyi), int(round(r)), (0, 220, 80), 2)

    # Detected center crosshair (green)
    cv2.drawMarker(display, (cxi, cyi), (0, 220, 80),
                   cv2.MARKER_CROSS, markerSize=20, thickness=2)

    # Inlier edge points (small cyan dots)
    for px, py in inliers[::3]:   # every 3rd point so it isn't too cluttered
        cv2.circle(display, (int(px), int(py)), 2, (255, 200, 0), -1)

    # Offset vector: image center → circle center (white line)
    off_x = cx - cx_img
    off_y = cy - cy_img
    cv2.arrowedLine(display, img_center, (cxi, cyi),
                    (255, 255, 255), 2, tipLength=0.2)

    # HUD text
    lines = [
        f"Circle center: ({cx:.1f}, {cy:.1f})",
        f"Radius:        {r:.1f} px",
        f"Offset X:      {off_x:+.1f} px",
        f"Offset Y:      {off_y:+.1f} px",
        f"Inliers:       {len(inliers)}",
        f"Canny: {canny_low}/{canny_high}   Tol: {ransac_tol:.1f} px",
    ]
    for i, line in enumerate(lines):
        cv2.putText(display, line, (20, 40 + i * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 230, 255), 2, cv2.LINE_AA)

    # Bottom hint
    cv2.putText(display,
                "q=quit  s=save  +/-=canny  [/]=tolerance  e=edges",
                (20, h - 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 160, 160), 1, cv2.LINE_AA)


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    global canny_low, canny_high, ransac_tol

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"Could not open camera {CAMERA_INDEX}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)

    show_edges = False
    print("Controls: q=quit  s=save  +/-=canny  [/]=RANSAC tolerance  e=edge overlay")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame.")
            break

        h, w    = frame.shape[:2]
        img_center = (w // 2, h // 2)

        edges, _ = preprocess(frame)
        circle   = ransac_circle(edges, tol=ransac_tol)

        display  = frame.copy()
        draw_overlay(display, circle, img_center, show_edges, edges)

        cv2.imshow("Circle Detection", display)
        if show_edges:
            cv2.imshow("Edge Map", edges)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("s"):
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(SAVE_DIR, f"circle_{ts}.png")
            cv2.imwrite(path, frame)
            print(f"Saved: {path}")
        elif key == ord("e"):
            show_edges = not show_edges
            if not show_edges:
                cv2.destroyWindow("Edge Map")
        elif key == ord("+") or key == ord("="):
            canny_low  = min(canny_low  + 5, 200)
            canny_high = min(canny_high + 10, 400)
            print(f"Canny: {canny_low}/{canny_high}")
        elif key == ord("-"):
            canny_low  = max(canny_low  - 5, 5)
            canny_high = max(canny_high - 10, 20)
            print(f"Canny: {canny_low}/{canny_high}")
        elif key == ord("]"):
            ransac_tol = min(ransac_tol + 0.5, 20.0)
            print(f"RANSAC tol: {ransac_tol}")
        elif key == ord("["):
            ransac_tol = max(ransac_tol - 0.5, 0.5)
            print(f"RANSAC tol: {ransac_tol}")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
