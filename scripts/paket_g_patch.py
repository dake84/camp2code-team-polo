#!/usr/bin/env python3
"""Paket G: Settings-Reiter, adaptive Geschwindigkeit, Vollbild, HUD im Cockpit,
Frame-Links im Ereignisprotokoll, Kennzahlen-Leiste, Info-Buttons, Telemetrie-Fix.

Setzt Paket F (2e9b147) voraus. Verankerte Aenderungen; fehlt ein Anker,
wird KEINE Datei geschrieben. Dateien mit Marker PAKET-G werden uebersprungen.
"""
import sys
import textwrap
from pathlib import Path

MARKER = 'PAKET-G'


class PatchError(Exception):
    pass


def _one(lines, needle, file, start=0):
    hits = [i for i in range(start, len(lines)) if needle in lines[i]]
    if len(hits) != 1:
        raise PatchError(f'{file}: Anker {needle!r} {len(hits)}x gefunden (erwartet 1)')
    return hits[0]


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def sub_once(text, old, new, file, count=1):
    found = text.count(old)
    if found != count:
        raise PatchError(f'{file}: Text {old[:70]!r} {found}x gefunden (erwartet {count})')
    return text.replace(old, new)


def insert(text, needle, block, file, after=True):
    lines = text.split('\n')
    i = _one(lines, needle, file)
    ind = _indent(lines[i])
    new = [(ind + l) if l.strip() else '' for l in block.strip('\n').split('\n')]
    pos = i + 1 if after else i
    return '\n'.join(lines[:pos] + new + lines[pos:])


# ------------------------------------------------------------------ driving/camera_mode.py
def patch_camera_mode(t, f):
    t = sub_once(t, 'from models import DrivingState\n',
                 'from models import DrivingState\nfrom speed_policy import SpeedPolicy  # PAKET-G\n', f)
    t = insert(t, "self.mode_key = 'camera'", '''
# PAKET-G: Geschwindigkeitsstrategie (Standard: fest 30 = bisheriges Verhalten).
# CarApplication.set_speed_policy() ersetzt das Objekt im Stillstand.
self.speed_policy = SpeedPolicy(fixed_speed=self.SPEED_CONTROL_VALUE)''', f)
    return sub_once(t, '            speed = driving_parameter[1]\n',
                    '            speed = self.speed_policy.speed_for(  # PAKET-G: fest oder adaptiv\n'
                    "                steering_angle, diagnostic['held_previous_angle'])\n", f)


# ------------------------------------------------------------------ car_application.py
def patch_car_application(t, f):
    t = sub_once(t, "metrics['camera'].hz if 'camera' in metrics else 0,\n"
                    "                metrics['controller'].hz if 'controller' in metrics else 0))",
                 "self._fresh_hz(metrics.get('camera')),  # PAKET-G: veraltete Werte = 0\n"
                 "                self._fresh_hz(metrics.get('controller'))))", f)
    return insert(t, '# ------------------------------------------------------------ Aufzeichnung', '''
# ------------------------------------------------------------ Einstellungen (PAKET-G)
@staticmethod
def _fresh_hz(counter, max_age_s=2.0):
    """Frequenz nur, wenn der letzte Tick juenger als ``max_age_s`` ist (sonst 0).

    Fehler vorher: Nach einem Stopp wurde die letzte Entscheidungsrate im
    Frequenzverlauf weiter fortgeschrieben.
    """
    if counter is None:
        return 0
    age = counter.age_seconds
    return counter.hz if age is not None and age < max_age_s else 0

def speed_policy(self):
    """Aktuelle Geschwindigkeitsstrategie des CameraMode (oder None)."""
    return getattr(self.modes.get('camera'), 'speed_policy', None)

def set_speed_policy(self, policy):
    """Setzt die Geschwindigkeitsstrategie; nur im Stillstand."""
    with self._lock:
        if self.running():
            raise RuntimeError('Geschwindigkeit nur im Stillstand ändern')
        mode = self.modes.get('camera')
        if mode is None or not hasattr(mode, 'speed_policy'):
            raise RuntimeError('CameraMode ohne Geschwindigkeitsstrategie')
        mode.speed_policy = policy
        self.health.record('SPEED_POLICY', policy.describe())

def set_jpeg_quality(self, quality):
    """JPEG-Qualitaet der naechsten Aufnahme (nur ohne laufende Aufzeichnung)."""
    quality = int(quality)
    if not 50 <= quality <= 100:
        raise ValueError('JPEG-Qualität 50–100')
    with self._lock:
        if self.recorder.is_running():
            raise RuntimeError('Bildqualität nur ohne laufende Aufzeichnung ändern')
        self.recorder.jpeg_quality = quality

''', f, after=False)


