"""Tab "Driving Cockpit": Livebild mit Linien, Fahrzustand, Simulation (Live | Simulation)."""
import time

from dashboard_overlay import render_calibration_overlay, render_overlay
from dashboard_steering import draw_steering_overlay
from replay_hud import draw_hud, row_from_camera
from sim_lab import SimLab

from ..helpers import IMAGE_STYLE, diagnostic_text, fmt
from .base import DashboardTab


class DrivingTab(DashboardTab):
    tab_id = 'drive'
    label = 'Driving Cockpit'

    # ------------------------------------------------------------------ Layout
    def layout(self):
        c = self.ctx
        dbc, html, dcc = c.dbc, c.html, c.dcc
        cockpit = dbc.Row([
            dbc.Col(c.card('Live Vision · Lane Overlay', [
                html.Div([dbc.Badge(id='drive-frame-badge', color='dark', className='me-2'),
                          html.Span(id='drive-age', className='small text-secondary'),
                          c.fs_button('drive-overlay'),
                          dbc.Checklist(id='cockpit-hud', options=[{'label': 'HUD', 'value': 'on'}],
                                        value=[], switch=True, inline=True,
                                        className='d-inline-block ms-3 small'),
                          *c.info('hud')], className='mb-2'),
                html.Img(id='drive-overlay', style={**IMAGE_STYLE, 'maxHeight': '68vh'}),
                c.legend(),
                html.Div('Grün/Gelb: erkannte Linien · Magenta: Reglerrichtung aus den Linien · '
                         'Instrument: Fahrzeug-Stellwert · Rohbild bleibt unverändert',
                         className='text-secondary small mt-2')]), lg=8),
            dbc.Col(c.card('Fahrzustand', [
                dbc.Row([
                    dbc.Col([c.help_label('Stellwert (Fahrzeug)', 'stell'),
                             html.Div(id='drive-angle', className='c2c-kpi')], width=6),
                    dbc.Col([c.help_label('Speed-Steuerwert', 'speed'),
                             html.Div(id='drive-speed', className='c2c-kpi')], width=6),
                    dbc.Col([c.help_label('Sollwinkel (Regler)', 'soll'),
                             html.Div(id='drive-requested', className='c2c-kpi-small')], width=6),
                    dbc.Col([c.help_label('Rohwinkel (Linien)', 'roh'),
                             html.Div(id='drive-raw', className='c2c-kpi-small')], width=6),
                ], className='g-2'),
                html.Hr(),
                html.Div(id='drive-mode-status', className='mb-2'),
                c.help_label('Controllerdiagnose', 'diag'),
                html.Div(id='drive-diagnostic', className='small'),
            ]), lg=4),
        ], className='g-3')
        # Umschalter Live | Simulation (die Simulation gehoert zum Cockpit)
        return html.Div([SimLab.wrap_cockpit(cockpit, dbc, dcc, html),
                         dcc.Store(id='drive-rendered', data=None, storage_type='memory')])

    # --------------------------------------------------------------- Callbacks
    def register_callbacks(self, app):
        c = self.ctx
        Input, Output, State, no_update = c.Input, c.Output, c.State, c.no_update
        application, car, uri = c.application, c.car, c.uri
        dbc = c.dbc

        c.sim.register(app)

        @app.callback(Output('drive-overlay', 'src'), Output('drive-rendered', 'data'),
                      Output('drive-angle', 'children'), Output('drive-speed', 'children'),
                      Output('drive-requested', 'children'), Output('drive-raw', 'children'),
                      Output('drive-mode-status', 'children'), Output('drive-diagnostic', 'children'),
                      Output('drive-frame-badge', 'children'), Output('drive-age', 'children'),
                      Input(self.tick_id, 'n_intervals'), Input('main-tabs', 'active_tab'),
                      State('drive-rendered', 'data'), State('cockpit-hud', 'value'))
        def cockpit_view(_, tab, rendered, hud=None):
            if tab != self.tab_id:
                return (no_update,) * 10
            with c.measure('dashboard.cockpit'):
                camera = car.camera_data
                running = application.running()
                diagnostic = application.last_diagnostic() if hasattr(application, 'last_diagnostic') else None
                stop_reason = application.stop_reason() if hasattr(application, 'stop_reason') else None
                record = car.frame_history.latest_complete() if running else None
                hud_on = bool(hud and 'on' in hud)
                if record is not None:
                    key = f'drive:{record.camera.frame_id}' + (':hud' if hud_on else '')
                    shown = record.camera
                else:
                    key = f'cal:{getattr(camera, "frame_id", None)}' + (':hud' if hud_on else '')
                    shown = camera
                image = no_update
                if key != rendered and shown is not None and shown.frame_id >= 0:
                    if hud_on:  # Replay-HUD-Stil fuer das Livebild
                        image = uri(draw_hud(shown.raw_image, row_from_camera(
                            shown, record.driving if record is not None else None)))
                    else:
                        overlay = (render_overlay(record.camera, record.driving) if record is not None
                                   else render_calibration_overlay(camera))
                        image = uri(draw_steering_overlay(overlay, car.steering_angle))
                else:
                    key = rendered
                age = '–'
                if shown is not None and getattr(shown, 'monotonic_ns', 0):
                    age = f'Datenalter {(time.monotonic_ns() - shown.monotonic_ns) / 1e6:.0f} ms'
                    if record is not None and record.driving.monotonic_ns:
                        age += f' · Entscheidung nach {(record.driving.monotonic_ns - shown.monotonic_ns) / 1e6:.0f} ms'
                mode_badge = dbc.Badge(f'Modus: {application.active_mode}' if running else 'Modus: aus',
                                       color='success' if running else 'secondary')
                return (image, key, fmt(car.steering_angle, 0, '°'), fmt(car.speed),
                        fmt(diagnostic.get('requested_angle'), 0, '°') if running and diagnostic else '–',
                        fmt(diagnostic.get('raw_angle'), 0, '°') if running and diagnostic else '–',
                        mode_badge, diagnostic_text(diagnostic, stop_reason, running),
                        f'Frame #{shown.frame_id}' if shown is not None else '', age)
