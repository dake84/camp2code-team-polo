#!/usr/bin/env python3
"""Paket H1b: Frame-Balken in den Diagrammen (Replay Lab, Simulation) und Kamera-/Reglereinstellungen.

Setzt Paket H1 und den Plug-in-Umbau (dashboard/tabs/) voraus. Verankerte Aenderungen; fehlt ein Anker oder
ist er mehrdeutig, wird KEINE Datei geschrieben (alles oder nichts). Dateien mit dem Marker PAKET-H1B werden
uebersprungen (idempotent). Achtung: "PAKET-H1" ist Teilstring von "PAKET-H1B" - geprueft wird deshalb immer
der vollstaendige Marker.

Aufruf: python scripts/paket_h1b_patch.py [REPO_ROOT]
"""
import sys
from pathlib import Path

MARKER = 'PAKET-H1B'


class PatchError(Exception):
    pass


def _hits(lines, needle, start=0):
    return [i for i in range(start, len(lines)) if needle in lines[i]]


def _one(lines, needle, file, start=0):
    hits = _hits(lines, needle, start)
    if len(hits) != 1:
        raise PatchError(f'{file}: Anker {needle!r} {len(hits)}x gefunden (erwartet 1)')
    return hits[0]


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def sub_once(text, old, new, file):
    count = text.count(old)
    if count != 1:
        raise PatchError(f'{file}: Text {old[:70]!r} {count}x gefunden (erwartet 1)')
    return text.replace(old, new)


def insert(text, needle, block, file, after=True, indent=None, blank_after=None, blank_before=0):
    """Fuegt ``block`` vor/nach der eindeutigen Zeile mit ``needle`` ein (Einrueckung der Ankerzeile)."""
    lines = text.split('\n')
    i = _one(lines, needle, file)
    ind = _indent(lines[i]) if indent is None else indent
    body = block.strip('\n').split('\n')
    new = [(ind + line) if line.strip() else '' for line in body]
    if blank_after is None:
        blank_after = 1 if (not after and len(body) > 1) else 0
    new = [''] * blank_before + new + [''] * blank_after
    pos = i + 1 if after else i
    return '\n'.join(lines[:pos] + new + lines[pos:])


def replace_to_end(text, start_needle, block, file, tail_check):
    """Ersetzt ab ``start_needle`` bis Dateiende; ``tail_check`` muss im ersetzten Rest stehen."""
    lines = text.split('\n')
    s = _one(lines, start_needle, file)
    rest = '\n'.join(lines[s:])
    if tail_check not in rest:
        raise PatchError(f'{file}: Dateiende nach {start_needle!r} entspricht nicht der Erwartung ({tail_check!r})')
    return '\n'.join(lines[:s]) + '\n' + block.strip('\n') + '\n'


def append(text, block):
    return text.rstrip('\n') + '\n' + block.strip('\n') + '\n'


# ------------------------------------------------------------------ ConfigReader.py
CONFIG_SET_VALUE = '''
def set_value(self, attribut: str, value) -> bool:
    """PAKET-H1B: setzt einen Wert nur im Speicher (legt den Namespace bei Bedarf an) und speichert nicht.

    ``set_config`` verwirft den Wert still, wenn der Namespace in der Datei fehlt.
    """
    with self._lock:
        if self._json_config is None:
            self._load_config_file()
        self._json_config.setdefault(self._cfg_namespace, {})[attribut] = value
    return True
'''


def patch_config_reader(t, f):
    t = sub_once(t, '        return int(self.get_config().get(attribut, dv))\n',
                 '        value = self.get_config().get(attribut, dv)\n'
                 '        try:  # PAKET-H1B: ungueltige Werte ergeben den Standardwert statt eines Startabbruchs\n'
                 '            return int(value)\n'
                 '        except (TypeError, ValueError):\n'
                 '            self._log.warning(f"Ungültiger Wert {value!r} für {self._cfg_namespace}.{attribut}, '
                 'Standardwert {dv}")\n'
                 '            return int(dv)\n', f)
    t = sub_once(t, '        return float(self.get_config().get(attribut, dv))\n',
                 '        value = self.get_config().get(attribut, dv)\n'
                 '        try:  # PAKET-H1B: ungueltige Werte ergeben den Standardwert statt eines Startabbruchs\n'
                 '            return float(value)\n'
                 '        except (TypeError, ValueError):\n'
                 '            self._log.warning(f"Ungültiger Wert {value!r} für {self._cfg_namespace}.{attribut}, '
                 'Standardwert {dv}")\n'
                 '            return float(dv)\n', f)
    return insert(t, 'def _save_config(self) -> bool:', CONFIG_SET_VALUE, f, after=False)