# ------------------------------------------------------------------ offline_application.py
def patch_offline(t, f):
    t = insert(t, 'self.controller_poll_hz = 0.0', '''
from speed_policy import SpeedPolicy  # PAKET-G
self._speed_policy = SpeedPolicy()''', f)
    return insert(t, 'def toggle_recording(self):', '''
def speed_policy(self):  # PAKET-G
    return self._speed_policy

def set_speed_policy(self, policy):
    self._speed_policy = policy

def set_jpeg_quality(self, quality):
    self._refuse()

''', f, after=False)


# ------------------------------------------------------------------ Camp2CodeDashboard.py
HELPERS = r'''
# --------------------------------------------------------------------- PAKET-G Hilfen
def parse_jump(hash_value):
    """``#jump/<session>/<frame>/<nonce>`` -> (session, frame) oder None."""
    if not isinstance(hash_value, str) or not hash_value.startswith('#jump/'):
        return None
    parts = hash_value[len('#jump/'):].split('/')
    if len(parts) < 2 or not parts[0]:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def fs_button(target):
    """Vollbild-Schalter (assets/fullscreen.js)."""
    return html.Button('⛶ Vollbild', type='button', className='c2c-fs btn btn-sm btn-outline-info ms-2',
                       **{'data-target': target})


def live_hud_row(camera, driving):
    """Zeile im Format von frames.csv fuer draw_hud aus Live-Daten (keine neue Erkennung)."""
    from replay_hud import encode_lanes
    lanes = driving.lanes if driving is not None and driving.lanes is not None else camera.lanes
    top, bottom = camera.roi_rows()
    row = {'frame_id': camera.frame_id, 'roi_top_px': top, 'roi_bottom_px': bottom,
           'lanes_json': encode_lanes(lanes), 'event_codes': '',
           'data_status': 'complete' if driving is not None else 'live · ohne Fahrentscheidung'}
    if driving is not None:
        row.update(steering_angle_car=driving.steering_angle_car, requested_angle=driving.requested_angle,
                   steering_angle=driving.steering_angle, speed=driving.speed)
    return row


def lane_quality(records):
    """Anteil plausibler Linienpaare im Ringbuffer (None ohne Bilder)."""
    from sim_engine import lanes_plausible
    good = total = 0
    for record in records:
        camera = record.camera
        raw = camera.raw_image
        if raw is None or not getattr(raw, 'size', 0) or camera.frame_id < 0:
            continue
        top, bottom = camera.roi_rows(raw.shape[0])
        total += 1
        good += lanes_plausible(camera.lanes, raw.shape[1], max(1, bottom - top))
    return good / total if total else None


def kpi(text, level):
    return html.Span(text, className={'ok': 'text-success', 'warn': 'text-warning',
                                      'bad': 'text-danger'}.get(level, 'text-secondary'))

'''

