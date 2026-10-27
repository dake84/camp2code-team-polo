"""Frame-ID-basierte Bereichsauswahl fuer Replay (Ringbuffer und Sessions)."""
import math


def selected_bounds(relayout):
    """Liest eine Plotly-X-Achsenauswahl ohne Annahmen ueber andere Achsen."""
    if not isinstance(relayout, dict):
        return None
    if relayout.get('xaxis.autorange'):
        return None
    values = relayout.get('xaxis.range')
    if values is None:
        values = (relayout.get('xaxis.range[0]'), relayout.get('xaxis.range[1]'))
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        return None
    try:
        a, b = float(values[0]), float(values[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a) or not math.isfinite(b):
        return None
    return min(a, b), max(a, b)


def record_frame_id(record):
    """Frame-ID eines FrameRecords oder einer CSV-Zeile (dict); ``None`` falls unbekannt."""
    if isinstance(record, dict):
        try:
            return int(record['frame_id'])
        except (KeyError, TypeError, ValueError):
            return None
    return record.camera.frame_id


def select_records(records, bounds, key=record_frame_id):
    """Filtert ausschliesslich vorhandene Frames und behaelt ihre Reihenfolge."""
    if bounds is None:
        return tuple(records)
    lo, hi = bounds
    selected = []
    for record in records:
        frame_id = key(record)
        if frame_id is not None and lo <= frame_id <= hi:
            selected.append(record)
    return tuple(selected)


def event_window(frame_id, before, after):
    """Vor-/Nachlauf um ein Ereignis als Frame-ID-Bereich (nicht negativ)."""
    before = max(0, int(before or 0))
    after = max(0, int(after or 0))
    return max(0, int(frame_id) - before), int(frame_id) + after
