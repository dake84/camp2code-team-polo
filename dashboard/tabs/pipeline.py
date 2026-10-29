"""Tab "Pipeline Studio": Stufen, Regler, Presets und A/B-Labor.

Das Studio kennt keine Pipeline-Stufen mehr. Welche Karten, Regler und Schalter erscheinen,
beschreibt der gewaehlte Bildprozessor selbst (``dashboard_stages()``, siehe
``pipeline_processors.py`` und ``processor_registry.processor_stages``). Ein neuer Prozessor
braucht daher keine Aenderung im Dashboard.

Komponenten-IDs (Muster, Dash Pattern-Matching):
    {'type': 'pipeline-stage-image',  'stage': <stufe>}   Bild einer Stufe
    {'type': 'pipeline-stage-info',   'stage': <stufe>}   Statustext einer Stufe
    {'type': 'pipeline-inspect',      'stage': <stufe>}   Schalter "Vergroessern"
    {'type': 'pipeline-control',      'key':   <regler>}  Regler (Schluessel der Studio-Konfiguration)
    {'type': 'pipeline-switch',       'key':   <schalter>} "Schritt aktiv" (Schluessel in ``enabled``)

Der Replay-Tab uebergibt Frames ueber den Store ``replay-studio-token`` und zeigt die
Herkunft in ``ab-source`` an (der einzige tabuebergreifende Callback, siehe tabs/replay.py).
"""
import time
from types import SimpleNamespace

import cv2

import pipeline_config
from dashboard_overlay import render_calibration_overlay
from pipeline_processors import STAGE_OVERLAY, STAGE_RAW
from processor_registry import (create_processor, is_live_processor, key_for_class, processor_options,
                                processor_stages)

from ..helpers import ACTIVE_PRESET, IMAGE_STYLE
from .base import DashboardTab

STAGE_IMAGE = 'pipeline-stage-image'
STAGE_INFO = 'pipeline-stage-info'
STAGE_INSPECT = 'pipeline-inspect'
CONTROL = 'pipeline-control'
SWITCH = 'pipeline-switch'
FALLBACK_PROCESSOR = 'copilot'


def stage_id(kind, stage):
    return {'type': kind, 'stage': stage}


def parameters_from_settings(settings):
    """Studio-Einstellungen (dict) -> Parameter-Tupel der Prozessoren."""
    return (tuple(settings['hue']), tuple(settings['sat']), tuple(settings['val']),
            tuple(settings['crop']), int(settings['blur']), int(settings['morph']),
            tuple(settings['canny']), dict(settings['enabled']))


def settings_from_parameters(params, base):
    """Parameter-Tupel (wie im CameraSensor) -> Einstellungen; fehlende Felder aus ``base``."""
    settings = dict(base)
    settings['enabled'] = dict(base.get('enabled', {}))
    if params is None or len(params) < 6:
        return settings
    settings.update(hue=list(params[0]), sat=list(params[1]), val=list(params[2]), crop=list(params[3]),
                    blur=int(params[4]), morph=int(params[5]))
    if len(params) > 6:
        settings['canny'] = list(params[6])
    if len(params) > 7 and isinstance(params[7], dict):
        settings['enabled'].update({str(k): bool(v) for k, v in params[7].items()})
    return settings


def merge_controls(base, control_ids, control_values, switch_ids, switch_values):
    """Legt die Werte der sichtbaren Regler und Schalter ueber die Einstellungen ``base``.

    Regler, die der Prozessor nicht anzeigt (z. B. HSV bei Modus Opus), behalten ihren Wert.
    """
    settings = dict(base)
    settings['enabled'] = dict(base.get('enabled', {}))
    for item, value in zip(control_ids or [], control_values or []):
        if value is not None:
            settings[item['key']] = value
    for item, value in zip(switch_ids or [], switch_values or []):
        settings['enabled'][item['key']] = 'on' in (value or [])
    return settings


