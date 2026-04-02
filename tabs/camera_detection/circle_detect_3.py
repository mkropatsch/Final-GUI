# circle_detect.py  v4
#
# Same as v3 but with spread-out RANSAC point sampling for tiny arcs:
#   - Deliberately picks 3 points far apart from each other each iteration
#   - Falls back to random sampling if arc is too small to find spread points
#   - SMOOTH_N bumped to 15, MAX_JUMP_PX tightened to 40
#
# Controls:
#   q        - quit
#   s        - save snapshot
#   +/-      - increase/decrease Canny threshold
#   [/]      - increase/decrease RANSAC inlier tolerance
#   e        - toggle edge overlay
#   r        - reset tracker

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

# ── Detection parameters ──────────────────────────────────────────────────────
canny_low    = 80
canny_high   = 200
ransac_tol   = 2.0        # tighter than v3 — more discriminating with small arcs
RANSAC_ITERS = 400        # slight bump to compensate for stricter sampling
MIN_INLIERS  = 20
MIN_SPREAD   = 40         # minimum pixels between sampled points (key new param)

# ── Smoothing / gating / centering ────────────────────────────────────────────
SMOOTH_N            = 15  # more smoothing — tiny arcs are noisier
MAX_JUMP_PX         = 40  # tighter gate — bad frames jump further with small arcs
CENTER_THRESHOLD_PX = 20
CENTER_CONFIRM_N    = 10


# ── Preprocessing (unchanged from v3) ────────────────────────────────────────
def preprocess(frame: np.ndarray) -> tuple:
    gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clahe    = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    smoothed = cv2.bilateralFilter(enhanced, d=9, sigmaColor=75, sigmaSpace=75)
    edges    = cv2.Canny(smoothed, canny_low, canny_high)

    h, w    = edges.shape
    mask    = np.zeros_like(edges)
    x1, y1  = int(0.15 * w), int(0.10 * h)
    x2, y2  = int(0.85 * w), int(0.90 * h)
    mask[y1:y2, x1:x2] = 255
    return cv2.bitwise_and(edges, mask), smoothed


# ── Circle math (unchanged) ───────────────────────────────────────────────────
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


# ── Spread-out point sampler ──────────────────────────────────────────────────
def _sample_spread(pts: np.ndarray, min_spread: float) -> tuple | None:
    """
    Pick 3 points that are each at least min_spread pixels apart.
    Returns (i, j, k) indices or None if impossible.

    Strategy:
      1. Pick p1 randomly
      2. Pick p2 from points far enough from p1
      3. Pick p3 from points far enough from both p1 and p2
    Falls back to None so the caller can use random sampling instead.
    """
    n = len(pts)
    if n < 3:
        return None

    # p1: random
    i = int(np.random.randint(0, n))

    # p2: must be >= min_spread from p1
    dists_from_p1 = np.hypot(pts[:, 0] - pts[i, 0],
                              pts[:, 1] - pts[i, 1])
    candidates_j  = np.where(dists_from_p1 >= min_spread)[0]
    if len(candidates_j) == 0:
        return None
    j = int(candidates_j[np.random.randint(0, len(candidates_j))])

    # p3: must be >= min_spread from BOTH p1 and p2
    dists_from_p2 = np.hypot(pts[:, 0] - pts[j, 0],
                              pts[:, 1] - pts[j, 1])
    candidates_k  = np.where(
        (dists_from_p1 >= min_spread) & (dists_from_p2 >= min_spread)
    )[0]
    if len(candidates_k) == 0:
        return None
    k = int(candidates_k[np.random.randint(0, len(candidates_k))])

    return (i, j, k)


# ── RANSAC circle fit ─────────────────────────────────────────────────────────
def ransac_circle(edge_img: np.ndarray):
    ys, xs = np.where(edge_img > 0)
    if len(xs) < 3:
        return None

    pts          = np.column_stack((xs.astype(np.float32), ys.astype(np.float32)))
    best_circle  = None
    best_inliers = 0
    max_dim      = max(edge_img.shape)
    fallbacks    = 0   # tracked for debug HUD

    for _ in range(RANSAC_ITERS):
        # Try spread sampling first, fall back to random if arc too small
        sample = _sample_spread(pts, MIN_SPREAD)
        if sample is None:
            fallbacks += 1
            idx = np.random.choice(len(pts), 3, replace=False)
            i, j, k = idx[0], idx[1], idx[2]
        else:
            i, j, k = sample

        result = circle_from_3_points(pts[i], pts[j], pts[k])
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

    return (cx, cy, r, inliers, fallbacks)


# ── Tracker (unchanged logic, handles extra fallbacks field) ──────────────────
class CircleTracker:
    def __init__(self, img_w: int, img_h: int):
        self.img_cx = img_w / 2
        self.img_cy = img_h / 2
        self._history: deque        = deque(maxlen=SMOOTH_N)
        self._last_cx: float | None = None
        self._last_cy: float | None = None
        self._confirm_count         = 0
        self.smooth_cx: float | None = None
        self.smooth_cy: float | None = None
        self.smooth_r:  float | None = None
        self.centered:  bool         = False
        self.confirm_count: int      = 0

    def update(self, ransac_result) -> None:
        accepted = None
        if ransac_result is not None:
            cx, cy, r = ransac_result[0], ransac_result[1], ransac_result[2]
            if self._last_cx is None:
                accepted = (cx, cy, r)
            else:
                jump = np.hypot(cx - self._last_cx, cy - self._last_cy)
                if jump <= MAX_JUMP_PX:
                    accepted = (cx, cy, r)

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

        if self.smooth_cx is not None:
            dist = np.hypot(self.smooth_cx - self.img_cx,
                            self.smooth_cy - self.img_cy)
            if dist <= CENTER_THRESHOLD_PX:
                self._confirm_count += 1
            else:
                self._confirm_count = 0
        else:
            self._confirm_count = 0

        self.confirm_count = self._confirm_count
        self.centered      = self._confirm_count >= CENTER_CONFIRM_N

    def reset(self) -> None:
        self._history.clear()
        self._last_cx = self._last_cy = None
        self._confirm_count = 0
        self.smooth_cx = self.smooth_cy = self.smooth_r = None
        self.centered  = False
        self.confirm_count = 0

    @property
    def offset(self):
        if self.smooth_cx is None:
            return None
        return (self.smooth_cx - self.img_cx,
                self.smooth_cy - self.img_cy)