SETTINGS_LAYOUT = r'''
# PAKET-G: Settings-Reiter
settings = dbc.Row([
    dbc.Col(card('Geschwindigkeit · CameraMode', [
        help_label('Modus', 'speedmode', dbc, html, 'small'),
        dbc.RadioItems(id='set-speed-mode', value='fixed', inline=True, options=[
            {'label': 'fest', 'value': 'fixed'}, {'label': 'adaptiv (Kurve langsamer)', 'value': 'adaptive'}]),
        dbc.Row([
            dbc.Col([help_label('fest', 'speedfixed', dbc, html, 'small'),
                     dcc.Input(id='set-speed-fixed', type='number', min=30, max=60, step=1, value=30,
                               className='form-control form-control-sm')]),
            dbc.Col([help_label('v_min (Kurve)', 'speedvmin', dbc, html, 'small'),
                     dcc.Input(id='set-speed-vmin', type='number', min=30, max=60, step=1, value=30,
                               className='form-control form-control-sm')]),
            dbc.Col([help_label('v_max (Gerade)', 'speedvmax', dbc, html, 'small'),
                     dcc.Input(id='set-speed-vmax', type='number', min=30, max=60, step=1, value=45,
                               className='form-control form-control-sm')]),
        ], className='g-2 mt-1'),
        html.Div('Nur im Stillstand · gilt bis zum Neustart · Werte sind Steuerwerte (PWM).',
                 className='small text-secondary mt-2')]), md=6),
    dbc.Col(card('Aufzeichnung', [
        help_label('JPEG-Qualität', 'recq', dbc, html, 'small'),
        dcc.Dropdown(id='set-rec-quality', className='c2c-dropdown', clearable=False, value=95, options=[
            {'label': '70 · klein', 'value': 70}, {'label': '85 · mittel', 'value': 85},
            {'label': '95 · hoch (Standard)', 'value': 95}, {'label': '100 · maximal', 'value': 100}]),
        html.Div('Auflösung = Kamera (cam_sensor.width/height in config.json), Neustart nötig.',
                 className='small text-secondary mt-2')]), md=6),
    dbc.Col([dbc.Button('Übernehmen', id='settings-apply', color='primary', size='sm'),
             html.Div(id='settings-msg', className='small mt-2')], md=12),
], className='g-3')
'''

CALLBACKS = r'''
    # ------------------------------------------------ PAKET-G: Kennzahlen-Leiste und Settings
    @app.callback(Output('kpi-rate', 'children'), Output('kpi-lat', 'children'), Output('kpi-qual', 'children'),
                  Input('refresh', 'n_intervals'))
    def kpi_bar(_):
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
        return rate_view, lat_view, qual_view

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
            setter(policy)
            messages.append(html.Div(f'✔ Geschwindigkeit: {policy.describe()}', className='text-success'))
        except (ValueError, RuntimeError) as exc:
            messages.append(html.Div(f'✖ Geschwindigkeit: {exc}', className='text-warning'))
        try:
            setter = getattr(application, 'set_jpeg_quality', None)
            if setter is None:
                raise RuntimeError('Anwendung ohne Aufnahme')
            setter(quality)
            messages.append(html.Div(f'✔ JPEG-Qualität {quality} ab der nächsten Aufnahme', className='text-success'))
        except (ValueError, RuntimeError, TypeError) as exc:
            messages.append(html.Div(f'✖ Aufnahme: {exc}', className='text-warning'))
        return messages

'''

EVENT_LINKS = r'''        # PAKET-G: Frame-Nummer verlinkt ins Replay Lab (Session oder Live-Ringbuffer)
        ring_ids = {r.camera.frame_id for r in car.frame_history.snapshot()}
        session_name, session_ids = None, set()
        session_path = getattr(recorder, 'path', None)
        if session_path:
            try:
                session_name = Path(str(session_path)).name
                _path, session_rows = load_session(session_name, str(Path(str(session_path)).parent))
                session_ids = {int(r['frame_id']) for r in session_rows if str(r.get('frame_id', '')).isdigit()}
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
'''