# ------------------------------------------------------------------ car_application.py
APP_METHODS = '''
# ------------------------------------------------------------ Kamera- und Reglereinstellungen (PAKET-H1B)
def runtime_settings(self):
    """Je Einstellung aktiver und gespeicherter Wert (siehe runtime_settings.snapshot)."""
    return runtime_settings.snapshot(self)

def set_runtime_settings(self, values):
    """Validiert, uebernimmt live und speichert dauerhaft; nur im Stillstand (kein Fahrmodus, keine Bergung).

    Liefert {'saved': True|False, 'applied': {schluessel: live uebernommen}, 'values': normalisierte Werte}.
    ``ValueError`` bei ungueltigen Werten, ``RuntimeError`` bei laufender Fahrt; dann wurde nichts veraendert.
    """
    with self._lock:
        if self.running() or self.recovery.enabled:
            raise RuntimeError('Kamera- und Reglereinstellungen nur im Stillstand ändern')
        values = runtime_settings.validate(values)
        applied = runtime_settings.apply_live(self, values)
        self.settings_revision += 1
    saved = self._persist(lambda: runtime_settings.save(values), 'Kamera- und Reglereinstellungen')
    return {'saved': saved, 'applied': applied, 'values': values}

def _apply_stored_runtime_settings(self):
    """PAKET-H1B: gespeicherte Kamera-/Reglerwerte anwenden; Fehler duerfen den Start nie verhindern."""
    try:
        runtime_settings.apply_live(self, runtime_settings.startup_values())
    except Exception:
        log.exception('Gespeicherte Kamera-/Reglereinstellungen nicht anwendbar - Standardwerte bleiben')

'''


def patch_car_application(t, f):
    t = insert(t, 'import config_store  # PAKET-H1', 'import runtime_settings  # PAKET-H1B', f)
    t = insert(t, 'self._apply_stored_settings()',
               'self.settings_revision = 0  # PAKET-H1B: zaehlt Aenderungen (Simulation liest danach neu)\n'
               'self._apply_stored_runtime_settings()', f)
    t = sub_once(t, '            action()\n            return True\n',
                 '            action()\n'
                 "            self.health.record('CONFIG_SAVED', f'{what} dauerhaft gespeichert')  # PAKET-H1B\n"
                 '            return True\n', f)
    return insert(t, '# ------------------------------------------------------------ Einstellungen (PAKET-G)',
                  APP_METHODS, f, after=False)


# ------------------------------------------------------------------ offline_application.py
OFFLINE_METHODS = '''
def runtime_settings(self):  # PAKET-H1B
    """Offline sind Kamera und Regler nicht aktiv; angezeigt werden nur die gespeicherten Werte."""
    snapshot = runtime_settings.snapshot(self)
    for entry in snapshot.values():
        entry['active'] = None
    return snapshot

def set_runtime_settings(self, values):
    """Offline wird nur gespeichert (falls config.json vorhanden ist); nichts ist live aktiv."""
    values = runtime_settings.validate(values)
    try:
        runtime_settings.save(values)
        saved = True
    except (config_store.ConfigStoreError, OSError, ValueError):
        saved = False
    return {'saved': saved, 'applied': {key: False for key in values}, 'values': values}

'''


def patch_offline(t, f):
    t = insert(t, 'import config_store  # PAKET-H1', 'import runtime_settings  # PAKET-H1B', f)
    t = insert(t, '    offline = True', 'settings_revision = 0  # PAKET-H1B', f)
    return insert(t, 'def toggle_recording(self):', OFFLINE_METHODS, f, after=False)


# ------------------------------------------------------------------ chart_kit.py
CHART_BLOCK = '''
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
'''


def patch_chart_kit(t, f):
    return replace_to_end(t, 'def chart_block(dcc, html, graph_id,', CHART_BLOCK, f, 'className=\'c2c-chart-box\')')


# ------------------------------------------------------------------ dashboard/context.py
def patch_context(t, f):
    t = sub_once(t, "'no_update', 'Patch', 'go'):", "'no_update', 'Patch', 'go', 'ClientsideFunction'):  # PAKET-H1B", f)
    t = sub_once(t, "    def chart_block(self, graph_id, height=260, hint='Noch keine Daten', note=None):\n",
                 "    def chart_block(self, graph_id, height=260, hint='Noch keine Daten', note=None, frame_bar=None):\n", f)
    return sub_once(t, '        return chart_kit.chart_block(self.dcc, self.html, graph_id, height, hint, note)\n',
                    '        return chart_kit.chart_block(self.dcc, self.html, graph_id, height, hint, note, frame_bar)'
                    '  # PAKET-H1B\n', f)