# ── Drawing ───────────────────────────────────────────────────────────────────
def draw_overlay(display, tracker, ransac_raw, show_edges, edges):
    h, w = display.shape[:2]
    ic   = (int(tracker.img_cx), int(tracker.img_cy))

    cv2.circle(display, ic, CENTER_THRESHOLD_PX, (180, 180, 180), 1, cv2.LINE_AA)

    if show_edges:
        mask = edges > 0
        display[mask] = (display[mask] * 0.4 +
                         np.array([0, 0, 200]) * 0.6).astype(np.uint8)

    if ransac_raw is not None:
        for px, py in ransac_raw[3][::4]:
            cv2.circle(display, (int(px), int(py)), 2, (255, 200, 0), -1)

    # ── No detection ─────────────────────────────────────────────────────────
    if tracker.smooth_cx is None:
        cv2.drawMarker(display, ic, (0, 255, 255),
                       cv2.MARKER_CROSS, markerSize=24, thickness=2)
        cv2.putText(display, "No circle detected", (20, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.95, (0, 80, 255), 2, cv2.LINE_AA)
        _draw_hints(display, h)
        return

    scx  = int(round(tracker.smooth_cx))
    scy  = int(round(tracker.smooth_cy))
    sr   = int(round(tracker.smooth_r))
    off  = tracker.offset
    dist = np.hypot(off[0], off[1])

    fallbacks = ransac_raw[4] if ransac_raw is not None else 0

    # ── CENTERED ─────────────────────────────────────────────────────────────
    if tracker.centered:
        overlay = display.copy()
        cv2.circle(overlay, ic, CENTER_THRESHOLD_PX, (0, 200, 0), -1)
        cv2.addWeighted(overlay, 0.18, display, 0.82, 0, display)
        cv2.drawMarker(display, ic, (0, 255, 0),
                       cv2.MARKER_CROSS, markerSize=28, thickness=2)
        cv2.circle(display, (scx, scy), sr, (0, 255, 80), 2, cv2.LINE_AA)
        cv2.drawMarker(display, (scx, scy), (0, 255, 80),
                       cv2.MARKER_CROSS, markerSize=16, thickness=2)
        cv2.putText(display, "CENTERED",
                    (w // 2 - 130, h // 2 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.4, (0, 255, 80), 4, cv2.LINE_AA)
        lines = [
            f"Circle center: ({tracker.smooth_cx:.1f}, {tracker.smooth_cy:.1f})",
            f"Radius:        {tracker.smooth_r:.1f} px",
            f"Offset X:      {off[0]:+.1f} px",
            f"Offset Y:      {off[1]:+.1f} px",
            f"Stable frames: {tracker.confirm_count}/{CENTER_CONFIRM_N}",
        ]
        for i, ln in enumerate(lines):
            cv2.putText(display, ln, (20, 45 + i * 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 255, 80), 2, cv2.LINE_AA)
        _draw_hints(display, h)
        return

    # ── Normal tracking ───────────────────────────────────────────────────────
    cv2.drawMarker(display, ic, (0, 255, 255),
                   cv2.MARKER_CROSS, markerSize=24, thickness=2)
    cv2.circle(display, (scx, scy), sr, (0, 220, 80), 2, cv2.LINE_AA)
    cv2.drawMarker(display, (scx, scy), (0, 220, 80),
                   cv2.MARKER_CROSS, markerSize=16, thickness=2)
    cv2.arrowedLine(display, ic, (scx, scy),
                    (255, 255, 255), 2, tipLength=0.15)

    if tracker.confirm_count > 0:
        bar_w        = 200
        bx, by       = w // 2 - bar_w // 2, h - 50
        fill         = int((tracker.confirm_count / CENTER_CONFIRM_N) * bar_w)
        cv2.rectangle(display, (bx, by), (bx + bar_w, by + 16), (60, 60, 60), -1)
        cv2.rectangle(display, (bx, by), (bx + fill,  by + 16), (0, 200, 120), -1)
        cv2.putText(display, "Centering...", (bx, by - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 120), 1, cv2.LINE_AA)

    n_inliers = len(ransac_raw[3]) if ransac_raw is not None else 0
    lines = [
        f"Circle center: ({tracker.smooth_cx:.1f}, {tracker.smooth_cy:.1f})",
        f"Radius:        {tracker.smooth_r:.1f} px",
        f"Offset X:      {off[0]:+.1f} px",
        f"Offset Y:      {off[1]:+.1f} px",
        f"Inliers: {n_inliers}   History: {len(tracker._history)}/{SMOOTH_N}",
        f"Canny: {canny_low}/{canny_high}   Tol: {ransac_tol:.1f}   Spread: {MIN_SPREAD}px",
        f"Dist: {dist:.1f} px   Fallbacks: {fallbacks}/{RANSAC_ITERS}",
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

        edges, _   = preprocess(frame)
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
