"""Tab "Telemetrie & Performance": Frequenzen, Verlaeufe, Datenalter, Latenzen, System."""
import time

import system_stats  # PAKET-H2
import telemetry_chart

from dashboard_overlay import render_calibration_overlay

from ..helpers import CHART_EVERY, IMAGE_STYLE, preview_uri
from ..helpers import system_stats as system_stats_text
from .base import DashboardTab


class TelemetryTab(DashboardTab):
    tab_id = 'telemetry'
    label = 'Telemetrie & Performance'

    def layout(self):
        c = self.ctx
        dbc, html = c.dbc, c.html
        return dbc.Row([
            dbc.Col(c.card('Live · Mini-Preview', html.Img(id='telemetry-preview', style=IMAGE_STYLE)), md=4),
            # PAKET-H2: Speicherplatz der SD-Karte (Aktualisierung alle ca. 30 s im Sampler, nicht je Takt)
            dbc.Col(c.card('Speicherplatz (SD-Karte, Data_AI)', [
                c.help_label('Frei', 'disk'), html.Div(id='telemetry-disk-free', className='c2c-kpi-small'),
                c.help_label('Aufnahmedauer (Schätzung)', 'diskest'),
                html.Div(id='telemetry-disk-estimate', className='small')]), md=8),
            dbc.Col(c.card(['Telemetrie · letzte 60 s ', *c.info('telechart'), ' · Entscheidungen ',
                            *c.info('decisions')],
                           c.chart_block('telemetry-chart', telemetry_chart.HEIGHT, 'Noch keine Messwerte')), md=12),
            dbc.Col(c.card(['Datenalter · Latenzen · System', *c.info('dataage')],
                           html.Details([html.Summary('Anzeigen / ausblenden', className='text-info small'),
                                         html.Div(id='data-age', className='mt-2'),
                                         html.Div(id='system-stats', className='small text-secondary mt-2')])),
                    md=12),
        ], className='g-3')

    def register_callbacks(self, app):
        c = self.ctx
        Input, Output = c.Input, c.Output
        ctx, no_update, html = c.ctx, c.no_update, c.html
        application, car = c.application, c.car

        @app.callback(Output('telemetry-chart', 'figure'), Output('data-age', 'children'),
                      Output('system-stats', 'children'), Output('telemetry-disk-free', 'children'),
                      Output('telemetry-disk-estimate', 'children'), Output('telemetry-preview', 'src'),
                      Input(self.tick_id, 'n_intervals'), Input('main-tabs', 'active_tab'))
        def telemetry_view(tick, tab):
            """Ein gekoppeltes Diagramm (dict), nur jeden CHART_EVERY-ten Takt; Systemwerte nur lesen."""
            if tab != self.tab_id:
                return (no_update,) * 6
            with c.measure('dashboard.telemetry'):
                camera = car.camera_data
                chart = no_update
                if ctx.triggered_id == 'main-tabs' or (tick or 0) % CHART_EVERY == 1:
                    rows, freqs, _events = application.sample()
                    extra = getattr(application, 'sample_extra', None)
                    latency, system = extra() if extra is not None else ((), ())
                    figure = telemetry_chart.build_figure(time.monotonic(), float(application.sensor.scan_frequency),
                                                          freqs, latency, system, rows)
                    chart = figure if figure is not None else c.empty_figure('Noch keine Messwerte')
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
                stats = application.system_stats() if hasattr(application, 'system_stats') else system_stats.empty()
                disk = stats.get('disk')
                free = f"{disk['free_gb']:.1f} GB ({disk['free_pct']:.0f} %)" if disk else '–'
                estimate = disk.get('estimate') if disk else None
                estimate_text = (f"reicht für ca. {estimate['minutes']:.0f} min Aufnahme (Schätzung aus mittlerer "
                                 f"JPEG-Größe {estimate['mean_kb']:.0f} kB und {estimate['fps']:.0f} fps)"
                                 if estimate else '–' if not disk else 'nicht schätzbar (noch keine Session)')
                return (chart, [html.Div(line) for line in lines] or 'Keine Daten verfügbar', system_stats_text(),
                        free, estimate_text, preview)