# ------------------------------------------------------------------ Camp2CodeDashboard.py
def patch_dashboard_start(t, f):
    t = insert(t, '    Patch = None', '''
try:  # PAKET-H1B: clientseitige Callbacks (Frame-Balken)
    from dash import ClientsideFunction
except ImportError:  # Dash-Attrappe ohne diese Klasse
    ClientsideFunction = None''', f, indent='')
    return sub_once(t, 'no_update=no_update, Patch=Patch)', 'no_update=no_update, Patch=Patch,\n'
                    '                             ClientsideFunction=ClientsideFunction)', f)


# ------------------------------------------------------------------ replay_selection.py
NEAREST = '''
def nearest_position(ids, frame):
    """PAKET-H1B: Position in ``ids`` des Frames, der ``frame`` am naechsten liegt.

    ``None`` bei ungueltiger Eingabe (kein Zahlenwert, nicht endlich) oder ohne bekannte IDs. Bei gleichem
    Abstand gewinnt der kleinere Frame; unbekannte IDs (``None``) werden uebergangen.
    """
    if isinstance(frame, bool) or not isinstance(frame, (int, float)) or not math.isfinite(frame):
        return None
    best = best_distance = None
    for position, frame_id in enumerate(ids):
        if frame_id is None:
            continue
        distance = abs(frame_id - frame)
        if best is None or distance < best_distance or (distance == best_distance and frame_id < ids[best]):
            best, best_distance = position, distance
    return best
'''


def patch_replay_selection(t, f):
    return append(t, NEAREST)


# ------------------------------------------------------------------ dashboard/tabs/replay.py
REPLAY_CLIENTSIDE = '''
# PAKET-H1B: Frame-Balken (assets/framebar.js) folgt dem Index clientseitig, auch bei Wiedergabe
if c.ClientsideFunction is not None and hasattr(app, 'clientside_callback'):
    app.clientside_callback(c.ClientsideFunction('c2c_framebar', 'move'), Output('replay-chart-barsync', 'data'),
                            Input('replay-index', 'value'), Input('replay-chart', 'figure'),
                            State('replay-chart-box', 'id'))
'''

REPLAY_BAR_SEEK = '''
if trigger == 'replay-bar':  # PAKET-H1B: Frame-Balken gezogen (assets/framebar.js), kein Neuaufbau des Diagramms
    records, _, _ = records_for(name, bounds)
    position = nearest_position([record_frame_id(r) for r in records], (bar or {}).get('frame'))
    return (position, no_update) if position is not None else (no_update, no_update)
'''


