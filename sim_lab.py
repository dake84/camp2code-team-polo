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
import logging
import threading
import time

import cv2
import numpy as np

from processor_registry import label_for, processor_options

log = logging.getLogger(__name__)

LIVE = '__live__'
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

    CALLBACKS = ('sim_view_mode', 'sim_run', 'sim_seek', 'sim_play_toggle', 'sim_render')

    def __init__(self, application, ui, uri, root='Data_AI/sessions'):
        self.application = application
        self.ui = ui
        self.uri = uri
        self.root = root
        self.active = False
        self._run_lock = threading.Lock()
        self._results = {}
        self._engine = None

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
                         dcc.Interval(id='sim-play-tick', interval=250, n_intervals=0, disabled=True),
                         dcc.Store(id='sim-result-token', data=None, storage_type='memory')])

    @staticmethod
    def _panel(dbc, dcc, html):
        def card(title, content):
            return dbc.Card([dbc.CardHeader(title), dbc.CardBody(content)], className='h-100')

        def number(id_, value=None, placeholder=''):
            return dcc.Input(id=id_, type='number', step=1, value=value, placeholder=placeholder,
                             className='form-control form-control-sm')

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
            html.Span('Keine Motoransteuerung · START ist im Simulationsmodus gesperrt',
                      className='small text-secondary'),
            html.Div(id='sim-status', className='small mt-2'),
        ])
        viewer = card('Damals · Jetzt', [
            html.Div(id='sim-frame-label', className='fw-semibold mb-2'),
            dbc.Row([dbc.Col([html.Div('DAMALS (Aufzeichnung)', className='c2c-kpi-label'),
                              html.Img(id='sim-img-then', style=IMAGE_STYLE)], md=6),
                     dbc.Col([html.Div('JETZT (Simulation)', className='c2c-kpi-label'),
                              html.Img(id='sim-img-now', style=IMAGE_STYLE)], md=6)], className='g-2'),
            dbc.ButtonGroup([dbc.Button('◀', id='sim-prev', size='sm'),
                             dbc.Button('▶ Play', id='sim-play', size='sm'),
                             dbc.Button('▶|', id='sim-next', size='sm')], className='mt-2 mb-2'),
            dcc.Slider(id='sim-index', min=0, max=1, step=1, value=0, marks=None,
                       tooltip={'placement': 'bottom'}),
        ])
        compare = card('Vergleich', [html.Div(id='sim-compare'), html.Hr(), html.Div(id='sim-stats')])
        chart = card('Stellwert und Linienanzahl · damals vs. jetzt', [
            dcc.Graph(id='sim-chart', config={'displayModeBar': False}),
            html.Div('Klick auf einen Punkt = Frame anzeigen · grau = Vorlauf (nicht in den Kennzahlen) · '
                     'rote Rauten = Regler-Stopp in der Simulation', className='small text-secondary')])
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
        if self._engine is None:
            from sim_engine import SimEngine
            self._engine = SimEngine()
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
        layout = dict(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#111a2b', height=280,
                      font={'color': '#e9f1ff'}, margin=dict(l=45, r=45, t=25, b=40),
                      legend=dict(orientation='h', y=1.15, x=0), shapes=shapes,
                      xaxis=dict(AXIS, title={'text': 'Frame-ID'}),
                      yaxis=dict(AXIS, title={'text': 'Stellwert °'}, range=[40, 140]),
                      yaxis2=dict(AXIS, title={'text': 'Linien'}, overlaying='y', side='right', range=[0, 3]),
                      uirevision=f'sim-{result.source_label}')
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
        from dashboard_overlay import render_calibration_overlay, render_overlay
        from models import CameraData, DrivingState
        frame = result.frames[position]
        raw = source.image(frame.index)
        then = source.then_image(frame.index)
        camera = CameraData(process_image=np.empty(0), raw_image=raw, frame_id=frame.frame_id,
                            crop=frame.crop, lanes=frame.lanes)
        if frame.car_angle is not None:
            state = DrivingState(frame.frame_id, 0, frame.raw_angle if frame.raw_angle is not None else 90,
                                 frame.speed or 0, lanes=frame.lanes, steering_angle_car=frame.car_angle,
                                 requested_angle=frame.requested_angle)
            now = render_overlay(camera, state)
        else:
            now = render_calibration_overlay(camera)
        tag = 'MOCK' if result.processor_key == 'mock' else label_for(result.processor_key)
        return then, _label(now, f'JETZT · {tag}', (0, 255, 255) if result.processor_key == 'mock' else (255, 255, 255))

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
                      State('sim-session', 'value'), State('sim-processor', 'value'),
                      State('sim-params', 'value'), State('sim-from', 'value'), State('sim-to', 'value'),
                      State('sim-warmup', 'value'), prevent_initial_call=True)
        def sim_run(_n, name, processor, param_mode, lo, hi, warmup):
            try:
                token, result = self.simulate(name, processor, param_mode, lo, hi, warmup)
            except (RuntimeError, ValueError, OSError) as exc:
                return f'Simulation nicht möglich: {exc}', no_update, no_update, no_update, no_update
            measured = result.measured
            text = (f'{result.source_label} · {label_for(result.processor_key)} · Parameter '
                    f"{'Studio' if result.param_mode == 'studio' else 'von damals'} · "
                    f'{len(measured)} Frames (+{len(result.frames) - len(measured)} Vorlauf) in '
                    f'{result.duration_s:.1f} s' + (' · ABGESCHNITTEN' if result.truncated else '')
                    + (f' · {result.errors} Frames übersprungen' if result.errors else ''))
            if not result.frames:
                return text + ' · keine verwertbaren Frames', no_update, no_update, no_update, no_update
            return text, self.stats_table(result), max(0, len(result.frames) - 1), self.chart(result), token

        @app.callback(Output('sim-index', 'value'),
                      Input('sim-prev', 'n_clicks'), Input('sim-next', 'n_clicks'),
                      Input('sim-play-tick', 'n_intervals'), Input('sim-chart', 'clickData'),
                      Input('sim-result-token', 'data'), State('sim-index', 'value'),
                      prevent_initial_call=True)
        def sim_seek(_prev, _next, _tick, click, token, index):
            entry = self.lookup(token)
            if entry is None:
                return no_update
            result = entry[0]
            last = max(0, len(result.frames) - 1)
            trigger = ui.ctx.triggered_id
            index = max(0, min(int(index or 0), last))
            if trigger == 'sim-result-token':
                return self.first_measured(result)
            if trigger == 'sim-prev':
                return max(0, index - 1)
            if trigger in ('sim-next', 'sim-play-tick'):
                return min(last, index + 1)
            if trigger == 'sim-chart' and click and click.get('points'):
                value = click['points'][0].get('customdata')
                if isinstance(value, (int, float)):
                    return max(0, min(last, int(value)))
            return no_update

        @app.callback(Output('sim-play-tick', 'disabled'), Output('sim-play', 'children'),
                      Input('sim-play', 'n_clicks'), State('sim-play-tick', 'disabled'),
                      prevent_initial_call=True)
        def sim_play_toggle(_n, disabled):
            playing = bool(disabled)          # war pausiert -> jetzt abspielen
            return not playing, '⏸ Pause' if playing else '▶ Play'

        @app.callback(Output('sim-img-then', 'src'), Output('sim-img-now', 'src'),
                      Output('sim-compare', 'children'), Output('sim-frame-label', 'children'),
                      Input('sim-index', 'value'), Input('sim-result-token', 'data'))
        def sim_render(index, token):
            entry = self.lookup(token)
            if entry is None:
                return None, None, 'Noch keine Simulation berechnet', ''
            result, source = entry
            if not result.frames:
                return None, None, 'Keine Frames', ''
            position = max(0, min(int(index or 0), len(result.frames) - 1))
            frame = result.frames[position]
            try:
                then, now = self.render_frame(result, source, position)
            except (ValueError, OSError, cv2.error) as exc:
                return None, None, f'Bild nicht verfügbar: {exc}', f'Frame #{frame.frame_id}'
            label = f'Frame #{frame.frame_id}' + (' · Vorlauf' if frame.warmup else '')
            if frame.stop_reason:
                label += f' · STOPP {frame.stop_reason}'
            return self.uri(then), self.uri(now), self.frame_table(frame), html.Span(label)

        return self.CALLBACKS
