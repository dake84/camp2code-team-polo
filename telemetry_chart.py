"""Gekoppeltes Telemetrie-Diagramm (Paket H2).

Ein Diagramm mit vier Spuren und gemeinsamer Zeitachse.

Spuren von oben nach unten:

1. Frequenzen:
   Kamera / Entscheidungen mit Soll-Linie (Hz)

2. Latenzen:
   Datenalter / Bildverarbeitung / Reaktionszeit (ms)

3. Fahren:
   Rohwinkel / Stellwert / Speed-Steuerwert
   sowie Linienanzahl auf der rechten Y-Achse

4. System:
   CPU / RAM / Temperatur

Die X-Achse zeigt die letzten 60 Sekunden relativ zu jetzt.

Nur tatsächlich gemessene Werte werden dargestellt. Fehlende Werte
bleiben ``None`` und erzeugen sichtbare Lücken. Liegt zwischen zwei
Messpunkten mehr als ``GAP_S``, wird ebenfalls eine Lücke eingefügt.

Die Figur ist ein einfaches Dictionary und kein ``go.Figure``.
Pro Kurve werden höchstens ``MAX_POINTS`` Punkte ausgegeben.

Die feste Höhe liegt am ``dcc.Graph`` beziehungsweise in
``chart_kit.chart_block`` und nicht in der Figur.

Benötigt Plotly >= 5.15 für Multiple Legends.
"""

WINDOW_S = 60
MAX_POINTS = 240
GAP_S = 1.0
HEIGHT = 780
UIREVISION = 'telemetry'

AXIS = {
    'gridcolor': '#2a3550',
    'zerolinecolor': '#3a4766',
    'color': '#c8d3e6',
}

BG = '#111a2b'

# Titel und y-Bereich als Anteil der Diagrammhöhe.
LANES = (
    ('Frequenzen (Hz)', (0.79, 1.0)),
    ('Latenzen (ms)', (0.535, 0.745)),
    ('Fahren', (0.275, 0.49)),
    ('System', (0.0, 0.23)),
)

# Zuordnung der Spur zur Plotly-Legende.
#
# Die erste Legende heißt in Plotly nur "legend".
# Weitere Legenden heißen "legend2", "legend3", ...
LANE_LEGENDS = {
    1: 'legend',
    2: 'legend2',
    3: 'legend3',
    4: 'legend4',
    5: 'legend3',  # Linienanzahl gehört ebenfalls zur Spur "Fahren".
}