def patch_replay_tab(t, f):
    t = sub_once(t, 'from replay_selection import event_window, record_frame_id, select_records, selected_bounds\n',
                 'from replay_selection import (event_window, nearest_position, record_frame_id, select_records,\n'
                 '                              selected_bounds)  # PAKET-H1B\n', f)
    t = sub_once(t, """                c.chart_block('replay-chart', 260, 'Noch keine Daten',
                              note='Klick auf Messpunkt = Frame wählen · Ziehen oder Mausrad = Bereich wählen · '
                                   'Doppelklick = alles · Legende: Klick = Kurve ein/aus')]), md=12)],""",
                 """                c.chart_block('replay-chart', 260, 'Noch keine Daten', frame_bar='replay-bar',
                              note='Blauen Balken ziehen oder Klick auf Messpunkt = Frame wählen · '
                                   'Ziehen im Diagramm oder Mausrad = Bereich wählen · '
                                   'Doppelklick = alles · Legende: Klick = Kurve ein/aus')]), md=12)],""", f)
    t = insert(t, "@app.callback(Output('replay-index', 'max'), Output('replay-range', 'children'),",
               REPLAY_CLIENTSIDE, f, after=False)
    t = sub_once(t, "State('main-tabs', 'active_tab'), Input('jump-url', 'hash'))\n"
                    "        def seek(_prev, _next, click, relayout, event_frame, _reset, _tick, index, playing, name, bounds,\n"
                    "                 pre, post, tab, jump=None):\n",
                 "State('main-tabs', 'active_tab'), Input('jump-url', 'hash'), Input('replay-bar', 'data'))\n"
                 "        def seek(_prev, _next, click, relayout, event_frame, _reset, _tick, index, playing, name, bounds,\n"
                 "                 pre, post, tab, jump=None, bar=None):\n", f)
    t = insert(t, "if trigger == 'replay-range-reset':", REPLAY_BAR_SEEK, f, after=False)
    t = sub_once(t, '''            """Diagramm nur bei Session-/Bereichswechsel neu; sonst wandert nur der Cursor."""''',
                 '''            """Diagramm nur bei Session-/Bereichswechsel (und im Live-Ringbuffer) neu aufbauen.

            PAKET-H1B: Der Frame-Balken ist ein eigenes, clientseitig bewegtes Element (assets/framebar.js); ein
            Indexwechsel baut das Diagramm einer gespeicherten Session daher nicht mehr neu und patcht auch keine Kurve.
            """''', f)
    t = sub_once(t, """            if not structural and 'replay-index' not in props:
                return no_update
            records, events, _path, record = record_at(name, bounds, index)
            cursor = record_frame_id(record) if record is not None else None
            if not structural and Patch is not None and cursor is not None:
                patch = Patch()
                patch['data'][2]['x'] = [cursor, cursor]
                return patch
""", """            if not structural:
                return no_update
            records, events, _path, record = record_at(name, bounds, index)
""", f)
    t = sub_once(t, "                      Input('replay-index', 'value'), State('main-tabs', 'active_tab'))\n"
                    "        def replay_chart(name, bounds, index, tab):\n",
                 "                      Input('replay-index', 'value'), Input('main-tabs', 'active_tab'))  # PAKET-H1B\n"
                 "        def replay_chart(name, bounds, index, tab):\n", f)
    t = sub_once(t, "structural = live or not props or bool(props & {'session-choice', 'replay-bounds', ''})",
                 "structural = live or not props or bool(props & {'session-choice', 'replay-bounds', 'main-tabs', ''})", f)
    return sub_once(t, """                # Trace 2 = Cursor; wird beim Blaettern per Patch verschoben.
                chart.add_scatter(x=[cursor, cursor], y=[45, 135], mode='lines', name='Aktueller Frame',
                                  line={'color': '#4da3ff', 'width': 2}, hoverinfo='skip')
""", '', f)


# ------------------------------------------------------------------ sim_lab.py
SIM_CLIENTSIDE = '''
clientside = getattr(ui, 'ClientsideFunction', None)
if clientside is not None and hasattr(app, 'clientside_callback'):  # PAKET-H1B: Frame-Balken (assets/framebar.js)
    app.clientside_callback(clientside('c2c_framebar', 'move'), Output('sim-chart-barsync', 'data'),
                            Input('sim-index', 'value'), Input('sim-chart', 'figure'),
                            State('sim-chart-box', 'id'))
'''

SIM_BAR_TRIGGER = '''
if trigger == 'sim-bar':  # PAKET-H1B: Frame-Balken gezogen; bei laufender Wiedergabe verankert advance() die Uhr neu
    from replay_selection import nearest_position
    position = nearest_position([frame.frame_id for frame in frames], (bar or {}).get('frame'))
    return (position, keep, keep) if position is not None else (keep, keep, keep)
'''


