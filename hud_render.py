"""HUD-Stile "Jetpilot" und "Arcade" (PAKET-H3B).

Gezeichnet wird ausschliesslich auf einer Kopie des Bildes und nur aus vorhandenen Werten (Stellwert, Soll, Roh,
Steuer-Speed, Zustand, Linien, Kamerawerte). Es werden keine Linien erkannt und keine Fahrentscheidungen berechnet.

``draw(style, image, info, trail)`` bekommt die Werte als ``info``-dict von ``replay_hud.draw_hud`` (dort werden Linien
bereits auf das grosse Bild umgerechnet, Texte sind ASCII):

    frame_id, lanes (Liste von (m, b) oder None; y = m*x + b im ROI des Bildes), geometry (ROI bekannt), top, bottom,
    stell, soll, roh, speed, label, kind, color (BGR des Zustands), mode_name, events, footer,
    exposure_us, gain, lux, frame_duration_us (jeweils None = unbekannt, nie geraten).

Alle Masse sind auf eine Referenzhoehe von 720 Pixeln entworfen und wachsen bzw. schrumpfen mit der Bildhoehe.
Bilder unter 420 Pixeln Hoehe ("kompakt") zeigen nur die wichtigsten Elemente.

Text-Kontur: acht verschobene Kopien mit derselben Strichstaerke wie die Fuellung (wie ``dashboard_overlay.put_text``);
eine dickere schwarze Schrift hatte auf dem Pi einen anderen Zeichenabstand als die Fuellung.

``TRAIL`` sammelt beim Anzeigen im Cockpit einen kurzen Verlauf (nur live) fuer "Lenkspur" und "Spur-Streak".
"""
import math
import threading
import time
from collections import deque

import cv2
import numpy as np

STYLES = ('jetpilot', 'arcade')
REF_HEIGHT = 720.0
MIN_SCALE, MAX_SCALE = 0.6, 2.2
COMPACT_HEIGHT = 420
FONT = cv2.FONT_HERSHEY_SIMPLEX
BOLD = cv2.FONT_HERSHEY_DUPLEX
SPEED_FULL_SCALE = 100.0           # Steuer-Speed fuer vollen Tacho-Ausschlag (Anzeige, keine Fahrgrenze)
STREAK_STARS = (5.0, 15.0, 30.0)   # Sekunden fuer 1/2/3 Sterne (frei gewaehlt)
STEER_WINDOW_S = 5.0               # Fenster der Lenkspur
TRAIL_WINDOW_S = 90.0              # laengster Verlauf (und laengste Streak-Anzeige)

# BGR
GREEN, AMBER, RED, CYAN, WHITE = (110, 255, 150), (0, 190, 255), (80, 80, 255), (255, 220, 0), (255, 255, 255)
GRAY = (170, 170, 170)
PANEL = (40, 20, 70)               # Arcade-Tafeln
PINK = (255, 120, 255)
LIGHT_PINK = (255, 200, 255)
YELLOW = (80, 255, 255)
GOLD = (0, 210, 255)
ARCADE_KIND = {'stopped': (60, 60, 230), 'recovery': (0, 140, 255), 'manual': (0, 190, 230), 'auto': (90, 200, 70),
               'auto_missing': (0, 190, 230), 'idle': (130, 130, 130), 'none': (110, 110, 110)}
_OFFSETS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


# ------------------------------------------------------------------ Verlauf (nur live)
class HudTrail:
    """Kurzer Verlauf (Frame, Zeit, Stell, Soll, Linienzahl) fuer Lenkspur und Spur-Streak. Thread-sicher."""

    def __init__(self, window_s=TRAIL_WINDOW_S, maxlen=2000):
        self._lock = threading.Lock()
        self._items = deque(maxlen=maxlen)
        self._window = float(window_s)

    def add(self, frame_id, stell, soll, lane_count, now=None):
        """Eintrag anhaengen; derselbe Frame nur einmal, sinkende Frame-ID (Neustart) leert den Verlauf."""
        try:
            frame = int(float(frame_id))
        except (TypeError, ValueError):
            return False
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            if self._items:
                last = self._items[-1][0]
                if frame == last:
                    return False
                if frame < last:
                    self._items.clear()
            self._items.append((frame, now, stell, soll, int(lane_count)))
            while self._items and now - self._items[0][1] > self._window:
                self._items.popleft()
        return True

    def snapshot(self):
        with self._lock:
            return list(self._items)

    def clear(self):
        with self._lock:
            self._items.clear()


