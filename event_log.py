"""Ereignisprotokoll fuer Cockpit und Replay Lab (Paket H2).

Quellen: der laufende ``DrivingHealthMonitor`` (Cockpit, Live-Replay) und ``events.csv`` einer gespeicherten Session.
Spalten: Zeit, Frame, Ereignistyp, Beschreibung; neueste zuerst. Filter "Wichtig" blendet die haeufigen Ereignisse
(LANE_PARTIAL, LANES_DETECTED, STEERING_COMPUTED, STEERING_HELD) aus, die sonst alles Wichtige ueberdecken.

Die Frame-Nummer ist ein Link ``#jump/<session>/<frame>/<nonce>`` ins Replay Lab. Ein Frame ist nur anspringbar, wenn er
in einer aufgezeichneten Session (``SessionRecorder.has_frame``) oder noch im Ringbuffer liegt; sonst ist die Nummer
grau mit Tooltip. Frame -1 oder ohne Angabe ergibt keinen Link. Dash-Komponenten werden uebergeben, nicht importiert.
"""
LIVE = '__live__'      # Quellenname des Live-Ringbuffers (wie dashboard.helpers.LIVE)

# Codes gegen die tatsaechlich vorhandenen geprueft (driving_health.py, car_application.py, session_recorder.py):
# STOP_REASON, CONTROLLER_STARTED/STOPPED, LANE_LOST, RECOVERY_ENABLED/DISABLED, RECORDING_STARTED/STOPPED,
# RECORDER_FRAME_GAP, SPEED_POLICY, CONFIG_SAVED (ab H1b), CONFIG_NOT_SAVED. Nicht wichtig: LANE_PARTIAL,
# LANES_DETECTED, STEERING_COMPUTED, STEERING_HELD, OFFLINE.
IMPORTANT_CODES = frozenset({'STOP_REASON', 'CONTROLLER_STARTED', 'CONTROLLER_STOPPED', 'LANE_LOST',
                             'RECORDER_FRAME_GAP', 'SPEED_POLICY', 'CONFIG_SAVED', 'CONFIG_NOT_SAVED'})
IMPORTANT_PREFIXES = ('RECOVERY_', 'RECORDING_')
FILTER_OPTIONS = [{'label': 'Wichtig', 'value': 'important'}, {'label': 'Alle', 'value': 'all'}]
COLORS = {'STOP_REASON': 'text-danger', 'CONTROLLER_STOPPED': 'text-warning', 'LANE_LOST': 'text-warning',
          'RECORDER_FRAME_GAP': 'text-danger', 'CONFIG_NOT_SAVED': 'text-warning'}
NOT_LINKABLE = 'Weder in einer Session noch im Ringbuffer'


def is_important(code):
    code = str(code or '')
    return code in IMPORTANT_CODES or code.startswith(IMPORTANT_PREFIXES)


def valid_mode(mode):
    return mode if mode in ('important', 'all') else 'important'


