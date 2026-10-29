"""Shell des Control Centers: Steuerleiste, Kennzahlen, Bergung und das Einstecken der Tabs.

Die Shell besitzt alles, was fuer alle Tabs gilt:
    * globale Steuerleiste (Status, Fahrmodus, START, STOP, Bergung, REC) und ``handle_actions``
    * Kennzahlen-Leiste (Regelrate, Reaktionszeit, Spurqualitaet)
    * Bergungspuls, Sprung-URL (``jump-url``), globale Stores (``last-action``, ``recovery-keys``)
    * Takte: ``refresh`` (global) und je Tab ``tick-<tab_id>``; nur der sichtbare Tab pollt
Die Inhalte der Reiter kommen ausschliesslich aus den Tab-Klassen (tabs/).
"""
import time
import logging

import event_log  # PAKET-H2
import system_stats
from help_texts import tip
from .helpers import mode_options, lane_quality

log = logging.getLogger(__name__)


class Shell:
    def __init__(self, context, tabs):
        self.ctx = context
        self.tabs = list(tabs)
        ids = [tab.tab_id for tab in self.tabs]
        if len(set(ids)) != len(ids):
            raise ValueError(f'Tab-IDs nicht eindeutig: {ids}')
        self.polling_tabs = [tab for tab in self.tabs if tab.poll_interval_ms is not None]

    @property
    def start_tab(self):
        return self.tabs[0].tab_id

    # ------------------------------------------------------------------ Layout
    def layout(self):
        c = self.ctx
        dbc, dcc, html = c.dbc, c.dcc, c.html
        control_bar = dbc.Card(dbc.CardBody(dbc.Row([
            dbc.Col([dbc.Badge(id='global-camera-state', color='secondary', className='me-1'),
                     dbc.Badge(id='global-mode-state', color='secondary', className='me-1'),
                     dbc.Badge(id='global-rec-state', color='secondary', className='me-1'),
                     html.Span(id='global-frame', className='ms-1 small text-secondary'),
                       dbc.Tooltip(id='global-rec-tooltip', target='global-rec-state',
                                   placement='bottom')],
                    xs=12, lg=4, className='mb-2 mb-lg-0'),
            dbc.Col(dcc.Dropdown(id='mode', options=mode_options(), value='camera', clearable=False,
                                 className='c2c-dropdown'), xs=12, md=4, lg=3, className='mb-2 mb-md-0'),
            dbc.Col([dbc.Button('▶ START', id='global-start', color='success', className='me-2'),
                     dbc.Button('■ STOP', id='global-stop', color='danger', className='me-2'),
                     dbc.Button('Bergung', id='recovery-toggle', color='warning', outline=True,
                                className='me-2'),
                     dbc.Button('● REC', id='record-toggle', color='secondary', outline=True)],
                    xs=12, md=8, lg=5, className='text-lg-end'),
        ], align='center', className='g-2')), className='mb-2 c2c-control-bar')
        recovery_panel = dbc.Collapse(dbc.Alert([
            html.Strong('Manuelle Bergung: '),
            'Leertaste gedrückt halten + W/S (vor/zurück), A/D (lenken). '
            'Ohne Tastensignal stoppt der Watchdog nach 0,35 s. Nur mit Sichtkontakt verwenden.',
            html.Div(id='recovery-status', className='small mt-1'),
        ], color='warning', className='mb-2'), id='recovery-panel', is_open=False)
        return dbc.Container([
            html.H2('Camp2Code · Control Center', className='mt-3 mb-2'),
            control_bar,
            dbc.Row([dbc.Col([c.help_label('Regelrate', 'rate'), html.Div(id='kpi-rate', className='c2c-kpi-small')]),
                     dbc.Col([c.help_label('Reaktionszeit', 'lat'), html.Div(id='kpi-lat', className='c2c-kpi-small')]),
                     dbc.Col([c.help_label('Spurqualität', 'qual'), html.Div(id='kpi-qual', className='c2c-kpi-small')]),
                       dbc.Col([c.help_label('Auslastung', 'cpu', suffix='kpi'),
                                html.Div(id='kpi-load', className='c2c-kpi-small')])],
                    className='g-2 mb-2 c2c-kpibar'),
            html.Div(id='global-action-message', className='small mb-2'),
            recovery_panel,
            dbc.Tabs([tab.tab_component() for tab in self.tabs], id='main-tabs',
                     active_tab=self.start_tab, className='mb-3'),
            dcc.Location(id='jump-url', refresh=False),  # Sprung aus dem Ereignisprotokoll
            dcc.Store(id='last-action', data=None, storage_type='memory'),
            dcc.Store(id='recovery-keys', data={'deadman': False}, storage_type='memory'),
            # Globale Statusleiste; Ansichten haben eigene Takte, nur der sichtbare Tab ist aktiv.
            dcc.Interval(id='refresh', interval=700, n_intervals=0),
            *[tab.interval_component(active=tab.tab_id == self.start_tab) for tab in self.polling_tabs],
            dcc.Interval(id='recovery-pulse', interval=120, n_intervals=0, disabled=True),
        ], fluid=True)

    # --------------------------------------------------------------- Callbacks
    def register_callbacks(self, app):
        c = self.ctx
        Input, Output, State = c.Input, c.Output, c.State
        ctx, no_update, html = c.ctx, c.no_update, c.html
        application, car, sim = c.application, c.car, c.sim
        polling = self.polling_tabs

        @app.callback(Output('kpi-rate', 'children'), Output('kpi-lat', 'children'), Output('kpi-qual', 'children'),
                      Output('kpi-load', 'children'), Input('refresh', 'n_intervals'))
        def kpi_bar(_):
            kpi = c.kpi
            metrics = getattr(car, 'thread_metrics', {})
            counter = metrics.get('controller')
            rate = counter.hz if counter is not None and counter.age_seconds is not None and counter.age_seconds < 2 else 0
            target = float(getattr(application.sensor, 'scan_frequency', 0) or 0)
            share = rate / target if target else 0
            rate_view = kpi(f'{rate:.0f} / {target:.0f} Hz',
                            'ok' if share >= .8 else 'warn' if share >= .5 else 'bad') if application.running() \
                else kpi(f'– / {target:.0f} Hz', 'off')
            record = car.frame_history.latest_complete()
            lat_view = kpi('–', 'off')
            if record is not None and record.driving.monotonic_ns and record.camera.monotonic_ns:
                ms = (record.driving.monotonic_ns - record.camera.monotonic_ns) / 1e6
                lat_view = kpi(f'{ms:.0f} ms', 'ok' if ms < 80 else 'warn' if ms < 150 else 'bad')
            quality = lane_quality(car.frame_history.snapshot())
            qual_view = kpi('–', 'off') if quality is None else kpi(
                f'{quality * 100:.0f} %', 'ok' if quality >= .9 else 'warn' if quality >= .7 else 'bad')
            # PAKET-H2: Auslastung = CPU gross (Ampel), RAM und Temperatur klein; nur lesen, nie messen
            stats = application.system_stats() if hasattr(application, 'system_stats') else system_stats.empty()
            summary = system_stats.load_summary(stats)
            load_view = [kpi(summary['cpu'], summary['cpu_level']),
                         html.Span(f" RAM {summary['ram']}", className='small text-secondary ms-2', title=tip('ram')),
                         html.Span(f" · {summary['temp']}", className='small text-secondary', title=tip('temp'))]
            return rate_view, lat_view, qual_view, load_view

        # ---- Aktionen (ein Callback)
        @app.callback(Output('last-action', 'data'),
                      Input('global-start', 'n_clicks'), Input('global-stop', 'n_clicks'),
                      Input('recovery-toggle', 'n_clicks'), Input('record-toggle', 'n_clicks'),
                      State('mode', 'value'), prevent_initial_call=True)
        def handle_actions(_start, _stop, _recovery, _record, mode):
            trigger = ctx.triggered_id
            try:
                if trigger == 'global-stop':
                    application.stop_all()
                    text = 'STOP ausgeführt'
                elif trigger == 'global-start':
                    if sim.active:  # im Simulationsmodus kein START
                        raise RuntimeError('Simulation aktiv – Cockpit zuerst auf Live umschalten')
                    application.start_mode(mode)
                    text = f'Fahrmodus {mode} gestartet'
                elif trigger == 'recovery-toggle':
                    if application.recovery.enabled:
                        application.disable_recovery()
                        text = 'Bergung beendet'
                    else:
                        application.enable_recovery()
                        text = 'Bergung aktiv'
                elif trigger == 'record-toggle':
                    text = 'Aufzeichnung läuft' if application.toggle_recording() else 'Aufzeichnung gestoppt'
                else:
                    return no_update
                return {'text': text, 'ok': True, 'time': time.strftime('%H:%M:%S')}
            except Exception as exc:
                log.warning('Aktion %s abgewiesen: %s', trigger, exc)
                return {'text': f'Aktion abgewiesen: {exc}', 'ok': False, 'time': time.strftime('%H:%M:%S')}

        @app.callback(*[Output(tab.tick_id, 'disabled') for tab in polling],
                      Input('main-tabs', 'active_tab'), Input('refresh', 'disabled'))
        def tab_intervals(active_tab, paused):
            """Nur der sichtbare Tab pollt; im Hintergrund-Browsertab pausiert alles."""
            return tuple(bool(paused) or tab.tab_id != active_tab for tab in polling)

        # ---- Globale Steuerleiste
        @app.callback(Output('global-camera-state', 'children'), Output('global-camera-state', 'color'),
                      Output('global-mode-state', 'children'), Output('global-mode-state', 'color'),
                      Output('global-rec-state', 'children'), Output('global-rec-state', 'color'),
                      Output('global-frame', 'children'),
                      Output('global-start', 'disabled'), Output('global-stop', 'outline'),
                      Output('recovery-toggle', 'children'), Output('recovery-toggle', 'outline'),
                      Output('recovery-panel', 'is_open'), Output('recovery-pulse', 'disabled'),
                      Output('record-toggle', 'children'), Output('record-toggle', 'color'),
                      Output('record-toggle', 'outline'), Output('mode', 'disabled'),
                      Output('global-action-message', 'children'), Output('global-rec-tooltip', 'children'),
                      Input('refresh', 'n_intervals'), Input('last-action', 'data'))
        def global_status(_tick, last_action):
            camera = car.camera_data
            has_frame = camera is not None and camera.frame_id >= 0
            camera_ok = application.camera_alive() if hasattr(application, 'camera_alive') else has_frame
            running = application.running()
            recovery = application.recovery.enabled
            recording = application.recorder.is_running()
            stop_reason = application.stop_reason() if hasattr(application, 'stop_reason') else None
            if running:
                status = f'Fahrt aktiv · Modus {application.active_mode}'
            elif recovery:
                status = 'Bergung aktiv · autonomer Fahrmodus gesperrt'
            else:
                status = 'Fahrmodus aus' + (f' · letzte Stoppursache: {stop_reason}' if stop_reason else '')
            message = [html.Span(status, className='me-3')]
            if last_action:
                message.append(html.Span(f"{last_action.get('time')} · {last_action.get('text')}",
                                         className='text-success' if last_action.get('ok') else 'text-warning'))
            camera_label = ('KAMERA LIVE' if camera_ok and has_frame else
                            'KAMERA WARTET' if camera_ok else 'KAMERA AUSGEFALLEN')
            return (camera_label, 'info' if camera_ok and has_frame else 'danger' if not camera_ok else 'secondary',
                    'FAHRT AKTIV' if running else 'BERGUNG' if recovery else 'FAHRMODUS AUS',
                    'success' if running else 'warning' if recovery else 'secondary',
                    'REC ●' if recording else 'REC AUS', 'danger' if recording else 'secondary',
                    f'Frame #{camera.frame_id}' if has_frame else '',
                    running or recovery or sim.active, not (running or recovery),
                    'Bergung beenden' if recovery else 'Bergung aktivieren', not recovery,
                    recovery, not recovery,
                    '■ REC stoppen' if recording else '● REC starten', 'danger' if recording else 'secondary',
                    not recording, running,
                    message, event_log.recording_summary(application.recorder))

        @app.callback(Output('recovery-status', 'children'),
                      Input('recovery-pulse', 'n_intervals'), State('recovery-keys', 'data'))
        def recovery_pulse(_, keys):
            if not application.recovery.enabled:
                return 'Bergung inaktiv'
            keys = keys or {}
            try:
                application.recovery.command(
                    forward=bool(keys.get('w')), reverse=bool(keys.get('s')),
                    left=bool(keys.get('a')), right=bool(keys.get('d')),
                    deadman=bool(keys.get('deadman')))
                return 'Deadman gedrückt' if keys.get('deadman') else 'Bereit · Leertaste halten zum Fahren'
            except RuntimeError as exc:
                return str(exc)
