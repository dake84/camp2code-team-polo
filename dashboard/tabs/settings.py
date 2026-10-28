"""Tab "Settings": Geschwindigkeit (CameraMode) und Aufzeichnung (JPEG-Qualitaet)."""
import runtime_settings  # PAKET-H1B
from ..helpers import saved_note
from .base import DashboardTab

QUALITY_OPTIONS = [{'label': '70 · klein', 'value': 70}, {'label': '85 · mittel', 'value': 85},
                   {'label': '95 · hoch (Standard)', 'value': 95}, {'label': '100 · maximal', 'value': 100}]


class SettingsTab(DashboardTab):
    tab_id = 'settings'
    label = 'Settings'
    poll_interval_ms = None      # reiner Formular-Tab, kein eigener Takt

    def layout(self):
        c = self.ctx
        dbc, dcc, html = c.dbc, c.dcc, c.html
        return dbc.Row([
            dbc.Col(c.card('Geschwindigkeit · CameraMode', [
                c.help_label('Modus', 'speedmode', 'small'),
                dbc.RadioItems(id='set-speed-mode', value='fixed', inline=True, options=[
                    {'label': 'fest', 'value': 'fixed'},
                    {'label': 'adaptiv (Kurve langsamer)', 'value': 'adaptive'}]),
                dbc.Row([
                    dbc.Col([c.help_label('fest', 'speedfixed', 'small'),
                             dcc.Input(id='set-speed-fixed', type='number', min=30, max=60, step=1, value=30,
                                       className='form-control form-control-sm')]),
                    dbc.Col([c.help_label('v_min (Kurve)', 'speedvmin', 'small'),
                             dcc.Input(id='set-speed-vmin', type='number', min=30, max=60, step=1, value=30,
                                       className='form-control form-control-sm')]),
                    dbc.Col([c.help_label('v_max (Gerade)', 'speedvmax', 'small'),
                             dcc.Input(id='set-speed-vmax', type='number', min=30, max=60, step=1, value=45,
                                       className='form-control form-control-sm')]),
                ], className='g-2 mt-1'),
                html.Div('Nur im Stillstand · wird dauerhaft in config.json gespeichert · '
                         'Werte sind Steuerwerte (PWM).', className='small text-secondary mt-2')]), md=6),
            dbc.Col(c.card('Aufzeichnung', [
                c.help_label('JPEG-Qualität', 'recq', 'small'),
                dcc.Dropdown(id='set-rec-quality', className='c2c-dropdown', clearable=False, value=95,
                             options=QUALITY_OPTIONS),
                html.Div('Auflösung = Kamera (cam_sensor.width/height in config.json), Neustart nötig.',
                         className='small text-secondary mt-2')]), md=6),
            dbc.Col(self.runtime_card(), md=12),  # PAKET-H1B
            dbc.Col([dbc.Button('Übernehmen', id='settings-apply', color='primary', size='sm'),
                     html.Div(id='settings-msg', className='small mt-2')], md=12),
        ], className='g-3')

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
            marker = [dbc.Badge('verhaltensrelevant', color='warning', text_color='dark', className='ms-1')] \
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

    def register_callbacks(self, app):
        c = self.ctx
        Input, Output, State, no_update = c.Input, c.Output, c.State, c.no_update
        application, html = c.application, c.html

        @app.callback(Output('settings-msg', 'children'), Input('settings-apply', 'n_clicks'),
                      State('set-speed-mode', 'value'), State('set-speed-fixed', 'value'),
                      State('set-speed-vmin', 'value'), State('set-speed-vmax', 'value'),
                      State('set-rec-quality', 'value'), prevent_initial_call=True)
        def settings_apply(_n, mode, fixed, vmin, vmax, quality):
            from speed_policy import SpeedPolicy
            messages = []
            try:
                policy = SpeedPolicy.from_values(mode, fixed, vmin, vmax)
                setter = getattr(application, 'set_speed_policy', None)
                if setter is None:
                    raise RuntimeError('Anwendung ohne Geschwindigkeitseinstellung')
                saved = setter(policy)
                messages.append(html.Div(f'✔ Geschwindigkeit: {policy.describe()}' + saved_note(saved),
                                         className='text-success'))
            except (ValueError, RuntimeError) as exc:
                messages.append(html.Div(f'✖ Geschwindigkeit: {exc}', className='text-warning'))
            try:
                setter = getattr(application, 'set_jpeg_quality', None)
                if setter is None:
                    raise RuntimeError('Anwendung ohne Aufnahme')
                saved = setter(quality)
                messages.append(html.Div(f'✔ JPEG-Qualität {quality} ab der nächsten Aufnahme' + saved_note(saved),
                                         className='text-success'))
            except (ValueError, RuntimeError, TypeError) as exc:
                messages.append(html.Div(f'✖ Aufnahme: {exc}', className='text-warning'))
            return messages

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

        @app.callback(Output('set-speed-mode', 'value'), Output('set-speed-fixed', 'value'),
                      Output('set-speed-vmin', 'value'), Output('set-speed-vmax', 'value'),
                      Output('set-rec-quality', 'value'), Output('set-rec-quality', 'options'),
                      Input('main-tabs', 'active_tab'))
        def settings_fill(tab):
            """Beim Oeffnen des Reiters die tatsaechlich aktiven Werte zeigen (nach Neustart: gespeicherte)."""
            if tab != self.tab_id:
                return (no_update,) * 6
            getter = getattr(application, 'speed_policy', None)
            policy = getter() if getter is not None else None
            quality = getattr(getattr(application, 'recorder', None), 'jpeg_quality', None)
            speed = ((policy.mode, policy.fixed_speed, policy.v_min, policy.v_max) if policy is not None
                     else (no_update,) * 4)
            if not isinstance(quality, int) or isinstance(quality, bool):
                return (*speed, no_update, no_update)
            options = QUALITY_OPTIONS if quality in (70, 85, 95, 100) else (
                QUALITY_OPTIONS + [{'label': f'{quality} · gespeichert', 'value': quality}])
            return (*speed, quality, options)
