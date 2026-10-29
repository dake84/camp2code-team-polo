"""Simulation im Driving Cockpit: Umschalter Live | Simulation (Punkt 5 aus Paket F).

Im Modus *Simulation* wird eine gespeicherte Session (oder der Live-Ringbuffer)
mit frei waehlbarem Prozessor und Parametersatz neu gerechnet
(``sim_engine.SimEngine``) und Frame fuer Frame "damals" gegen "jetzt" gezeigt.

Sicherheit:
    * Die Simulation nutzt eine ``ReplayCar``; es werden keine Motoren angesteuert.
    * Solange das Cockpit auf *Simulation* steht, ist START gesperrt
      (``SimLab.active`` wird in ``handle_actions`` und ``global_status`` geprueft).
    * Waehrend einer Fahrt wird keine Simulation berechnet (CPU-Last).
    * Der Zustand ist serverseitig; bei mehreren Browsern gilt die letzte Auswahl.

Dash-Objekte werden uebergeben statt importiert, damit Tests (Dash-Attrappe)
und Betrieb (echtes Dash) dieselbe Klasse nutzen koennen.
"""
import bisect
import itertools
import logging
import threading
import time
from collections import deque
from types import SimpleNamespace

import cv2
import numpy as np

import chart_kit  # PAKET-H1
from processor_registry import label_for, processor_options

log = logging.getLogger(__name__)

LIVE = '__live__'
# PAKET-H1: Wiedergabe nach aufgezeichneten Zeitstempeln
PLAY_TICK_MS = 150   # Anzeigetakt; bei 150 ms sind hoechstens etwa 6 bis 7 Anzeigeframes/s moeglich
SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
SPEED_LABELS = (('0,25x', 0.25), ('0,5x', 0.5), ('1x', 1.0), ('2x', 2.0), ('4x', 4.0))
PLAY_LABEL = '▶ Play'
PAUSE_LABEL = '⏸ Pause'
STALE_HINT = 'Einstellungen geändert - bitte neu berechnen'
IMAGE_STYLE = {'width': '100%', 'background': '#0b1220', 'borderRadius': '8px', 'objectFit': 'contain'}
AXIS = dict(gridcolor='#2a3550', zerolinecolor='#3a4766', color='#c8d3e6')


def _fmt(value, digits=0, unit=''):
    if value is None:
        return '–'
    try:
        return f'{float(value):.{digits}f}{unit}'
    except (TypeError, ValueError):
        return str(value)


def _pct(value):
    return '–' if value is None else f'{value * 100:.0f} %'


