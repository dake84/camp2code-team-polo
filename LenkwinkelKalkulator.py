import cv2
import numpy as np


def put_steering_lines_into_image(img, lanes=None):

    if img is None or img.size == 0:
        return None

    img_with_lines = img.copy()

    if img_with_lines.ndim == 2:
        img_with_lines = cv2.cvtColor(img_with_lines, cv2.COLOR_GRAY2BGR)

    w = img_with_lines.shape[1]

    if lanes is not None:
        for lane in lanes:

            if lane is None:
                continue

            m, b = lane

            x1 = 0
            x2 = w - 1

            y1 = int(np.clip(m * x1 + b, -1e6, 1e6))
            y2 = int(np.clip(m * x2 + b, -1e6, 1e6))

            cv2.line(
                img_with_lines,
                (x1, y1),
                (x2, y2),
                (0, 255, 0),
                2
            )

    return img_with_lines


def detect_lanes_from_areas(
        mask,
        n_lanes=2,
        min_area=150,
        m_max=10,
        fit_frac=1.0,
        morph_kernel=(5, 5)
):

    if mask is None:
        return [], None

    if mask.size == 0:
        return [], mask.copy()

    # Falls ein BGR-Bild übergeben wird, in Graustufen umwandeln
    if mask.ndim == 3:
        mask_gray = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    else:
        mask_gray = mask

    mask_bin = (mask_gray > 0).astype(np.uint8) * 255

    kernel = np.ones(morph_kernel, dtype=np.uint8)

    mask_bin = cv2.morphologyEx(
        mask_bin,
        cv2.MORPH_CLOSE,
        kernel
    )

    num, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask_bin,
        connectivity=8
    )

    if num <= 1:
        return [], np.zeros_like(mask_bin)

    order = np.argsort(
        stats[1:, cv2.CC_STAT_AREA]
    )[::-1][:n_lanes] + 1

    order = order[
        stats[order, cv2.CC_STAT_AREA] >= min_area
    ]

    h = mask_bin.shape[0]
    y_min = int((1.0 - fit_frac) * h)

    lanes = []
    x_bottom = []
    valid_labels = []

    for lbl in order:

        ys, xs = np.nonzero(labels == lbl)

        keep = ys >= y_min
        ys = ys[keep]
        xs = xs[keep]

        if ys.size < 2 or np.ptp(ys) < 2:
            continue

        # Stabiler Fit für nahezu senkrechte Linien: x = a*y + c
        a, c = np.polyfit(ys, xs, 1)

        if abs(a) > 1.0 / m_max:
            m = float(1.0 / a)
        else:
            sign = 1.0 if a >= 0 else -1.0
            m = sign * float(m_max)

        b = float(ys.mean() - m * xs.mean())

        lanes.append((m, b))
        x_bottom.append(float(a * (h - 1) + c))
        valid_labels.append(lbl)

    if not lanes:
        return [], np.zeros_like(mask_bin)

    sort_indices = np.argsort(x_bottom)

    lanes = [lanes[i] for i in sort_indices]
    valid_labels = [valid_labels[i] for i in sort_indices]

    sel_mask = np.isin(labels, valid_labels).astype(np.uint8) * 255

    return lanes, sel_mask


def cals_drive_parameter_from_lanes(
        img,
        lanes
) -> tuple[float, np.ndarray]:

    steering_angle = 90.0

    if img is None or img.size == 0:
        return steering_angle, img

    img_with_steering_lane = img.copy()

    if img_with_steering_lane.ndim == 2:
        img_with_steering_lane = cv2.cvtColor(
            img_with_steering_lane,
            cv2.COLOR_GRAY2BGR
        )

    h, w = img_with_steering_lane.shape[:2]

    # Kein Abbruch mehr, wenn nicht genau zwei Fahrbahnen erkannt wurden
    if lanes is None or len(lanes) != 2:
        cv2.putText(
            img_with_steering_lane,
            'Keine zwei Fahrbahnen erkannt',
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
            cv2.LINE_AA
        )

        return steering_angle, img_with_steering_lane

    if not all(lane is not None for lane in lanes):
        return steering_angle, img_with_steering_lane

    y_bottom = h - 1
    y_top = 0

    x_bottom_arr = np.zeros(2, dtype=float)
    x_top_arr = np.zeros(2, dtype=float)

    for i, lane in enumerate(lanes):

        m, b = lane

        x1 = 0
        x2 = w - 1

        y1 = int(np.clip(m * x1 + b, -1e6, 1e6))
        y2 = int(np.clip(m * x2 + b, -1e6, 1e6))

        cv2.line(
            img_with_steering_lane,
            (x1, y1),
            (x2, y2),
            (0, 255, 0),
            2
        )

        if abs(m) < 1e-6:
            m_safe = np.copysign(1e-6, m if m != 0 else 1.0)
        else:
            m_safe = m

        x_bottom_arr[i] = (y_bottom - b) / m_safe
        x_top_arr[i] = (y_top - b) / m_safe

    x_steer_start = int(np.clip(
        np.mean(x_bottom_arr),
        0,
        w - 1
    ))

    x_steer_end = int(np.clip(
        np.mean(x_top_arr),
        0,
        w - 1
    ))

    delta_x = x_steer_end - x_steer_start
    delta_y = y_top - y_bottom

    slope_degree = (
        180.0 + np.degrees(np.arctan2(delta_y, delta_x))
    ) % 180.0

    cv2.putText(
        img_with_steering_lane,
        f'{slope_degree:.1f}',
        (20, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 0, 0),
        2,
        cv2.LINE_AA
    )

    cv2.line(
        img_with_steering_lane,
        (x_steer_start, y_bottom),
        (x_steer_end, y_top),
        (0, 0, 255),
        2
    )

    steering_angle = float(slope_degree)

    return steering_angle, img_with_steering_lane


def lenkwinkel_from_image(
        img
) -> tuple[float, np.ndarray]:

    steering_angle = 90.0

    if img is None:
        return steering_angle, None

    if img.size == 0:
        return steering_angle, img

    lanes, img_areas = detect_lanes_from_areas(
        img,
        n_lanes=2,
        min_area=150,
        fit_frac=1.0
    )

    # Sicherheitsfallback, falls keine Ausgabemaske erzeugt wurde
    if img_areas is None:
        img_areas = np.zeros(img.shape[:2], dtype=np.uint8)

    img_with_lines = put_steering_lines_into_image(
        img_areas,
        lanes
    )

    steering_angle, img_with_steering_lane = (
        cals_drive_parameter_from_lanes(
            img_with_lines,
            lanes
        )
    )

    return steering_angle, img_with_steering_lane