def frame_number(value):
    """Frame-ID als int; ``None`` bei fehlender, ungueltiger oder negativer Angabe (-1 = kein Frame)."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def link_target(frame, session_name=None, session_has=None, ring_ids=()):
    """Ziel des Sprunglinks: Sessionname, ``LIVE`` (Ringbuffer) oder ``None`` (nicht anspringbar).

    ``session_has(frame)`` fragt die aufgezeichnete Session (``SessionRecorder.has_frame`` bzw. die Frame-IDs einer
    gespeicherten Session). Die Session hat Vorrang, weil der Ringbuffer fluechtig ist.
    """
    if frame is None:
        return None
    if session_name and session_has is not None and session_has(frame):
        return session_name
    return LIVE if frame in ring_ids else None


def jump_href(target, frame, nonce):
    return f'#jump/{target}/{frame}/{nonce}'


def live_entries(events, now_ns, mode='important', session_name=None, session_has=None, ring_ids=(), limit=40):
    """Eintraege aus HealthEvents (monotone Zeitstempel), neueste zuerst."""
    mode = valid_mode(mode)
    entries = []
    for event in reversed(list(events)):
        if mode == 'important' and not is_important(event.code):
            continue
        frame = frame_number(event.frame_id)
        entries.append({'ts': event.timestamp_ns, 'when': f'vor {max(0.0, (now_ns - event.timestamp_ns) / 1e9):.1f} s',
                        'frame': frame, 'code': event.code, 'detail': str(event.detail or ''),
                        'target': link_target(frame, session_name, session_has, ring_ids)})
        if len(entries) >= limit:
            break
    return entries


def session_entries(event_rows, frame_rows, session_name, mode='important', limit=300):
    """Eintraege aus ``events.csv`` einer Session; Zeit relativ zum ersten Frame; Links nur innerhalb der Session."""
    mode = valid_mode(mode)
    frame_ids = set()
    origin = None
    for row in frame_rows:
        number = frame_number(row.get('frame_id'))
        if number is not None:
            frame_ids.add(number)
        if origin is None:
            try:
                origin = int(row.get('camera_monotonic_ns') or 0) or None
            except (TypeError, ValueError):
                origin = None
    stamps = []
    for row in event_rows:
        try:
            stamps.append(int(row.get('timestamp_ns')))
        except (TypeError, ValueError):
            stamps.append(0)
    if origin is None and stamps:
        origin = min(stamps)
    entries = []
    for row, stamp in zip(reversed(list(event_rows)), reversed(stamps)):
        code = row.get('code', '')
        if mode == 'important' and not is_important(code):
            continue
        frame = frame_number(row.get('frame_id'))
        seconds = (stamp - (origin or 0)) / 1e9
        entries.append({'ts': stamp, 'when': f'{seconds:+.1f} s', 'frame': frame, 'code': code,
                        'detail': str(row.get('detail') or ''),
                        'target': link_target(frame, session_name, frame_ids.__contains__)})
        if len(entries) >= limit:
            break
    return entries


def signature(entries, mode):
    """Aendert sich nur, wenn die Tabelle anders aussehen wuerde (spart das erneute Senden)."""
    return (valid_mode(mode), tuple((e['ts'], e['frame'], e['code'], e['target']) for e in entries),
            tuple(e['when'] for e in entries[:1]))


def event_table(html, dcc, entries, empty='Keine Ereignisse'):
    """Tabelle Zeit | Frame | Ereignistyp | Beschreibung; die Frame-Nummer ist ein Link oder grau mit Tooltip."""
    if not entries:
        return html.Div(empty, className='text-secondary small')

    def frame_cell(entry):
        frame = entry['frame']
        if frame is None:
            return html.Span('–', className='text-secondary')
        if entry['target'] is None:
            return html.Span(str(frame), className='text-secondary', title=NOT_LINKABLE)
        return dcc.Link(str(frame), href=jump_href(entry['target'], frame, entry['ts']), className='c2c-link',
                        title='Im Replay Lab öffnen (mit Vor- und Nachlauf)')
    head = html.Thead(html.Tr([html.Th('Zeit'), html.Th('Frame'), html.Th('Ereignistyp'), html.Th('Beschreibung')]))
    body = html.Tbody([html.Tr([html.Td(e['when'], className='text-secondary text-nowrap'), html.Td(frame_cell(e)),
                                html.Td(html.Strong(e['code'], className=COLORS.get(e['code'], '')),
                                        className='text-nowrap'),
                                html.Td(e['detail'], className='small')]) for e in entries])
    return html.Table([head, body], className='table table-sm table-dark mb-0 small c2c-events-table')


def recording_summary(recorder):
    """Aufnahmezustand und Zaehler als Text (REC-Chip-Tooltip, Zeile im Cockpit)."""
    try:
        stats = recorder.stats() if hasattr(recorder, 'stats') else {}
        running = bool(recorder.is_running())
    except Exception:
        return 'Aufnahmezustand nicht lesbar'
    path = stats.get('path')
    name = str(path).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1] if path else '–'
    return (f"{'Aufnahme läuft' if running else 'keine Aufnahme aktiv'} · Frames {stats.get('frames', 0)} · "
            f"Lücken {stats.get('gap_frames', 0)} · fehlende Entscheidungen {stats.get('missing_decisions', 0)} · "
            f"Session {name} · Fehler {stats.get('error') or 'keiner'}")
