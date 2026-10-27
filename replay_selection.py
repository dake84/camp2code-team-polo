"""Frame-ID-basierte Bereichsauswahl für den flüchtigen Replay-Puffer."""

def selected_bounds(relayout):
    """Liest eine Plotly-X-Achsenauswahl ohne Annahmen über andere Achsen."""
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
    import math
    if not math.isfinite(a) or not math.isfinite(b):
        return None
    return min(a, b), max(a, b)


def select_records(records, bounds):
    """Filtert ausschließlich vorhandene Frames und behält ihre Reihenfolge."""
    if bounds is None:
        return tuple(records)
    lo, hi = bounds
    return tuple(record for record in records if lo <= record.camera.frame_id <= hi)
