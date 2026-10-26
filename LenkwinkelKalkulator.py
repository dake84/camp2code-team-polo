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
        fit_frac=1.0
):

    if mask is None:
        return [], None

    if mask.size == 0:
        return [], mask.copy()

    # # Falls ein BGR-Bild übergeben wird, in Graustufen umwandeln
    # if mask.ndim == 3:
    #     mask_gray = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    # else:
    #     mask_gray = mask

    # mask_bin = (mask_gray > 0).astype(np.uint8) * 255

    # kernel = np.ones(morph_kernel, dtype=np.uint8)

    # mask_bin = cv2.morphologyEx(
    #     mask_bin,
    #     cv2.MORPH_CLOSE,
    #     kernel
    # )

    num, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8
    )

    if num <= 1:
        return [], np.zeros_like(mask)

    order = np.argsort(
        stats[1:, cv2.CC_STAT_AREA]
    )[::-1][:n_lanes] + 1

    order = order[
        stats[order, cv2.CC_STAT_AREA] >= min_area
    ]

    h = mask.shape[0]
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
        return [], np.zeros_like(mask)

    sort_indices = np.argsort(x_bottom)

    lanes = [lanes[i] for i in sort_indices]
    valid_labels = [valid_labels[i] for i in sort_indices]

    sel_mask = np.isin(labels, valid_labels).astype(np.uint8) * 255

    return lanes, sel_mask


def cals_drive_parameter_from_lanes(
        img,
        lanes,
        lookahead_fraction=0.55,
        max_steering_delta=35.0
) -> tuple[float | None, np.ndarray]:

    if img is None or img.size == 0:
        return None, img

    img_with_steering_lane = img.copy()

    if img_with_steering_lane.ndim == 2:
        img_with_steering_lane = cv2.cvtColor(
            img_with_steering_lane,
            cv2.COLOR_GRAY2BGR
        )

    h, w = img_with_steering_lane.shape[:2]

    # Es werden zwingend zwei Fahrbahnbegrenzungen benötigt
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

        return None, img_with_steering_lane

    if not all(lane is not None for lane in lanes):
        return None, img_with_steering_lane

    y_bottom = h - 1

    # Nicht bis zum oberen Bildrand extrapolieren
    y_lookahead = int(np.clip(
        lookahead_fraction * h,
        0,
        h - 2
    ))

    x_bottom_arr = np.zeros(2, dtype=float)
    x_lookahead_arr = np.zeros(2, dtype=float)

    for i, lane in enumerate(lanes):

        m, b = lane

        if not np.isfinite(m) or not np.isfinite(b):
            return None, img_with_steering_lane

        if abs(m) < 1e-6:
            return None, img_with_steering_lane

        # Aus y = m*x + b folgt x = (y - b) / m
        x_bottom_arr[i] = (y_bottom - b) / m
        x_lookahead_arr[i] = (y_lookahead - b) / m

        # Fahrbahnlinien nur innerhalb des relevanten Bildbereichs zeichnen
        x_bottom_draw = int(np.clip(
            x_bottom_arr[i],
            0,
            w - 1
        ))

        x_lookahead_draw = int(np.clip(
            x_lookahead_arr[i],
            0,
            w - 1
        ))

        cv2.line(
            img_with_steering_lane,
            (x_bottom_draw, y_bottom),
            (x_lookahead_draw, y_lookahead),
            (0, 255, 0),
            2
        )

    # Prüfen, ob links und rechts weiterhin richtig sortiert sind
    if (
        x_bottom_arr[0] >= x_bottom_arr[1]
        or x_lookahead_arr[0] >= x_lookahead_arr[1]
    ):
        cv2.putText(
            img_with_steering_lane,
            'Fahrbahnlinien unplausibel',
            (10, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
            cv2.LINE_AA
        )

        return None, img_with_steering_lane

    # Fahrbahnmitte im Look-ahead-Bereich
    lane_center_lookahead = float(np.mean(x_lookahead_arr))

    # Unterer Startpunkt entspricht der Fahrzeug-/Kameramitte
    x_steer_start = float(w / 2.0)
    x_steer_end = lane_center_lookahead

    delta_x = x_steer_end - x_steer_start
    delta_y = y_bottom - y_lookahead

    # 90 Grad = geradeaus
    steering_delta = np.degrees(
        np.arctan2(delta_x, delta_y)
    )

    # Extremes Lenken durch einzelne fehlerhafte Frames verhindern
    steering_delta = float(np.clip(
        steering_delta,
        -max_steering_delta,
        max_steering_delta
    ))

    steering_angle = float(np.clip(
        90.0 + steering_delta,
        45.0,
        135.0
    ))

    x_steer_end_draw = int(np.clip(
        x_steer_end,
        0,
        w - 1
    ))

    cv2.line(
        img_with_steering_lane,
        (int(x_steer_start), y_bottom),
        (x_steer_end_draw, y_lookahead),
        (0, 0, 255),
        2
    )

    cv2.circle(
        img_with_steering_lane,
        (x_steer_end_draw, y_lookahead),
        5,
        (255, 0, 0),
        -1
    )

    cv2.putText(
        img_with_steering_lane,
        f'Lenkwinkel: {steering_angle:.1f}',
        (10, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 0, 0),
        2,
        cv2.LINE_AA
    )

    cv2.putText(
        img_with_steering_lane,
        f'Abweichung: {delta_x:.1f}px',
        (10, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 0, 0),
        1,
        cv2.LINE_AA
    )

    return steering_angle, img_with_steering_lane



def lenkwinkel_from_image(img) -> tuple[int, np.ndarray]:

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