def streak_seconds(items):
    """Sekunden seit dem letzten Eintrag mit weniger als zwei Linien (oder seit dem ersten Eintrag); None ohne Verlauf."""
    if len(items) < 2:
        return None
    last_time = items[-1][1]
    for item in reversed(items):
        if item[4] < 2:
            return max(0.0, last_time - item[1])
    return max(0.0, last_time - items[0][1])


def stars_for(seconds):
    return 0 if seconds is None else sum(seconds >= limit for limit in STREAK_STARS)


def recent(items, seconds=STEER_WINDOW_S):
    """Eintraege der letzten ``seconds`` Sekunden (bezogen auf den neuesten Eintrag)."""
    if not items:
        return []
    start = items[-1][1] - seconds
    return [item for item in items if item[1] >= start]


TRAIL = HudTrail()


# ------------------------------------------------------------------ Zeichenwerkzeug
def _safe(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def _signed(value):
    value = int(round(value))
    return '0' if value == 0 else f'{value:+d}'


def _bgr_copy(image):
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image.copy()


def _clamp_pt(point):
    return int(max(-1e6, min(1e6, point[0]))), int(max(-1e6, min(1e6, point[1])))


class _Canvas:
    """Zeichenflaeche mit Skalierung; alle Masse in Referenzpixeln (Hoehe 720)."""

    def __init__(self, image):
        self.img = image
        self.h, self.w = image.shape[:2]
        self.s = min(MAX_SCALE, max(MIN_SCALE, self.h / REF_HEIGHT))
        self.compact = self.h < COMPACT_HEIGHT

    def px(self, value):
        return int(round(value * self.s))

    def th(self, value=1):
        return max(1, int(round(value * self.s)))

    def tw(self, text, scale, th=1, font=FONT):
        return cv2.getTextSize(_safe(text), font, scale * self.s, self.th(th))[0][0]

    def text(self, text, org, scale=0.6, color=WHITE, th=1, font=FONT):
        text = _safe(text)
        thickness = self.th(th)
        step = max(1, int(round(self.s)))
        x, y = int(org[0]), int(org[1])
        for dx, dy in _OFFSETS:
            cv2.putText(self.img, text, (x + dx * step, y + dy * step), font, scale * self.s, (0, 0, 0), thickness,
                        cv2.LINE_AA)
        cv2.putText(self.img, text, (x, y), font, scale * self.s, color, thickness, cv2.LINE_AA)

    def ctext(self, text, cx, y, scale=0.6, color=WHITE, th=1, font=FONT):
        self.text(text, (int(cx - self.tw(text, scale, th, font) // 2), y), scale, color, th, font)

    def fit(self, texts, scale, avail, th=1, font=FONT, gap=0):
        """Schriftgroesse so verkleinern, dass alle ``texts`` samt ``gap`` in ``avail`` Pixel passen."""
        while scale > 0.25:
            total = sum(self.tw(t, scale, th, font) for t in texts) + gap * (len(texts) - 1)
            if total <= avail:
                break
            scale *= 0.92
        return scale

    def line(self, p, q, color, th=1):
        p, q = _clamp_pt(p), _clamp_pt(q)
        cv2.line(self.img, p, q, (0, 0, 0), self.th(th) + max(2, self.px(2)), cv2.LINE_AA)
        cv2.line(self.img, p, q, color, self.th(th), cv2.LINE_AA)

    def dashed(self, p, q, color, th=1, dash=14, gap=8):
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        if length < 1:
            return
        step = max(2.0, self.px(dash + gap))
        on = max(1.0, self.px(dash))
        t = 0.0
        while t < length:
            a, b = t / length, min(length, t + on) / length
            self.line((p[0] + (q[0] - p[0]) * a, p[1] + (q[1] - p[1]) * a),
                      (p[0] + (q[0] - p[0]) * b, p[1] + (q[1] - p[1]) * b), color, th)
            t += step

    def polyline(self, points, color, th, outline=True, closed=False):
        pts = np.array([_clamp_pt(p) for p in points], np.int32)
        if len(pts) < 2:
            return
        if outline:
            cv2.polylines(self.img, [pts], closed, (0, 0, 0), self.th(th) + max(2, self.px(3)), cv2.LINE_AA)
        cv2.polylines(self.img, [pts], closed, color, self.th(th), cv2.LINE_AA)

    def panel(self, x0, y0, x1, y1, color, alpha=0.5, r=12):
        """Halbtransparente Tafel mit runden Ecken; nur der betroffene Bildausschnitt wird gemischt."""
        x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(self.w, int(x1)), min(self.h, int(y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        roi = self.img[y0:y1, x0:x1]
        hh, ww = roi.shape[:2]
        r = max(0, min(int(r), hh // 2, ww // 2))
        mask = np.zeros((hh, ww), np.uint8)
        cv2.rectangle(mask, (r, 0), (ww - 1 - r, hh - 1), 255, -1)
        cv2.rectangle(mask, (0, r), (ww - 1, hh - 1 - r), 255, -1)
        for cx, cy in ((r, r), (ww - 1 - r, r), (r, hh - 1 - r), (ww - 1 - r, hh - 1 - r)):
            cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
        tint = np.empty_like(roi)
        tint[:] = color
        blended = cv2.addWeighted(roi, 1 - alpha, tint, alpha, 0)
        cv2.copyTo(blended, mask, roi)                        # direkt in den Bildausschnitt (viel schneller als Masken-Indizierung)

    def frame_box(self, x0, y0, x1, y1, color, th=1):
        cv2.rectangle(self.img, (int(x0), int(y0)), (int(x1), int(y1)), color, self.th(th), cv2.LINE_AA)


def _lane_segment(lane, top, bottom, width, height):
    """Endpunkte der Linie ``y = m*x + b`` (ROI-Koordinaten) zwischen oberer und unterer ROI-Zeile, auf das Bild
    beschnitten; None bei fehlender, nicht endlicher oder waagerechter Linie."""
    if lane is None:
        return None
    try:
        m, b = float(lane[0]), float(lane[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(m) and math.isfinite(b)) or abs(m) < 1e-6:
        return None
    p = _clamp_pt(((0 - b) / m, top))
    q = _clamp_pt(((bottom - top - b) / m, bottom))
    inside, p, q = cv2.clipLine((0, 0, width, height), p, q)
    return (p, q) if inside else None


def _lane_x_at(lane, y, top):
    """x der Linie in der Bildzeile ``y`` (Vollbild) oder None."""
    try:
        m, b = float(lane[0]), float(lane[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(m) and math.isfinite(b)) or abs(m) < 1e-6:
        return None
    return (y - top - b) / m


def _valid_lanes(info):
    return [lane for lane in (info.get('lanes') or ()) if lane is not None]


def _cam_parts(info):
    parts = []
    if info.get('exposure_us') is not None:
        parts.append(f"EXP {info['exposure_us'] / 1000:.1f}ms")
    if info.get('gain') is not None:
        parts.append(f"GAIN {info['gain']:.1f}")
    if info.get('lux') is not None:
        parts.append(f"LUX {info['lux']:.0f}")
    return parts


def _cam_rate(info):
    duration = info.get('frame_duration_us')
    return f'CAM {1e6 / duration:.1f} FPS' if duration else None


def _bars(c, info):
    """Berechnet die Grundlinie ueber Ereignis- und Kennzeichnungsleiste; gibt (Grundlinie, Zeichenfunktion) zurueck."""
    footer, events = info.get('footer') or '', info.get('events') or ''
    footer_h = c.px(30) if footer else 0
    event_h = c.px(36) if events else 0
    base = c.h - footer_h - event_h

    def paint():
        if events:
            y1 = c.h - footer_h
            cv2.rectangle(c.img, (0, y1 - event_h), (c.w, y1), (15, 30, 100), -1)
            c.text('EVENT: ' + events, (c.px(12), y1 - c.px(11)), 0.5, WHITE)
        if footer:
            cv2.rectangle(c.img, (0, c.h - footer_h), (c.w, c.h), (40, 28, 12), -1)
            c.text(footer, (c.px(12), c.h - c.px(9)), 0.5, (0, 255, 255))
    return base, paint


# ------------------------------------------------------------------ Stil A: Jetpilot
def _jetpilot(c, info, base, trail):
    w, h, s = c.w, c.h, c.s
    cx = w // 2
    top, bottom, geometry = info.get('top'), info.get('bottom'), bool(info.get('geometry'))
    stell, soll, roh, speed = info.get('stell'), info.get('soll'), info.get('roh'), info.get('speed')
    kind, state_color = info.get('kind'), tuple(info.get('color') or WHITE)
    lanes = _valid_lanes(info)

    if geometry:                                              # ROI als Eckklammern, Linien gestrichelt
        arm = c.px(46)
        for x, y, dx, dy in ((c.px(8), top, 1, 1), (w - c.px(8), top, -1, 1),
                             (c.px(8), bottom, 1, -1), (w - c.px(8), bottom, -1, -1)):
            c.line((x, y), (x + arm * dx, y), CYAN, 2)
            c.line((x, y), (x, y + arm * dy), CYAN, 2)
        for lane in info.get('lanes') or ():
            segment = _lane_segment(lane, top, bottom, w, h)
            if segment:
                c.dashed(segment[0], segment[1], GREEN, 2)

    # Kompassband: Mitte = Stellwert; die Enden 45 und 135 sind die Lenkgrenzen
    center = stell if stell is not None else 90.0
    half = min(c.px(270), int(w * 0.42))
    ppd = half / 45.0
    y0 = c.px(62)
    c.line((cx - half, y0), (cx + half, y0), GREEN, 1)
    for angle in range(45, 136, 5):
        x = int(cx + (angle - center) * ppd)
        if abs(x - cx) > half:
            continue
        major = angle % 15 == 0
        c.line((x, y0), (x, y0 - c.px(16 if major else 8)), GREEN, 1)
        if major:
            c.ctext(str(angle), x, y0 - c.px(22), 0.5, GREEN)
    c.panel(cx - c.px(34), c.px(8), cx + c.px(34), c.px(36), (0, 0, 0), 0.35, c.px(6))
    c.frame_box(cx - c.px(34), c.px(8), cx + c.px(34), c.px(36), GREEN)
    c.ctext(f'{stell:.0f}' if stell is not None else '--', cx, c.px(30), 0.8, GREEN, 2, BOLD)
    c.line((cx, y0 + c.px(2)), (cx - c.px(7), y0 + c.px(14)), GREEN, 2)
    c.line((cx, y0 + c.px(2)), (cx + c.px(7), y0 + c.px(14)), GREEN, 2)
    if soll is not None and stell is not None and abs(soll - stell) >= 1:      # Soll wie im klassischen HUD nur bei Abweichung
        xs = int(max(cx - half, min(cx + half, cx + (soll - center) * ppd)))
        d = c.px(7)
        c.polyline([(xs, y0 + c.px(6)), (xs + d, y0 + c.px(14)), (xs, y0 + c.px(22)), (xs - d, y0 + c.px(14))],
                   AMBER, 2, closed=True)
        c.text(f'SOLL {soll:.0f}', (max(c.px(8), cx - c.px(150)), c.px(102)), 0.5, AMBER)
    if roh is not None:
        xr = int(max(cx - half, min(cx + half, cx + (roh - center) * ppd)))
        cv2.circle(c.img, (xr, y0 + c.px(14)), c.px(4) + 1, (0, 0, 0), -1, cv2.LINE_AA)
        cv2.circle(c.img, (xr, y0 + c.px(14)), c.px(3), WHITE, -1, cv2.LINE_AA)
        c.text(f'ROH {roh:.0f}', (cx + c.px(70), c.px(102)), 0.5, WHITE)

    if not c.compact:
        c.text(f"F {info.get('frame_id', '?')}", (c.px(24), c.px(30)), 0.5, GREEN)
        lx = c.px(96)                                         # Speed-Leiter links
        cy = int(h * 0.55)
        c.text('SPEED', (c.px(34), cy - c.px(168)), 0.5, GREEN)
        if speed is not None:
            c.line((lx, cy - c.px(150)), (lx, cy + c.px(150)), GREEN, 1)
            ppu = 7 * s
            first = int(math.floor(speed - 23))
            for v in range(first - first % 5, int(speed + 24), 5):
                y = int(cy - (v - speed) * ppu)
                if abs(y - cy) > c.px(150):
                    continue
                c.line((lx, y), (lx - c.px(14 if v % 10 == 0 else 7), y), GREEN, 1)
                if v % 10 == 0:
                    c.text(str(v), (c.px(34), y + c.px(5)), 0.5, GREEN)
            c.panel(lx + c.px(4), cy - c.px(15), lx + c.px(74), cy + c.px(15), (0, 0, 0), 0.35, c.px(5))
            c.frame_box(lx + c.px(4), cy - c.px(15), lx + c.px(74), cy + c.px(15), GREEN)
            c.ctext(_signed(speed), lx + c.px(39), cy + c.px(8), 0.8, GREEN, 2, BOLD)
        else:
            c.text('--', (lx - c.px(10), cy), 0.8, GRAY, 2, BOLD)

        # Spur-Lock rechts: Anzahl erkannter Linien (aus den Daten)
        count = len(lanes)
        driving = kind in ('auto', 'auto_missing', 'stopped')
        if not geometry and count:
            lock, lock_color = 'ROI ?', AMBER
        elif count >= 2:
            lock, lock_color = 'LOCK 2/2', GREEN
        elif count == 1:
            lock, lock_color = 'LOCK 1/2', AMBER
        else:
            lock, lock_color = 'NO LOCK', RED if driving else AMBER
        c.panel(w - c.px(190), c.px(190), w - c.px(20), c.px(262), (0, 0, 0), 0.35, c.px(8))
        c.text('SPUR', (w - c.px(176), c.px(218)), 0.6, GREEN)
        c.text(lock, (w - c.px(176), c.px(248)), 0.8, lock_color, 2, BOLD)

    # Flugbahn-Marker (Pipper): Lenkrichtung auf Hoehe der ROI-Mitte
    if stell is not None:
        py = int((top + bottom) / 2) if geometry else int(h * 0.65)
        pxx = int(max(c.px(40), min(w - c.px(40), cx + (stell - 90) * 8 * s)))
        r = c.px(15)
        cv2.circle(c.img, (pxx, py), r, (0, 0, 0), c.th(2) + 3, cv2.LINE_AA)
        cv2.circle(c.img, (pxx, py), r, GREEN, c.th(2), cv2.LINE_AA)
        for a, b in (((pxx - r, py), (pxx - r - c.px(19), py)), ((pxx + r, py), (pxx + r + c.px(19), py)),
                     ((pxx, py - r), (pxx, py - r - c.px(13)))):
            c.line(a, b, GREEN, 2)

    # Unten: Modus + Zustand (Zustandsfarbe), Kamerawerte, Takt
    name, label = info.get('mode_name') or '', info.get('label') or ''
    texts = [t for t in (name, label) if t]
    scale = c.fit(texts, 0.75, w * 0.9 - c.px(40), 2, BOLD, gap=c.px(28))
    widths = [c.tw(t, scale, 2, BOLD) for t in texts]
    box_w = sum(widths) + c.px(28) * max(0, len(texts) - 1) + c.px(28)
    x0 = cx - box_w // 2
    y1 = base - c.px(18)
    c.panel(x0, y1 - c.px(44), x0 + box_w, y1, (0, 0, 0), 0.4, c.px(8))
    c.frame_box(x0, y1 - c.px(44), x0 + box_w, y1, state_color)
    x = x0 + c.px(14)
    for index, (t, tw) in enumerate(zip(texts, widths)):
        color = GREEN if (name and index == 0) else state_color
        c.text(t, (x, y1 - c.px(14)), scale, color, 2, BOLD)
        x += tw + c.px(28)
    if not c.compact:                                         # Kamerawerte ueber der Zustandsbox (kollidieren nie mit ihr)
        parts = _cam_parts(info)
        if parts:
            c.text('  '.join(parts), (c.px(24), y1 - c.px(58)), 0.5, GREEN)
        rate = _cam_rate(info)
        if rate:
            c.text(rate, (w - c.px(24) - c.tw(rate, 0.5), y1 - c.px(58)), 0.5, GREEN)


# ------------------------------------------------------------------ Stil B: Arcade
def _star(c, center, radius, color, filled):
    points = []
    for i in range(10):
        rad = radius if i % 2 == 0 else radius * 0.45
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((center[0] + rad * math.cos(angle), center[1] + rad * math.sin(angle)))
    pts = np.array(points, np.int32)
    cv2.polylines(c.img, [pts], True, (0, 0, 0), c.th(2) + 2, cv2.LINE_AA)
    if filled:
        cv2.fillPoly(c.img, [pts], color, cv2.LINE_AA)
    cv2.polylines(c.img, [pts], True, color, c.th(2), cv2.LINE_AA)


def _arcade(c, info, base, trail):
    w, h, s = c.w, c.h, c.s
    cx = w // 2
    top, bottom, geometry = info.get('top'), info.get('bottom'), bool(info.get('geometry'))
    stell, soll, speed = info.get('stell'), info.get('soll'), info.get('speed')
    kind = info.get('kind')
    lanes_all = info.get('lanes') or ()
    lanes = _valid_lanes(info)
    band_top, band_bottom = (top, bottom) if geometry else (int(h * 0.45), int(h * 0.85))
    driving = kind in ('auto', 'auto_missing', 'stopped')

    if geometry and band_bottom > band_top:                   # Spur-Teppich (zwei Linien) bzw. Warnton (keine Linie)
        band = c.img[band_top:band_bottom]
        mask = np.zeros(band.shape[:2], np.uint8)
        tint_color = None
        if len(lanes) >= 2:
            mid = (band_top + band_bottom) / 2
            ordered = sorted(lanes[:2], key=lambda lane: _lane_x_at(lane, mid, top) or 0.0)
            xs_top = [_lane_x_at(lane, band_top, top) for lane in ordered]
            xs_bot = [_lane_x_at(lane, band_bottom, top) for lane in ordered]
            if None not in xs_top + xs_bot:
                height_band = band_bottom - band_top
                polygon = np.array([_clamp_pt((xs_top[0], 0)), _clamp_pt((xs_top[1], 0)),
                                    _clamp_pt((xs_bot[1], height_band)), _clamp_pt((xs_bot[0], height_band))], np.int32)
                cv2.fillPoly(mask, [polygon], 255)
                tint_color = (90, 230, 90)
        elif not lanes and driving:
            mask[:] = 255
            tint_color = (60, 60, 230)
        if tint_color is not None:
            x0, y0, bw, bh = cv2.boundingRect(mask)           # nur das Umgebungsrechteck mischen
            if bw > 0 and bh > 0:
                sub = band[y0:y0 + bh, x0:x0 + bw]
                tint = np.empty_like(sub)
                tint[:] = tint_color
                cv2.copyTo(cv2.addWeighted(sub, 0.7, tint, 0.3, 0), mask[y0:y0 + bh, x0:x0 + bw], sub)
        for lane in lanes_all:                                # Linien: schwarz, gruen, weisser Kern
            segment = _lane_segment(lane, top, bottom, w, h)
            if segment:
                cv2.line(c.img, segment[0], segment[1], (0, 0, 0), c.th(6) + 3, cv2.LINE_AA)
                cv2.line(c.img, segment[0], segment[1], (80, 255, 120), c.th(6), cv2.LINE_AA)
                cv2.line(c.img, segment[0], segment[1], WHITE, c.th(2), cv2.LINE_AA)

    # Chevrons in Fahrtrichtung, zur Lenkrichtung geneigt
    lean = ((stell - 90) if stell is not None else 0.0)
    count = 4 if c.compact else 5
    chevron_base = band_bottom if c.compact else min(band_bottom, base - c.px(28 + 150 + 24))   # ueber dem Tacho
    for k in range(count):
        y = int(chevron_base - c.px(20) - k * c.px(42))
        if y < band_top:
            break
        xl = _lane_x_at(lanes[0], y, top) if geometry and len(lanes) >= 2 else None
        xr = _lane_x_at(lanes[1], y, top) if geometry and len(lanes) >= 2 else None
        mid = (xl + xr) / 2 if xl is not None and xr is not None else cx
        mid = max(0, min(w, mid + lean * (1 - k / 6.0) * s))
        half = c.px(46 - k * 7)
        c.polyline([(mid - half, y), (mid, y - c.px(18)), (mid + half, y)], WHITE, 5)

    # Pillen oben: Modus und Zustand
    name, label = info.get('mode_name') or '', info.get('label') or ''
    side = (c.px(300) if not c.compact else c.px(20))
    avail = max(c.px(160), w - 2 * side)
    pad = c.px(22)
    names = [t for t in (name, label) if t]
    pills = [(t, color) for t, color in ((name, (0, 140, 255)), (label, ARCADE_KIND.get(kind, (110, 110, 110)))) if t]
    scale = 0.7
    total = sum(c.tw(t, scale, 2, BOLD) + 2 * pad for t in names) + c.px(20) * max(0, len(names) - 1)
    stacked = total > avail
    if stacked:                                               # untereinander, jede Pille muss einzeln in die Breite passen
        scale = c.fit(names, scale, avail - 2 * pad, 2, BOLD) if len(names) == 1 else min(
            c.fit([t], scale, avail - 2 * pad, 2, BOLD) for t in names)
    else:
        total = sum(c.tw(t, scale, 2, BOLD) + 2 * pad for t in names) + c.px(20) * max(0, len(names) - 1)
    pill_h = c.px(40)
    y_pill = c.px(18)
    x = cx - total // 2
    for t, color in pills:
        pill_w = c.tw(t, scale, 2, BOLD) + 2 * pad
        if stacked:
            x = cx - pill_w // 2
        c.panel(x, y_pill, x + pill_w, y_pill + pill_h, color, 0.88, pill_h // 2)
        c.text(t, (x + pad, y_pill + int(pill_h * 0.68)), scale, WHITE, 2, BOLD)
        if stacked:
            y_pill += pill_h + c.px(8)
        else:
            x += pill_w + c.px(20)

    items = trail.snapshot() if trail is not None else []
    if not c.compact:
        # Streak links oben
        seconds = streak_seconds(items)
        c.panel(c.px(20), c.px(18), c.px(270), c.px(136), PANEL, 0.62, c.px(12))
        c.text('SPUR-STREAK', (c.px(36), c.px(46)), 0.6, LIGHT_PINK)
        c.text(f'{seconds:.1f} s' if seconds is not None else '-- s', (c.px(36), c.px(96)), 1.4, WHITE, 3, BOLD)
        for i in range(3):
            _star(c, (c.px(36) + i * c.px(30), c.px(116)), c.px(11), GOLD, i < stars_for(seconds))
        # Lenkspur rechts oben
        x0, x1, y0, y1 = w - c.px(290), w - c.px(20), c.px(18), c.px(138)
        c.panel(x0, y0, x1, y1, PANEL, 0.62, c.px(12))
        c.text('LENKSPUR 5 s', (x0 + c.px(16), y0 + c.px(26)), 0.55, LIGHT_PINK)
        steer = [i for i in recent(items) if i[2] is not None]
        if len(steer) >= 2:
            t_end = steer[-1][1]
            gx0, gx1, gy0, gy1 = x0 + c.px(16), x1 - c.px(16), y0 + c.px(40), y1 - c.px(12)

            def to_xy(rows, column):
                times = np.array([i[1] for i in rows], float)
                angles = np.clip(np.array([i[column] for i in rows], float), 45.0, 135.0)
                return np.stack([gx0 + (times - (t_end - STEER_WINDOW_S)) / STEER_WINDOW_S * (gx1 - gx0),
                                 gy1 - (angles - 45.0) / 90.0 * (gy1 - gy0)], axis=1)
            cv2.line(c.img, (gx0, (gy0 + gy1) // 2), (gx1, (gy0 + gy1) // 2), (150, 120, 170), 1, cv2.LINE_AA)
            soll_rows = [i for i in steer if i[3] is not None]
            if len(soll_rows) >= 2:
                c.polyline(to_xy(soll_rows, 3), (120, 200, 255), 1, outline=False)
            c.polyline(to_xy(steer, 2), YELLOW, 2, outline=False)
        else:
            c.text('KEIN VERLAUF', (x0 + c.px(16), y0 + c.px(80)), 0.5, GRAY)

        # Tacho unten Mitte (Steuer-Speed)
        radius = c.px(150)
        center = (cx, base - c.px(28))
        frac = 0.0 if speed is None else min(1.0, abs(speed) / SPEED_FULL_SCALE)
        reverse = speed is not None and speed < 0
        cv2.ellipse(c.img, center, (radius, radius), 0, 180, 360, (0, 0, 0), c.th(24), cv2.LINE_AA)
        cv2.ellipse(c.img, center, (radius, radius), 0, 180, 360, (70, 50, 90), c.th(18), cv2.LINE_AA)
        if frac > 0:
            color = (0, 140, 255) if reverse else PINK
            cv2.ellipse(c.img, center, (radius, radius), 0, 180, 180 + int(180 * frac), color, c.th(18), cv2.LINE_AA)
            cv2.ellipse(c.img, center, (radius, radius), 0, 180, 180 + int(180 * frac), WHITE, c.th(3), cv2.LINE_AA)
        c.ctext(_signed(speed) if speed is not None else '--', cx, base - c.px(66), 2.0, WHITE, 4, BOLD)
        c.ctext('STEUER-SPEED', cx, base - c.px(36), 0.55, LIGHT_PINK)

        # Lenkregler unten links: Ist-Knopf, Soll-Ring
        c.panel(c.px(20), base - c.px(100), c.px(330), base - c.px(20), PANEL, 0.62, c.px(12))
        c.text(f'LENKUNG {stell:.0f}' if stell is not None else 'LENKUNG --', (c.px(36), base - c.px(72)), 0.6, WHITE)
        sx0, sx1, sy = c.px(40), c.px(310), base - c.px(40)
        cv2.line(c.img, (sx0, sy), (sx1, sy), (150, 120, 170), c.th(6), cv2.LINE_AA)
        cv2.line(c.img, ((sx0 + sx1) // 2, sy - c.px(10)), ((sx0 + sx1) // 2, sy + c.px(10)), WHITE, c.th(2), cv2.LINE_AA)

        def slider_x(angle):
            return int(sx0 + (max(45.0, min(135.0, angle)) - 45.0) / 90.0 * (sx1 - sx0))
        if soll is not None and stell is not None and abs(soll - stell) >= 1:
            cv2.circle(c.img, (slider_x(soll), sy), c.px(11), WHITE, c.th(2), cv2.LINE_AA)
        if stell is not None:
            cv2.circle(c.img, (slider_x(stell), sy), c.px(10), (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(c.img, (slider_x(stell), sy), c.px(8), YELLOW, -1, cv2.LINE_AA)

        # Kamera unten rechts
        parts, rate = _cam_parts(info), _cam_rate(info)
        if parts or rate:
            c.panel(w - c.px(330), base - c.px(100), w - c.px(20), base - c.px(20), PANEL, 0.62, c.px(12))
            if rate:
                c.text(rate, (w - c.px(314), base - c.px(66)), 0.6, WHITE)
            if parts:
                c.text('  '.join(parts), (w - c.px(314), base - c.px(36)), 0.5, LIGHT_PINK)
    else:
        label_speed = _signed(speed) if speed is not None else '--'
        c.ctext(f'SPEED {label_speed}', cx, base - c.px(14), 0.8, WHITE, 2, BOLD)


def draw(style, image, info, trail=None):
    """Zeichnet den Stil ``jetpilot`` oder ``arcade`` auf eine Kopie von ``image`` und gibt sie zurueck."""
    if style not in STYLES:
        raise ValueError(f'unbekannter HUD-Stil: {style!r}')
    if image is None or getattr(image, 'size', 0) == 0:
        return image
    result = _bgr_copy(image)
    canvas = _Canvas(result)
    base, paint_bars = _bars(canvas, info)
    (_jetpilot if style == 'jetpilot' else _arcade)(canvas, info, base, trail)
    paint_bars()
    return result