def thin(rows, limit=MAX_POINTS):
    """Messreihen auf höchstens ``limit`` Einträge ausdünnen."""
    step = max(1, -(-len(rows) // limit))
    return list(rows)[::step]


def window(rows, now, seconds=WINDOW_S):
    """Zeilen ``(t, ...)`` der letzten ``seconds`` Sekunden."""
    return [
        row
        for row in rows
        if row[0] is not None
        and 0 <= now - row[0] <= seconds
    ]


def series(rows, column, now, gap_s=GAP_S, limit=MAX_POINTS):
    """X- und Y-Werte einer Messwertspalte erzeugen.

    ``None`` bleibt erhalten und erzeugt eine sichtbare Lücke.
    Bei einem Zeitabstand größer als ``gap_s`` wird ein zusätzlicher
    ``None``-Punkt zwischen den Messpunkten eingefügt.
    """
    x = []
    y = []
    previous = None

    for row in thin(rows, limit):
        value = row[column] if len(row) > column else None
        timestamp = row[0]

        if previous is not None and timestamp - previous > gap_s:
            x.append(round((previous + timestamp) / 2 - now, 2))
            y.append(None)

        x.append(round(timestamp - now, 2))
        y.append(value)
        previous = timestamp

    return x, y


def has_values(values):
    """Prüfen, ob mindestens ein echter Messwert enthalten ist."""
    return any(value is not None for value in values)


def _trace(name, rows, column, now, lane, unit='', value_format='.1f', **style):
    x, y = series(rows, column, now)

    if not has_values(y):
        return None

    trace = {
        'type': 'scatter',
        'mode': 'lines',
        'name': name,
        'x': x,
        'y': y,
        'connectgaps': False,

        'yaxis': 'y' if lane == 1 else f'y{lane}',

        # WICHTIG: Multiple Legend beibehalten!
        'legend': LANE_LEGENDS[lane],

        # Tooltip
        'hovertemplate':
            f'{name}: %{{y:{value_format}}}{unit}<extra></extra>',
    }

    trace.update(style)
    return trace



def build_traces(now, camera_target, freqs, latency, system, driving):
    """Alle aktuell verfügbaren Telemetrie-Traces erzeugen."""
    freqs = window(freqs, now)
    latency = window(latency, now)
    system = window(system, now)
    driving = window(driving, now)

    traces = [
        # Spur 1: Frequenzen
        _trace(
            'Kamera (Ist)',
            freqs,
            1,
            now,
            1,
            line={'color': '#4da3ff'},
        ),
        _trace(
            'Entscheidungen (Ist)',
            freqs,
            2,
            now,
            1,
            line={'color': '#2fbf8f'},
        ),

        # Spur 2: Latenzen
        _trace(
            'Datenalter',
            latency,
            1,
            now,
            2,
            line={'color': '#c27cff'},
        ),
        _trace(
            'Bildverarbeitung',
            latency,
            2,
            now,
            2,
            line={'color': '#ffc533'},
        ),
        _trace(
            'Reaktionszeit',
            latency,
            3,
            now,
            2,
            mode='lines+markers',
            line={'color': '#e5534b'},
            marker={'size': 3},
        ),

        # Spur 3: Fahren
        _trace(
            'Rohwinkel',
            driving,
            2,
            now,
            3,
            line={'color': '#93a2bd'},
        ),
        _trace(
            'Stellwert',
            driving,
            6,
            now,
            3,
            line={'color': '#ffc533'},
        ),
        _trace(
            'Speed-Steuerwert',
            driving,
            3,
            now,
            3,
            line={'color': '#2fbf8f'},
        ),
        _trace(
            'Linienanzahl',
            driving,
            5,
            now,
            5,
            line={
                'color': '#5aa0ff',
                'shape': 'hv',
            },
        ),

        # Spur 4: System
        _trace(
            'CPU',
            system,
            1,
            now,
            4,
            line={'color': '#e5534b'},
        ),
        _trace(
            'RAM',
            system,
            2,
            now,
            4,
            line={'color': '#5aa0ff'},
        ),
        _trace(
            'Temperatur',
            system,
            3,
            now,
            4,
            line={'color': '#ffc533'},
        ),
    ]

    traces = [trace for trace in traces if trace is not None]

    frequency_values_exist = any(
        trace.get('yaxis') == 'y'
        for trace in traces
    )

    if camera_target and frequency_values_exist:
        traces.insert(
            0,
            {
                'type': 'scatter',
                'mode': 'lines',
                'name': 'Kamera (Soll)',
                'x': [-WINDOW_S, 0],
                'y': [float(camera_target)] * 2,
                'yaxis': 'y',
                'legend': 'legend',
                'line': {
                    'color': '#6c7a92',
                    'dash': 'dash',
                },
                'hoverinfo': 'skip',
            },
        )

    return traces


def _legend(y):
    """Gemeinsames Layout einer spurbezogenen Legende.

    Die Legende wird horizontal direkt oberhalb der jeweiligen Spur
    dargestellt. Die Position bezieht sich auf die Plotfläche.
    """
    return {
        'orientation': 'h',
        'x': 0.085,
        'xanchor': 'left',
        'y': y,
        'yanchor': 'bottom',

        'font': {
            'color': '#c8d3e6',
            'size': 13,            # <-- größer
        },

        'bgcolor': 'rgba(17, 26, 43, 0.80)',
        'bordercolor': 'rgba(58, 71, 102, 0.35)',
        'borderwidth': 0,
        'groupclick': 'toggleitem',
        'itemwidth': 30,
        'entrywidthmode': 'pixels',
        'itemsizing': 'trace',
    }


def _lane_annotations():
    """Spurbezeichnungen links neben den jeweiligen Legenden."""
    return [
        {
            'text': title,
            'xref': 'paper',
            'yref': 'paper',
            'x': 0,
            'y': top + 0.004,
            'xanchor': 'left',
            'yanchor': 'bottom',
            'showarrow': False,
            'font': {
                'size': 13,
                'color': '#9aa8bf',
            },
        }
        for title, (_bottom, top) in LANES
    ]


def build_figure(now, camera_target, freqs, latency, system, driving):
    """Figur als Dictionary erzeugen.

    Gibt ``None`` zurück, wenn kein einziger Messwert vorliegt.
    """
    traces = build_traces(
        now,
        camera_target,
        freqs,
        latency,
        system,
        driving,
    )

    if not traces:
        return None

    layout = {
        'paper_bgcolor': 'rgba(0,0,0,0)',
        'plot_bgcolor': BG,
        'font': {
            'color': '#e9f1ff',
            'size': 14,
        },

        # Dunkler, gut lesbarer Unified Tooltip.
        'hoverlabel': {
            'bgcolor': '#172238',
            'bordercolor': '#3a4766',
            'font': {
                'color': '#e9f1ff',
                'size': 14,
            },
            'namelength': -1,
        },

        # Oben nur noch wenig Zusatzraum notwendig, weil die einzelnen
        # Legenden direkt an ihren Spuren sitzen.
        'margin': {
            'l': 55,
            'r': 55,
            't': 55,
            'b': 45,
        },

        'hovermode': 'x unified',
        'uirevision': UIREVISION,

        # Die vier echten Plotly-Legenden.
        #
        # Jeweils knapp oberhalb des oberen Domain-Rands der Spur.
        'legend': _legend(LANES[0][1][1] + 0.004),
        'legend2': _legend(LANES[1][1][1] + 0.004),
        'legend3': _legend(LANES[2][1][1] + 0.004),
        'legend4': _legend(LANES[3][1][1] + 0.004),

        'xaxis': {
            **AXIS,
            'title': {
                'text': 'Sekunden vor jetzt',
            },
            'range': [-WINDOW_S, 0],
            'anchor': 'y4',
        },

        'yaxis': {
            **AXIS,
            'domain': list(LANES[0][1]),
            'rangemode': 'tozero',
            'title': {
                'text': 'Hz',
            },
        },

        'yaxis2': {
            **AXIS,
            'domain': list(LANES[1][1]),
            'rangemode': 'tozero',
            'title': {
                'text': 'ms',
            },
            'anchor': 'x',
        },

        'yaxis3': {
            **AXIS,
            'domain': list(LANES[2][1]),
            'title': {
                'text': 'Grad / Steuerwert',
            },
            'anchor': 'x',
        },

        'yaxis5': {
            **AXIS,
            'overlaying': 'y3',
            'side': 'right',
            'range': [0, 3],
            'title': {
                'text': 'Linien',
            },
            'showgrid': False,
        },

        'yaxis4': {
            **AXIS,
            'domain': list(LANES[3][1]),
            'range': [0, 100],
            'title': {
                'text': '% / °C',
            },
            'anchor': 'x',
        },

        'annotations': _lane_annotations(),
    }

    return {
        'data': traces,
        'layout': layout,
    }