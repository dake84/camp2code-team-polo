import cv2
import numpy as np


def put_steering_lines_into_image(img, lanes = None):

    img_with_lines = img.copy()
    if img_with_lines.ndim == 2:
        img_with_lines = cv2.cvtColor(img_with_lines, cv2.COLOR_GRAY2RGB)

    w = img_with_lines.shape[1]
    if lanes is not None and len(lanes) > 0:
        for lane in lanes:
            if lane is None:
                continue
            m, b = lane

            x1 = 0
            x2 = w - 1
            y1 = int(np.clip(m * x1 + b, -1e6, 1e6))
            y2 = int(np.clip(m * x2 + b, -1e6, 1e6))

            cv2.line(img_with_lines, (x1, y1), (x2, y2), (0, 255, 0), 2)

    return img_with_lines

def detect_lanes_from_areas(mask, n_lanes=2, min_area=150, m_max=10, fit_frac=1.0, morph_kernel=(5, 5)):
    '''Fasst zusammenhaengende Pixel der Maske zu Flaechen zusammen, nimmt die n_lanes groessten
    und fittet durch deren Pixel je eine Gerade y = m*x + b. Rueckgabe: lanes (links -> rechts), sel_mask'''
    if mask is None or mask.size == 0:
        return [], None

    # Binaer machen + kleine Luecken schliessen, damit eine Linie eine Flaeche bleibt
    mask_bin = cv2.morphologyEx((mask > 0).astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones(morph_kernel, np.uint8))

    # Zusammenhaengende Flaechen (Label 0 = Hintergrund)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask_bin, connectivity=8)
    if num <= 1:
        return [], np.zeros_like(mask_bin)

    # n groesste Flaechen, zu kleine verwerfen
    order = np.argsort(stats[1:, cv2.CC_STAT_AREA])[::-1][:n_lanes] + 1
    order = order[stats[order, cv2.CC_STAT_AREA] >= min_area]

    h = mask_bin.shape[0]
    y_min = int((1.0 - fit_frac) * h)   # nur unteren Bildanteil fuer den Fit nutzen (fit_frac=1.0 -> ganzes Bild)
    lanes, x_bottom = [], []
    for lbl in order:
        ys, xs = np.nonzero(labels == lbl)
        keep = ys >= y_min
        ys, xs = ys[keep], xs[keep]
        if ys.size < 2 or np.ptp(ys) < 2:   # zu wenig Punkte oder horizontale Flaeche
            continue

        # Fit x = a*y + c (stabil auch bei fast senkrechten Linien)
        a, c = np.polyfit(ys, xs, 1)
        m = float(1.0 / a) if abs(a) > 1.0 / m_max else float(np.copysign(m_max, a))
        b = float(ys.mean() - m * xs.mean())   # Gerade geht durch den Schwerpunkt der Flaeche

        lanes.append((m, b))
        x_bottom.append(a * (h - 1) + c)

    lanes = [lanes[i] for i in np.argsort(x_bottom)]   # links -> rechts ueber x am unteren Bildrand
    sel_mask = np.isin(labels, order).astype(np.uint8) * 255
    return lanes, sel_mask


def cals_drive_parameter_from_lanes(img, lanes) -> tuple[int, np.ndarray]:

    img_with_steering_lane = img.copy()
    if img_with_steering_lane.ndim == 2:
        img_with_steering_lane = cv2.cvtColor(img_with_steering_lane, cv2.COLOR_GRAY2RGB)

    h, w = img_with_steering_lane.shape[:2]

    
    if lanes is not None and len(lanes) == 2 and all(lane is not None for lane in lanes):
        y_bottom = h - 1
        y_top = 0
        x_bottom_arr = np.zeros(2)
        x_top_arr = np.zeros(2)
        for i, lane in enumerate(lanes):
            m, b = lane

            x1 = 0
            x2 = w - 1
            y1 = int(np.clip(m * x1 + b, -1e6, 1e6))
            y2 = int(np.clip(m * x2 + b, -1e6, 1e6))

            cv2.line(img_with_steering_lane, (x1, y1), (x2, y2), (0, 255, 0), 2)

            m_safe = m if abs(m) > 1e-6 else 1e-6
            x_bottom_arr[i] = (y_bottom - b) / m_safe
            x_top_arr[i] = (y_top - b) / m_safe

        x_steer_start = int(np.clip(np.mean(x_bottom_arr), -1e6, 1e6))
        x_steer_end = int(np.clip(np.mean(x_top_arr), -1e6, 1e6))

        Delta_x = x_steer_end - x_steer_start
        Delta_y = y_top - y_bottom

        slope_degree = 180 + np.degrees(np.arctan2(Delta_y, Delta_x))

        text = f"{slope_degree:.1f}"
        org = (20, 20)  # Position (x, y) - untere linke Ecke des Textes
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        color = (255, 0, 0)  # Farbe im BGR-Format (Grün)
        thickness = 2

        # Text ins Bild einfügen
        cv2.putText(img_with_steering_lane, text, org, font, font_scale, color, thickness, cv2.LINE_AA)

        cv2.line(img_with_steering_lane, (x_steer_start, y_bottom), (x_steer_end, y_top), (0, 0, 255), 2)
        
    else:
        raise ValueError('number of lines <2')

    steering_angle = slope_degree

    return steering_angle, img_with_steering_lane

def lenkwinkel_from_image(img)-> tuple[int, np.ndarray]:
# NEU: Flaechen der Maske -> zwei groesste Flaechen -> Geradengleichungen (m, b)
    lanes, img_areas = detect_lanes_from_areas(img, n_lanes=2, min_area=150, fit_frac=1.0)

    # Plotte die Maxima Linien ins Bild
    img_with_lines = put_steering_lines_into_image(img_areas, lanes)
    
    # Berechne Fahrparameter aus lanes
    return cals_drive_parameter_from_lanes(img_with_lines, lanes)