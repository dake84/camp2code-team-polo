"""Gemeinsame Bausteine fuer alle Diagramme (Paket H1).

Ursache-Pruefung zum "wachsenden leeren Diagramm" (siehe CHANGELOG 6.6): Im Stand 062f360 hatten
alle ``dcc.Graph`` keine feste Hoehe (nur ``layout.height`` in der Figur) und standen ohne
Hoehenbegrenzung in einer Bootstrap-Karte. Ein Padding am Plotly-Element liegt in diesem Stand
NICHT vor (der fruehere Chart-Zusatz ist nicht enthalten); die Ursache des Wachsens ist daher
nicht am Code verifiziert, sondern nur ein Verdacht. Die Loesung vermeidet beide Kandidaten:

    * Hoehe ausschliesslich als ``style.height`` am ``dcc.Graph`` (kein ``layout.height``),
    * kein Padding am Plotly-Element,
    * Vollbild-Schalter als Geschwister in einer Leiste ueber dem Diagramm,
    * jedes Diagramm startet mit einer dunklen leeren Figur samt Hinweistext.

Dash-Objekte (``dcc``, ``html``) werden uebergeben statt importiert, damit Dash-Attrappe
und echtes Dash dieselben Funktionen nutzen.
"""

BG = '#111a2b'
GRID = '#2a3550'
TEXT = '#e9f1ff'
MUTED = '#8fa0bd'

# Zoom, Verschieben, Reset, PNG; Mausrad-Zoom; Doppelklick = alles zuruecksetzen.
# Legende: Klick = Kurve ein/aus, Doppelklick = nur diese Kurve (Plotly-Standard).
CHART_CONFIG = {
    'displayModeBar': True,
    'displaylogo': False,
    'scrollZoom': True,
    'doubleClick': 'reset+autosize',
    'modeBarButtonsToRemove': ['select2d', 'lasso2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d',
                               'hoverClosestCartesian', 'hoverCompareCartesian', 'toggleSpikelines'],
    'toImageButtonOptions': {'format': 'png', 'filename': 'camp2code_diagramm'},
}

DEFAULT_NOTE = ('Legende: Klick = Kurve ein/aus · Doppelklick = nur diese Kurve · '
                'Mausrad = Zoom · Doppelklick ins Diagramm = alles')


def empty_figure(text='Noch keine Daten'):
    """Dunkle leere Figur mit Hinweistext (statt des weissen Plotly-Standards)."""
    hidden = dict(visible=False, showgrid=False, zeroline=False, fixedrange=True)
    return {'data': [],
            'layout': dict(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor=BG, font={'color': TEXT},
                           margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                           xaxis=hidden, yaxis=dict(hidden),
                           annotations=[dict(text=text, xref='paper', yref='paper', x=0.5, y=0.5,
                                             showarrow=False, font={'size': 14, 'color': MUTED})],
                           uirevision='empty')}


def fullscreen_button(html, target):
    """Vollbild-Schalter (assets/fullscreen.js, Klasse ``c2c-fs``)."""
    return html.Button('⛶ Vollbild', type='button', className='c2c-fs btn btn-sm btn-outline-info ms-2',
                       **{'data-target': target})


def chart_block(dcc, html, graph_id, height=260, hint='Noch keine Daten', note=None, frame_bar=None):
    """Diagramm mit fester Hoehe; ueber dem Diagramm eine Leiste mit Hinweis und Vollbild-Schalter.

    Die Leiste ist Geschwister des ``dcc.Graph`` (nicht darin). Das Vollbild gilt fuer den
    ganzen Block (``<graph_id>-box``), die Leiste bleibt dabei sichtbar.

    PAKET-H1B: ``frame_bar`` = ID eines ``dcc.Store``. Dann traegt der Block ``data-framebar`` und
    assets/framebar.js legt einen ziehbaren Frame-Balken ueber das Diagramm; beim Ziehen meldet er
    ``{frame, nonce}`` an diesen Store. ``<graph_id>-barsync`` ist der Ausgang des clientseitigen Callbacks,
    der den Balken dem Index folgen laesst (ohne Serveranfrage).
    """
    box = f'{graph_id}-box'
    bar_children, attributes = [html.Span(note or DEFAULT_NOTE, className='small text-secondary'),
                                fullscreen_button(html, box)], {}
    if frame_bar:   # Stores ohne Darstellung in der Leiste: die Kinder des Blocks bleiben (Leiste, Diagramm)
        bar_children += [dcc.Store(id=frame_bar, data=None, storage_type='memory'),
                         dcc.Store(id=f'{graph_id}-barsync', data=None, storage_type='memory')]
        attributes['data-framebar'] = frame_bar
    bar = html.Div(bar_children, className='c2c-chart-bar d-flex justify-content-between align-items-center mb-1')
    graph = dcc.Graph(id=graph_id, figure=empty_figure(hint), config=CHART_CONFIG, responsive=True,
                      className='c2c-graph', style={'height': f'{int(height)}px'})
    return html.Div([bar, graph], id=box, className='c2c-chart-box', **attributes)
