"""Tab "Driving Cockpit": Livebild mit Linien, Fahrzustand, Simulation (Live | Simulation)."""
import time
from pathlib import Path  # PAKET-H2

import event_log
import system_stats

from dashboard_overlay import render_calibration_overlay, render_overlay
from dashboard_steering import draw_steering_overlay
from replay_hud import draw_hud, row_from_camera
import hud_render  # PAKET-H3B: HUD-Stile
import hud_style
from sim_lab import SimLab
import live_player  # PAKET-I5: Livebild im Browser
import sim_player  # PAKET-I3: Wiedergabe der Simulation im Browser

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
                          dbc.Select(id='cockpit-hud', options=hud_style.OPTIONS, value='off', size='sm',
                                     className='d-inline-block ms-3',
                                     style={'width': 'auto', 'minWidth': '9.5rem'}),
                          *c.info('hud')], className='mb-2'),
                live_player.bar(html),  # PAKET-I5: Bedienleiste des Browser-Livebilds (Mount-Punkt)
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
                html.Hr(),
                dbc.Row([
                    dbc.Col([c.help_label('Auslastung (CPU)', 'cpu'), html.Div(id='drive-cpu', className='c2c-kpi')], width=6),
                    dbc.Col([c.help_label('RAM', 'ram'), html.Div(id='drive-ram', className='c2c-kpi-small'),
                             c.help_label('Temperatur', 'temp'), html.Div(id='drive-temp', className='c2c-kpi-small')], width=6),
                ], className='g-2'),
                html.Hr(),
                html.Div(id='drive-rec-line', className='small text-secondary mb-2'),
                dbc.Button('Frame im Pipeline Studio öffnen', id='drive-to-studio', color='info', size='sm'),
                html.Div(id='drive-studio-msg', className='small mt-1'),
            ]), lg=4),
            dbc.Col(c.card(['Ereignisprotokoll (neueste zuerst)', *c.info('eventlog')], [
                dbc.RadioItems(id='drive-events-filter', options=event_log.FILTER_OPTIONS, value='important', inline=True,
                               className='mb-2'),
                html.Div(id='drive-events', className='c2c-events')]), lg=12),
        ], className='g-3')
        # Umschalter Live | Simulation (die Simulation gehoert zum Cockpit)
        return html.Div([sim_player.install_layout(SimLab.wrap_cockpit(cockpit, dbc, dcc, html), c.sim.ui),
                         dcc.Store(id='drive-rendered', data=None, storage_type='memory')])

    # --------------------------------------------------------------- Callbacks
    def register_callbacks(self, app):
        c = self.ctx
        Input, Output, State, no_update = c.Input, c.Output, c.State, c.no_update
        application, car, uri = c.application, c.car, c.uri
        dbc = c.dbc

        c.sim.register(app)
        live_player.register(app, self)  # PAKET-I5: HTTP-Routen fuer das Browser-Livebild (kein Dash-Callback)
        sim_player.register(app, c.sim)  # PAKET-I3

        @app.callback(Output('drive-overlay', 'src'), Output('drive-rendered', 'data'),
                      Output('drive-angle', 'children'), Output('drive-speed', 'children'),
                      Output('drive-requested', 'children'), Output('drive-raw', 'children'),
                      Output('drive-mode-status', 'children'), Output('drive-diagnostic', 'children'),
                      Output('drive-frame-badge', 'children'), Output('drive-age', 'children'),
                      Output('drive-cpu', 'children'), Output('drive-ram', 'children'),
                      Output('drive-temp', 'children'), Output('drive-rec-line', 'children'),
                      Input(self.tick_id, 'n_intervals'), Input('main-tabs', 'active_tab'),
                      State('drive-rendered', 'data'), State('cockpit-hud', 'value'))
        def cockpit_view(_, tab, rendered, hud=None):
            if tab != self.tab_id:
                return (no_update,) * 14
            with c.measure('dashboard.cockpit'):
                camera = car.camera_data
                running = application.running()
                diagnostic = application.last_diagnostic() if hasattr(application, 'last_diagnostic') else None
                stop_reason = application.stop_reason() if hasattr(application, 'stop_reason') else None
                record = car.frame_history.latest_complete() if running else None
                # PAKET-H3B: Auswahl statt Schalter; die alte Liste ['on'] bedeutet klassisch
                hud_style_name = hud_style.set_current(hud)   # Replay Lab und Simulation folgen diesem Stil
                hud_on = hud_style_name != 'off'
                if record is not None:
                    key = f'drive:{record.camera.frame_id}' + (f':hud-{hud_style_name}' if hud_on else '')
                    shown = record.camera
                else:
                    key = f'cal:{getattr(camera, "frame_id", None)}' + (f':hud-{hud_style_name}' if hud_on else '')
                    shown = camera
                image = no_update
                # PAKET-I5: holt der Browser die Bilder selbst (assets/live_player.js), rendert der Server hier keines
                if not live_player.browser_active() and key != rendered and shown is not None and shown.frame_id >= 0:
                    if hud_on:  # Replay-HUD-Stil fuer das Livebild
                        image = uri(draw_hud(shown.raw_image, row_from_camera(
                            shown, record.driving if record is not None else None),
                            style=hud_style_name, trail=hud_render.TRAIL))
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
                # PAKET-H2: Auslastung (nur lesen) und Aufnahmezustand
                summary = system_stats.load_summary(application.system_stats() if hasattr(application, 'system_stats')
                                                    else system_stats.empty())
                return (image, key, fmt(car.steering_angle, 0, '°'), fmt(car.speed),
                        fmt(diagnostic.get('requested_angle'), 0, '°') if running and diagnostic else '–',
                        fmt(diagnostic.get('raw_angle'), 0, '°') if running and diagnostic else '–',
                        mode_badge, diagnostic_text(diagnostic, stop_reason, running),
                        f'Frame #{shown.frame_id}' if shown is not None else '', age,
                        c.kpi(summary['cpu'], summary['cpu_level']), c.kpi(summary['ram'], summary['ram_level']),
                        c.kpi(summary['temp'], summary['temp_level']),
                        event_log.recording_summary(application.recorder))

        # PAKET-H2: Ereignisprotokoll im Cockpit (Frame-Nummer = Link ins Replay Lab)
        @app.callback(Output('drive-events', 'children'),
                      Input(self.tick_id, 'n_intervals'), Input('drive-events-filter', 'value'),
                      Input('main-tabs', 'active_tab'))
        def drive_events(_, mode, tab):
            if tab != self.tab_id:
                return no_update
            recorder = application.recorder
            path = getattr(recorder, 'path', None)
            ring_ids = {record.camera.frame_id for record in car.frame_history.snapshot()}
            entries = event_log.live_entries(application.health.events(), time.monotonic_ns(), mode,
                                             Path(str(path)).name if path else None, getattr(recorder, 'has_frame', None),
                                             ring_ids)
            return event_log.event_table(c.html, c.dcc, entries)