def patch_sim_lab(t, f):
    t = sub_once(t, """            chart_kit.chart_block(dcc, html, 'sim-chart', 280, 'Noch keine Simulation berechnet',
                                  note='Klick auf einen Punkt = Frame anzeigen · grau = Vorlauf (nicht in den Kennzahlen) · '
                                       'rote Rauten = Regler-Stopp · Mausrad = Zoom · Legende: Klick = Kurve ein/aus')])""",
                 """            chart_kit.chart_block(dcc, html, 'sim-chart', 280, 'Noch keine Simulation berechnet',
                                  frame_bar='sim-bar',
                                  note='Blauen Balken ziehen oder Klick auf einen Punkt = Frame anzeigen · '
                                       'grau = Vorlauf (nicht in den Kennzahlen) · '
                                       'rote Rauten = Regler-Stopp · Mausrad = Zoom · Legende: Klick = Kurve ein/aus')])""", f)
    t = sub_once(t, """        if self._engine is None:
            from sim_engine import SimEngine
            self._engine = SimEngine()
        return self._engine
""", """        revision = getattr(self.application, 'settings_revision', 0)  # PAKET-H1B: nach Settings neu lesen
        if self._engine is None or getattr(self, '_engine_revision', None) != revision:
            from sim_engine import SimEngine
            self._engine = SimEngine()
            self._engine_revision = revision
        return self._engine
""", f)
    t = sub_once(t, '    def playback(self, trigger, token, speed, timing, index, paused, click=None, now=None):\n',
                 '    def playback(self, trigger, token, speed, timing, index, paused, click=None, now=None, bar=None):\n', f)
    t = insert(t, "if trigger == 'sim-chart':", SIM_BAR_TRIGGER, f, after=False)
    t = sub_once(t, """                      State('sim-index', 'value'), State('sim-play-tick', 'disabled'), prevent_initial_call=True)
        def sim_playback(_prev, _next, _play, _tick, click, token, speed, timing, index, paused):
            \"\"\"Schritte, Sprung ins Diagramm und Wiedergabe nach den aufgezeichneten Zeitstempeln.\"\"\"
            return self.playback(ui.ctx.triggered_id, token, speed, timing, index, paused, click)
""", """                      State('sim-index', 'value'), State('sim-play-tick', 'disabled'), Input('sim-bar', 'data'),
                      prevent_initial_call=True)
        def sim_playback(_prev, _next, _play, _tick, click, token, speed, timing, index, paused, bar=None):
            \"\"\"Schritte, Sprung ins Diagramm (Klick oder Frame-Balken) und Wiedergabe nach den Zeitstempeln.\"\"\"
            return self.playback(ui.ctx.triggered_id, token, speed, timing, index, paused, click, bar=bar)
""", f)
    return insert(t, 'return self.CALLBACKS', SIM_CLIENTSIDE, f, after=False)


# ------------------------------------------------------------------ help_texts.py
HELP_ENTRIES = """
# PAKET-H1B: Kamera, Regler und Fahrzeug (Settings)
'camfreq': ('Kamera-Abtastung', 'Einstellung',
            'Soll-Bildrate der Kameraschleife in Hertz, die nur erreicht wird, wenn Aufnahme und Bildverarbeitung '
            'schnell genug sind, und die unter 5 Hz die Kamera-Überwachung des Reglers auslösen kann.'),
'ctlfreq': ('Regler-Abtastung', 'Einstellung',
            'Wie oft der CameraMode pro Sekunde nach einem neuen Kamerabild schaut, wobei nur neue Bilder '
            'ausgewertet werden und die Entscheidungsrate daher nie über der Kamerarate liegt.'),
'losttimeout': ('Linien-Timeout', 'Einstellung',
                'Sekunden ohne zwei erkannte Linien oder ohne neues Kamerabild, nach denen der Regler stoppt '
                '(LANE_TIMEOUT beziehungsweise CAMERA_STALE), wobei kleine Werte früher abbrechen.'),
'steergain': ('Lenkverstärkung', 'Einstellung',
              'Faktor, mit dem die Abweichung des Rohwinkels von 90 Grad in den Sollwinkel übersetzt wird '
              '(Standard 2,0), wobei größere Werte stärker lenken und der Stellwert auf 45 bis 135 Grad begrenzt bleibt.'),
'vmin': ('Minimaler Steuerwert', 'Einstellung',
         'Untergrenze des PWM-Steuerwerts für jede Fahrt (Standard 20), auf die kleinere Werte angehoben werden, '
         'wobei das Auto laut Erfahrung erst ab etwa 30 anfährt.'),
'vmax': ('Maximaler Steuerwert', 'Einstellung',
         'Obergrenze des PWM-Steuerwerts für jede Fahrt (Standard 100), die auch die adaptive Geschwindigkeit '
         'und die Bergung begrenzt und nicht kleiner als der minimale Steuerwert sein darf.'),
"""


def patch_help_texts(t, f):
    return insert(t, "'simrate': ('Anzeigerate', 'gemessen',", HELP_ENTRIES, f, after=False, blank_after=0)


# ------------------------------------------------------------------ dashboard/tabs/settings.py
SETTINGS_CARD = '''
dbc.Col(self.runtime_card(), md=12),  # PAKET-H1B
'''

