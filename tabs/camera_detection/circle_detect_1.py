# circle_detect.py  v2
#
# Well circle detection with:
#   - RANSAC circle fit (robust to partial arcs / glass noise)
#   - Temporal smoothing (rolling average over last N frames)
#   - Consistency gating (reject results that jump too far from last frame)
#   - Center-lock indicator (holds last good result when arc too small to detect)
#
# Controls:
#   q        - quit
#   s        - save snapshot
#   +/-      - increase/decrease Canny threshold
#   [/]      - increase/decrease RANSAC inlier tolerance
#   e        - toggle edge overlay
#   r        - reset lock / smoothing history

import cv2
import numpy as np
import os
from collections import deque
from datetime import datetime

# ── Camera ────────────────────────────────────────────────────────────────────
CAMERA_INDEX = 1
FRAME_W      = 1280
FRAME_H      = 720
SAVE_DIR     = "circle_detect_snapshots"
os.makedirs(SAVE_DIR, exist_ok=True)

# ── Detection parameters (tunable at runtime) ─────────────────────────────────
canny_low    = 40
canny_high   = 120
ransac_tol   = 3.0
RANSAC_ITERS = 300
MIN_INLIERS  = 15         # lowered slightly so near-center arcs still register

# ── Smoothing & gating parameters ────────────────────────────────────────────
SMOOTH_N          = 8     # number of frames to average over
MAX_JUMP_PX       = 80    # reject result if center moves more than this per frame
LOCK_RADIUS_PX    = 40    # pixel radius around image center — inside = LOCKED
LOCK_HOLD_FRAMES  = 20    # once locked, hold for this many frames before unlocking


# ── Preprocessing ─────────────────────────────────────────────────────────────
def preprocess(frame: np.ndarray) -> np.ndarray:
    gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clahe    = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    smoothed = cv2.bilateralFilter(enhanced, d=9, sigmaColor=75, sigmaSpace=75)
    edges    = cv2.Canny(smoothed, canny_low, canny_high)

    # Central ROI mask
    h, w    = edges.shape
    mask    = np.zeros_like(edges)
    x1, y1  = int(0.05 * w), int(0.05 * h)
    x2, y2  = int(0.95 * w), int(0.95 * h)
    mask[y1:y2, x1:x2] = 255
    return cv2.bitwise_and(edges, mask)


# ── RANSAC circle fit ──────────────────────────────────────────────────────────
def circle_from_3_points(p1, p2, p3):
    ax, ay = p1;  bx, by = p2;  cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-6:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) +
          (bx**2 + by**2) * (cy - ay) +
          (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) +
          (bx**2 + by**2) * (ax - cx) +
          (cx**2 + cy**2) * (bx - ax)) / d
    return (ux, uy, np.hypot(ax - ux, ay - uy))


def _refine_circle(pts: np.ndarray):
    if len(pts) < 3:
        return None
    x, y = pts[:, 0], pts[:, 1]
    A    = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    b    = x**2 + y**2
    try:
        res, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = res[0], res[1]
    r      = np.sqrt(max(res[2] + cx**2 + cy**2, 0))
    return (cx, cy, r) if r >= 1 else None


def ransac_circle(edge_img: np.ndarray):
    ys, xs = np.where(edge_img > 0)
    if len(xs) < 3:
        return None

    pts          = np.column_stack((xs.astype(np.float32), ys.astype(np.float32)))
    best_circle  = None
    best_inliers = 0
    max_dim      = max(edge_img.shape)

    for _ in range(RANSAC_ITERS):
        idx    = np.random.choice(len(pts), 3, replace=False)
        result = circle_from_3_points(pts[idx[0]], pts[idx[1]], pts[idx[2]])
        if result is None:
            continue
        cx, cy, r = result
        if r < 10 or r > max_dim * 3:
            continue

        dists       = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r)
        inlier_mask = dists < ransac_tol
        n           = np.sum(inlier_mask)

        if n > best_inliers:
            best_inliers = n
            best_circle  = (cx, cy, r, pts[inlier_mask])

    if best_circle is None or best_inliers < MIN_INLIERS:
        return None

    cx, cy, r, inliers = best_circle
    refined = _refine_circle(inliers)
    if refined:
        cx, cy, r = refined

    return (cx, cy, r, inliers)


