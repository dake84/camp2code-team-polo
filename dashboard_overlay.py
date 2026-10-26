"""Zeichnet das Fahrspur-Overlay ausschliesslich fuer das Dashboard."""
import cv2
import numpy as np


def render_overlay(camera, driving, processed=False):
    """Zeigt ROI, erkannte Geraden und Lenkwinkel auf einer Rohbild-Kopie.

    Die Linien sind im Koordinatensystem des zugeschnittenen Bildes definiert.
    Deshalb wird die y-Koordinate um den oberen ROI-Rand verschoben.
    """
    image = camera.process_image if processed else camera.raw_image
    if image is None or image.size == 0:
        return None
    if camera.frame_id != driving.frame_id:
        return None
    result = image.copy()
    if result.ndim == 2:
        result = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    h, w = result.shape[:2]
    if processed:
        # Das vorverarbeitete Bild ist bereits auf den ROI zugeschnitten.
        # Seine Linienkoordinaten brauchen daher keinen Versatz.
        top = 0
    else:
        crop = getattr(camera, 'crop', (0.1, 0.9))
        low, high = crop
        top = max(0, min(h, int(h * (1 - high))))
        bottom = max(top, min(h, int(h * (1 - low))))
        cv2.rectangle(result, (0, top), (w - 1, max(top, bottom - 1)), (255, 200, 0), 1)
    for lane in (driving.lanes if driving.lanes is not None else []):
        if lane is None or len(lane) != 2:
            continue
        m, b = lane
        if not np.isfinite(m) or not np.isfinite(b):
            continue
        # Die Liniengleichung lautet y = m*x + b (bezogen auf den ROI).
        y0 = int(np.clip(float(b) + top, -100000, 100000))
        y1 = int(np.clip(float(m) * (w - 1) + float(b) + top, -100000, 100000))
        cv2.line(result, (0, y0), (w - 1, y1), (0, 255, 0), 2)
    # Die Anzeige ist keine neue Fahrentscheidung.
    cv2.putText(result, f'Frame {camera.frame_id}  Winkel {driving.steering_angle:.0f}  '
                f'Speed {driving.speed:.0f}', (8, 22), cv2.FONT_HERSHEY_SIMPLEX,
                0.48, (255, 255, 255), 2, cv2.LINE_AA)
    return result


def render_calibration_overlay(camera, processed=False):
    """Zeigt erkannte Linien auch ohne gestarteten Fahrmodus an.

    Das Dashboard berechnet keine Linien selbst, sondern zeichnet nur
    die bereits im Kamerapaket enthaltenen Parameter.
    """
    if camera is None:
        return None
    source = camera.process_image if processed else camera.raw_image
    if source is None or source.size == 0:
        return None
    from types import SimpleNamespace
    preview = SimpleNamespace(
        frame_id=camera.frame_id,
        lanes=camera.lanes,
        steering_angle=90.0,
        speed=0.0,
    )
    image = render_overlay(camera, preview, processed=processed)
    if image is not None:
        cv2.putText(image, 'KALIBRIERUNG - FAHRMODUS AUS',
                    (8, max(45, image.shape[0] - 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 2, cv2.LINE_AA)
    return image
