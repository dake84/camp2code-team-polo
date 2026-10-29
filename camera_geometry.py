"""Geometrie fuer getrennte Aufnahme- und Verarbeitungsgroesse (PAKET-H3).

Begriffe:
    Aufnahmebild      grosses Bild ("main"): wird aufgezeichnet und angezeigt.
    Verarbeitungsbild kleines Bild ("lores" der Kamera oder per Software verkleinert): wird vom
                      Bildprozessor ausgewertet. Alle Linien (``CameraData.lanes``, ``DrivingState.lanes``,
                      ``frames.csv``) gelten in DIESEM Bild.

Linienmodell (unveraendert): ``(m, b)`` mit ``y = m*x + b``, Pixel im ROI-Bild, Ursprung oben links des ROI.
Vollbild: ``y_voll = y + roi_top`` mit ``roi_top = int(hoehe * (1 - crop_oben))``.

Dieses Modul enthaelt nur reine Funktionen (kein Dash, keine Hardware). Gespeicherte Werte werden nie veraendert:
alle Umrechnungen liefern neue Tupel.
"""
import math

import cv2

MIN_PROCESS_SIDE = 32          # kleinere Verarbeitungsbilder sind fuer die Linienerkennung nicht sinnvoll
ASPECT_TOLERANCE = 0.02        # zulaessige Abweichung des Seitenverhaeltnisses (Rundung auf gerade Seiten)

# Bezugsgroesse der Ringbuffer-Laenge: bisher fest 60 Frames bei 320x240 (Aufnahme = Verarbeitung)
REFERENCE_SIZE = (320, 240)
RING_DEFAULT = 60
RING_MIN = 10
# Schaetzung des Speichers je Frame: Rohbild (3 Byte/Pixel) plus Diagnosebilder des Verarbeitungsbilds
# (HSV 3 Byte + fuenf Einkanal-Stufen = 8 Byte je Pixel; obere Schranke, der ROI ist kleiner). Annahme dieses Pakets.
STAGE_BYTES_PER_PIXEL = 8

UI_BASE_HEIGHT = 240           # bei dieser Bildhoehe gelten die bisherigen Schrift- und Linienstaerken


def as_size(value):
    """``(breite, hoehe)`` als int-Tupel oder ``None`` (ungueltig, nicht positiv, nicht endlich)."""
    try:
        width, height = value
        if isinstance(width, bool) or isinstance(height, bool):
            return None
        width, height = float(width), float(height)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(width) and math.isfinite(height)) or width < 1 or height < 1:
        return None
    return int(width), int(height)


def _even(value):
    value = int(round(value))
    return value if value % 2 == 0 else value + 1


def resolve_process_size(capture_size, process_width=0, process_height=0):
    """Verarbeitungsgroesse aus der Konfiguration: ``(groesse oder None, Hinweis oder None)``.

    * Keine Angabe (0/None) -> ``None``: es wird auf dem Aufnahmebild verarbeitet (Verhalten wie bisher).
    * Nur eine Seite angegeben -> die andere folgt aus dem Seitenverhaeltnis (gerade Zahl).
    * Beide angegeben -> muessen zum Seitenverhaeltnis der Aufnahme passen (Toleranz 2 %).
    * Nie groesser als die Aufnahme; gleiche Groesse -> ``None``; fehlerhafte Werte -> ``None`` mit Hinweis.
    Der Hinweis ist ein Satz fuer das Log (nur gesetzt, wenn die Angabe nicht uebernommen wurde).
    """
    capture = as_size(capture_size)
    if capture is None:
        return None, 'Aufnahmegroesse unbekannt - Verarbeitung auf dem Aufnahmebild'
    cw, ch = capture
    pw = int(process_width or 0)
    ph = int(process_height or 0)
    if pw <= 0 and ph <= 0:
        return None, None
    if pw < 0 or ph < 0:
        return None, 'process_width/process_height muessen positiv sein - Verarbeitung auf dem Aufnahmebild'
    if pw > 0 and ph <= 0:
        ph = _even(pw * ch / cw)
    elif ph > 0 and pw <= 0:
        pw = _even(ph * cw / ch)
    if pw < MIN_PROCESS_SIDE or ph < MIN_PROCESS_SIDE:
        return None, (f'Verarbeitungsgroesse {pw}x{ph} kleiner als {MIN_PROCESS_SIDE} Pixel '
                      '- Verarbeitung auf dem Aufnahmebild')
    if pw > cw or ph > ch:
        return None, (f'Verarbeitungsgroesse {pw}x{ph} groesser als die Aufnahme {cw}x{ch} '
                      '- Verarbeitung auf dem Aufnahmebild')
    if abs(pw / ph - cw / ch) / (cw / ch) > ASPECT_TOLERANCE:
        return None, (f'Verarbeitungsgroesse {pw}x{ph} hat nicht das Seitenverhaeltnis der Aufnahme {cw}x{ch} '
                      '- Verarbeitung auf dem Aufnahmebild')
    if (pw, ph) == (cw, ch):
        return None, None
    return (pw, ph), None