# ── Tracker: smoothing + gating + lock ────────────────────────────────────────
class CircleTracker:
    """
    Wraps raw RANSAC results with:
      - consistency gating  (rejects jumps > MAX_JUMP_PX)
      - temporal smoothing  (rolling average over SMOOTH_N frames)
      - center-lock         (holds result and shows LOCKED when inside threshold)
    """

    def __init__(self, img_w: int, img_h: int):
        self.img_cx = img_w / 2
        self.img_cy = img_h / 2

        # Rolling history of accepted (cx, cy, r) tuples
        self._history: deque = deque(maxlen=SMOOTH_N)

        # Last accepted raw result (for gating)
        self._last_cx: float | None = None
        self._last_cy: float | None = None

        # Lock state
        self._locked         = False
        self._lock_countdown = 0

        # Public display values
        self.smooth_cx: float | None = None
        self.smooth_cy: float | None = None
        self.smooth_r:  float | None = None
        self.locked:    bool         = False

    def update(self, ransac_result) -> None:
        # ── Step 1: consistency gate ──────────────────────────────────────────
        accepted = None

        if ransac_result is not None:
            cx, cy, r, inliers = ransac_result

            if self._last_cx is None:
                accepted = (cx, cy, r)               # first result, accept freely
            else:
                jump = np.hypot(cx - self._last_cx, cy - self._last_cy)
                if jump <= MAX_JUMP_PX:
                    accepted = (cx, cy, r)
                # else: discard silently — big jump, likely bad detection

        # ── Step 2: update history & smoothed values ──────────────────────────
        if accepted is not None:
            self._last_cx, self._last_cy = accepted[0], accepted[1]
            self._history.append(accepted)

        if self._history:
            arr            = np.array(self._history)
            self.smooth_cx = float(np.mean(arr[:, 0]))
            self.smooth_cy = float(np.mean(arr[:, 1]))
            self.smooth_r  = float(np.mean(arr[:, 2]))
        else:
            self.smooth_cx = self.smooth_cy = self.smooth_r = None

        # ── Step 3: center-lock logic ─────────────────────────────────────────
        if self.smooth_cx is not None:
            off = np.hypot(self.smooth_cx - self.img_cx,
                           self.smooth_cy - self.img_cy)
            if off <= LOCK_RADIUS_PX:
                self._locked         = True
                self._lock_countdown = LOCK_HOLD_FRAMES
            elif self._locked:
                self._lock_countdown -= 1
                if self._lock_countdown <= 0:
                    self._locked = False

        self.locked = self._locked

    def reset(self) -> None:
        self._history.clear()
        self._last_cx = self._last_cy = None
        self._locked = False
        self._lock_countdown = 0
        self.smooth_cx = self.smooth_cy = self.smooth_r = None
        self.locked = False

    @property
    def offset(self):
        if self.smooth_cx is None:
            return None
        return (self.smooth_cx - self.img_cx,
                self.smooth_cy - self.img_cy)