class PipelineTab(DashboardTab):
    tab_id = 'pipeline'
    label = 'Pipeline Studio'

    # ------------------------------------------------------------ Einstellungen
    def base_settings(self):
        """Aktuell wirksame Einstellungen (CameraSensor), sonst die gespeicherte Konfiguration."""
        settings = pipeline_config.load()
        sensor = getattr(self.ctx.application, 'sensor', None)
        params = getattr(sensor, 'processing_parameters', None)
        if params is not None:
            try:
                settings = settings_from_parameters(params, settings)
            except (TypeError, ValueError, KeyError, IndexError):
                pass
        name = getattr(sensor, 'processor_name', None)
        if isinstance(name, str) and is_live_processor(key_for_class(name)):
            settings['processor'] = key_for_class(name)
        return settings

    @staticmethod
    def _stages(processor):
        try:
            return processor_stages(processor)
        except ValueError:
            return processor_stages(FALLBACK_PROCESSOR)

    def _processor_value(self, settings):
        keys = [o['value'] for o in processor_options(live_only=True)]
        return settings.get('processor') if settings.get('processor') in keys else FALLBACK_PROCESSOR

    # ------------------------------------------------------------------ Karten
    def _slider(self, control, settings):
        c = self.ctx
        html, dcc = c.html, c.dcc
        value = settings.get(control.key)
        if control.pair:
            value = list(value) if isinstance(value, (list, tuple)) and len(value) == 2 else [
                control.minimum, control.maximum]
            component = dcc.RangeSlider
        else:
            value = value if isinstance(value, (int, float)) else control.minimum
            component = dcc.Slider
        help_key = control.help_key or control.key
        try:
            info = c.info(help_key)
        except KeyError:
            info = []
        return html.Div([html.Label([control.label, ' ', *info], className='small'),
                         component(id={'type': CONTROL, 'key': control.key}, min=control.minimum,
                                   max=control.maximum, step=control.step, value=value, marks=None,
                                   tooltip={'placement': 'bottom'})], className='mb-2')

    def _stage_card(self, index, stage, settings):
        c = self.ctx
        dbc, html = c.dbc, c.html
        body = [html.Img(id=stage_id(STAGE_IMAGE, stage.key), style={**IMAGE_STYLE, 'maxHeight': '200px'}),
                html.Div(id=stage_id(STAGE_INFO, stage.key), className='small text-secondary mt-1'),
                *[self._slider(control, settings) for control in stage.controls]]
        if stage.switch:
            active = settings.get('enabled', {}).get(stage.switch, True)
            body.append(dbc.Checklist(id={'type': SWITCH, 'key': stage.switch},
                                      options=[{'label': 'Schritt aktiv', 'value': 'on'}],
                                      value=['on'] if active else [], switch=True, className='small'))
        body.append(dbc.Button('Vergrößern', id=stage_id(STAGE_INSPECT, stage.key), color='info',
                               outline=True, size='sm', className='mt-2'))
        return dbc.Col(c.card(f'{index:02d} · {stage.title}', body), md=6, xl=3)

    def stage_cards(self, processor, settings):
        """Alle Karten eines Prozessors (eine Karte je beschriebene Stufe)."""
        stages = self._stages(processor)
        return self.ctx.dbc.Row([self._stage_card(i, stage, settings) for i, stage in enumerate(stages, 1)],
                                className='g-3')

    # ------------------------------------------------------------------ Layout
    def layout(self):
        c = self.ctx
        dbc, dcc, html = c.dbc, c.dcc, c.html
        settings = self.base_settings()
        processor = self._processor_value(settings)
        return html.Div([
            dbc.Row([
                dbc.Col(html.Div(id='pipeline-lock'), lg=7),
                dbc.Col([dbc.Button('Freeze / Live', id='pipeline-freeze', color='warning', className='me-2'),
                         html.Span(id='pipeline-frame-label', className='small')],
                        lg=5, className='text-lg-end'),
            ], className='g-2 mb-2', align='center'),
            dbc.Row([
                dbc.Col([html.Label('Bildprozessor', className='small'),
                         dcc.Dropdown(id='cfg-processor', className='c2c-dropdown', clearable=False,
                                      value=processor, options=processor_options(live_only=True))], md=4),
                dbc.Col([dbc.Button('Übernehmen', id='cfg-apply', size='sm', className='me-2 mt-4'),
                         dbc.Button('Als aktiv speichern', id='cfg-save', size='sm', color='success',
                                    className='mt-4'),
                         html.Div(id='cfg-message', className='small mt-2')], md=8),
            ], className='g-2 mb-3'),
            html.Div(id='pipeline-stage-cards', children=self.stage_cards(processor, settings)),
            dbc.Row([
                dbc.Col(c.card('Pipeline Inspector', [
                    html.Div([html.Span(id='inspect-title', className='fw-semibold'),
                              c.fs_button('inspect-image')], className='mb-2'),
                    html.Img(id='inspect-image', style={**IMAGE_STYLE, 'maxHeight': '430px'}),
                    html.Div('Zeigt das veröffentlichte Zwischenbild. Keine zweite Bildverarbeitung.',
                             className='text-secondary small mt-2')]), lg=7),
                dbc.Col(c.card('Presets', [
                    html.Label('Preset laden', className='small'),
                    dcc.Dropdown(id='preset-choice', className='c2c-dropdown', clearable=False,
                                 value=ACTIVE_PRESET, options=[]),
                    dbc.Button('Laden', id='preset-load', size='sm', color='info', className='mt-2'),
                    html.Label('Preset speichern unter', className='small mt-3 d-block'),
                    dcc.Input(id='preset-name', type='text', placeholder='z. B. halle_hell',
                              className='form-control form-control-sm', debounce=True),
                    dbc.Button('Preset speichern', id='preset-save', size='sm', color='success',
                               className='mt-2'),
                    html.Div(id='preset-message', className='small mt-2')]), lg=5),
            ], className='g-3 mt-1'),
            dbc.Row([dbc.Col(c.card('A/B Calibration Lab · gleicher Frame', [
                html.Div('A = Referenz beim Einfrieren · B = aktuelle Slider. Keine Änderung am Fahrmodus.',
                         className='small text-secondary mb-2'),
                dbc.Button('Referenzframe einfrieren', id='ab-freeze', color='info', className='me-2'),
                dbc.Button('A/B berechnen', id='ab-run', color='primary', className='me-2'),
                dbc.Button('A/B zurücksetzen', id='ab-reset', color='secondary'),
                html.Div(id='ab-status', className='small mt-2'),
                html.Div(id='ab-source', className='small text-info'),
                dbc.Row([dbc.Col([html.H6('A · Referenz'), html.Img(id='ab-image-a', style=IMAGE_STYLE),
                                  html.Div(id='ab-lanes-a', className='small mt-2')], md=6),
                         dbc.Col([html.H6('B · Kandidat'), html.Img(id='ab-image-b', style=IMAGE_STYLE),
                                  html.Div(id='ab-lanes-b', className='small mt-2')], md=6)],
                        className='g-3 mt-1'),
            ]), md=12)], className='mt-3'),
            # Zustand des Tabs
            dcc.Store(id='pipeline-frozen', data=None, storage_type='memory'),
            dcc.Store(id='replay-studio-token', data=None, storage_type='memory'),  # Ausgang: Replay-Tab
            dcc.Store(id='inspected-stage', data=STAGE_OVERLAY, storage_type='memory'),
            dcc.Store(id='pipeline-rendered', data=None, storage_type='memory'),
            dcc.Store(id='pipeline-card-version', data=0, storage_type='memory'),
            dcc.Store(id='pipeline-preset-data', data=None, storage_type='memory'),
        ])

    # --------------------------------------------------------------- Callbacks
    def register_callbacks(self, app):
        c = self.ctx
        Input, Output, State, ALL = c.Input, c.Output, c.State, c.ALL
        ctx, no_update = c.ctx, c.no_update
        application, car, ab, uri = c.application, c.car, c.ab, c.uri
        dbc = c.dbc

        controls_id = {'type': CONTROL, 'key': ALL}
        switches_id = {'type': SWITCH, 'key': ALL}
        images_id = {'type': STAGE_IMAGE, 'stage': ALL}
        infos_id = {'type': STAGE_INFO, 'stage': ALL}
        inspect_id = {'type': STAGE_INSPECT, 'stage': ALL}

        def settings_from_controls(processor, control_ids, control_values, switch_ids, switch_values):
            settings = merge_controls(self.base_settings(), control_ids, control_values, switch_ids,
                                      switch_values)
            settings['processor'] = processor
            return settings

        # ---- Karten dynamisch aus dem Prozessor (Prozessorwechsel oder Preset)
        @app.callback(Output('pipeline-stage-cards', 'children'), Output('pipeline-card-version', 'data'),
                      Input('cfg-processor', 'value'), Input('pipeline-preset-data', 'data'),
                      State(controls_id, 'id'), State(controls_id, 'value'),
                      State(switches_id, 'id'), State(switches_id, 'value'), prevent_initial_call=True)
        def rebuild_stage_cards(processor, preset, control_ids, control_values, switch_ids, switch_values):
            """Baut die Karten neu auf. Werte stammen aus den bisherigen Reglern oder aus dem Preset."""
            if 'pipeline-preset-data' in c.triggered_ids() and isinstance(preset, dict):
                settings = preset['settings']          # bereits mit Standardwerten ergaenzt (load/load_preset)
                processor = settings.get('processor', processor)
            else:
                settings = settings_from_controls(processor, control_ids, control_values, switch_ids,
                                                  switch_values)
            return self.stage_cards(processor, settings), time.monotonic_ns()

        # ---- Live uebernehmen / speichern
        @app.callback(Output('cfg-message', 'children'),
                      Input('cfg-apply', 'n_clicks'), Input('cfg-save', 'n_clicks'),
                      Input('cfg-processor', 'value'),
                      Input(controls_id, 'value'), Input(switches_id, 'value'),
                      State(controls_id, 'id'), State(switches_id, 'id'), prevent_initial_call=True)
        def configure(_apply, _save, processor, control_values, switch_values, control_ids, switch_ids):
            settings = settings_from_controls(processor, control_ids, control_values, switch_ids,
                                              switch_values)
            explicit = ctx.triggered_id in ('cfg-apply', 'cfg-save')
            if not explicit and settings == self.base_settings():
                # Dash ruft Callbacks mit Wildcard-Eingaengen auch beim Laden der Seite und beim Aufbau neuer
                # Karten auf. Sind die Werte bereits wirksam, wird nichts angewendet (und nichts gemeldet).
                return no_update
            try:
                if ctx.triggered_id == 'cfg-save':
                    application.save_pipeline(settings)
                    return 'Als aktive Konfiguration gespeichert (pipeline_studio.json)'
                application.set_pipeline(settings)
                return 'Live übernommen · dauerhaft mit „Als aktiv speichern“'
            except Exception as exc:
                return f'Nicht übernommen: {exc}'

        # ---- Presets
        @app.callback(Output('cfg-processor', 'value'), Output('pipeline-preset-data', 'data'),
                      Output('preset-choice', 'options'), Output('preset-message', 'children'),
                      Input('preset-load', 'n_clicks'), Input('preset-save', 'n_clicks'),
                      Input('main-tabs', 'active_tab'),
                      State('preset-choice', 'value'), State('preset-name', 'value'),
                      State('cfg-processor', 'value'),
                      State(controls_id, 'id'), State(controls_id, 'value'),
                      State(switches_id, 'id'), State(switches_id, 'value'))
        def preset_actions(_load, _save, _tab, choice, name, processor, control_ids, control_values,
                           switch_ids, switch_values):
            options = [{'label': 'Aktive Konfiguration (pipeline_studio.json)', 'value': ACTIVE_PRESET}] + [
                {'label': preset, 'value': preset} for preset in pipeline_config.list_presets()]
            keep = (no_update, no_update)
            trigger = ctx.triggered_id
            try:
                if trigger == 'preset-save':
                    settings = settings_from_controls(processor, control_ids, control_values, switch_ids,
                                                      switch_values)
                    pipeline_config.save_preset(name, settings)
                    options = [options[0]] + [{'label': preset, 'value': preset}
                                              for preset in pipeline_config.list_presets()]
                    return (*keep, options, f'Preset „{name}“ gespeichert')
                if trigger == 'preset-load':
                    if application.running():
                        return (*keep, options, 'Laden nur im Stillstand')
                    data = (pipeline_config.load() if choice in (None, ACTIVE_PRESET)
                            else pipeline_config.load_preset(choice))
                    return (self._processor_value(data), {'settings': data, 'nonce': time.monotonic_ns()},
                            options, f'Geladen: {choice or ACTIVE_PRESET} · wird live übernommen')
            except (ValueError, OSError, KeyError) as exc:
                return (*keep, options, f'Preset-Fehler: {exc}')
            return (*keep, options, no_update)

        # ---- Freeze (Livebild <-> eingefrorener Frame; Replay-Frame als eigener Freeze-Zustand)
        @app.callback(Output('pipeline-frozen', 'data'), Input('pipeline-freeze', 'n_clicks'),
                      Input('replay-studio-token', 'data'),
                      State('pipeline-frozen', 'data'), prevent_initial_call=True)
        def freeze(_, token, frozen):
            if ctx.triggered_id == 'replay-studio-token':
                return dict(token) if token else no_update
            if frozen is not None:
                return None
            camera = car.camera_data
            return camera.frame_id if camera is not None and camera.frame_id >= 0 else None

        # ---- Inspector
        @app.callback(Output('inspected-stage', 'data'), Input(inspect_id, 'n_clicks'),
                      prevent_initial_call=True)
        def inspect(_clicks):
            button = ctx.triggered_id
            triggered = (ctx.triggered or [{}])[0].get('value')
            if not isinstance(button, dict) or not triggered:  # neue Karten melden n_clicks=None
                return no_update
            return button.get('stage', STAGE_OVERLAY)

        def replay_view(frozen, processor, stage_ids, inspected, rendered, version):
            """Stufen fuer einen aus dem Replay uebergebenen Frame (Rohbild der Referenz A).

            Das Rohbild wird mit dem GEWAEHLTEN Prozessor und den AKTUELLEN Reglern verarbeitet
            (Regler wirken sofort); das A/B-Labor unten zeigt die damaligen Parameter.
            """
            if not isinstance(frozen, dict):
                return None
            raw, frame_id, reference, _processor = ab.reference()
            if raw is None or str(frame_id) != str(frozen.get('frame_id')):
                return None
            params = getattr(application.sensor, 'processing_parameters', None) or reference
            key = f'replay:{frame_id}:{processor}:{params!r}:{inspected}:{version}:{",".join(stage_ids)}'
            label = f"REPLAY · Frame {frame_id} · {frozen.get('source') or ''}"
            info = dbc.Alert('Replay-Frame: Stufen = dieser Frame mit den aktuellen Slidern · '
                             'A/B unten = damalige Parameter · „Freeze / Live“ zurück zum Livebild.',
                             color='info', className='mb-0 py-2')
            count = len(stage_ids)
            if key == rendered:
                return ([no_update] * count, [no_update] * count, no_update, no_update, label,
                        info, no_update)
            diagnostic = FALLBACK_PROCESSOR if processor == 'legacy' else processor  # Legacy liefert keine Stufen
            try:
                # PAKET-H3: auf der damaligen Verarbeitungsgroesse rechnen (Software-Verkleinern)
                result = create_processor(diagnostic).process(ab.downscale(raw), params)
            except (ValueError, TypeError, IndexError, cv2.error) as exc:
                return ([None] * count, [''] * count, None, f'Replay-Frame nicht verarbeitbar: {exc}',
                        label, info, None)
            enabled = params[7] if len(params) > 7 else {}
            crop = tuple(params[3]) if enabled.get('roi', True) else (0.0, 1.0)
            view = SimpleNamespace(raw_image=raw, frame_id=frame_id, crop=crop, lanes=result.lanes,
                                   process_image=result.processed_image, process_size=ab.process_size())
            stages = dict(result.stages)
            stages[STAGE_RAW] = raw
            images, infos, shown, title = self.render_stages(stage_ids, processor, inspected, stages, view,
                                                             'Replay · verfügbar', 'Replay · Schritt deaktiviert')
            return images, infos, shown, title, label, info, key

        # ---- Anzeige der Stufen
        @app.callback(Output(images_id, 'src'), Output(infos_id, 'children'),
                      Output('inspect-image', 'src'), Output('inspect-title', 'children'),
                      Output('pipeline-frame-label', 'children'), Output('pipeline-lock', 'children'),
                      Output('pipeline-rendered', 'data'),
                      Input(self.tick_id, 'n_intervals'), Input('inspected-stage', 'data'),
                      Input('pipeline-frozen', 'data'), Input('main-tabs', 'active_tab'),
                      Input('pipeline-card-version', 'data'),
                      State(images_id, 'id'), State('cfg-processor', 'value'),
                      State('pipeline-rendered', 'data'))
        def pipeline_view(_, inspected, frozen, tab, version, image_ids, processor, rendered):
            stage_ids = [item['stage'] for item in image_ids or []]
            count = len(stage_ids)
            if tab != self.tab_id:
                return ([no_update] * count, [no_update] * count) + (no_update,) * 5
            with c.measure('dashboard.pipeline'):
                camera = car.camera_data
                running = application.running()
                lock = dbc.Alert('Fahrt aktiv: Änderungen gesperrt, Anzeige läuft weiter.', color='warning',
                                 className='mb-0 py-2') if running else dbc.Alert(
                    'Kalibrierung im Stillstand. LIVE folgt der Kamera, FREEZE hält einen Frame aus dem Ringbuffer.',
                    color='secondary', className='mb-0 py-2')
                replayed = replay_view(frozen, processor, stage_ids, inspected, rendered, version)
                if replayed is not None:
                    return replayed
                if camera is None or camera.frame_id < 0:
                    return [None] * count, [''] * count, None, 'Warte auf Kamera', '', lock, None
                pipeline_camera = camera
                if frozen is not None:
                    match = car.frame_history.find(frozen)
                    if match is not None:
                        pipeline_camera = match.camera
                    else:
                        frozen = None
                key = (f'{pipeline_camera.frame_id}:{processor}:{inspected}:{version}:'
                       f'{",".join(stage_ids)}')
                label = f'{"FREEZE" if frozen is not None else "LIVE"} · Frame {pipeline_camera.frame_id}'
                if key == rendered:
                    return ([no_update] * count, [no_update] * count, no_update, no_update, label, lock,
                            no_update)
                stages = dict(getattr(pipeline_camera, 'stages', {}) or {})
                stages[STAGE_RAW] = pipeline_camera.raw_image
                images, infos, shown, title = self.render_stages(
                    stage_ids, processor, inspected, stages, pipeline_camera, 'Verfügbar',
                    'Schritt deaktiviert oder ohne Diagnosebild (Legacy-Prozessor)')
                return images, infos, shown, title, label, lock, key

        # ---- A/B-Labor
        @app.callback(Output('ab-status', 'children'), Output('ab-image-a', 'src'),
                      Output('ab-image-b', 'src'), Output('ab-lanes-a', 'children'),
                      Output('ab-lanes-b', 'children'),
                      Input('ab-freeze', 'n_clicks'), Input('ab-run', 'n_clicks'), Input('ab-reset', 'n_clicks'),
                      State('cfg-processor', 'value'),
                      State(controls_id, 'id'), State(controls_id, 'value'),
                      State(switches_id, 'id'), State(switches_id, 'value'), prevent_initial_call=True)
        def ab_calibrate(_freeze, _run, _reset, processor, control_ids, control_values, switch_ids,
                         switch_values):
            trigger = ctx.triggered_id
            if trigger == 'ab-reset':
                ab.clear()
                return 'A/B zurückgesetzt', None, None, '', ''
            params = parameters_from_settings(settings_from_controls(
                processor, control_ids, control_values, switch_ids, switch_values))
            try:
                if trigger == 'ab-freeze':
                    frame = ab.freeze(car.camera_data, params, processor)
                    return f'Referenz A: Frame {frame} eingefroren', None, None, '', ''
                comparison = ab.compare(params, processor)
                return (f'A/B auf Frame {comparison.frame_id} berechnet',
                        uri(comparison.image_a), uri(comparison.image_b),
                        f'Linien A: {len(comparison.lanes_a)}', f'Linien B: {len(comparison.lanes_b)}')
            except (ValueError, TypeError, cv2.error) as exc:
                return f'A/B nicht möglich: {exc}', no_update, no_update, no_update, no_update

    # ----------------------------------------------------------- Hilfsfunktionen
    def render_stages(self, stage_ids, processor, inspected, stages, camera, ok_text, missing_text):
        """Kodiert die gebrauchten Stufenbilder genau einmal (Karten und Inspector teilen sich das Bild).

        ``stages`` enthaelt die Prozessorbilder plus ``raw``; ``overlay`` zeichnet das Dashboard
        (Linien auf einer Kopie des Rohbilds) und wird nur berechnet, wenn es gebraucht wird.
        """
        titles = {s.key: s.title for s in self._stages(processor)}
        if inspected not in stage_ids:
            inspected = STAGE_OVERLAY if STAGE_OVERLAY in stage_ids else (stage_ids[-1] if stage_ids else None)
        needed = set(stage_ids) | {inspected}
        if STAGE_OVERLAY in needed:
            stages = {**stages, STAGE_OVERLAY: render_calibration_overlay(camera)}
        encoded = {key: self.ctx.uri(stages.get(key)) for key in needed if key is not None}
        images = [encoded.get(key) for key in stage_ids]
        infos = [ok_text if encoded.get(key) else missing_text for key in stage_ids]
        return images, infos, encoded.get(inspected), titles.get(inspected, inspected or '')
