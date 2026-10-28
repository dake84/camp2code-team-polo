"""Tab "Replay Lab": Frame-Inspector mit HUD, Zeitnavigation, Ereignissprung, Export.

Tabuebergreifende Navigation (einziger Callback mit fremden Ausgaengen): ``replay_to_studio`` oeffnet
Tabs per ``main-tabs.active_tab`` und uebergibt einen Frame an das Pipeline Studio
(``replay-studio-token``, ``ab-source``, Referenz A im A/B-Labor). Er gehoert hierher, weil er
von Replay-Schaltern, dem Recording-Tab und dem Sprunglink im Ereignisprotokoll ausgeloest wird.
"""
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import cv2

from replay_hud import draw_hud, row_from_camera
from replay_selection import (event_window, nearest_position, record_frame_id, select_records,
                              selected_bounds)  # PAKET-H1B
from replay_studio import load_session_image, parameters_from_record, processor_key
from session_recorder import (export_live_records, export_session_range, list_sessions, load_events,
                              load_session)

from ..helpers import IMAGE_STYLE, LIVE, dark_figure, fmt, parse_jump
from .base import DashboardTab

log = logging.getLogger(__name__)


class ReplayTab(DashboardTab):
    tab_id = 'replay'
    label = 'Replay Lab'

    # ------------------------------------------------------------------ Layout
    def layout(self):
        c = self.ctx
        dbc, dcc, html = c.dbc, c.dcc, c.html
        return html.Div([
            dbc.Row([
                dbc.Col(dcc.Dropdown(id='session-choice', className='c2c-dropdown', clearable=False,
                                     options=[{'label': 'Live-Ringbuffer (flüchtig)', 'value': LIVE}],
                                     value=LIVE), md=6),
                dbc.Col(dbc.Button('Sessions aktualisieren', id='session-refresh', color='secondary'), md=2),
                dbc.Col(html.Div('Replay steuert keine Motoren.', className='small text-secondary'), md=4),
            ], className='g-2 mb-2', align='center'),
            dbc.Row([
                dbc.Col(c.card('Frame Inspector · Replay-HUD', [
                    html.Div([html.Span(id='replay-frame-label', className='fw-semibold'),
                              c.fs_button('replay-image'),
                              *c.info('hud', suffix='replay')], className='mb-2'),
                    html.Img(id='replay-image', style={**IMAGE_STYLE, 'maxHeight': '60vh'}),
                    c.legend(),
                    html.Div(id='replay-details', className='small mt-2')]), lg=8),
                dbc.Col(c.card('Replay-Steuerung', [
                    dbc.ButtonGroup([dbc.Button('◀', id='replay-prev'),
                                     dbc.Button('▶ Play', id='replay-play'),
                                     dbc.Button('▶|', id='replay-next')], className='mb-2'),
                    dcc.Slider(id='replay-index', min=0, max=1, step=1, value=0, marks=None,
                               tooltip={'placement': 'bottom'}),
                    html.Div(id='replay-range', className='small text-secondary mt-2'),
                    html.Hr(),
                    html.Label('Zu Ereignis springen', className='small'),
                    dcc.Dropdown(id='replay-event', className='c2c-dropdown', options=[], value=None),
                    dbc.Row([dbc.Col([html.Label('Vorlauf (Frames)', className='small'),
                                      dcc.Input(id='replay-pre', type='number', min=0, step=1, value=30,
                                                className='form-control form-control-sm')]),
                             dbc.Col([html.Label('Nachlauf (Frames)', className='small'),
                                      dcc.Input(id='replay-post', type='number', min=0, step=1, value=30,
                                                className='form-control form-control-sm')])],
                            className='g-2 mt-1'),
                    dbc.Button('Bereich zurücksetzen', id='replay-range-reset', color='secondary', size='sm',
                               className='mt-2 me-2'),
                    dbc.Button('Bereich exportieren', id='replay-export', color='success', size='sm',
                               className='mt-2'),
                    dcc.Download(id='replay-download'),
                    html.Hr(className='my-2'),
                    dbc.Button('Frame im Pipeline Studio analysieren', id='replay-to-studio',
                               color='info', size='sm'),
                    html.Div(id='replay-studio-msg', className='small mt-1')]), lg=4),
            ], className='g-3'),
            dbc.Row([dbc.Col(c.card('Zeitliche Navigation', [
                c.chart_block('replay-chart', 260, 'Noch keine Daten', frame_bar='replay-bar',
                              note='Blauen Balken ziehen oder Klick auf Messpunkt = Frame wählen · '
                                   'Ziehen im Diagramm oder Mausrad = Bereich wählen · '
                                   'Doppelklick = alles · Legende: Klick = Kurve ein/aus')]), md=12)],
                    className='mt-3'),
            dcc.Store(id='replay-playing', data=False, storage_type='memory'),
            dcc.Store(id='replay-bounds', data=None, storage_type='memory'),
        ])

    # ------------------------------------------------------------------ Daten
    def records(self, name, bounds):
        """(Datensaetze, Ereignisse, Pfad) gefiltert auf den Frame-ID-Bereich."""
        application, car = self.ctx.application, self.ctx.car
        bounds = tuple(bounds) if bounds else None
        if not name or name == LIVE:
            events = [{'frame_id': e.frame_id, 'code': e.code, 'detail': e.detail}
                      for e in application.health.events() if e.frame_id is not None]
            return select_records(car.frame_history.snapshot(), bounds), events, None
        try:
            path, rows = load_session(name)
            events = load_events(name)
        except (ValueError, OSError):
            return (), [], None
        return select_records(rows, bounds), events, path

    def record_at(self, name, bounds, index):
        """Datensaetze des Bereichs und der Datensatz an der Slider-Position."""
        records, events, path = self.records(name, bounds)
        if not records:
            return records, events, path, None
        position = max(0, min(int(index or 0), len(records) - 1))
        return records, events, path, records[position]

    # --------------------------------------------------------------- Callbacks
    def register_callbacks(self, app):
        c = self.ctx
        Input, Output, State = c.Input, c.Output, c.State
        ctx, no_update, dcc, Patch, go = c.ctx, c.no_update, c.dcc, c.Patch, c.go
        application, car, ab, uri = c.application, c.car, c.ab, c.uri
        records_for, record_at = self.records, self.record_at

        @app.callback(Output('session-choice', 'options'), Input('session-refresh', 'n_clicks'),
                      Input('last-action', 'data'))
        def refresh_sessions(_, _action):
            return [{'label': 'Live-Ringbuffer (flüchtig)', 'value': LIVE}] + [
                {'label': name, 'value': name} for name in list_sessions()]

        @app.callback(Output('replay-event', 'options'), Output('replay-event', 'value'),
                      Input('session-choice', 'value'))
        def replay_event_options(name):
            _, events, _ = records_for(name, None)
            options = []
            for event in events[-300:]:
                try:
                    frame_id = int(event.get('frame_id'))
                except (TypeError, ValueError):
                    continue
                options.append({'label': f"Frame {frame_id} · {event.get('code')}", 'value': frame_id})
            return options, None

        @app.callback(Output('replay-index', 'value'), Output('replay-bounds', 'data'),
                      Input('replay-prev', 'n_clicks'), Input('replay-next', 'n_clicks'),
                      Input('replay-chart', 'clickData'), Input('replay-chart', 'relayoutData'),
                      Input('replay-event', 'value'), Input('replay-range-reset', 'n_clicks'),
                      Input(self.tick_id, 'n_intervals'),
                      State('replay-index', 'value'), State('replay-playing', 'data'),
                      State('session-choice', 'value'), State('replay-bounds', 'data'),
                      State('replay-pre', 'value'), State('replay-post', 'value'),
                      State('main-tabs', 'active_tab'), Input('jump-url', 'hash'), Input('replay-bar', 'data'))
        def seek(_prev, _next, click, relayout, event_frame, _reset, _tick, index, playing, name, bounds,
                 pre, post, tab, jump=None, bar=None):
            trigger = ctx.triggered_id
            if tab != self.tab_id and trigger == self.tick_id:
                return no_update, no_update
            if trigger == 'jump-url':  # Sprung mit Vor-/Nachlauf, Session aus dem Link
                target = parse_jump(jump)
                if target is None:
                    return no_update, no_update
                session, frame = target
                window = event_window(frame, pre, post)
                records, _, _ = records_for(session, window)
                ids = [record_frame_id(r) for r in records]
                position = next((i for i, fid in enumerate(ids) if fid is not None and fid >= frame), 0)
                return position, list(window)
            if trigger == 'replay-bar':  # PAKET-H1B: Frame-Balken gezogen (assets/framebar.js), kein Neuaufbau des Diagramms
                records, _, _ = records_for(name, bounds)
                position = nearest_position([record_frame_id(r) for r in records], (bar or {}).get('frame'))
                return (position, no_update) if position is not None else (no_update, no_update)

            if trigger == 'replay-range-reset':
                return 0, None
            if trigger == 'replay-chart' and ctx.triggered and ctx.triggered[0]['prop_id'].endswith('relayoutData'):
                if not isinstance(relayout, dict) or not any(k.startswith('xaxis') for k in relayout):
                    return no_update, no_update
                chosen = selected_bounds(relayout)
                return 0, list(chosen) if chosen else None
            if trigger == 'replay-event' and event_frame is not None:
                window = event_window(event_frame, pre, post)
                records, _, _ = records_for(name, window)
                ids = [record_frame_id(r) for r in records]
                position = next((i for i, fid in enumerate(ids) if fid is not None and fid >= event_frame), 0)
                return position, list(window)
            records, _, _ = records_for(name, bounds)
            if not records:
                return 0, no_update
            index = max(0, min(int(index or 0), len(records) - 1))
            if trigger == 'replay-prev':
                return max(0, index - 1), no_update
            if trigger == 'replay-next':
                return min(len(records) - 1, index + 1), no_update
            if trigger == 'replay-chart' and click and click.get('points'):
                fid = click['points'][0].get('customdata')
                return next((i for i, r in enumerate(records) if record_frame_id(r) == fid), index), no_update
            if trigger == self.tick_id and playing:
                return min(len(records) - 1, index + 1), no_update
            return no_update, no_update

        # PAKET-H1B: Frame-Balken (assets/framebar.js) folgt dem Index clientseitig, auch bei Wiedergabe
        if c.ClientsideFunction is not None and hasattr(app, 'clientside_callback'):
            app.clientside_callback(c.ClientsideFunction('c2c_framebar', 'move'), Output('replay-chart-barsync', 'data'),
                                    Input('replay-index', 'value'), Input('replay-chart', 'figure'),
                                    State('replay-chart-box', 'id'))

        @app.callback(Output('replay-index', 'max'), Output('replay-range', 'children'),
                      Input('replay-bounds', 'data'), Input('session-choice', 'value'),
                      Input(self.tick_id, 'n_intervals'), State('main-tabs', 'active_tab'))
        def replay_limits(bounds, name, _, tab):
            if tab != self.tab_id and ctx.triggered_id == self.tick_id:
                return no_update, no_update
            records, _, _ = records_for(name, bounds)
            source = 'Live-Ringbuffer (gleitend)' if name in (None, LIVE) else 'gespeicherte Session'
            selection = f' · Bereich Frame {int(bounds[0])}–{int(bounds[1])}' if bounds else ''
            return max(0, len(records) - 1), f'{len(records)} Frames · {source}{selection}'

        @app.callback(Output('replay-playing', 'data'), Input('replay-play', 'n_clicks'),
                      State('replay-playing', 'data'), prevent_initial_call=True)
        def replay_toggle(_, playing):
            return not bool(playing)

        @app.callback(Output('replay-play', 'children'), Input('replay-playing', 'data'))
        def replay_play_label(playing):
            return '⏸ Pause' if playing else '▶ Play'

        @app.callback(Output('replay-image', 'src'), Output('replay-frame-label', 'children'),
                      Output('replay-details', 'children'),
                      Input('replay-index', 'value'), Input('session-choice', 'value'),
                      Input('replay-bounds', 'data'), Input(self.tick_id, 'n_intervals'),
                      State('main-tabs', 'active_tab'))
        def replay_render(index, name, bounds, _, tab):
            """Nur das HUD-Bild.

            Beim Abspielen loesen Takt und (ueber ``seek``) der Index im selben Zyklus aus;
            ``ctx.triggered_id`` meldet nur die erste Komponente. Deshalb werden alle ausloesenden
            Komponenten geprueft, sonst bliebe das Bild einer gespeicherten Session stehen.
            """
            if tab != self.tab_id:
                return (no_update,) * 3
            live = name in (None, LIVE)
            if not live and c.triggered_ids() == {self.tick_id}:
                return (no_update,) * 3
            with c.measure('dashboard.replay'):
                _, events, path, record = record_at(name, bounds, index)
                if record is None:
                    return None, 'Keine Frames', 'Kamera starten oder Session auswählen'
                if isinstance(record, dict):
                    try:
                        image = load_session_image(path, record)
                    except ValueError as exc:
                        return None, str(exc), f"Frame {record.get('frame_id')}"
                    details = (f"Status {record.get('data_status')} · Stellwert {record.get('steering_angle_car') or '–'} · "
                               f"Soll {record.get('requested_angle') or '–'} · Roh {record.get('steering_angle') or '–'} · "
                               f"Speed {record.get('speed') or '–'} · Linien {record.get('lane_count') or '–'} · "
                               f"Ereignisse {record.get('event_codes') or '–'}")
                    return uri(draw_hud(image, record, events=events), 80), f"Frame #{record.get('frame_id')}", details
                # HUD auch fuer den Live-Ringbuffer (Zustand, Stopp-Ursache aus den Ereignissen)
                image = draw_hud(record.camera.raw_image,
                                 row_from_camera(record.camera, record.driving, events=events), events=events)
                info = (f'Frame {record.camera.frame_id} · '
                        + (f'Stellwert {fmt(record.driving.steering_angle_car)} · Roh {fmt(record.driving.steering_angle)} · '
                           f'Speed {fmt(record.driving.speed)}' if record.driving else 'Keine zugeordnete Fahrentscheidung'))
                return uri(image, 80), f'Frame #{record.camera.frame_id}', info

        @app.callback(Output('replay-chart', 'figure'),
                      Input('session-choice', 'value'), Input('replay-bounds', 'data'),
                      Input('replay-index', 'value'), Input('main-tabs', 'active_tab'))  # PAKET-H1B
        def replay_chart(name, bounds, index, tab):
            """Diagramm nur bei Session-/Bereichswechsel (und im Live-Ringbuffer) neu aufbauen.

            PAKET-H1B: Der Frame-Balken ist ein eigenes, clientseitig bewegtes Element (assets/framebar.js); ein
            Indexwechsel baut das Diagramm einer gespeicherten Session daher nicht mehr neu und patcht auch keine Kurve.
            """
            if tab != self.tab_id:
                return no_update
            props = c.triggered_ids()
            live = name in (None, LIVE)
            structural = live or not props or bool(props & {'session-choice', 'replay-bounds', 'main-tabs', ''})
            if not structural:
                return no_update
            records, events, _path, record = record_at(name, bounds, index)
            with c.measure('dashboard.replay_chart'):
                chart = go.Figure()
                # Keine layout.height; der Legendenzustand haengt an layout.uirevision (je Quelle), die
                # Achsenzustaende an xaxis.uirevision (je Quelle und Bereich): eine Bereichsauswahl setzt die
                # Achsen neu, ausgeblendete Kurven bleiben ausgeblendet.
                dark_figure(chart, xaxis=dict(title={'text': 'Frame-ID'}, uirevision=f'{name}|{bounds}'),
                            yaxis=dict(title={'text': 'Winkel'}, fixedrange=True),
                            yaxis2=dict(overlaying='y', side='right', range=[0, 3], title={'text': 'Linien'},
                                        fixedrange=True),
                            uirevision=f'{name}')
                if not records:
                    return c.empty_figure('Keine Frames - Kamera starten oder Session wählen')
                ids = [record_frame_id(r) for r in records]
                if isinstance(records[0], dict):
                    def number(row, key):
                        try:
                            return float(row.get(key) or 'nan')
                        except ValueError:
                            return None
                    angles = [number(r, 'steering_angle_car') for r in records]
                    lanes = [number(r, 'lane_count') for r in records]
                else:
                    angles = [r.driving.steering_angle_car if r.driving else None for r in records]
                    lanes = [sum(lane is not None for lane in (r.camera.lanes or ())) for r in records]
                chart.add_scatter(x=ids, y=angles, customdata=ids, mode='lines+markers', name='Stellwert',
                                  marker={'size': 4})
                chart.add_scatter(x=ids, y=lanes, customdata=ids, mode='lines', name='Linienanzahl',
                                  line={'shape': 'hv'}, yaxis='y2')
                valid = [i for i in ids if i is not None]
                lo, hi = (min(valid), max(valid)) if valid else (0, -1)
                for event in events:
                    try:
                        fid = int(event.get('frame_id'))
                    except (TypeError, ValueError):
                        continue
                    if lo <= fid <= hi and event.get('code') not in ('LANES_DETECTED', 'STEERING_COMPUTED'):
                        chart.add_vline(x=fid, line_width=1, line_dash='dot', line_color='#f0ad4e')
                return chart

        # ---- Navigation (tabuebergreifend): Replay/Recording/Sprunglink -> Tab, Studio, Session
        @app.callback(Output('main-tabs', 'active_tab'), Output('replay-studio-msg', 'children'),
                      Output('ab-source', 'children'), Output('replay-studio-token', 'data'),
                      Output('session-choice', 'value'),
                      Input('replay-to-studio', 'n_clicks'), Input('record-open-replay', 'n_clicks'),
                      State('session-choice', 'value'), State('replay-bounds', 'data'),
                      State('replay-index', 'value'), Input('jump-url', 'hash'), prevent_initial_call=True)
        def replay_to_studio(_, _open, name, bounds, index, jump=None):
            """Uebergibt Rohbild + damalige Parameter als Referenz A an das A/B-Labor."""
            if ctx.triggered_id == 'jump-url':  # Link im Ereignisprotokoll
                target = parse_jump(jump)
                if target is None:
                    return (no_update,) * 5
                return self.tab_id, no_update, no_update, no_update, target[0]
            if ctx.triggered_id == 'record-open-replay':  # letzte Session im Replay oeffnen
                path = getattr(application.recorder, 'path', None)
                if not path:
                    return (no_update,) * 5
                return self.tab_id, no_update, no_update, no_update, Path(str(path)).name
            try:
                _, _, path, record = record_at(name, bounds, index)
                if record is None:
                    raise ValueError('Kein Frame ausgewählt')
                if isinstance(record, dict):
                    image = load_session_image(path, record)
                    params = parameters_from_record(record.get('processing_parameters_json'))
                    processor = processor_key(record.get('processor_name'))
                    frame_id = record.get('frame_id')
                else:
                    image = record.camera.raw_image
                    params = parameters_from_record(record.camera.processing_parameters)
                    processor = processor_key(record.camera.processor_name)
                    frame_id = record.camera.frame_id
                ab.freeze(SimpleNamespace(raw_image=image, frame_id=frame_id), params, processor)
            except (ValueError, TypeError, OSError, cv2.error) as exc:
                return no_update, f'Übergabe nicht möglich: {exc}', no_update, no_update, no_update
            source = 'Live-Ringbuffer' if name in (None, LIVE) else name
            return ('pipeline', f'Frame {frame_id} an das Pipeline Studio übergeben',
                    f'Referenz A = Frame {frame_id} aus {source} mit den damaligen Parametern. '
                    '„A/B berechnen“ vergleicht mit den aktuellen Slidern (B).',
                    {'frame_id': frame_id, 'source': source, 'nonce': time.time()}, no_update)

        @app.callback(Output('replay-download', 'data'), Input('replay-export', 'n_clicks'),
                      State('session-choice', 'value'), State('replay-bounds', 'data'),
                      prevent_initial_call=True)
        def export_range(_, name, bounds):
            lo, hi = (int(bounds[0]), int(bounds[1])) if bounds else (None, None)
            try:
                if name in (None, LIVE):
                    records = select_records(car.frame_history.snapshot(), tuple(bounds) if bounds else None)
                    if not records:
                        return no_update
                    target = export_live_records(records)
                else:
                    target = export_session_range(name, lo, hi)
            except (ValueError, OSError) as exc:
                log.warning('Export fehlgeschlagen: %s', exc)
                return no_update
            return dcc.send_file(str(target))
