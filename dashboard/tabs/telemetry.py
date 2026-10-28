"""Tab "Telemetrie & Performance": Frequenzen, Verlaeufe, Datenalter, Latenzen, System."""
import time

from dashboard_overlay import render_calibration_overlay

from ..helpers import (CHART_EVERY, CHART_WINDOW_S, IMAGE_STYLE, axis, plain_figure, preview_uri,
                       system_stats, thin)
from .base import DashboardTab


class TelemetryTab(DashboardTab):
    tab_id = 'telemetry'
    label = 'Telemetrie & Performance'

    def layout(self):
        c = self.ctx
        dbc, html = c.dbc, c.html
        return dbc.Row([
            dbc.Col(c.card('Live · Mini-Preview', html.Img(id='telemetry-preview', style=IMAGE_STYLE)), md=4),
            dbc.Col(c.card(['Soll / Ist Frequenz', *c.info('freq')],
                           c.chart_block('freq-bars', 240, 'Noch keine Messwerte')), md=8),
            dbc.Col(c.card(['Frequenzverlauf (letzte 60 s)', *c.info('freqhist')],
                           c.chart_block('freq-history', 240, 'Noch keine Frequenzdaten')), md=12),
            dbc.Col(c.card(['Lenkwinkel / Speed / Linienanzahl', *c.info('drivehist')],
                           c.chart_block('drive-history', 300, 'Noch keine Fahrdaten - START im Cockpit')),
                    md=12),
            dbc.Col(c.card(['Datenalter · Latenzen · System', *c.info('dataage')],
                           [html.Div(id='data-age'),
                            html.Div(id='system-stats', className='small text-secondary mt-2')]), md=12),
        ], className='g-3')

    def register_callbacks(self, app):
        c = self.ctx
        Input, Output = c.Input, c.Output
        ctx, no_update, html = c.ctx, c.no_update, c.html
        application, car = c.application, c.car

        @app.callback(Output('freq-bars', 'figure'), Output('freq-history', 'figure'),
                      Output('drive-history', 'figure'), Output('data-age', 'children'),
                      Output('system-stats', 'children'), Output('telemetry-preview', 'src'),
                      Input(self.tick_id, 'n_intervals'), Input('main-tabs', 'active_tab'))
        def telemetry_view(tick, tab):
            """Diagramme als dict, nur jeden CHART_EVERY-ten Takt, Fenster 60 s."""
            if tab != self.tab_id:
                return (no_update,) * 6
            with c.measure('dashboard.telemetry'):
                camera = car.camera_data
                bars = fplot = dplot = no_update
                if ctx.triggered_id == 'main-tabs' or (tick or 0) % CHART_EVERY == 1:
                    rows, freqs, _events = application.sample()
                    metrics = car.thread_metrics
                    running = application.running()
                    camera_target = float(application.sensor.scan_frequency)
                    actual = [metrics[key].hz if metrics[key].age_seconds is not None
                              and metrics[key].age_seconds < 2 else 0 for key in ('camera', 'controller')]
                    targets = [camera_target, camera_target if running else 0.0]
                    names = ['Kamera', 'Entscheidungen']
                    bars = plain_figure([dict(type='bar', name='Soll', x=names, y=targets,
                                              marker={'color': '#6c7a92'}),
                                         dict(type='bar', name='Ist', x=names, y=actual,
                                              marker={'color': '#2fbf8f'})],
                                        barmode='group', yaxis=axis('Hz'), uirevision='bars')
                    ftraces = []
                    if freqs:
                        end = freqs[-1][0]
                        recent = thin([r for r in freqs if end - r[0] <= CHART_WINDOW_S])
                        for i, name in ((1, 'Kamera'), (2, 'Entscheidungen')):
                            ftraces.append(dict(type='scatter', mode='lines', name=name,
                                                x=[round(r[0] - end, 2) for r in recent],
                                                y=[r[i] for r in recent]))
                    fplot = (plain_figure(ftraces, xaxis=axis('Sekunden'), yaxis=axis('Hz'), uirevision='freq')
                             if ftraces else c.empty_figure('Noch keine Frequenzdaten'))
                    dtraces = []
                    if rows:
                        end = time.monotonic()
                        recent = thin([r for r in rows if end - r[0] <= CHART_WINDOW_S])
                        x = [round(r[0] - end, 2) for r in recent]
                        dtraces = [
                            dict(type='scatter', mode='lines', name='Rohwinkel', x=x, y=[r[2] for r in recent]),
                            dict(type='scatter', mode='lines', name='Stellwert', x=x,
                                 y=[r[6] if len(r) > 6 else None for r in recent]),
                            dict(type='scatter', mode='lines', name='Speed-Steuerwert', x=x,
                                 y=[r[3] for r in recent]),
                            dict(type='scatter', mode='lines', name='Linienanzahl', x=x, line={'shape': 'hv'},
                                 yaxis='y2', y=[r[5] if len(r) > 5 else None for r in recent]),
                        ]
                    dplot = (plain_figure(dtraces, xaxis=axis('Sekunden', range=[-CHART_WINDOW_S, 0]),
                                          yaxis=axis('Grad / Steuerwert'),
                                          yaxis2=axis('Linien', overlaying='y', side='right', range=[0, 3]),
                                          uirevision='drive')
                             if dtraces else c.empty_figure('Noch keine Fahrdaten - START im Cockpit'))
                lines = []
                if camera is not None and camera.monotonic_ns:
                    lines.append('Kameradatenalter (seit Aufnahme): '
                                 f'{(time.monotonic_ns() - camera.monotonic_ns) / 1e6:.0f} ms')
                    if getattr(camera, 'processing_ns', 0):
                        lines.append(f'Bildverarbeitung: {camera.processing_ns / 1e6:.1f} ms')
                record = car.frame_history.latest_complete()
                if record is not None and record.driving.monotonic_ns and record.camera.monotonic_ns:
                    lines.append(f'Entscheidungslatenz (Aufnahme → Fahrbefehl): '
                                 f'{(record.driving.monotonic_ns - record.camera.monotonic_ns) / 1e6:.1f} ms '
                                 f'(Frame {record.camera.frame_id})')
                history = car.frame_history
                lines.extend(c.thread_cpu.lines())
                lines.append(f'Ringbuffer: {len(history.snapshot())}/{getattr(history, "maxlen", "?")} Frames · '
                             f'Regler-Abtastung {application.controller_poll_hz:.0f} Hz '
                             '(nur neue Frames werden ausgewertet)')
                timings = getattr(application, 'timings', None)
                if timings is not None:
                    for name, values in sorted(timings.summary().items()):
                        lines.append(f'{name}: Ø {values["mean_ms"]:.1f} ms · max {values["max_ms"]:.1f} ms')
                preview = None
                if camera is not None and camera.frame_id >= 0:
                    preview = preview_uri(render_calibration_overlay(camera))
                return (bars, fplot, dplot, [html.Div(line) for line in lines] or 'Keine Daten verfügbar',
                        system_stats(), preview)