# ── Drawing ────────────────────────────────────────────────────────────────────
def draw_overlay(display: np.ndarray,
                 tracker: CircleTracker,
                 ransac_raw,
                 show_edges: bool,
                 edges: np.ndarray) -> None:

    h, w = display.shape[:2]
    ic   = (int(tracker.img_cx), int(tracker.img_cy))

    # Lock-zone ring around image center (dim white)
    cv2.circle(display, ic, LOCK_RADIUS_PX, (180, 180, 180), 1, cv2.LINE_AA)

    # Image center crosshair — green when locked, yellow otherwise
    cross_color = (0, 255, 0) if tracker.locked else (0, 255, 255)
    cv2.drawMarker(display, ic, cross_color,
                   cv2.MARKER_CROSS, markerSize=28, thickness=2)

    # Edge overlay (optional)
    if show_edges:
        mask = edges > 0
        display[mask] = (display[mask] * 0.4 +
                         np.array([0, 0, 200]) * 0.6).astype(np.uint8)

    # Raw inlier dots from this frame (cyan)
    if ransac_raw is not None:
        _, _, _, inliers = ransac_raw
        for px, py in inliers[::4]:
            cv2.circle(display, (int(px), int(py)), 2, (255, 200, 0), -1)

    # ── No detection ─────────────────────────────────────────────────────────
    if tracker.smooth_cx is None:
        cv2.putText(display, "No circle detected", (20, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.95, (0, 80, 255), 2, cv2.LINE_AA)
        _draw_hints(display, h)
        return

    scx = int(round(tracker.smooth_cx))
    scy = int(round(tracker.smooth_cy))
    sr  = int(round(tracker.smooth_r))
    off = tracker.offset

    # ── LOCKED ───────────────────────────────────────────────────────────────
    if tracker.locked:
        # Green fill tint inside lock zone
        overlay = display.copy()
        cv2.circle(overlay, ic, LOCK_RADIUS_PX, (0, 180, 0), -1)
        cv2.addWeighted(overlay, 0.15, display, 0.85, 0, display)

        cv2.putText(display, "LOCKED", (w // 2 - 110, h // 2 - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 255, 80), 4, cv2.LINE_AA)

        cv2.circle(display, (scx, scy), sr, (0, 255, 80), 2, cv2.LINE_AA)
        cv2.drawMarker(display, (scx, scy), (0, 255, 80),
                       cv2.MARKER_CROSS, markerSize=18, thickness=2)

        lines = [
            f"Circle center: ({tracker.smooth_cx:.1f}, {tracker.smooth_cy:.1f})",
            f"Radius:        {tracker.smooth_r:.1f} px",
            f"Offset X:      {off[0]:+.1f} px  << CENTERED",
            f"Offset Y:      {off[1]:+.1f} px  << CENTERED",
        ]
        for i, ln in enumerate(lines):
            cv2.putText(display, ln, (20, 45 + i * 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 255, 80), 2, cv2.LINE_AA)
        _draw_hints(display, h)
        return

    # ── Normal tracking ───────────────────────────────────────────────────────
    cv2.circle(display, (scx, scy), sr, (0, 220, 80), 2, cv2.LINE_AA)
    cv2.drawMarker(display, (scx, scy), (0, 220, 80),
                   cv2.MARKER_CROSS, markerSize=18, thickness=2)

    # Offset arrow
    cv2.arrowedLine(display, ic, (scx, scy),
                    (255, 255, 255), 2, tipLength=0.15)

    # Distance bar
    dist      = np.hypot(off[0], off[1])
    bar_max   = 200
    bar_fill  = int(min(dist / max(tracker.smooth_r, 1), 1.0) * bar_max)
    bx, by    = 20, h - 50
    bar_color = (0, 200, 255) if dist > LOCK_RADIUS_PX * 2 else (0, 255, 120)
    cv2.rectangle(display, (bx, by), (bx + bar_max, by + 14), (60, 60, 60), -1)
    cv2.rectangle(display, (bx, by), (bx + bar_fill, by + 14), bar_color, -1)
    cv2.putText(display, f"dist: {dist:.0f} px",
                (bx + bar_max + 8, by + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

    n_inliers = len(ransac_raw[3]) if ransac_raw is not None else 0
    lines = [
        f"Circle center: ({tracker.smooth_cx:.1f}, {tracker.smooth_cy:.1f})",
        f"Radius:        {tracker.smooth_r:.1f} px",
        f"Offset X:      {off[0]:+.1f} px",
        f"Offset Y:      {off[1]:+.1f} px",
        f"Inliers: {n_inliers}   History: {len(tracker._history)}/{SMOOTH_N}",
        f"Canny: {canny_low}/{canny_high}   Tol: {ransac_tol:.1f} px",
    ]
    for i, ln in enumerate(lines):
        cv2.putText(display, ln, (20, 45 + i * 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.72, (200, 230, 255), 2, cv2.LINE_AA)

    _draw_hints(display, h)


def _draw_hints(display, h):
    cv2.putText(display,
                "q=quit  s=save  +/-=canny  [/]=tol  e=edges  r=reset",
                (20, h - 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.58, (130, 130, 130), 1, cv2.LINE_AA)


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    global canny_low, canny_high, ransac_tol

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"Could not open camera {CAMERA_INDEX}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)

    h_actual = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w_actual = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    tracker    = CircleTracker(w_actual, h_actual)
    show_edges = False

    print("Controls: q=quit  s=save  +/-=canny  [/]=tol  e=edges  r=reset")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame.")
            break

        edges      = preprocess(frame)
        raw_result = ransac_circle(edges)
        tracker.update(raw_result)

        display = frame.copy()
        draw_overlay(display, tracker, raw_result, show_edges, edges)

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
        elif key == ord("r"):
            tracker.reset()
            print("Tracker reset.")
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
            print(f"RANSAC tol: {ransac_tol:.1f}")
        elif key == ord("["):
            ransac_tol = max(ransac_tol - 0.5, 0.5)
            print(f"RANSAC tol: {ransac_tol:.1f}")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
