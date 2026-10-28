"""Reine Hilfsfunktionen des Control Centers (ohne Dash-Import).

Alles, was Dash-Komponenten braucht, steckt im ``DashboardContext`` (context.py).
Hier stehen nur Funktionen ohne Seiteneffekte: Kodierung, Formatierung, Diagramm-Dicts.
"""
import base64
import contextlib
import threading

import cv2

try:  # optional, fuer CPU- und Speicheranzeige
    import psutil
except ImportError:  # pragma: no cover - abhaengig von der Installation
    psutil = None

LIVE = '__live__'
ACTIVE_PRESET = '__active__'
MODES = [{'label': 'CameraMode (autonom)', 'value': 'camera'},
         {'label': 'PSControllerMode (Gamepad)', 'value': 'gamepad'}]
IMAGE_STYLE = {'width': '100%', 'background': '#0b1220', 'borderRadius': '8px', 'objectFit': 'contain'}

# PAKET-E: Diagramme als einfache dicts. go.Figure validiert jeden Trace in Python;
# auf dem Pi kostete das pro Takt viel CPU und bremste (GIL) den Kamerathread.
CHART_EVERY = 3          # Telemetrie-Diagramme nur jeden 3. Takt (ca. 2 s)
CHART_WINDOW_S = 60      # Zeitfenster der Verlaeufe
AXIS = dict(gridcolor='#2a3550', zerolinecolor='#3a4766', color='#c8d3e6')


def uri(image, quality=70):
    """Kodiert ein Bild als JPEG-Data-URI (eine Kodierung je Bild und Tick)."""
    if image is None or getattr(image, 'size', 0) == 0:
        return None
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii') if ok else None


def dark_figure(figure, **layout):
    """Dunkles Layout ohne layout.height (die feste Hoehe liegt am dcc.Graph, siehe chart_block)."""
    figure.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
                         plot_bgcolor='#111a2b', font={'color': '#e9f1ff'},
                         margin=dict(l=45, r=20, t=25, b=40),
                         legend=dict(orientation='h', y=1.12, x=0), **layout)
    return figure


def fmt(value, digits=0, unit=''):
    """Formatiert Zahlen; fehlende Werte werden als Gedankenstrich angezeigt."""
    if value is None:
        return '–'
    try:
        return f'{float(value):.{digits}f}{unit}'
    except (TypeError, ValueError):
        return str(value)


def diagnostic_text(diagnostic, stop_reason, running):
    """Controllerdiagnose als Klartext; ohne Fahrt als 'letzte Fahrt' gekennzeichnet."""
    if not diagnostic:
        return 'Noch keine Controllerdiagnose'
    lanes = [x for x in diagnostic.get('lanes', ()) if x is not None]
    prefix = 'Aktuell' if running else 'Letzte Fahrt'
    return (f"{prefix} · Frame {diagnostic.get('frame_id')} · Linien: {len(lanes)} · "
            f"Rohwinkel: {fmt(diagnostic.get('raw_angle'))} · "
            f"Vorwert gehalten: {'ja' if diagnostic.get('held_previous_angle') else 'nein'} · "
            f"Soll: {fmt(diagnostic.get('requested_angle'))} · "
            f"Stellwert: {fmt(diagnostic.get('actual_angle'))} · "
            f"Stoppursache: {stop_reason or 'keine'}")


def system_stats():
    """CPU und Speicher des Prozesses (nur wenn psutil installiert ist)."""
    if psutil is None:
        return 'psutil nicht installiert – CPU/Speicher nicht verfügbar'
    process = psutil.Process()
    load_avg = ''
    with contextlib.suppress(AttributeError, OSError):
        load_avg = ' · Load ' + ' / '.join(f'{x:.2f}' for x in psutil.getloadavg())
    return (f'Prozess-CPU {process.cpu_percent(interval=None):.0f} % · '
            f'RSS {process.memory_info().rss / 2**20:.0f} MB · '
            f'Threads {threading.active_count()}{load_avg}')


def axis(title=None, **kwargs):
    result = dict(AXIS, **kwargs)
    if title:
        result['title'] = {'text': title}
    return result


def plain_figure(traces, **layout):
    """Dict-Figur ohne layout.height (feste Hoehe: dcc.Graph.style, siehe chart_block)."""
    base = dict(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#111a2b',
                font={'color': '#e9f1ff'}, margin=dict(l=45, r=20, t=25, b=40),
                legend=dict(orientation='h', y=1.12, x=0), xaxis=axis(), yaxis=axis())
    base.update(layout)
    return {'data': list(traces), 'layout': base}


def thin(rows, limit=240):
    step = max(1, -(-len(rows) // limit))
    return list(rows)[::step]


def saved_note(saved):
    """Hinweis, ob eine Einstellung dauerhaft in config.json gespeichert wurde (None = unbekannt)."""
    if saved is True:
        return ' · dauerhaft gespeichert (config.json)'
    if saved is False:
        return ' · nur bis zum Neustart (config.json nicht beschreibbar)'
    return ''


def parse_jump(hash_value):
    """``#jump/<session>/<frame>/<nonce>`` -> (session, frame) oder None."""
    if not isinstance(hash_value, str) or not hash_value.startswith('#jump/'):
        return None
    parts = hash_value[len('#jump/'):].split('/')
    if len(parts) < 2 or not parts[0]:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def lane_quality(records):
    """Anteil plausibler Linienpaare im Ringbuffer (None ohne Bilder)."""
    from sim_engine import lanes_plausible
    good = total = 0
    for record in records:
        camera = record.camera
        raw = camera.raw_image
        if raw is None or not getattr(raw, 'size', 0) or camera.frame_id < 0:
            continue
        top, bottom = camera.roi_rows(raw.shape[0])
        total += 1
        good += lanes_plausible(camera.lanes, raw.shape[1], max(1, bottom - top))
    return good / total if total else None


def preview_uri(image, width=320, quality=60):
    """Verkleinerte Vorschau (Telemetrie und Recording)."""
    if image is None:
        return None
    h, w = image.shape[:2]
    scale = min(1.0, width / max(1, w))
    return uri(cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale)))), quality)
