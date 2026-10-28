"""Tab "Recording & Events": Aufzeichnungsstatus, Ereignisprotokoll mit Sprung ins Replay Lab."""
import time
from pathlib import Path

from dashboard_overlay import render_calibration_overlay
from session_recorder import load_session

from ..helpers import IMAGE_STYLE, LIVE, preview_uri
from .base import DashboardTab

EVENT_COLORS = {'STOP_REASON': 'text-danger', 'CONTROLLER_STOPPED': 'text-warning',
                'LANE_LOST': 'text-warning', 'RECORDER_FRAME_GAP': 'text-danger'}


class RecordingTab(DashboardTab):
    tab_id = 'record'
    label = 'Recording & Events'

    def layout(self):
        c = self.ctx
        dbc, html = c.dbc, c.html
        return dbc.Row([
            dbc.Col(c.card('Live · Mini-Preview', html.Img(id='record-preview', style=IMAGE_STYLE)), md=4),
            dbc.Col(c.card('Aufzeichnung', [
                html.Div('Start/Stopp über den REC-Button in der Steuerleiste.', className='small text-secondary'),
                html.Div(id='record-status', className='mt-2 fw-semibold'),
                html.Div(id='session-stats', className='small text-secondary mt-2'),
                # Ausgang: Replay-Tab (Navigation, siehe tabs/replay.py)
                dbc.Button('Letzte Session im Replay öffnen', id='record-open-replay', color='info',
                           size='sm', className='mt-2')]), md=8),
            dbc.Col(c.card('Ereignisprotokoll (neueste zuerst)', html.Div(id='event-list', className='c2c-events')),
                    md=12),
        ], className='g-3')

    def register_callbacks(self, app):
        c = self.ctx
        Input, Output = c.Input, c.Output
        no_update, html, dcc = c.no_update, c.html, c.dcc
        application, car = c.application, c.car

        @app.callback(Output('record-status', 'children'), Output('session-stats', 'children'),
                      Output('event-list', 'children'), Output('record-preview', 'src'),
                      Input(self.tick_id, 'n_intervals'), Input('last-action', 'data'),
                      Input('main-tabs', 'active_tab'))
        def recording_view(_, _action, tab):
            if tab != self.tab_id:
                return (no_update,) * 4
            recorder = application.recorder
            stats = recorder.stats() if hasattr(recorder, 'stats') else {
                'frames': getattr(recorder, 'count', 0), 'path': getattr(recorder, 'path', None),
                'error': getattr(recorder, 'error', None)}
            status = 'REC ● Aufzeichnung läuft' if recorder.is_running() else 'Keine Aufzeichnung aktiv'
            details = (f"{stats.get('frames', 0)} Frames · Lücken {stats.get('gap_frames', 0)} · "
                       f"fehlende Entscheidungen {stats.get('missing_decisions', 0)} · "
                       f"{stats.get('path') or 'keine Session'} · {stats.get('error') or 'kein Fehler'}")
            now = time.monotonic_ns()
            events = list(application.health.events())[-25:][::-1]
            # Frame-Nummer verlinkt ins Replay Lab (Session oder Live-Ringbuffer)
            ring_ids = {r.camera.frame_id for r in car.frame_history.snapshot()}
            session_name, session_ids = None, set()
            session_path = getattr(recorder, 'path', None)
            if session_path:
                try:
                    session_name = Path(str(session_path)).name
                    _path, session_rows = load_session(session_name, str(Path(str(session_path)).parent))
                    session_ids = {int(r['frame_id']) for r in session_rows
                                   if str(r.get('frame_id', '')).isdigit()}
                except (ValueError, OSError):
                    session_name = None

            def frame_cell(e):
                if e.frame_id is None or e.frame_id < 0:
                    return html.Span('Frame –', className='me-2 text-secondary')
                target = session_name if e.frame_id in session_ids else (LIVE if e.frame_id in ring_ids else None)
                if target is None:
                    return html.Span(f'Frame {e.frame_id}', className='me-2 text-secondary',
                                     title='Weder in der Session noch im Ringbuffer')
                return dcc.Link(f'Frame {e.frame_id}', href=f'#jump/{target}/{e.frame_id}/{e.timestamp_ns}',
                                className='me-2 c2c-link', title='Im Replay Lab öffnen')

            items = [html.Div([html.Span(f'vor {(now - e.timestamp_ns) / 1e9:5.1f} s',
                                         className='text-secondary me-2'),
                               frame_cell(e),
                               html.Strong(e.code, className=EVENT_COLORS.get(e.code, '') + ' me-2'),
                               html.Span(e.detail, className='small')]) for e in events] or 'Keine Ereignisse'
            camera = car.camera_data
            preview = None
            if camera is not None and camera.frame_id >= 0:
                preview = preview_uri(render_calibration_overlay(camera))
            return status, details, items, preview
