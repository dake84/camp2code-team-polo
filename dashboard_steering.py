"""Visualisiert den Lenkwinkel ohne Veränderung des Kameradatenpakets."""
import math
import cv2


def draw_steering_overlay(image, angle):
    """Zeichnet eine kleine Lenkrichtungsanzeige auf eine Bildkopie."""
    if image is None or getattr(image, 'size', 0) == 0:
        return image
    result = image.copy()
    h, w = result.shape[:2]
    radius = max(15, min(h, w) // 13)
    center = (max(radius + 12, w - radius - 14), radius + 16)
    cv2.circle(result, center, radius + 6, (20, 30, 45), -1, cv2.LINE_AA)
    cv2.circle(result, center, radius, (160, 180, 200), 2, cv2.LINE_AA)
    try:
        degrees = float(angle)
        if not math.isfinite(degrees):
            raise ValueError('Ungültiger Winkel')
        # 90 Grad entsprechen der Neutralstellung. Keine neue Fahrentscheidung.
        delta = math.radians(degrees - 90)
        end = (int(center[0] + radius * .78 * math.sin(delta)),
               int(center[1] - radius * .78 * math.cos(delta)))
        cv2.line(result, center, end, (0, 220, 255), 3, cv2.LINE_AA)
        label = f'{degrees:.0f} deg'
    except (TypeError, ValueError, OverflowError):
        label = 'n/a'
    cv2.putText(result, label, (max(5, center[0] - radius), center[1] + radius + 20),
                cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1, cv2.LINE_AA)
    return result