def patch_dashboard(t, f):
    t = insert(t, 'from replay_hud import draw_hud',
               'from help_texts import info, label as help_label  # PAKET-G', f, after=False)
    t = insert(t, 'def card(title, content, **kwargs):', HELPERS, f, after=False)
    # Info-Buttons an allen Slidern
    t = sub_once(t, "    return html.Div([html.Label(label, className='small'),",
                 "    help_key = key if key in ('crop', 'hue', 'sat', 'val', 'blur', 'morph', 'canny') else None  # PAKET-G\n"
                 "    return html.Div([html.Label([label, ' ', *(info(help_key, dbc, html) if help_key else [])],\n"
                 "                                className='small'),", f)
    # Cockpit: Info-Buttons, HUD-Schalter, Vollbild
    for text, key in (('Stellwert (Fahrzeug)', 'stell'), ('Speed-Steuerwert', 'speed'),
                      ('Sollwinkel (Regler)', 'soll'), ('Rohwinkel (Linien)', 'roh')):
        t = sub_once(t, f"html.Div('{text}', className='c2c-kpi-label')",
                     f"help_label('{text}', '{key}', dbc, html)", f)
    t = sub_once(t, "            html.Div('Controllerdiagnose', className='c2c-kpi-label'),",
                 "            help_label('Controllerdiagnose', 'diag', dbc, html),", f)
    t = sub_once(t, "                      html.Span(id='drive-age', className='small text-secondary')], className='mb-2'),",
                 "                      html.Span(id='drive-age', className='small text-secondary'),\n"
                 "                      fs_button('drive-overlay'),  # PAKET-G\n"
                 "                      dbc.Checklist(id='cockpit-hud', options=[{'label': 'HUD', 'value': 'on'}],\n"
                 "                                    value=[], switch=True, inline=True, className='d-inline-block ms-3 small'),\n"
                 "                      *info('hud', dbc, html)], className='mb-2'),", f)
    t = sub_once(t, "                html.Div(id='replay-frame-label', className='fw-semibold mb-2'),",
                 "                html.Div([html.Span(id='replay-frame-label', className='fw-semibold'),\n"
                 "                          fs_button('replay-image')], className='mb-2'),  # PAKET-G", f)
    t = sub_once(t, "                html.Div(id='inspect-title', className='fw-semibold mb-2'),",
                 "                html.Div([html.Span(id='inspect-title', className='fw-semibold'),\n"
                 "                          fs_button('inspect-image')], className='mb-2'),  # PAKET-G", f)
    # Telemetrie-Karten mit Info
    for title, key in (("'Soll / Ist Frequenz'", 'freq'), ("'Frequenzverlauf (letzte 60 s)'", 'freqhist'),
                       ("'Lenkwinkel / Speed / Linienanzahl'", 'drivehist'),
                       ("'Datenalter · Latenzen · System'", 'dataage')):
        t = sub_once(t, f"card({title},", f"card([{title}, *info('{key}', dbc, html)],", f)
    # Settings-Reiter, Kennzahlen-Leiste, Sprung-URL
    t = insert(t, '    return dbc.Container([', SETTINGS_LAYOUT, f, after=False)
    t = sub_once(t, "        control_bar,\n",
                 "        control_bar,\n"
                 "        dbc.Row([dbc.Col([help_label('Regelrate', 'rate', dbc, html), html.Div(id='kpi-rate', className='c2c-kpi-small')]),\n"
                 "                 dbc.Col([help_label('Reaktionszeit', 'lat', dbc, html), html.Div(id='kpi-lat', className='c2c-kpi-small')]),\n"
                 "                 dbc.Col([help_label('Spurqualität', 'qual', dbc, html), html.Div(id='kpi-qual', className='c2c-kpi-small')])],\n"
                 "                className='g-2 mb-2 c2c-kpibar'),  # PAKET-G\n", f)
    t = sub_once(t, "dbc.Tab(replay, label='Replay Lab', tab_id='replay')],",
                 "dbc.Tab(replay, label='Replay Lab', tab_id='replay'),\n"
                 "                  dbc.Tab(settings, label='Settings', tab_id='settings')],  # PAKET-G", f)
    t = sub_once(t, "        dcc.Store(id='last-action', data=None, storage_type='memory'),",
                 "        dcc.Location(id='jump-url', refresh=False),  # PAKET-G: Sprung aus dem Ereignisprotokoll\n"
                 "        dcc.Store(id='last-action', data=None, storage_type='memory'),", f)
    t = insert(t, 'sim.register(app)  # PAKET-F', textwrap.dedent(CALLBACKS).strip('\n'), f)
    # Cockpit: HUD-Option
    t = sub_once(t, "                  State('drive-rendered', 'data'))\n    def cockpit_view(_, tab, rendered):",
                 "                  State('drive-rendered', 'data'), State('cockpit-hud', 'value'))\n"
                 "    def cockpit_view(_, tab, rendered, hud=None):", f)
    t = sub_once(t, "                key = f'drive:{record.camera.frame_id}'",
                 "                key = f'drive:{record.camera.frame_id}' + (':hud' if hud and 'on' in hud else '')", f)
    t = sub_once(t, "                key = f'cal:{getattr(camera, \"frame_id\", None)}'",
                 "                key = f'cal:{getattr(camera, \"frame_id\", None)}' + (':hud' if hud and 'on' in hud else '')", f)
    t = sub_once(t, "                overlay = (render_overlay(record.camera, record.driving) if record is not None\n"
                    "                           else render_calibration_overlay(camera))\n"
                    "                image = uri(draw_steering_overlay(overlay, car.steering_angle))",
                 "                if hud and 'on' in hud:  # PAKET-G: Replay-HUD-Stil fuer das Livebild\n"
                 "                    image = uri(draw_hud(shown.raw_image, live_hud_row(\n"
                 "                        shown, record.driving if record is not None else None)))\n"
                 "                else:\n"
                 "                    overlay = (render_overlay(record.camera, record.driving) if record is not None\n"
                 "                               else render_calibration_overlay(camera))\n"
                 "                    image = uri(draw_steering_overlay(overlay, car.steering_angle))", f)
    # Ereignisprotokoll: Frame-Links
    t = insert(t, "        events = list(application.health.events())[-25:][::-1]", textwrap.dedent(EVENT_LINKS).strip('\n'), f)
    t = sub_once(t, "html.Span(f'Frame {e.frame_id}' if e.frame_id is not None else 'Frame –', className='me-2'),",
                 "frame_cell(e),  # PAKET-G", f)
    # Sprung: Tab + Session (replay_to_studio) und Bereich + Index (seek)
    t = sub_once(t, "                  State('replay-index', 'value'), prevent_initial_call=True)\n"
                    "    def replay_to_studio(_, _open, name, bounds, index):",
                 "                  State('replay-index', 'value'), Input('jump-url', 'hash'), prevent_initial_call=True)\n"
                 "    def replay_to_studio(_, _open, name, bounds, index, jump=None):", f)
    t = insert(t, "        if ctx.triggered_id == 'record-open-replay':  # PAKET-E: letzte Session im Replay", '''
if ctx.triggered_id == 'jump-url':  # PAKET-G: Link im Ereignisprotokoll
    target = parse_jump(jump)
    if target is None:
        return (no_update,) * 5
    return 'replay', no_update, no_update, no_update, target[0]''', f, after=False)
    t = sub_once(t, "                  State('main-tabs', 'active_tab'))\n"
                    "    def seek(_prev, _next, click, relayout, event_frame, _reset, _tick, index, playing, name, bounds,\n"
                    "             pre, post, tab):",
                 "                  State('main-tabs', 'active_tab'), Input('jump-url', 'hash'))\n"
                 "    def seek(_prev, _next, click, relayout, event_frame, _reset, _tick, index, playing, name, bounds,\n"
                 "             pre, post, tab, jump=None):", f)
    t = insert(t, "        if trigger == 'replay-range-reset':", '''
if trigger == 'jump-url':  # PAKET-G: Sprung mit Vor-/Nachlauf, Session aus dem Link
    target = parse_jump(jump)
    if target is None:
        return no_update, no_update
    session, frame = target
    window = event_window(frame, pre, post)
    records, _, _ = replay_records(session, window)
    ids = [record_frame_id(r) for r in records]
    position = next((i for i, fid in enumerate(ids) if fid is not None and fid >= frame), 0)
    return position, list(window)''', f, after=False)
    return t