SETTINGS_METHODS = '''
def runtime_card(self):
    """PAKET-H1B: Kamera, Regler und Fahrzeug (runtime_settings.py); nur im Stillstand, dauerhaft gespeichert."""
    c = self.ctx
    dbc, dcc, html = c.dbc, c.dcc, c.html
    rows, group = [], None
    for setting in runtime_settings.SETTINGS:
        if setting.group != group:
            group = setting.group
            rows.append(html.Div(group, className='fw-semibold text-info mt-3 mb-1'))
        title = setting.label + (f' ({setting.unit})' if setting.unit else '')
        marker = [dbc.Badge('verhaltensrelevant', color='warning', text_color='dark', className='ms-1')] \\
            if setting.behavior else []
        rows.append(dbc.Row([
            dbc.Col(html.Div([c.help_label(title, setting.help_key, 'small d-inline-block'), *marker]), md=4),
            dbc.Col(dcc.Input(id=f'rt-{setting.key}', type='number', min=setting.minimum, max=setting.maximum,
                              step=setting.step, value=setting.default, className='form-control form-control-sm'),
                    md=2),
            dbc.Col(html.Div(id=f'rt-status-{setting.key}', className='small text-secondary'), md=6),
        ], className='g-2 align-items-center mb-1'))
    return c.card('Kamera & Regler', [
        html.Div('Nur im Stillstand · wirkt sofort · wird dauerhaft in config.json gespeichert · Bereiche: '
                 + ' · '.join(f'{s.label} {runtime_settings.number_text(s.minimum)}–'
                              f'{runtime_settings.number_text(s.maximum)}' for s in runtime_settings.SETTINGS),
                 className='small text-secondary'),
        *rows,
        dbc.Alert('Verhaltensrelevante Werte ändern die Fahrregelung beziehungsweise die Steuerwerte aller Fahrmodi; '
                  'nach einer Änderung zuerst aufgebockt prüfen.', color='warning', className='small mt-3 mb-2 py-2'),
        dbc.Button('Übernehmen (Kamera & Regler)', id='rt-apply', color='primary', size='sm'),
        html.Div(id='rt-msg', className='small mt-2')])
'''

SETTINGS_CALLBACK = '''
# PAKET-H1B: Kamera & Regler (ein Callback fuer Anzeige beim Oeffnen und Uebernehmen)
setting_keys = [s.key for s in runtime_settings.SETTINGS]
level_class = {'ok': 'text-success', 'warn': 'text-warning', 'off': 'text-secondary'}

@app.callback(*[Output(f'rt-{key}', 'value') for key in setting_keys],
              *[Output(f'rt-status-{key}', 'children') for key in setting_keys], Output('rt-msg', 'children'),
              Input('main-tabs', 'active_tab'), Input('rt-apply', 'n_clicks'),
              *[State(f'rt-{key}', 'value') for key in setting_keys])
def rt_settings(tab, _n, *values):
    """Zeigt aktive und gespeicherte Werte beim Oeffnen des Reiters; uebernimmt und speichert bei Klick."""
    count = len(setting_keys)
    getter = getattr(application, 'runtime_settings', None)
    message = no_update
    fill = [no_update] * count
    if c.ctx.triggered_id == 'rt-apply':
        try:
            result = application.set_runtime_settings(dict(zip(setting_keys, values)))
            late = [runtime_settings.BY_KEY[k].label for k, live in result['applied'].items() if not live]
            message = [html.Div('✔ Kamera- und Reglerwerte übernommen' + saved_note(result['saved']),
                                className='text-success')]
            if late:
                message.append(html.Div('Gilt erst ab dem nächsten Start: ' + ', '.join(late),
                                        className='text-warning'))
        except (ValueError, RuntimeError, AttributeError) as exc:
            message = html.Div(f'✖ {exc}', className='text-warning')
    elif tab != self.tab_id:
        return (no_update,) * (2 * count + 1)
    else:
        snap = getter() if getter is not None else None
        if snap is not None:
            fill = [next((v for v in (snap[k]['active'], snap[k]['stored']) if v is not None),
                         runtime_settings.BY_KEY[k].default) for k in setting_keys]
    snap = getter() if getter is not None else None
    status = [no_update] * count
    if snap is not None:
        status = []
        for key in setting_keys:
            text, level = runtime_settings.describe(runtime_settings.BY_KEY[key], snap[key])
            status.append(html.Span(text, className=level_class[level]))
    return (*fill, *status, message)
'''


def patch_settings_tab(t, f):
    t = sub_once(t, 'from ..helpers import saved_note\n',
                 'import runtime_settings  # PAKET-H1B\nfrom ..helpers import saved_note\n', f)
    t = insert(t, "dbc.Col([dbc.Button('Übernehmen', id='settings-apply', color='primary', size='sm'),", SETTINGS_CARD,
               f, after=False)
    t = insert(t, '    def register_callbacks(self, app):', SETTINGS_METHODS, f, after=False, indent='    ',
               blank_after=1)
    return insert(t, "@app.callback(Output('set-speed-mode', 'value'), Output('set-speed-fixed', 'value'),",
                  SETTINGS_CALLBACK, f, after=False)


