"""Gekoppeltes Telemetrie-Diagramm (Paket H2): EIN Diagramm, vier Spuren, gemeinsame Zeitachse.

Spuren von oben nach unten (gemeinsame x-Achse "Sekunden vor jetzt", letzte 60 s):
    1. Frequenzen Kamera / Entscheidungen mit Soll-Linie (Hz)
    2. Latenzen Datenalter / Bildverarbeitung / Reaktionszeit (ms)
    3. Fahren: Rohwinkel, Stellwert, Speed-Steuerwert (links), Linienanzahl (rechts)
    4. System: CPU %, RAM %, Temperatur Grad C

Nur gemessene Werte: fehlende Werte sind ``None`` und erzeugen Luecken; liegt zwischen zwei Messpunkten mehr als
``GAP_S``, wird eine Luecke eingefuegt (im Stillstand entstehen keine Fahrdaten, es wird nichts fortgeschrieben).
Die Figur ist ein einfaches dict (kein ``go.Figure``); pro Kurve hoechstens ``MAX_POINTS`` Punkte.
Die feste Hoehe liegt am ``dcc.Graph`` (chart_kit.chart_block), nicht in der Figur.
"""
WINDOW_S = 60
MAX_POINTS = 240
GAP_S = 1.0
HEIGHT = 780
UIREVISION = 'telemetry'

AXIS = dict(gridcolor='#2a3550', zerolinecolor='#3a4766', color='#c8d3e6')
BG = '#111a2b'
# (Titel, y-Bereich oben/unten als Anteil der Diagrammhoehe)
LANES = (('Frequenzen (Hz)', (0.79, 1.0)), ('Latenzen (ms)', (0.535, 0.745)),
         ('Fahren', (0.275, 0.49)), ('System', (0.0, 0.23)))


def thin(rows, limit=MAX_POINTS):
    step = max(1, -(-len(rows) // limit))
    return list(rows)[::step]


def window(rows, now, seconds=WINDOW_S):
    """Zeilen ``(t, ...)`` der letzten ``seconds`` Sekunden."""
    return [r for r in rows if r[0] is not None and 0 <= now - r[0] <= seconds]


def series(rows, column, now, gap_s=GAP_S, limit=MAX_POINTS):
    """(x, y) einer Spalte; ``None`` bleibt ``None`` (Luecke), grosse Zeitspruenge ergeben eine Luecke."""
    x, y = [], []
    previous = None
    for row in thin(rows, limit):
        value = row[column] if len(row) > column else None
        t = row[0]
        if previous is not None and t - previous > gap_s:
            x.append(round((previous + t) / 2 - now, 2))
            y.append(None)
        x.append(round(t - now, 2))
        y.append(value)
        previous = t
    return x, y


def has_values(y):
    return any(v is not None for v in y)


def _trace(name, rows, column, now, lane, **style):
    x, y = series(rows, column, now)
    if not has_values(y):
        return None
    trace = dict(type='scatter', mode='lines', name=name, x=x, y=y, connectgaps=False,
                 yaxis='y' if lane == 1 else f'y{lane}', legendgroup=f'lane{lane}')
    trace.update(style)
    return trace


def build_traces(now, camera_target, freqs, latency, system, driving):
    freqs, latency = window(freqs, now), window(latency, now)
    system, driving = window(system, now), window(driving, now)
    traces = [
        _trace('Kamera (Ist)', freqs, 1, now, 1, line={'color': '#4da3ff'}),
        _trace('Entscheidungen (Ist)', freqs, 2, now, 1, line={'color': '#2fbf8f'}),
        _trace('Datenalter', latency, 1, now, 2, line={'color': '#c27cff'}),
        _trace('Bildverarbeitung', latency, 2, now, 2, line={'color': '#ffc533'}),
        _trace('Reaktionszeit', latency, 3, now, 2, line={'color': '#e5534b'}, mode='lines+markers',
               marker={'size': 3}),
        _trace('Rohwinkel', driving, 2, now, 3, line={'color': '#93a2bd'}),
        _trace('Stellwert', driving, 6, now, 3, line={'color': '#ffc533'}),
        _trace('Speed-Steuerwert', driving, 3, now, 3, line={'color': '#2fbf8f'}),
        _trace('Linienanzahl', driving, 5, now, 5, line={'color': '#5aa0ff', 'shape': 'hv'}),
        _trace('CPU', system, 1, now, 4, line={'color': '#e5534b'}),
        _trace('RAM', system, 2, now, 4, line={'color': '#5aa0ff'}),
        _trace('Temperatur', system, 3, now, 4, line={'color': '#ffc533'}),
    ]
    traces = [t for t in traces if t is not None]
    if camera_target and traces and any(t['yaxis'] == 'y' for t in traces):
        traces.insert(0, dict(type='scatter', mode='lines', name='Kamera (Soll)', x=[-WINDOW_S, 0],
                              y=[float(camera_target)] * 2, yaxis='y', legendgroup='lane1',
                              line={'color': '#6c7a92', 'dash': 'dash'}, hoverinfo='skip'))
    return traces


def build_figure(now, camera_target, freqs, latency, system, driving):
    """Figur (dict) oder ``None``, wenn kein einziger Messwert vorliegt."""
    traces = build_traces(now, camera_target, freqs, latency, system, driving)
    if not traces:
        return None
    layout = dict(
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor=BG, font={'color': '#e9f1ff'},
        margin=dict(l=55, r=55, t=110, b=45), hovermode='x unified', uirevision=UIREVISION,
        legend=dict(orientation='h', y=1.07, yanchor='bottom', x=0),
        xaxis=dict(AXIS, title={'text': 'Sekunden vor jetzt'}, range=[-WINDOW_S, 0], anchor='y4'),
        yaxis=dict(AXIS, domain=list(LANES[0][1]), rangemode='tozero', title={'text': 'Hz'}),
        yaxis2=dict(AXIS, domain=list(LANES[1][1]), rangemode='tozero', title={'text': 'ms'}, anchor='x'),
        yaxis3=dict(AXIS, domain=list(LANES[2][1]), title={'text': 'Grad / Steuerwert'}, anchor='x'),
        yaxis5=dict(AXIS, overlaying='y3', side='right', range=[0, 3], title={'text': 'Linien'}, showgrid=False),
        yaxis4=dict(AXIS, domain=list(LANES[3][1]), range=[0, 100], title={'text': '% / °C'}, anchor='x'),
        annotations=[dict(text=title, xref='paper', yref='paper', x=0, y=top, xanchor='left', yanchor='bottom',
                          showarrow=False, font={'size': 12, 'color': '#9aa8bf'}) for title, (_bottom, top) in LANES])
    return {'data': traces, 'layout': layout}
