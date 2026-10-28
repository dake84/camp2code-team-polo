"""Tab "Settings": Geschwindigkeit (CameraMode) und Aufzeichnung (JPEG-Qualitaet)."""
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
            dbc.Col([dbc.Button('Übernehmen', id='settings-apply', color='primary', size='sm'),
                     html.Div(id='settings-msg', className='small mt-2')], md=12),
        ], className='g-3')

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