CSS = '''
/* PAKET-G: Info-Buttons, Kennzahlen-Leiste, Links, Vollbild */
.c2c-info{cursor:pointer;color:#7fb3ff;font-size:.85em;margin-left:2px;user-select:none}
.c2c-info:focus-visible{outline:2px solid #4da3ff;border-radius:50%}
.popover{max-width:360px}
.c2c-kpibar{background:var(--c2c-card);border:1px solid var(--c2c-line);border-radius:10px;padding:6px 10px}
.c2c-link{color:#7fb3ff;text-decoration:underline}
img:fullscreen{object-fit:contain;background:#000;max-height:none!important}
'''


def patch_css(t, f):
    return t.rstrip('\n') + '\n' + CSS


def patch_test_callbacks(t, f):
    t = sub_once(t, "'sim_render'}", "'sim_render', 'kpi_bar', 'settings_apply'}  # PAKET-G", f)
    return sub_once(t, "self.assertEqual(per_tick['refresh'], 1)         # nur die globale Statusleiste",
                    "self.assertEqual(per_tick['refresh'], 2)         # Statusleiste + Kennzahlen (PAKET-G)", f)


def patch_test_real_dash(t, f):
    t = sub_once(t, "[('main-tabs', 'active_tab', 'drive'), ('drive-rendered', 'data', None)],",
                 "[('main-tabs', 'active_tab', 'drive'), ('drive-rendered', 'data', None),\n"
                 "                              ('cockpit-hud', 'value', [])],  # PAKET-G", f)
    t = sub_once(t, "[('main-tabs', 'active_tab', 'telemetry'), ('drive-rendered', 'data', None)],",
                 "[('main-tabs', 'active_tab', 'telemetry'), ('drive-rendered', 'data', None),\n"
                 "                              ('cockpit-hud', 'value', [])],", f)
    return sub_once(t, 'self.assertEqual(len(deps), 28)', 'self.assertEqual(len(deps), 30)  # PAKET-G', f)


