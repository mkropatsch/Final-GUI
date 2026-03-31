import cv2
import numpy as np
import os
from datetime import datetime

CAMERA_INDEX = 1
SAVE_DIR = "well_plate_snapshots"

os.makedirs(SAVE_DIR, exist_ok=True)


def detect_edge_points_and_main_line(frame):
    """
    Detect filtered edge points and fit ONE clean line to the strongest edge contour.

    Returns:
        edges_roi       - binary edge image after ROI masking
        edge_points     - Nx2 array of (x, y) filtered edge coordinates
        roi_box         - (x1, y1, x2, y2)
        line_info       - None or dict with fitted line info
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Mild contrast boost
    gray = cv2.equalizeHist(gray)

    # Smooth noise a bit
    blur = cv2.GaussianBlur(gray, (7, 7), 2)

    # Stronger Canny thresholds = less weak junk
    edges = cv2.Canny(blur, 80, 200)

    h, w = edges.shape

    # Central ROI so we ignore some junk near the frame border
    x1 = int(0.15 * w)
    x2 = int(0.85 * w)
    y1 = int(0.10 * h)
    y2 = int(0.90 * h)

    roi_mask = np.zeros_like(edges)
    roi_mask[y1:y2, x1:x2] = 255

    edges_roi = cv2.bitwise_and(edges, roi_mask)

    # Gradient images from blurred grayscale
    grad_x = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.sqrt(grad_x**2 + grad_y**2)

    # Get edge pixel coordinates
    ys, xs = np.where(edges_roi > 0)

    if len(xs) == 0:
        edge_points = np.empty((0, 2), dtype=np.int32)
        return edges_roi, edge_points, (x1, y1, x2, y2), []

    # Gradient strength at edge locations
    strengths = grad_mag[ys, xs]

    # Edge direction at edge locations
    gx = grad_x[ys, xs]
    gy = grad_y[ys, xs]
    angles = np.arctan2(gy, gx)

    # Keep only strong edges
    strong_keep = strengths > 50

    # Keep edges that are not too close to purely horizontal/vertical
    direction_keep = np.abs(np.sin(angles)) > 0.3

    # Combine both filters
    keep = strong_keep & direction_keep

    xs = xs[keep]
    ys = ys[keep]

    edge_points = np.column_stack((xs, ys)).astype(np.int32)

    # Build filtered binary image from kept points
    filtered_edges = np.zeros_like(edges_roi)
    if len(edge_points) > 0:
        filtered_edges[edge_points[:, 1], edge_points[:, 0]] = 255

    # Find contours on filtered edge map
    contours, _ = cv2.findContours(
        filtered_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )

    best_contour = None
    best_len = 0

    for cnt in contours:
        if len(cnt) < 30:
            continue

        arc_len = cv2.arcLength(cnt, False)
        if arc_len > best_len:
            best_len = arc_len
            best_contour = cnt

    line_info = None

    # if best_contour is not None and len(best_contour) >= 2:
    #     pts = best_contour.reshape(-1, 2).astype(np.float32)

    #     # Fit one line to the strongest contour
    #     vx, vy, x0, y0 = cv2.fitLine(
    #         pts, cv2.DIST_L2, 0, 0.01, 0.01
    #     ).flatten()

    #     line_info = {
    #         "vx": float(vx),
    #         "vy": float(vy),
    #         "x0": float(x0),
    #         "y0": float(y0),
    #         "contour": best_contour,
    #     }

    # return edges_roi, edge_points, (x1, y1, x2, y2), line_info
    
    ## Keep the two longest contours
    contour_data = []
    
    for cnt in contours:
        if len(cnt) < 30:
            continue
        
        arc_len = cv2.arcLength(cnt, False)
        contour_data.append((arc_len, cnt))
        
    contour_data.sort(key=lambda item: item[0], reverse=True)
    top_contours = [cnt for _, cnt in contour_data[:2]]
    
    line_info_list = []
    
    for cnt in top_contours:
        if len(cnt) < 2:
            continue
        
        pts = cnt.reshape(-1, 2).astype(np.float32)
        
        vx, vy, x0, y0 = cv2.fitLine(
            pts, cv2.DIST_L2, 0, 0.01, 0.01
        ).flatten()
        
        line_info_list.append({
            "vx": float(vx),
            "vy": float(vy),
            "x0": float(x0),
            "y0": float(y0),
            "contour": cnt,
        })
    return edges_roi, edge_points, (x1, y1, x2, y2), line_info_list

def clip_line_to_box(vx, vy, x0, y0, x_min, y_min, x_max, y_max):
    """
    Clip a parametric line to a rectangle.
    Returns two endpoints or None if it cannot be clipped.
    """
    points = []

    eps = 1e-8

    # Intersections with x = x_min and x = x_max
    if abs(vx) > eps:
        t = (x_min - x0) / vx
        y = y0 + t * vy
        if y_min <= y <= y_max:
            points.append((int(round(x_min)), int(round(y))))

        t = (x_max - x0) / vx
        y = y0 + t * vy
        if y_min <= y <= y_max:
            points.append((int(round(x_max)), int(round(y))))

    # Intersections with y = y_min and y = y_max
    if abs(vy) > eps:
        t = (y_min - y0) / vy
        x = x0 + t * vx
        if x_min <= x <= x_max:
            points.append((int(round(x)), int(round(y_min))))

        t = (y_max - y0) / vy
        x = x0 + t * vx
        if x_min <= x <= x_max:
            points.append((int(round(x)), int(round(y_max))))

    # Remove duplicates
    unique_points = []
    for p in points:
        if p not in unique_points:
            unique_points.append(p)

    if len(unique_points) < 2:
        return None

    # If more than 2, choose the farthest pair
    max_dist = -1
    best_pair = None
    for i in range(len(unique_points)):
        for j in range(i + 1, len(unique_points)):
            p1 = np.array(unique_points[i], dtype=np.float32)
            p2 = np.array(unique_points[j], dtype=np.float32)
            d = np.linalg.norm(p1 - p2)
            if d > max_dist:
                max_dist = d
                best_pair = (unique_points[i], unique_points[j])

    return best_pair


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
        h, w = frame.shape[:2]
        img_center = (w // 2, h // 2)

        edges, edge_points, roi_box, line_info_list = detect_edge_points_and_main_line(frame)
        x1, y1, x2, y2 = roi_box

        # Draw image center
        cv2.drawMarker(
            display,
            img_center,
            (255, 255, 0),
            markerType=cv2.MARKER_CROSS,
            markerSize=20,
            thickness=2,
        )

        # Draw ROI box
        cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 255), 1)

        # Draw red edge points
        for x, y in edge_points[::6]:
            cv2.circle(display, (int(x), int(y)), 1, (0, 0, 255), -1)

        # Draw up to two fitted edge lines
        midpoint_points = []
        
        for idx, line_info in enumerate(line_info_list or []):
            vx = line_info["vx"]
            vy = line_info["vy"]
            x0 = line_info["x0"]
            y0 = line_info["y0"]
            
            endpoints = clip_line_to_box(vx, vy, x0, y0, x1, y1, x2, y2)
            if endpoints is not None:
                p1, p2 = endpoints
                midpoint_points.append((p1, p2))
                
                # draw edge line
                cv2.line(display, p1, p2, (0, 255, 0), 2)
                
            # draw contour used for this line
            cv2.drawContours(display, [line_info["contour"]], -1, (255, 0, 0), 1)
        
        # Draw midpoint line if we got two lines
        if len(midpoint_points) == 2:
            (p1a, p2a), (p1b, p2b) = midpoint_points
            
            mid1 = (
                int(round((p1a[0] + p1b[0]) / 2)),
                int(round((p1a[1] + p1b[1]) / 2)),
            )
            mid2 = (
                int(round((p2a[0] + p2b[0]) / 2)),
                int(round((p2a[1] + p2b[1]) / 2)),
            )
            
            cv2.line(display, mid1, mid2, (0, 255, 255), 4)
        
        cv2.putText(
            display,
            f"Edge points: {len(edge_points)}",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            display,
            "Red dots = filtered edge points",
            (20, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            display,
            "Green = edge lines, Yellow = midpoint",
            (20, 105),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            display,
            "Blue contour = contour used for line fit",
            (20, 140),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 0, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            display,
            "Press 's' to save snapshot, 'q' to quit",
            (20, h - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow("Live Edge Point Detection", display)
        cv2.imshow("Edges ROI", edges)

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