# ------------------------------------------------------------------ assets/studio.css
CSS = """
/* PAKET-H1B: Frame-Balken (assets/framebar.js) als Element ueber dem Diagramm */
.c2c-graph{position:relative}
.c2c-framebar{position:absolute;width:14px;z-index:5;cursor:ew-resize;touch-action:none;user-select:none;display:none}
.c2c-framebar-line{position:absolute;left:6px;width:2px;top:0;bottom:0;background:#4da3ff;box-shadow:0 0 4px #4da3ff99}
.c2c-framebar-grip{position:absolute;left:1px;top:0;width:12px;height:9px;border-radius:0 0 3px 3px;background:#4da3ff}
.c2c-framebar-tag{position:absolute;left:16px;top:10px;padding:0 4px;border-radius:3px;background:#172235;color:#e9f1ff;font-size:.72rem;white-space:nowrap}
.c2c-framebar:hover .c2c-framebar-line,.c2c-framebar.c2c-dragging .c2c-framebar-line{left:5px;width:4px;background:#8cc4ff}
"""


def patch_css(t, f):
    return append(t, CSS)


# ------------------------------------------------------------------ Tests und Doku
def patch_dash_stub(t, f):
    t = sub_once(t, 'class CallbackContext:', '''class ClientsideFunction(namedtuple('ClientsideFunction', 'namespace function_name')):  # PAKET-H1B
    pass


class CallbackContext:''', f)
    t = sub_once(t, '''        self.layout = None
        self.callbacks = {}
''', '''        self.layout = None
        self.callbacks = {}
        self.clientside = []  # PAKET-H1B: (Funktion, Abhaengigkeiten) clientseitiger Callbacks
''', f)
    t = sub_once(t, '''    def run(self, *args, **kwargs):  # pragma: no cover''', '''    def clientside_callback(self, function, *deps):  # PAKET-H1B
        self.clientside.append((function, deps))

    def run(self, *args, **kwargs):  # pragma: no cover''', f)
    return sub_once(t, 'dash.ALL, dash.MATCH = ALL, MATCH\n',
                    'dash.ALL, dash.MATCH = ALL, MATCH\n    dash.ClientsideFunction = ClientsideFunction  # PAKET-H1B\n', f)


def patch_test_callbacks(t, f):
    return sub_once(t, "'settings_apply', 'settings_fill'}", "'settings_apply', 'settings_fill', 'rt_settings'}  # PAKET-H1B", f)


def patch_test_plugins(t, f):
    return sub_once(t, "'SettingsTab': {'settings_apply', 'settings_fill'},",
                    "'SettingsTab': {'settings_apply', 'settings_fill', 'rt_settings'},  # PAKET-H1B", f)


def patch_test_real_dash(t, f):
    return sub_once(t, 'self.assertEqual(len(deps), 31)',
                    'self.assertEqual(len(deps), 34)  # PAKET-H1B: + rt_settings, + 2 clientseitige Frame-Balken', f)


def patch_test_paket_c(t, f):
    return sub_once(t, 'self.assertEqual(len(figure.data), 3)                    # Stellwert, Linien, Cursor',
                    'self.assertEqual(len(figure.data), 2)                    # Stellwert, Linien (PAKET-H1B: Balken = DOM)', f)


def patch_test_paket_h1(t, f):
    t = sub_once(t, 'self.assertEqual(len(figure.data), 3)                                    # Stellwert, Linien, Cursor',
                 'self.assertEqual(len(figure.data), 2)                                    # Stellwert, Linien (PAKET-H1B)', f)
    return sub_once(t, "self.assertEqual(len(figure['data']), 3)",
                    "self.assertEqual(len(figure['data']), 2)  # PAKET-H1B: Frame-Balken ist ein DOM-Element", f)