def _label(image, text, color=(255, 255, 255)):
    if image is None:
        return None
    image = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    cv2.putText(image, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return image


def _int_or_none(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


class SimLab:
    """Layout und Callbacks der Cockpit-Simulation."""

    CALLBACKS = ('sim_view_mode', 'sim_run', 'sim_playback', 'sim_render')  # PAKET-H1

    def __init__(self, application, ui, uri, root='Data_AI/sessions'):
        self.application = application
        self.ui = ui
        self.uri = uri
        self.root = root
        self.active = False
        self._run_lock = threading.Lock()
        self._results = {}
        self._engine = None
        self._play = None            # PAKET-H1: Medienuhr der Wiedergabe (Anker, Tempo, Modus)
        self._play_lock = threading.Lock()
        self._ts_cache = None
        self._skipped = 0
        self._renders = deque(maxlen=20)
        self._clock = time.monotonic

    # ------------------------------------------------------------------ Layout
    @staticmethod
    def wrap_cockpit(cockpit, dbc, dcc, html):
        """Umschalter + bisheriges Cockpit (Live) + Simulationsbereich."""
        switch = dbc.Row([
            dbc.Col(dbc.RadioItems(id='cockpit-source', value='live', inline=True, className='mb-1',
                                   options=[{'label': 'Live', 'value': 'live'},
                                            {'label': 'Simulation (Session-Replay)', 'value': 'sim'}]),
                    xs=12, md=6),
            dbc.Col(html.Div(id='sim-mode-msg', className='small text-info'), xs=12, md=6),
        ], className='g-2 mb-2', align='center')
        return html.Div([switch,
                         dbc.Collapse(cockpit, id='cockpit-live-wrap', is_open=True),
                         dbc.Collapse(SimLab._panel(dbc, dcc, html), id='sim-wrap', is_open=False),
                         dcc.Interval(id='sim-play-tick', interval=PLAY_TICK_MS, n_intervals=0, disabled=True),
                         dcc.Store(id='sim-result-token', data=None, storage_type='memory')])

    @staticmethod
    def _panel(dbc, dcc, html):
        from help_texts import info as help_info, label as help_label  # PAKET-H1
        from replay_hud import legend_block

        def card(title, content):
            return dbc.Card([dbc.CardHeader(title), dbc.CardBody(content)], className='h-100')

        def number(id_, value=None, placeholder=''):
            # debounce: erst bei Enter/Verlassen des Feldes (nicht bei jeder Ziffer verwerfen)
            return dcc.Input(id=id_, type='number', step=1, value=value, placeholder=placeholder,
                             debounce=True, className='form-control form-control-sm')

        settings = card('Simulation · Quelle, Prozessor, Parameter', [
            dbc.Row([
                dbc.Col([html.Label('Quelle', className='small'),
                         dcc.Dropdown(id='sim-session', className='c2c-dropdown', clearable=False,
                                      options=[], value=LIVE)], md=4),
                dbc.Col([html.Label('Prozessor', className='small'),
                         dcc.Dropdown(id='sim-processor', className='c2c-dropdown', clearable=False,
                                      options=processor_options(live_only=False), value='copilot')], md=3),
                dbc.Col([html.Label('Parameter', className='small'),
                         dbc.RadioItems(id='sim-params', value='recorded', options=[
                             {'label': 'von damals', 'value': 'recorded'},
                             {'label': 'aktuelle Studio-Slider', 'value': 'studio'}])], md=2),
                dbc.Col([html.Label('Frame von / bis', className='small'),
                         dbc.Row([dbc.Col(number('sim-from', placeholder='Anfang')),
                                  dbc.Col(number('sim-to', placeholder='Ende'))], className='g-1')], md=2),
                dbc.Col([html.Label('Vorlauf', className='small'), number('sim-warmup', 30)], md=1),
            ], className='g-2'),
            dbc.Button('Simulation berechnen', id='sim-run', color='primary', size='sm', className='mt-2 me-2'),
            html.Span('Keine Motoransteuerung · START ist im Simulationsmodus gesperrt · '
                      'Änderungen an diesen Einstellungen verwerfen das Ergebnis',
                      className='small text-secondary'),
            html.Div(id='sim-status', className='small mt-2'),
        ])
        controls = dbc.Row([
            dbc.Col([help_label('Tempo', 'simspeed', dbc, html, 'small'),
                     dbc.RadioItems(id='sim-speed', value=1.0, inline=True,
                                    options=[{'label': text, 'value': value} for text, value in SPEED_LABELS])],
                    md=5),
            dbc.Col([help_label('Wiedergabe', 'simmode', dbc, html, 'small'),
                     dbc.RadioItems(id='sim-timing', value='realtime', inline=True, options=[
                         {'label': 'Echtzeit', 'value': 'realtime'}, {'label': 'Jeder Frame', 'value': 'every'}])],
                    md=4),
            dbc.Col([help_label('Anzeigerate', 'simrate', dbc, html, 'small'),
                     html.Div(id='sim-display-rate', className='small')], md=12),
        ], className='g-2 mt-1')
        viewer = card(['Damals · Jetzt ', *help_info('hud', dbc, html, suffix='sim')], [
            html.Div(id='sim-frame-label', className='fw-semibold mb-2'),
            dbc.Row([dbc.Col([html.Div('DAMALS (Aufzeichnung)', className='c2c-kpi-label'),
                              html.Img(id='sim-img-then', style=IMAGE_STYLE)], md=6),
                     dbc.Col([html.Div('JETZT (Simulation)', className='c2c-kpi-label'),
                              html.Img(id='sim-img-now', style=IMAGE_STYLE)], md=6)], className='g-2'),
            legend_block(html),
            dbc.ButtonGroup([dbc.Button('◀', id='sim-prev', size='sm'),
                             dbc.Button('▶ Play', id='sim-play', size='sm'),
                             dbc.Button('▶|', id='sim-next', size='sm')], className='mt-2 mb-2'),
            dcc.Slider(id='sim-index', min=0, max=1, step=1, value=0, marks=None,
                       tooltip={'placement': 'bottom'}),
            controls,
        ])
        compare = card('Vergleich', [html.Div(id='sim-compare'), html.Hr(), html.Div(id='sim-stats')])
        chart = card('Stellwert und Linienanzahl · damals vs. jetzt', [
            chart_kit.chart_block(dcc, html, 'sim-chart', 280, 'Noch keine Simulation berechnet',
                                  frame_bar='sim-bar',
                                  note='Blauen Balken ziehen oder Klick auf einen Punkt = Frame anzeigen · '
                                       'grau = Vorlauf (nicht in den Kennzahlen) · '
                                       'rote Rauten = Regler-Stopp · Mausrad = Zoom · Legende: Klick = Kurve ein/aus')])
        return html.Div([
            dbc.Row([dbc.Col(settings, md=12)], className='g-3 mb-3'),
            dbc.Row([dbc.Col(viewer, lg=8), dbc.Col(compare, lg=4)], className='g-3 mb-3'),
            dbc.Row([dbc.Col(chart, md=12)], className='g-3'),
        ])

    # ------------------------------------------------------------------ Logik
    def _source(self, name):
        from frame_source import RingbufferSource, SessionSource
        if name in (None, LIVE):
            health = getattr(self.application, 'health', None)
            events = health.events() if health is not None else ()
            return RingbufferSource(self.application.car.frame_history.snapshot(), events)
        return SessionSource(name, self.root)

    def _studio_params(self):
        params = getattr(getattr(self.application, 'sensor', None), 'processing_parameters', None)
        if params is None:
            raise ValueError('Keine aktuellen Studio-Parameter verfügbar')
        return tuple(params)

    def _get_engine(self):
        revision = getattr(self.application, 'settings_revision', 0)  # PAKET-H1B: nach Settings neu lesen
        if self._engine is None or getattr(self, '_engine_revision', None) != revision:
            from sim_engine import SimEngine
            self._engine = SimEngine()
            self._engine_revision = revision
        return self._engine

    def simulate(self, name, processor, param_mode, lo, hi, warmup):
        """Rechnet und speichert das Ergebnis; liefert (token, result)."""
        if self.application.running():
            raise RuntimeError('Während einer Fahrt wird nicht simuliert (CPU-Last). Erst STOP.')
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError('Es läuft bereits eine Simulation')
        try:
            source = self._source(name)
            override = self._studio_params() if param_mode == 'studio' else None
            result = self._get_engine().run(source, processor or 'copilot', override,
                                            _int_or_none(lo), _int_or_none(hi), _int_or_none(warmup) or 0)
        finally:
            self._run_lock.release()
        token = time.monotonic_ns()
        self._results = {key: value for key, value in self._results.items() if key == token}
        self._results[token] = (result, source)
        return token, result

    def lookup(self, token):
        return self._results.get(token)

    @staticmethod
    def first_measured(result):
        return next((i for i, frame in enumerate(result.frames) if not frame.warmup), 0)

    def valid_speed(self, value):
        """Erlaubte Wiedergabetempi (0,25x bis 4x); alles andere ergibt 1x."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 1.0
        return value if value in SPEEDS else 1.0

    def discard(self):
        """Verwirft Ergebnis und Wiedergabezustand (Einstellungen wurden geaendert)."""
        self._results = {}
        self._play = None
        self._ts_cache = None
        self._skipped = 0
        self._renders.clear()

    def _timestamps(self, token, result):
        """Nicht fallende aufgezeichnete Zeitstempel (SimFrame.captured_ns) je Frame."""
        if self._ts_cache is None or self._ts_cache[0] != token:
            self._ts_cache = (token, list(itertools.accumulate((f.captured_ns for f in result.frames), max)))
        return self._ts_cache[1]

    def _start_clock(self, frames, index, now, speed, timing):
        """Verankert die Medienuhr am angezeigten Frame (Start, Sprung, Tempo- oder Modus-Wechsel)."""
        self._play = {'index': index, 'media_ns': frames[index].captured_ns, 'wall': now,
                      'speed': speed, 'timing': timing}

    def advance(self, result, token, index, now, speed, timing):
        """Naechster Anzeigeframe nach den aufgezeichneten Zeitstempeln; liefert (index, fertig).

        * ``realtime``: Medienzeit = Anker + Wanduhr * Tempo; es wird der letzte Frame bis zu dieser
          Zeit gezeigt, dazwischenliegende Frames werden uebersprungen.
        * ``every``: hoechstens ein Frame weiter je Aufruf, nie ueberspringen; der Anker wandert mit dem
          angezeigten Frame mit, die Wiedergabe laeuft also langsamer, wenn die Anzeige nicht hinterherkommt.
        """
        frames = result.frames
        last = len(frames) - 1
        play = self._play
        if play is None or play['index'] != index or play['speed'] != speed or play['timing'] != timing:
            self._start_clock(frames, index, now, speed, timing)
            play = self._play
        if index >= last:
            return index, True
        stamps = self._timestamps(token, result)
        media = play['media_ns'] + (now - play['wall']) * 1e9 * speed
        if timing == 'every':
            if media >= stamps[index + 1]:
                index += 1
                play.update(index=index, media_ns=stamps[index], wall=now)
        else:
            target = max(index, min(last, bisect.bisect_right(stamps, media) - 1))
            if target != index:
                self._skipped += target - index - 1
                index = target
                play['index'] = index
        return index, index >= last

    def playback(self, trigger, token, speed, timing, index, paused, click=None, now=None, bar=None):
        """Logik des Wiedergabe-Callbacks: (Index, Takt pausiert, Play-Beschriftung).

        Play ist nur mit gueltigem (berechnetem) Ergebnis moeglich; ein neues oder verworfenes Ergebnis
        pausiert. Am Ende wird automatisch pausiert. Tempo und Modus brauchen keine Neuberechnung.
        """
        keep = self.ui.no_update
        now = self._clock() if now is None else now
        with self._play_lock:
            entry = self.lookup(token)
            if entry is None:
                self._play = None
                return 0, True, PLAY_LABEL
            result = entry[0]
            frames = result.frames
            last = max(0, len(frames) - 1)
            index = max(0, min(int(index or 0), last))
            speed = self.valid_speed(speed)
            timing = 'every' if timing == 'every' else 'realtime'
            playing = not paused
            if trigger == 'sim-result-token':
                self._play = None
                return self.first_measured(result), True, PLAY_LABEL
            if trigger == 'sim-prev':
                return max(0, index - 1), keep, keep
            if trigger == 'sim-next':
                return min(last, index + 1), keep, keep
            if trigger == 'sim-bar':  # PAKET-H1B: Frame-Balken gezogen; bei laufender Wiedergabe verankert advance() die Uhr neu
                from replay_selection import nearest_position
                position = nearest_position([frame.frame_id for frame in frames], (bar or {}).get('frame'))
                return (position, keep, keep) if position is not None else (keep, keep, keep)

            if trigger == 'sim-chart':
                points = (click or {}).get('points') or [{}]
                value = points[0].get('customdata')
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return max(0, min(last, int(value))), keep, keep
                return keep, keep, keep
            if trigger == 'sim-play':
                if playing:
                    self._play = None
                    return keep, True, PLAY_LABEL
                if index >= last:
                    index = self.first_measured(result)
                self._start_clock(frames, index, now, speed, timing)
                self._skipped = 0
                self._renders.clear()
                return index, False, PAUSE_LABEL
            if trigger in ('sim-speed', 'sim-timing'):
                if playing:
                    self._start_clock(frames, index, now, speed, timing)
                return keep, keep, keep
            if trigger == 'sim-play-tick' and playing:
                new_index, finished = self.advance(result, token, index, now, speed, timing)
                if finished:
                    self._play = None
                return (new_index if new_index != index else keep), (True if finished else keep), \
                    (PLAY_LABEL if finished else keep)
            return keep, keep, keep

    def note_render(self, frame, now=None):
        """Merkt sich einen ausgelieferten Anzeigeframe (Wanduhr, aufgezeichnete Zeit) fuer die Anzeigerate."""
        self._renders.append((self._clock() if now is None else now, frame.captured_ns))

    def baseline_text(self, source):
        fps = getattr(source, 'median_fps', None)
        if not fps:
            return '–'
        return f'Aufnahme: Median {fps:.1f} Hz ({getattr(source, "fps_source", "?")})'

    def rate_text(self, source):
        """Gemessene Anzeigerate (serverseitig ausgelieferte Frames/s) und effektives Tempo.

        Gemessen wird, wie oft der Server Anzeigeframes ausliefert; die Darstellung im Browser ist nicht gemessen.
        """
        parts = []
        renders = list(self._renders)
        if len(renders) >= 2 and renders[-1][0] > renders[0][0]:
            span = renders[-1][0] - renders[0][0]
            parts.append(f'Anzeige {(len(renders) - 1) / span:.1f} Frames/s (serverseitig gemessen)')
            parts.append(f'effektives Tempo {(renders[-1][1] - renders[0][1]) / 1e9 / span:.2f}x')
        else:
            parts.append('Anzeige wird gemessen')
        if self._skipped:
            parts.append(f'übersprungen {self._skipped}')
        fps = getattr(source, 'median_fps', None)
        if fps:
            parts.append(f'Aufnahme Median {fps:.1f} Hz ({getattr(source, "fps_source", "?")})')
        return ' · '.join(parts)

    def chart(self, result):
        frames = result.frames
        ids = [f.frame_id for f in frames]
        pos = list(range(len(frames)))
        traces = [
            dict(type='scatter', mode='lines', name='Stellwert damals', x=ids, customdata=pos,
                 y=[f.then_car_angle for f in frames], line={'color': '#93a2bd'}),
            dict(type='scatter', mode='lines+markers', name='Stellwert jetzt', x=ids, customdata=pos,
                 y=[f.car_angle for f in frames], marker={'size': 3}, line={'color': '#ffc533'}),
            dict(type='scatter', mode='lines', name='Linien damals', x=ids, customdata=pos, yaxis='y2',
                 y=[f.then_lane_count for f in frames], line={'shape': 'hv', 'dash': 'dot', 'color': '#5aa0ff'}),
            dict(type='scatter', mode='lines', name='Linien jetzt', x=ids, customdata=pos, yaxis='y2',
                 y=[f.lane_count for f in frames], line={'shape': 'hv', 'color': '#2fbf8f'}),
        ]
        stops = [(i, f) for i, f in enumerate(frames) if f.stop_reason]
        if stops:
            traces.append(dict(type='scatter', mode='markers', name='Regler-Stopp',
                               x=[f.frame_id for _, f in stops], y=[f.then_car_angle or 90 for _, f in stops],
                               customdata=[i for i, _ in stops], text=[f.stop_reason for _, f in stops],
                               marker={'symbol': 'diamond', 'size': 11, 'color': '#e5534b'}))
        shapes = []
        warm = [f.frame_id for f in frames if f.warmup]
        if warm:
            shapes.append(dict(type='rect', xref='x', yref='paper', x0=min(warm), x1=max(warm), y0=0, y1=1,
                               fillcolor='rgba(147,162,189,0.15)', line={'width': 0}))
        layout = dict(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#111a2b',
                      font={'color': '#e9f1ff'}, margin=dict(l=45, r=45, t=25, b=40),
                      legend=dict(orientation='h', y=1.15, x=0), shapes=shapes,
                      xaxis=dict(AXIS, title={'text': 'Frame-ID'}),
                      yaxis=dict(AXIS, title={'text': 'Stellwert °'}, range=[40, 140]),
                      yaxis2=dict(AXIS, title={'text': 'Linien'}, overlaying='y', side='right', range=[0, 3]),
                      uirevision=f'sim-{id(result)}')
        return {'data': traces, 'layout': layout}

    def stats_table(self, result):
        html = self.ui.html
        s = result.stats()
        rows = [('2 Linien erkannt', _pct(s['two_lanes_then']), _pct(s['two_lanes_now'])),
                ('Plausibles Linienpaar', _pct(s['plausible_then']), _pct(s['plausible_now'])),
                ('Längste unplausible Serie', _fmt(s['longest_gap_then']), _fmt(s['longest_gap_now'])),
                ('Vorwert gehalten (Frames)', '–', _fmt(s['held_now'])),
                ('Regler-Stopps', '–', _fmt(s['stops_now'])),
                ('Ø |Δ Stellwert|', '', _fmt(s['mean_abs_delta'], 1, '°')),
                ('Abweichung ≥ 5°', '', _fmt(s['differs_5deg'])),
                ('Rechenzeit Median', '', _fmt(s['process_ms_median'], 1, ' ms'))]
        head = html.Thead(html.Tr([html.Th('Bereich'), html.Th('damals'), html.Th('jetzt')]))
        body = html.Tbody([html.Tr([html.Td(a), html.Td(b), html.Td(c)]) for a, b, c in rows])
        return html.Div([html.Div(f"{s['frames']} Frames ohne Vorlauf", className='small text-secondary'),
                         html.Table([head, body], className='table table-sm table-dark mb-0 small')])

    def frame_table(self, frame):
        html = self.ui.html
        rows = [('Linien', _fmt(frame.then_lane_count), _fmt(frame.lane_count)),
                ('plausibel', '–' if frame.then_plausible is None else ('ja' if frame.then_plausible else 'nein'),
                 'ja' if frame.plausible else 'nein'),
                ('Rohwinkel', _fmt(frame.then_raw_angle, 0, '°'), _fmt(frame.raw_angle, 0, '°')),
                ('Stellwert', _fmt(frame.then_car_angle, 0, '°'), _fmt(frame.car_angle, 0, '°')),
                ('Speed-Steuerwert', _fmt(frame.then_speed), _fmt(frame.speed)),
                ('Status', frame.then_status or '–',
                 frame.stop_reason or ('Vorwert gehalten' if frame.held else 'Entscheidung'))]
        head = html.Thead(html.Tr([html.Th('Frame'), html.Th('damals'), html.Th('jetzt')]))
        body = html.Tbody([html.Tr([html.Td(a), html.Td(b), html.Td(c)]) for a, b, c in rows])
        return html.Table([head, body], className='table table-sm table-dark mb-0 small')

    def render_frame(self, result, source, position):
        """Damals (HUD der Aufzeichnung) und jetzt (HUD der Simulation), Kennzeichnung unten im Bild."""
        from dashboard_overlay import roi_rows
        from replay_hud import draw_hud, encode_lanes
        frame = result.frames[position]
        raw = source.image(frame.index)
        then = source.then_image(frame.index)
        top, bottom = roi_rows(SimpleNamespace(crop=frame.crop), raw.shape[0])
        row = {'frame_id': frame.frame_id, 'roi_top_px': top, 'roi_bottom_px': bottom,
               'lanes_json': encode_lanes(frame.lanes), 'vehicle_mode': 'auto',
               'data_status': 'complete' if frame.car_angle is not None else 'no_driving_state',
               'steering_angle': frame.raw_angle, 'requested_angle': frame.requested_angle,
               'steering_angle_car': frame.car_angle, 'speed': frame.speed, 'vehicle_mode_key': 'camera'}
        if frame.stop_reason:
            row['event_codes'] = 'STOP_REASON'
            row['stop_reason'] = frame.stop_reason
        footer = f'JETZT (Simulation) - {result.processor_key.upper()}' + (' - VORLAUF' if frame.warmup else '')
        return then, draw_hud(raw, row, footer=footer)

    # ------------------------------------------------------------------ Callbacks
    def register(self, app):
        ui = self.ui
        Input, Output, State, no_update = ui.Input, ui.Output, ui.State, ui.no_update
        html = ui.html

        @app.callback(Output('cockpit-live-wrap', 'is_open'), Output('sim-wrap', 'is_open'),
                      Output('sim-session', 'options'), Output('sim-mode-msg', 'children'),
                      Input('cockpit-source', 'value'))
        def sim_view_mode(source):
            """Live | Simulation. Im Simulationsmodus ist START gesperrt."""
            self.active = source == 'sim'
            offline = getattr(self.application, 'offline', False)
            from session_recorder import list_sessions
            options = [{'label': 'Live-Ringbuffer (Momentaufnahme)', 'value': LIVE}] + [
                {'label': name, 'value': name} for name in list_sessions(self.root)]
            if self.active:
                message = 'SIMULATION · START gesperrt · keine Motoransteuerung'
            else:
                message = 'OFFLINE-Modus: keine Kamera, keine Motoren' if offline else ''
            return not self.active, self.active, options, message

        @app.callback(Output('sim-status', 'children'), Output('sim-stats', 'children'),
                      Output('sim-index', 'max'), Output('sim-chart', 'figure'),
                      Output('sim-result-token', 'data'),
                      Input('sim-run', 'n_clicks'),
                      Input('sim-session', 'value'), Input('sim-processor', 'value'),
                      Input('sim-params', 'value'), Input('sim-from', 'value'), Input('sim-to', 'value'),
                      Input('sim-warmup', 'value'), prevent_initial_call=True)
        def sim_run(_n, name, processor, param_mode, lo, hi, warmup):
            """Berechnen (Button) oder Ergebnis verwerfen (eine Einstellung wurde geaendert).

            Quelle, Prozessor, Parameterquelle, Frame-Bereich und Vorlauf gehoeren zum Ergebnis: Aendert der
            Nutzer eines davon, wird die Wiedergabe pausiert (Token None), das Ergebnis verworfen, die
            Anzeigen werden geleert und es erscheint der Hinweis zum Neuberechnen.
            """
            if ui.ctx.triggered_id != 'sim-run':
                self.discard()
                return (STALE_HINT, '', 1, chart_kit.empty_figure(STALE_HINT), None)
            try:
                token, result = self.simulate(name, processor, param_mode, lo, hi, warmup)
            except (RuntimeError, ValueError, OSError) as exc:
                return f'Simulation nicht möglich: {exc}', no_update, no_update, no_update, no_update
            measured = result.measured
            entry = self.lookup(token)
            source = entry[1] if entry is not None else None
            fps = getattr(source, 'median_fps', None)
            text = (f'{result.source_label} · {label_for(result.processor_key)} · Parameter '
                    f"{'Studio' if result.param_mode == 'studio' else 'von damals'} · "
                    f'{len(measured)} Frames (+{len(result.frames) - len(measured)} Vorlauf) in '
                    f'{result.duration_s:.1f} s' + (' · ABGESCHNITTEN' if result.truncated else '')
                    + (f' · {result.errors} Frames übersprungen' if result.errors else '')
                    + (f' · Aufnahme Median {fps:.1f} Hz ({source.fps_source})' if fps else ''))
            if not result.frames:
                return text + ' · keine verwertbaren Frames', no_update, no_update, no_update, no_update
            return text, self.stats_table(result), max(0, len(result.frames) - 1), self.chart(result), token

        @app.callback(Output('sim-index', 'value'), Output('sim-play-tick', 'disabled'),
                      Output('sim-play', 'children'),
                      Input('sim-prev', 'n_clicks'), Input('sim-next', 'n_clicks'), Input('sim-play', 'n_clicks'),
                      Input('sim-play-tick', 'n_intervals'), Input('sim-chart', 'clickData'),
                      Input('sim-result-token', 'data'), Input('sim-speed', 'value'), Input('sim-timing', 'value'),
                      State('sim-index', 'value'), State('sim-play-tick', 'disabled'), Input('sim-bar', 'data'),
                      prevent_initial_call=True)
        def sim_playback(_prev, _next, _play, _tick, click, token, speed, timing, index, paused, bar=None):
            """Schritte, Sprung ins Diagramm (Klick oder Frame-Balken) und Wiedergabe nach den Zeitstempeln."""
            return self.playback(ui.ctx.triggered_id, token, speed, timing, index, paused, click, bar=bar)

        @app.callback(Output('sim-img-then', 'src'), Output('sim-img-now', 'src'),
                      Output('sim-compare', 'children'), Output('sim-frame-label', 'children'),
                      Output('sim-display-rate', 'children'),
                      Input('sim-index', 'value'), Input('sim-result-token', 'data'),
                      State('sim-play-tick', 'disabled'))
        def sim_render(index, token, paused):
            entry = self.lookup(token)
            if entry is None:
                return None, None, 'Noch keine Simulation berechnet', '', ''
            result, source = entry
            if not result.frames:
                return None, None, 'Keine Frames', '', ''
            position = max(0, min(int(index or 0), len(result.frames) - 1))
            frame = result.frames[position]
            if paused:
                # Nach einer Pause bleibt die zuletzt gemessene Rate stehen; bei neuem Ergebnis nur die Aufnahmerate.
                rate = self.baseline_text(source) if ui.ctx.triggered_id == 'sim-result-token' else no_update
            else:
                self.note_render(frame)
                rate = self.rate_text(source)
            try:
                then, now = self.render_frame(result, source, position)
            except (ValueError, OSError, cv2.error) as exc:
                return None, None, f'Bild nicht verfügbar: {exc}', f'Frame #{frame.frame_id}', rate
            label = f'Frame #{frame.frame_id}' + (' · Vorlauf' if frame.warmup else '')
            if frame.stop_reason:
                label += f' · STOPP {frame.stop_reason}'
            return self.uri(then), self.uri(now), self.frame_table(frame), html.Span(label), rate

        clientside = getattr(ui, 'ClientsideFunction', None)
        if clientside is not None and hasattr(app, 'clientside_callback'):  # PAKET-H1B: Frame-Balken (assets/framebar.js)
            app.clientside_callback(clientside('c2c_framebar', 'move'), Output('sim-chart-barsync', 'data'),
                                    Input('sim-index', 'value'), Input('sim-chart', 'figure'),
                                    State('sim-chart-box', 'id'))

        return self.CALLBACKS