CHANGELOG = '''
## 6.5 – Paket G (PAKET-G)

* **Settings-Reiter:** Geschwindigkeit fest/adaptiv (v_min 30 … v_max), JPEG-Qualität der Aufnahme.
* **Adaptive Geschwindigkeit** (`speed_policy.py`): v_max auf Geraden, linear bis v_min bei vollem
  Einschlag, v_min bei gehaltenem Vorwert. Standard bleibt fest 30. Änderung nur im Stillstand.
* **Kennzahlen-Leiste:** Regelrate, Reaktionszeit, Spurqualität (immer sichtbar).
* **Ereignisprotokoll:** Frame-Nummern verlinken ins Replay Lab (Session oder Ringbuffer, ±Vor-/Nachlauf).
* **Cockpit:** HUD-Darstellung schaltbar; Vollbild für Cockpit, Replay und Inspector.
* **Info-Buttons (ⓘ)** an Kennzahlen, Telemetrie-Karten, Pipeline-Slidern und Settings (`help_texts.py`).
* **Fix Frequenzverlauf:** veraltete Werte werden als 0 statt als letzter Wert geschrieben.
'''

PATCHES = {
    'driving/camera_mode.py': patch_camera_mode,
    'car_application.py': patch_car_application,
    'offline_application.py': patch_offline,
    'Camp2CodeDashboard.py': patch_dashboard,
    'assets/studio.css': patch_css,
    'tests/test_dashboard_callbacks.py': patch_test_callbacks,
    'tests/test_dashboard_real_dash.py': patch_test_real_dash,
    'CHANGELOG.md': lambda t, f: insert(t, '# Changelog', CHANGELOG, f),
}


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    results = {}
    try:
        for name, func in PATCHES.items():
            path = root / name
            text = path.read_text(encoding='utf-8')
            if MARKER in text:
                print(f'  = {name} (bereits gepatcht)')
                continue
            new = func(text, name)
            if MARKER not in new:
                raise PatchError(f'{name}: Marker fehlt nach dem Patch')
            if name.endswith('.py'):
                compile(new, name, 'exec')
            results[path] = new
    except (PatchError, SyntaxError, OSError) as exc:
        print(f'PATCH ABGEBROCHEN, keine Datei geaendert: {exc}', file=sys.stderr)
        return 1
    for path, new in results.items():
        path.write_text(new, encoding='utf-8')
        print(f'  + {path.relative_to(root)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