CHANGELOG = '''
## 6.8 – Paket H1b (PAKET-H1B)
* **Frame-Balken** in Replay Lab („Zeitliche Navigation“) und Simulations-Diagramm: senkrechter, ziehbarer Balken am
  angezeigten Frame (`assets/framebar.js`). Der Balken ist ein eigenes Element über dem Plotly-Diagramm (keine Plotly-Shape:
  deren Ziehen meldet erst beim Loslassen, verschiebt frei in y und machte auch Ereignislinien ziehbar). Ziehen baut das
  Diagramm nicht neu (Zoom, Legende und Bereichsauswahl bleiben), meldet gedrosselt (150 ms) an `replay-bar`/`sim-bar` und
  setzt Frame und Bild; bei Wiedergabe läuft er clientseitig mit (Dash-Callback `c2c_framebar.move`). Programmatisches
  Setzen meldet nichts zurück. Die Kurve „Aktueller Frame“ (Patch) im Replay-Diagramm entfällt.
* **Kamera & Regler** im Settings-Reiter (`runtime_settings.py`): Kamera-Abtastung (`cam_sensor.scan_frequency`),
  Regler-Abtastung, Linien-Timeout, Lenkverstärkung (`cameracar_controller.*`), minimaler/maximaler Steuerwert
  (`car.v_min/v_max`). Nur im Stillstand, Bereiche validiert, sofort wirksam, dauerhaft über `config_store`; je Wert
  Anzeige „aktiv“ / „gespeichert (gilt ab nächstem Start)“; Standardwerte unverändert; fehlerhafte gespeicherte Werte
  ergeben die Standardwerte (`ConfigReader.get_int/get_float` brechen bei ungültigen Werten nicht mehr ab).
  Verhaltensrelevante Werte sind gekennzeichnet; Hardware-Kalibrierung, Auflösung und colormode bleiben außen vor (H3).
* Ereignis `CONFIG_SAVED` bei erfolgreichem dauerhaften Speichern; `ConfigReader.set_value`; Simulation liest nach
  Änderungen der Settings die Regler-Konfiguration neu.
'''

ARCH_NOTE = ('Sessions v1 (V5.4/5.5) werden gelesen; ohne ROI-Angabe zeichnet das HUD keine Linien.')


def patch_architecture(t, f):
    return sub_once(t, '### 7.2 Pipeline Studio: Stufen kommen aus dem Prozessor',
                    '### 7.1b Frame-Balken (PAKET-H1B)\n'
                    'Diagramme mit `frame_bar=<Store-ID>` (`chart_kit.chart_block`) tragen `data-framebar`; `assets/framebar.js` legt ein\n'
                    'DOM-Element über das Plotly-Diagramm. Index → Balken: clientseitiger Callback `c2c_framebar.move` (x-Wert =\n'
                    '`data[0].x[index]`); Balken → Server: `dcc.Store` `{frame, nonce}` (gedrosselt), ausgewertet in `seek` (Replay) und\n'
                    '`sim_playback` (Simulation) über `replay_selection.nearest_position`.\n\n'
                    '### 7.2 Pipeline Studio: Stufen kommen aus dem Prozessor', f)


PATCHES = {
    'ConfigReader.py': patch_config_reader,
    'car_application.py': patch_car_application,
    'offline_application.py': patch_offline,
    'chart_kit.py': patch_chart_kit,
    'dashboard/context.py': patch_context,
    'Camp2CodeDashboard.py': patch_dashboard_start,
    'replay_selection.py': patch_replay_selection,
    'dashboard/tabs/replay.py': patch_replay_tab,
    'sim_lab.py': patch_sim_lab,
    'help_texts.py': patch_help_texts,
    'dashboard/tabs/settings.py': patch_settings_tab,
    'assets/studio.css': patch_css,
    'tests/dash_stub.py': patch_dash_stub,
    'tests/test_dashboard_callbacks.py': patch_test_callbacks,
    'tests/test_dashboard_plugins.py': patch_test_plugins,
    'tests/test_dashboard_real_dash.py': patch_test_real_dash,
    'tests/test_paket_c.py': patch_test_paket_c,
    'tests/test_paket_h1.py': patch_test_paket_h1,
    'CHANGELOG.md': lambda t, f: insert(t, '# Changelog', CHANGELOG, f, blank_after=1),
    'ARCHITECTURE.md': patch_architecture,
}


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    results = {}
    try:
        for name, func in PATCHES.items():
            path = root / name
            text = path.read_text(encoding='utf-8')
            if MARKER in text:
                print(f'  = {name} (bereits gepatcht)')
                continue
            new = func(text, name)
            if MARKER not in new:
                raise PatchError(f'{name}: Marker fehlt nach dem Patch')
            if name.endswith('.py'):
                compile(new, name, 'exec')
            results[path] = new
    except (PatchError, SyntaxError, OSError) as exc:
        print(f'PATCH ABGEBROCHEN, keine Datei geaendert: {exc}', file=sys.stderr)
        return 1
    for path, new in results.items():
        path.write_text(new, encoding='utf-8', newline='\n')
        print(f'  + {path.relative_to(root)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