def roi_top_px(height, crop):
    """Obere ROI-Zeile in einem Bild der Hoehe ``height`` (identisch zu ``CameraData.roi_rows``)."""
    try:
        high = float(crop[1])
    except (TypeError, ValueError, IndexError):
        high = 0.9
    return max(0, min(int(height), int(height * (1 - high))))


def convert_lanes(lanes, src_size, dst_size, src_top=0, dst_top=0):
    """Rechnet Linien ``y = m*x + b`` (ROI-Koordinaten) vom Bild ``src_size`` in das Bild ``dst_size`` um.

    ``src_top``/``dst_top`` sind die oberen ROI-Zeilen im jeweiligen Bild. Herleitung (sx = dst_w/src_w,
    sy = dst_h/src_h): ``y_dst_voll = sy * (m * x_dst / sx + b + src_top)``, abzueglich ``dst_top`` ergibt
    ``m' = m * sy / sx`` und ``b' = sy * b + sy * src_top - dst_top``.

    ``None`` und nicht endliche Linien bleiben ``None``. Das Ergebnis ist ein neues Tupel; ``lanes`` wird nicht
    veraendert. Gleiche Groesse und gleicher ROI-Versatz liefern die Linien unveraendert.
    """
    src, dst = as_size(src_size), as_size(dst_size)
    if src is None or dst is None:
        return tuple(lanes or ())
    sx, sy = dst[0] / src[0], dst[1] / src[1]
    result = []
    for lane in lanes or ():
        if lane is None:
            result.append(None)
            continue
        try:
            m, b = float(lane[0]), float(lane[1])
        except (TypeError, ValueError, IndexError):
            result.append(None)
            continue
        if not (math.isfinite(m) and math.isfinite(b)):
            result.append(None)
            continue
        result.append((m * sy / sx, sy * b + sy * src_top - dst_top))
    return tuple(result)


def downscale_image(image, size):
    """Software-Verkleinern (INTER_AREA) auf ``size``; nie hochskalieren. Ohne Groesse: das Bild selbst."""
    target = as_size(size)
    if target is None or image is None or getattr(image, 'size', 0) == 0:
        return image
    height, width = image.shape[:2]
    if target == (width, height) or target[0] > width or target[1] > height:
        return image
    return cv2.resize(image, target, interpolation=cv2.INTER_AREA)


def ui_scale(height):
    """Faktor fuer Schrift und Linienstaerke: 1.0 bis 240 Pixel Bildhoehe (bisheriges Aussehen), darueber linear."""
    try:
        return max(1.0, float(height) / UI_BASE_HEIGHT)
    except (TypeError, ValueError):
        return 1.0


def frame_bytes_estimate(main_size, process_size=None):
    """Geschaetzter Speicher eines Ringbuffer-Frames in Byte (Rohbild + Diagnosebilder des Verarbeitungsbilds)."""
    main = as_size(main_size) or REFERENCE_SIZE
    small = as_size(process_size) or main
    return main[0] * main[1] * 3 + small[0] * small[1] * STAGE_BYTES_PER_PIXEL


def ring_length(main_size, process_size=None, default=RING_DEFAULT, minimum=RING_MIN):
    """Ringbuffer-Laenge fuer ein festes RAM-Budget.

    Budget = ``default`` Frames bei 320x240 (Aufnahme = Verarbeitung), also der bisherige Wert. Bei kleinen oder
    gleich grossen Bildern bleibt es bei ``default`` (nie mehr), bei groesseren Bildern sinkt die Laenge bis
    ``minimum``. Der Aufnahme-Thread (SessionRecorder) und das Replay Lab brauchen nur wenige Frames Vorlauf.
    """
    budget = default * frame_bytes_estimate(REFERENCE_SIZE, REFERENCE_SIZE)
    count = int(budget // frame_bytes_estimate(main_size, process_size))
    return max(int(minimum), min(int(default), count))
