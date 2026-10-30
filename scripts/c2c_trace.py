#!/usr/bin/env python3
"""c2c_trace - Diagnose der Simulations-Wiedergabe (Server, Netzwerk, Browser) ohne Aenderung am Projekt.

Der Trace ist ein eigener Starter: er haengt Zeitmessungen zur Laufzeit ein (Monkeypatching im Speicher) und startet dann
das normale Dashboard. Es werden KEINE Projektdateien veraendert; ohne diesen Starter ist alles wie vorher.

    python scripts/c2c_trace.py run [--offline] [--port N]     Dashboard MIT Trace starten (Standardport 8050)
    python scripts/c2c_trace.py report [DATEI]                 Auswertung (Standard: letzte Datei in ~/c2c-trace)
    python scripts/c2c_trace.py bench SESSION [--frames 30]    reine Rechenzeit je Anzeigebild, ohne Dash/Netz/Browser

Waehrend ``run``:   http://<pi>:8050/_c2c_trace/net       Netztest vom Browser aus (Latenz, Durchsatz)
                    http://<pi>:8050/_c2c_trace/summary   laufende Auswertung als Text
Gemessen wird (Datei ~/c2c-trace/trace_<zeit>.jsonl):
    server   Dauer und Antwortgroesse jedes Dash-Callbacks; Zeitanteile (cv2-Aufrufe, JPEG lesen, HUD, Kodieren, Cache)
    browser  Round-Trip je Callback (Antwortbeginn, Download), Bild-Dekodierung, Haenger des Browser-Threads
    system   CPU je Kern und je Thread, Last, Temperatur, Drosselung (vcgencmd)
"""
import argparse
import functools
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

TRACE_DIR = Path.home() / 'c2c-trace'
VERSION = '1'


# ------------------------------------------------------------------ Ausgabe
class Sink:
    """Thread-sichere JSONL-Datei."""

    def __init__(self):
        self.lock = threading.Lock()
        self.fh = None
        self.path = None

    def open(self, path=None):
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else TRACE_DIR / f'trace_{time.strftime("%Y%m%d_%H%M%S")}.jsonl'
        self.fh = self.path.open('a', encoding='utf-8')
        return self.path

    def emit(self, **event):
        if self.fh is None:
            return
        event.setdefault('t', round(time.time(), 3))
        line = json.dumps(event, ensure_ascii=False, separators=(',', ':'), default=str)
        with self.lock:
            self.fh.write(line + '\n')
            self.fh.flush()


SINK = Sink()


# ------------------------------------------------------------------ Zeitanteile (Spans und Teile)
_tls = threading.local()


def _stack():
    stack = getattr(_tls, 'stack', None)
    if stack is None:
        stack = _tls.stack = []
    return stack


def add_part(label, ms):
    """Zeit ``ms`` allen gerade offenen Spans dieses Threads zurechnen."""
    for frame in getattr(_tls, 'stack', ()):
        part = frame.setdefault(label, [0, 0.0])
        part[0] += 1
        part[1] += ms


def wrap_part(owner, attr, label):
    """Zeitet ``owner.attr`` - aber nur, solange ein Span offen ist (Live-Betrieb bleibt unberuehrt)."""
    fn = getattr(owner, attr, None)
    if fn is None or getattr(fn, '_c2c_wrapped', False):
        return False

    @functools.wraps(fn)
    def timed(*args, **kwargs):
        if not getattr(_tls, 'stack', None):
            return fn(*args, **kwargs)
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            add_part(label, (time.perf_counter() - t0) * 1000)
    timed._c2c_wrapped = True
    try:
        setattr(owner, attr, timed)
    except (AttributeError, TypeError):
        return False
    return True


def wrap_span(owner, attr, label=None, extra=None):
    """Zeitet einen Aufruf als eigenen Eintrag (``k=span``) samt Teilen; ``extra(result)`` ergaenzt Felder."""
    fn = getattr(owner, attr, None)
    if fn is None or getattr(fn, '_c2c_wrapped', False):
        return False
    label = label or f'{getattr(owner, "__name__", "?")}.{attr}'

    @functools.wraps(fn)
    def spanned(*args, **kwargs):
        stack = _stack()
        parts = {}
        stack.append(parts)
        t0 = time.perf_counter()
        error, result = None, None
        try:
            result = fn(*args, **kwargs)
            return result
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000
            stack.pop()
            for parent in stack:
                part = parent.setdefault('> ' + label, [0, 0.0])
                part[0] += 1
                part[1] += ms
            fields = {}
            if extra is not None and error is None:
                try:
                    fields = extra(args, kwargs, result) or {}
                except Exception:
                    fields = {}
            SINK.emit(k='span', name=label, ms=round(ms, 2), th=threading.current_thread().name, err=error,
                      parts={k: [n, round(v, 2)] for k, (n, v) in parts.items()}, **fields)
    spanned._c2c_wrapped = True
    try:
        setattr(owner, attr, spanned)
    except (AttributeError, TypeError):
        return False
    return True


CV2_FUNCTIONS = ('imread', 'imdecode', 'imencode', 'resize', 'putText', 'line', 'polylines', 'fillPoly', 'rectangle',
                 'circle', 'ellipse', 'arrowedLine', 'addWeighted', 'cvtColor', 'GaussianBlur', 'Canny', 'inRange',
                 'morphologyEx', 'HoughLinesP', 'warpAffine', 'flip', 'copyMakeBorder')


def instrument_project(notes):
    """Haengt die Messungen ein. Fehlende Namen werden uebersprungen (die Projektstaende duerfen sich unterscheiden)."""
    import cv2
    for name in CV2_FUNCTIONS:
        wrap_part(cv2, name, 'cv2.' + name)

    def present(module_name):
        try:
            return __import__(module_name)
        except Exception as exc:  # Import-Fehler gehoeren in den Kopf der Auswertung, nicht zum Absturz
            notes.append(f'Modul {module_name} nicht ladbar ({type(exc).__name__}: {exc})')
            return None

    def uri_size(args, kwargs, result):
        return {'uri_kb': round(len(result) / 1024, 1)} if isinstance(result, str) else {}

    frame_source = present('frame_source')
    if frame_source is not None:
        for attr in ('image', 'then_image', 'then_hud'):
            wrap_part(frame_source.SessionSource, attr, f'SessionSource.{attr}')
    replay_hud = present('replay_hud')
    if replay_hud is not None:
        wrap_part(replay_hud, 'draw_hud', 'replay_hud.draw_hud')
    hud_render = present('hud_render')
    if hud_render is not None:
        wrap_part(hud_render, 'draw', 'hud_render.draw')
    engine = present('sim_engine')
    if engine is not None:
        wrap_span(engine.SimEngine, 'run', 'SimEngine.run')
        wrap_part(engine, 'downscale_image', 'sim_engine.downscale_image')
        original = engine.create_processor

        def create_processor(*args, **kwargs):
            processor = original(*args, **kwargs)
            wrap_part(type(processor), 'process', 'Processor.process')
            return processor
        engine.create_processor = create_processor
    camera = present('driving.camera_mode')
    if camera is not None:
        wrap_part(camera.CameraMode, '_run', 'CameraMode._run (Regler)')
    runs = present('simulation_runs')
    if runs is not None:
        notes.append('Paket I1 (simulation_runs) erkannt')
        wrap_span(runs.SimulationRunStore, 'open_reader', 'RunStore.open_reader')
        wrap_span(runs.SimulationRunStore, 'list_runs', 'RunStore.list_runs')
        wrap_part(runs.ResultsWriter, 'write', 'ResultsWriter.write')
        wrap_part(runs.SimulationRunStore, 'write_manifest', 'RunStore.write_manifest')
    else:
        notes.append('Paket I1 (simulation_runs) NICHT erkannt')
    frames = present('replay_frames')
    if frames is not None:
        notes.append('Paket I2 (replay_frames) erkannt')
        original_render = frames.FrameRenderCache.render

        @functools.wraps(original_render)
        def render(self, key, factory):
            before = self.hits
            t0 = time.perf_counter()
            try:
                return original_render(self, key, factory)
            finally:
                SINK.emit(k='cache', hit=self.hits > before, ms=round((time.perf_counter() - t0) * 1000, 2),
                          th=threading.current_thread().name)
        frames.FrameRenderCache.render = render
    else:
        notes.append('Paket I2 (replay_frames) NICHT erkannt')
    sim_lab = present('sim_lab')
    if sim_lab is not None:
        lab = sim_lab.SimLab
        for attr in ('render_frame', 'render_uris', '_render_pair', 'playback', 'advance', 'load_run', 'chart',
                     'stats_table', 'frame_table', 'start_job', 'job_view', 'run_options', 'match_hint', 'simulate'):
            wrap_span(lab, attr, f'SimLab.{attr}')
        original_init = lab.__init__

        @functools.wraps(original_init)
        def init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            inner = self.uri

            def timed_uri(*a, **k):
                t0 = time.perf_counter()
                result = inner(*a, **k)
                add_part('uri() JPEG+base64', (time.perf_counter() - t0) * 1000)
                return result
            self.uri = timed_uri
        lab.__init__ = init
    else:
        notes.append('sim_lab nicht ladbar')


# ------------------------------------------------------------------ Flask: Callback-Dauer, Client-Meldungen, Netztest
CLIENT_JS = r"""
(function () {
  if (window.__c2cTrace) { return; }
  window.__c2cTrace = true;
  var queue = [], fetch0 = window.fetch.bind(window);
  function push(o) { o.t = Date.now() / 1000; queue.push(o); }
  function flush() {
    if (!queue.length) { return; }
    var body = JSON.stringify(queue.splice(0, queue.length));
    try {
      if (navigator.sendBeacon && navigator.sendBeacon('/_c2c_trace/client', new Blob([body], {type: 'application/json'}))) { return; }
    } catch (e) {}
    fetch0('/_c2c_trace/client', {method: 'POST', body: body, headers: {'Content-Type': 'application/json'}, keepalive: true});
  }
  setInterval(flush, 3000);
  window.addEventListener('pagehide', flush);
  document.addEventListener('visibilitychange', flush);
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('_dash-update-component') < 0) { return fetch0(input, init); }
    var out = '?', trig = '', reqBytes = 0;
    try { var j = JSON.parse(init.body); out = j.output; trig = (j.changedPropIds || []).join(','); reqBytes = init.body.length; } catch (e) {}
    var t0 = performance.now();
    return fetch0(input, init).then(function (res) {
      var t1 = performance.now(), srv = res.headers.get('X-C2C-Srv-Ms');
      res.clone().arrayBuffer().then(function (buf) {
        push({k: 'cli', out: out, trig: trig, ttfb: t1 - t0, total: performance.now() - t0, bytes: buf.byteLength,
              req: reqBytes, srv: srv === null ? null : parseFloat(srv), enc: res.headers.get('Content-Encoding')});
      });
      return res;
    });
  };
  try {
    new MutationObserver(function (list) {
      list.forEach(function (m) {
        var img = m.target;
        if (!img || img.tagName !== 'IMG' || !img.id || img.id.indexOf('sim-img') !== 0 || !img.src) { return; }
        var t0 = performance.now(), id = img.id, len = img.src.length;
        img.decode().then(function () { push({k: 'img', id: id, dec: performance.now() - t0, len: len}); },
                          function () { push({k: 'img', id: id, dec: -1, len: len}); });
      });
    }).observe(document.documentElement, {subtree: true, attributes: true, attributeFilter: ['src']});
  } catch (e) {}
  try {
    new PerformanceObserver(function (list) {
      list.getEntries().forEach(function (e) { push({k: 'long', ms: e.duration}); });
    }).observe({entryTypes: ['longtask']});
  } catch (e) {}
  var c = navigator.connection || {};
  push({k: 'env', ua: navigator.userAgent, type: c.effectiveType || null, rtt: c.rtt || null, down: c.downlink || null,
        w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio});
})();
"""

NET_PAGE = """<!doctype html><meta charset=utf-8><title>c2c Netztest</title>
<body style="font-family:monospace;background:#0b1220;color:#e9f1ff;padding:1em">
<h3>c2c Netztest (Browser &harr; Dashboard-Prozess)</h3>
<button id=go style="font-size:1.1em">Test starten</button><pre id=out>bereit</pre>
<script>
var out = document.getElementById('out');
function med(a) { var s = a.slice().sort(function (x, y) { return x - y; }); return s[Math.floor(s.length / 2)]; }
async function once(url) {
  var t0 = performance.now(); var r = await fetch(url, {cache: 'no-store'}); var b = await r.arrayBuffer();
  return [performance.now() - t0, b.byteLength];
}
document.getElementById('go').onclick = async function () {
  var res = {k: 'net'}, text = '';
  out.textContent = 'Latenz ...';
  var rtts = [];
  for (var i = 0; i < 20; i++) { rtts.push((await once('/_c2c_trace/ping?' + i))[0]); }
  res.rtt_min = Math.min.apply(null, rtts); res.rtt_med = med(rtts); res.rtt_max = Math.max.apply(null, rtts);
  text += 'Latenz (Anfrage ohne Inhalt, 20x): min ' + res.rtt_min.toFixed(1) + ' / Median ' + res.rtt_med.toFixed(1) +
          ' / max ' + res.rtt_max.toFixed(1) + ' ms\\n';
  res.dl = {};
  var sizes = [100, 500, 2000];
  for (var s = 0; s < sizes.length; s++) {
    out.textContent = text + 'Download ' + sizes[s] + ' KB ...';
    var times = [];
    for (var n = 0; n < 4; n++) { times.push((await once('/_c2c_trace/blob?kb=' + sizes[s] + '&n=' + n))[0]); }
    var ms = med(times), mbit = sizes[s] * 1024 * 8 / (ms / 1000) / 1e6;
    res.dl[sizes[s]] = {ms: ms, mbit: mbit};
    text += 'Download ' + sizes[s] + ' KB: Median ' + ms.toFixed(0) + ' ms = ' + mbit.toFixed(1) + ' Mbit/s\\n';
  }
  out.textContent = text + '\\nEin Anzeigebild (damals+jetzt) hat typisch 60-250 KB; die Zeile fuer die passende Groesse zeigt die reine Uebertragungszeit.';
  res.t = Date.now() / 1000;
  fetch('/_c2c_trace/client', {method: 'POST', body: JSON.stringify([res]), headers: {'Content-Type': 'application/json'}});
};
</script></body>"""


def short_output(output):
    """'..a.b...c.d..' -> 'a(+1)'."""
    if not isinstance(output, str):
        return '?'
    ids = [part.rsplit('.', 1)[0] for part in output.strip('.').split('...') if part]
    if not ids:
        return '?'
    return ids[0] + (f'(+{len(ids) - 1})' if len(ids) > 1 else '')


def hook_flask(app):
    server = getattr(app, 'server', None)
    if server is None:
        return False
    from flask import Response, g, request

    @server.before_request
    def c2c_before():
        g.c2c_t0 = time.perf_counter()

    @server.after_request
    def c2c_after(response):
        t0 = getattr(g, 'c2c_t0', None)
        if t0 is None or request.path != '/_dash-update-component':
            return response
        ms = (time.perf_counter() - t0) * 1000
        response.headers['X-C2C-Srv-Ms'] = f'{ms:.1f}'
        body = request.get_json(silent=True) or {}
        try:
            size = response.calculate_content_length()
            if size is None:
                size = len(response.get_data())
        except Exception:
            size = None
        SINK.emit(k='srv', out=short_output(body.get('output')), ms=round(ms, 2), bytes=size,
                  trig=','.join(body.get('changedPropIds') or []), status=response.status_code,
                  enc=response.headers.get('Content-Encoding'))
        return response

    @server.route('/_c2c_trace/client', methods=['POST'])
    def c2c_client():
        events = request.get_json(silent=True)
        for event in events if isinstance(events, list) else []:
            if isinstance(event, dict):
                event['src'] = 'browser'
                event['bt'] = event.pop('t', None)      # Uhr des Browsers (kann abweichen); t = Empfang am Server
                SINK.emit(**event)
        return Response(status=204)

    @server.route('/_c2c_trace/ping')
    def c2c_ping():
        return Response(b'', headers={'Cache-Control': 'no-store'})

    blob = os.urandom(1024 * 1024)

    @server.route('/_c2c_trace/blob')
    def c2c_blob():
        kb = max(1, min(8192, int(request.args.get('kb', 100))))
        data = (blob * (kb // 1024 + 1))[:kb * 1024]
        return Response(data, mimetype='application/octet-stream', headers={'Cache-Control': 'no-store'})

    @server.route('/_c2c_trace/net')
    def c2c_net():
        return Response(NET_PAGE, mimetype='text/html')

    @server.route('/_c2c_trace/summary')
    def c2c_summary():
        return Response(build_report(load_events(SINK.path)), mimetype='text/plain; charset=utf-8')

    # Browser-Skript vor dem Dash-Renderer laden
    marker = '{%metas%}'
    html = app.index_string
    if marker in html and '__c2cTrace' not in html:
        app.index_string = html.replace(marker, marker + '<script>' + CLIENT_JS + '</script>', 1)
    return True


# ------------------------------------------------------------------ System
THROTTLE_BITS = {0: 'Unterspannung', 1: 'Takt begrenzt', 2: 'gedrosselt', 3: 'Temperaturlimit',
                 16: 'Unterspannung (seit Start)', 17: 'Takt begrenzt (seit Start)', 18: 'gedrosselt (seit Start)',
                 19: 'Temperaturlimit (seit Start)'}


def read_temp():
    try:
        return int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000.0
    except (OSError, ValueError):
        return None


def read_throttled():
    try:
        out = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=1).stdout
        return int(out.strip().split('=')[1], 16)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def normalize_thread(name):
    import re
    return re.sub(r'\d+', '*', name)


def sampler(interval=2.0):
    try:
        import psutil
        proc = psutil.Process()
        last = {t.id: t.user_time + t.system_time for t in proc.threads()}
        psutil.cpu_percent(percpu=True)
        proc.cpu_percent(None)
    except Exception:
        psutil, proc, last = None, None, {}
    last_t, tick = time.time(), 0
    while True:
        time.sleep(interval)
        tick += 1
        event = {'k': 'sys'}
        try:
            event['load1'] = round(os.getloadavg()[0], 2)
        except OSError:
            pass
        temp = read_temp()
        if temp is not None:
            event['temp'] = round(temp, 1)
        if tick % 5 == 1:
            throttled = read_throttled()
            if throttled is not None:
                event['thr'] = throttled
        if psutil is not None:
            now = time.time()
            try:
                event['cpu'] = [round(v) for v in psutil.cpu_percent(percpu=True)]
                event['proc'] = round(proc.cpu_percent(None))      # Dashboard-Prozess, 100 = ein voller Kern
                names = {t.native_id: t.name for t in threading.enumerate()}
                current, rows = {}, {}
                for t in proc.threads():
                    current[t.id] = t.user_time + t.system_time
                    delta = (current[t.id] - last.get(t.id, current[t.id])) / max(0.001, now - last_t) * 100
                    if delta >= 1:
                        label = normalize_thread(names.get(t.id, 'nativ (ohne Python-Namen)'))
                        rows[label] = rows.get(label, 0) + delta
                last, last_t = current, now
                event['th'] = sorted(([k, round(v)] for k, v in rows.items()), key=lambda r: -r[1])[:8]
            except Exception:
                pass
        SINK.emit(**event)


# ------------------------------------------------------------------ Auswertung
def load_events(path=None):
    if path is None:
        files = sorted(TRACE_DIR.glob('trace_*.jsonl'))
        if not files:
            raise SystemExit(f'Keine Trace-Datei in {TRACE_DIR}')
        path = files[-1]
    events = []
    with Path(path).open(encoding='utf-8', errors='replace') as handle:
        for line in handle:
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return events


def p95(values):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * 0.95))] if values else 0.0


def mean(values):
    return statistics.fmean(values) if values else 0.0


def table(rows, header):
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    lines = ['  '.join(str(h).ljust(w) for h, w in zip(header, widths))]
    lines += ['  '.join(str(c).ljust(w) for c, w in zip(r, widths)) for r in rows]
    return lines


def build_report(events):
    lines, findings = [], []
    if not events:
        return 'Noch keine Ereignisse.\n'
    t_first, t_last = min(e['t'] for e in events), max(e['t'] for e in events)
    by = {}
    for event in events:
        by.setdefault(event.get('k'), []).append(event)
    lines.append(f'== c2c-Trace: {len(events)} Ereignisse, {t_last - t_first:.0f} s ==')
    for note in (e.get('text') for e in by.get('note', [])):
        lines.append('  ' + str(note))

    # Browser/Umgebung
    for env in by.get('env', [])[-1:]:
        lines.append(f"  Browser: {str(env.get('ua'))[:90]} | Fenster {env.get('w')}x{env.get('h')} @{env.get('dpr')} | "
                     f"Netzschaetzung {env.get('type')} rtt {env.get('rtt')} ms down {env.get('down')} Mbit/s")

    # Callbacks serverseitig
    srv = {}
    for e in by.get('srv', []):
        srv.setdefault(e['out'], []).append(e)
    lines += ['', '-- Dash-Callbacks (Serverzeit = Callback + Serialisierung, ohne Netz) --']
    rows = []
    for out, items in sorted(srv.items(), key=lambda kv: -sum(i['ms'] for i in kv[1])):
        ms = [i['ms'] for i in items]
        sizes = [i['bytes'] for i in items if i.get('bytes')]
        span = max(1.0, items[-1]['t'] - items[0]['t'])
        rows.append((out, len(items), f'{len(items) / span:.1f}', f'{mean(ms):.0f}', f'{p95(ms):.0f}', f'{max(ms):.0f}',
                     f'{mean(sizes) / 1024:.0f}' if sizes else '-'))
    lines += table(rows, ('Output', 'n', '/s', 'Ø ms', 'p95', 'max', 'Ø KB'))

    # Browser-Round-Trip
    cli = {}
    for e in by.get('cli', []):
        cli.setdefault(short_output(e['out']), []).append(e)
    if cli:
        lines += ['', '-- Browser: Round-Trip je Callback (alles in ms) --',
                  '   Netz+Warten = Antwortbeginn - Serverzeit (enthaelt auch Wartezeit im Browser) | Download = Gesamt - Antwortbeginn']
        rows = []
        for out, items in sorted(cli.items(), key=lambda kv: -mean([i['total'] for i in kv[1]])):
            known = [i for i in items if i.get('srv') is not None]
            net = [i['ttfb'] - i['srv'] for i in known]
            dl = [i['total'] - i['ttfb'] for i in items]
            rows.append((out, len(items), f"{mean([i['total'] for i in items]):.0f}",
                         f"{p95([i['total'] for i in items]):.0f}", f'{mean([i["srv"] for i in known]):.0f}' if known else '-',
                         f'{mean(net):.0f}' if net else '-', f'{mean(dl):.0f}',
                         f"{mean([i['bytes'] for i in items]) / 1024:.0f}"))
        lines += table(rows, ('Output', 'n', 'Gesamt Ø', 'p95', 'Server Ø', 'Netz+Warten Ø', 'Download Ø', 'KB Ø'))
    else:
        lines += ['', '-- Browser: keine Messwerte (Seite im Browser neu laden, nachdem das Dashboard mit "run" gestartet wurde) --']

    # Bilder im Browser
    imgs = by.get('img', [])
    if imgs:
        lines += ['', '-- Browser: Bild setzen -> dekodiert --']
        for image_id in sorted({i['id'] for i in imgs}):
            items = [i for i in imgs if i['id'] == image_id and i['dec'] >= 0]
            stamps = [i.get('bt') or i['t'] for i in items]
            gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b - a < 5]
            lines.append(f"  {image_id}: n={len(items)}  Dekodieren Ø {mean([i['dec'] for i in items]):.0f} ms, "
                         f"p95 {p95([i['dec'] for i in items]):.0f} ms | angezeigt alle Ø {mean(gaps) * 1000:.0f} ms "
                         f"(= {1 / mean(gaps):.1f} Bilder/s)" if gaps else f'  {image_id}: n={len(items)}')
    longs = by.get('long', [])
    if longs:
        lines.append(f"  Browser-Thread blockiert (Long Tasks >50 ms): {len(longs)}x, Summe {sum(l['ms'] for l in longs) / 1000:.1f} s, "
                     f"max {max(l['ms'] for l in longs):.0f} ms")

    # Server-Spans
    spans = {}
    for e in by.get('span', []):
        spans.setdefault(e['name'], []).append(e)
    lines += ['', '-- Server: Funktionen (Ø je Aufruf) und Zeitanteile --']
    for name, items in sorted(spans.items(), key=lambda kv: -sum(i['ms'] for i in kv[1])):
        if name == 'SimEngine.run':
            continue
        ms = [i['ms'] for i in items]
        threads = sorted({normalize_thread(i['th']) for i in items})
        lines.append(f"  {name}: n={len(items)}  Ø {mean(ms):.0f} ms  p95 {p95(ms):.0f}  max {max(ms):.0f}   Thread: {', '.join(threads)}"
                     + (f"   Fehler: {sorted({i['err'] for i in items if i.get('err')})}" if any(i.get('err') for i in items) else ''))
        merged = {}
        for item in items:
            for label, (count, total) in item['parts'].items():
                row = merged.setdefault(label, [0, 0.0])
                row[0] += count
                row[1] += total
        for label, (count, total) in sorted(merged.items(), key=lambda kv: -kv[1][1])[:7]:
            lines.append(f'      {label:<34} {total / len(items):7.1f} ms je Aufruf   ({count / len(items):.1f} Aufrufe)')
    uri_kb = [i['uri_kb'] for i in by.get('span', []) if i.get('uri_kb')]
    if uri_kb:
        lines.append(f'  Bildgroesse je uri(): Ø {mean(uri_kb):.0f} KB')

    # Berechnung
    runs = spans.get('SimEngine.run', [])
    if runs:
        lines += ['', '-- Berechnung (SimEngine.run) --']
        for item in runs[-3:]:
            frames = item['parts'].get('Processor.process', [0, 0])[0] or 1
            lines.append(f"  Lauf {item['ms'] / 1000:.1f} s, {frames} Frames = {item['ms'] / frames:.0f} ms/Frame")
            for label, (count, total) in sorted(item['parts'].items(), key=lambda kv: -kv[1][1])[:8]:
                lines.append(f'      {label:<34} {total / frames:7.1f} ms/Frame  ({total / max(1, item["ms"]) * 100:.0f} %)')

    # Cache
    caches = by.get('cache', [])
    if caches:
        hits = sum(1 for c in caches if c['hit'])
        fore = [c for c in caches if not c['th'].startswith('sim-frame-prefetch')]
        fore_hits = sum(1 for c in fore if c['hit'])
        lines += ['', f'-- Bildcache (I2): {hits}/{len(caches)} Treffer; Anzeige-Callback {fore_hits}/{len(fore)} Treffer, '
                      f"Fehlgriffe kosten Ø {mean([c['ms'] for c in fore if not c['hit']]):.0f} ms --"]

    # System
    sys_events = by.get('sys', [])
    if sys_events:
        lines += ['', '-- System (alle 2 s) --']
        temps = [s['temp'] for s in sys_events if 'temp' in s]
        loads = [s['load1'] for s in sys_events if 'load1' in s]
        cpus = [mean(s['cpu']) for s in sys_events if s.get('cpu')]
        procs = [s['proc'] for s in sys_events if 'proc' in s]
        lines.append(f"  CPU gesamt Ø {mean(cpus):.0f} % (max {max(cpus) if cpus else 0:.0f})  Last Ø {mean(loads):.1f}  "
                     + (f'Temperatur max {max(temps):.0f} °C' if temps else '')
                     + (f"   Dashboard-Prozess Ø {mean(procs):.0f} % (100 % = ein Kern, max {max(procs)} %)" if procs else ''))
        flags = 0
        for s in sys_events:
            flags |= s.get('thr', 0) or 0
        if any('thr' in s for s in sys_events):
            active = [name for bit, name in THROTTLE_BITS.items() if flags & (1 << bit)]
            lines.append(f"  Drosselung (vcgencmd): 0x{flags:X}  {', '.join(active) if active else 'keine'}")
        threads = {}
        for s in sys_events:
            for name, pct in s.get('th', []):
                threads.setdefault(name, []).append(pct)
        for name, values in sorted(threads.items(), key=lambda kv: -sum(kv[1]))[:6]:
            lines.append(f'  Thread {name:<46} Ø {sum(values) / len(sys_events):5.1f} % eines Kerns (max {max(values)} %)')
        lines.append('  (kurzlebige Request-Threads des Servers erscheinen hier nicht einzeln; sie stecken im Prozesswert)')

    # Netztest
    for net in by.get('net', [])[-1:]:
        lines += ['', '-- Netztest --',
                  f"  Latenz min {net['rtt_min']:.1f} / Median {net['rtt_med']:.1f} / max {net['rtt_max']:.1f} ms"]
        for kb, item in net['dl'].items():
            lines.append(f"  Download {kb} KB: {item['ms']:.0f} ms = {item['mbit']:.1f} Mbit/s")

    # Einordnung
    tick = 150
    duration = max(1.0, t_last - t_first)
    long_share = sum(l['ms'] for l in longs) / 1000 / duration if longs else 0.0
    decs = [i['dec'] for i in imgs if i['dec'] >= 0]
    browser_busy = long_share > 0.2 or (decs and p95(decs) > 100)
    net = by.get('net', [None])[-1]
    net_fast = bool(net) and net['rtt_med'] < 30 and max(v['mbit'] for v in net['dl'].values()) >= 50
    img_srv = next((v for k, v in srv.items() if k.startswith('sim-img')), None)
    img_cli = next((v for k, v in cli.items() if k.startswith('sim-img')), None)
    play_cli = next((v for k, v in cli.items() if k.startswith('sim-index(')), None)
    if img_srv:
        s_ms = mean([i['ms'] for i in img_srv])
        if s_ms > tick:
            findings.append(f'SERVER: Der Bild-Callback braucht Ø {s_ms:.0f} ms und damit mehr als einen Anzeigetakt ({tick} ms). '
                            'Die Zeitanteile oben zeigen, welcher Schritt (Dekodieren, HUD, Kodieren) dominiert.')
        elif s_ms > tick * 0.6:
            findings.append(f'SERVER: Der Bild-Callback braucht Ø {s_ms:.0f} ms - knapp unter dem Takt ({tick} ms), wenig Reserve.')
        else:
            findings.append(f'SERVER OK: Der Bild-Callback braucht nur Ø {s_ms:.0f} ms (Takt {tick} ms) - die Rechenzeit ist nicht der Engpass.')
    if img_cli:
        known = [i for i in img_cli if i.get('srv') is not None]
        total = mean([i['total'] for i in img_cli])
        if known:
            wait = mean([i['ttfb'] - i['srv'] for i in known])
            dl = mean([i['total'] - i['ttfb'] for i in img_cli])
            srv_ms = mean([i['srv'] for i in known])
            parts = {'Server': srv_ms, 'Netz/Warten': wait, 'Download': dl}
            top = max(parts, key=parts.get)
            findings.append(f'ROUND-TRIP: Ein Bild-Callback dauert im Browser Ø {total:.0f} ms = Server {srv_ms:.0f} + Netz/Warten {wait:.0f} '
                            f'+ Download {dl:.0f}. Groesster Anteil: {top}.')
            if top != 'Server' and parts[top] > tick:
                if browser_busy or net_fast:
                    findings.append('AUSSERHALB DES SERVERS, ABER EHER BROWSER ALS NETZ: Der Browser-Thread ist belegt bzw. der Netztest ist schnell. '
                                    'Die Zeit geht verloren, bevor der Browser die Antwort verarbeitet (Rechner mit dem Browser, Plotly-Diagramm, '
                                    'viele gleichzeitige Callbacks).')
                else:
                    findings.append('NETZ (wahrscheinlich): Zeit geht ausserhalb des Servers verloren, der Browser ist nicht auffaellig belegt. '
                                    'Netztest unter /_c2c_trace/net ausfuehren und Bildgroesse (KB) mit dem Durchsatz vergleichen.')
        kb = mean([i['bytes'] for i in img_cli]) / 1024
        if kb > 150:
            findings.append(f'DATENMENGE: Ein Bild-Callback liefert Ø {kb:.0f} KB (zwei Bilder als Base64). Das ist bei WLAN/VPN viel.')
        if play_cli:
            chain = mean([i['total'] for i in play_cli]) + total
            findings.append(f'KETTE JE TAKT: Wiedergabe-Callback (Ø {mean([i["total"] for i in play_cli]):.0f} ms) und danach Bild-Callback '
                            f'(Ø {total:.0f} ms) laufen nacheinander = Ø {chain:.0f} ms je Bild, also hoechstens {1000 / chain:.1f} Bilder/s, '
                            'egal wie schnell der Server rechnet.')
    if decs and p95(decs) > 100:
        findings.append(f'BROWSER: Bild-Dekodierung p95 {p95(decs):.0f} ms - der Rechner mit dem Browser ist der Engpass.')
    if long_share > 0.2:
        findings.append(f'BROWSER: Der Browser-Thread war {long_share * 100:.0f} % der Zeit blockiert (Long Tasks), z. B. durch Diagramm/Plotly, '
                        'Bilder oder zu viele Callbacks.')
    other = [(k, len(v)) for k, v in srv.items() if not k.startswith('sim-')]
    if img_srv and other:
        rate = sum(n for _, n in other) / duration
        busiest = ', '.join(f'{k} ({n / duration:.1f}/s)' for k, n in sorted(other, key=lambda kv: -kv[1])[:4])
        if rate > 2:
            findings.append(f'HINTERGRUND: Waehrend der Wiedergabe laufen zusaetzlich Ø {rate:.1f} Callbacks/s anderer Ansichten '
                            f'({busiest}). Jeder kostet Round-Trip und Browser-Arbeit und konkurriert mit den Bildern.')
    if sys_events:
        if flags & 0b1111:
            findings.append('PI: Unterspannung/Drosselung ist AKTIV gewesen - die CPU lief langsamer als normal.')
        elif flags & (0b1111 << 16):
            findings.append('PI: Seit dem Start gab es Unterspannung/Drosselung (Netzteil/Kuehlung pruefen).')
        if cpus and mean(cpus) > 85:
            findings.append(f'PI: Die CPU ist insgesamt zu {mean(cpus):.0f} % ausgelastet.')
        busy = [(name, sum(v) / len(sys_events)) for name, v in threads.items()]
        extra = [(n, v) for n, v in busy if v > 30 and 'process_request' not in n and 'prefetch' not in n
                 and 'simulation-runner' not in n and 'MainThread' not in n]
        if extra:
            findings.append('PI: Diese Threads belasten die CPU, obwohl sie nicht zur Wiedergabe gehoeren: ' +
                            ', '.join(f'{n} ({v:.0f} %)' for n, v in sorted(extra, key=lambda r: -r[1])[:3]) +
                            ' (z. B. Live-Kamera/Regler laufen auch im Simulationsmodus weiter).')
    lines += ['', '-- Einordnung (Hinweise aus den Messwerten, keine Diagnose) --']
    lines += ['  * ' + f for f in findings] or ['  * keine Auffaelligkeit - Wiedergabe lange genug laufen lassen (>= 30 s) und erneut auswerten.']
    return '\n'.join(lines) + '\n'


# ------------------------------------------------------------------ bench
def cmd_bench(args):
    sys.path.insert(0, os.getcwd())
    import cv2
    from dashboard.helpers import uri
    from frame_source import SessionSource
    from replay_hud import draw_hud
    source = SessionSource(args.session, args.root)
    count = len(source.frames)
    picks = sorted({int(i * (count - 1) / max(1, args.frames - 1)) for i in range(min(args.frames, count))})
    rows, kb = {'JPEG lesen+dekodieren': [], 'verkleinern': [], 'HUD zeichnen (1 Bild)': [], 'JPEG kodieren+base64 (1 Bild)': []}, []
    for index in picks:
        t0 = time.perf_counter()
        raw = source.image(index)
        rows['JPEG lesen+dekodieren'].append((time.perf_counter() - t0) * 1000)
        height, width = raw.shape[:2]
        t0 = time.perf_counter()
        small = raw if args.width >= width else cv2.resize(raw, (args.width, int(height * args.width / width)),
                                                           interpolation=cv2.INTER_AREA)
        rows['verkleinern'].append((time.perf_counter() - t0) * 1000)
        sh = small.shape[0]
        row = {'frame_id': index, 'roi_top_px': int(sh * .45), 'roi_bottom_px': int(sh * .85),
               'lanes_json': '[[-1.2,166.0],[1.6,-317.0]]', 'vehicle_mode': 'auto', 'data_status': 'complete',
               'steering_angle': 95, 'requested_angle': 100, 'steering_angle_car': 99, 'speed': 30,
               'vehicle_mode_key': 'camera', 'process_width': 320, 'process_height': 240, 'roi_high': 0.55}
        t0 = time.perf_counter()
        hud = draw_hud(small, row, style=args.style)
        rows['HUD zeichnen (1 Bild)'].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        encoded = uri(hud, quality=args.quality, max_width=args.width)
        rows['JPEG kodieren+base64 (1 Bild)'].append((time.perf_counter() - t0) * 1000)
        kb.append(len(encoded) / 1024)
    print(f'Session {args.session}: {count} Frames, Original {width}x{height}, Anzeigebreite {args.width}, HUD {args.style}, '
          f'{len(picks)} Stichproben, {os.cpu_count()} Kerne')
    total = 0.0
    for name, values in rows.items():
        factor = 2 if 'Bild)' in name else 1          # damals + jetzt
        total += mean(values) * factor
        print(f'  {name:<32} Ø {mean(values):6.1f} ms  p95 {p95(values):6.1f}  ' + ('(x2 je Anzeigetakt)' if factor == 2 else '(1x; vor Paket I2 2x)' if 'dekod' in name else ''))
    per_tick = mean(kb) * 2
    print(f'\n  Rechenzeit je Anzeigetakt (damals+jetzt, einmal dekodiert): ca. {total:.0f} ms (Takt: 150 ms)')
    print(f'  Datenmenge je Anzeigetakt: ca. {per_tick:.0f} KB')
    for mbit in (5, 20, 50, 100):
        print(f'     bei {mbit:>3} Mbit/s: {per_tick * 1024 * 8 / (mbit * 1e6) * 1000:5.0f} ms Uebertragung')
    print('\n  Das ist nur die Rechenzeit OHNE Dash, Netz, Browser und Cache. Zum Vergleich: "run" und "report".')
    return 0


# ------------------------------------------------------------------ run / report
def cmd_run(rest):
    project = Path(os.environ.get('C2C_PROJECT') or Path(__file__).resolve().parent.parent)
    if not (project / 'Camp2CodeDashboard.py').is_file():
        project = Path.cwd()
    if not (project / 'Camp2CodeDashboard.py').is_file():
        raise SystemExit('Camp2CodeDashboard.py nicht gefunden - im Projektordner starten oder C2C_PROJECT setzen')
    os.chdir(project)
    sys.path.insert(0, str(project))
    path = SINK.open()
    notes = []
    instrument_project(notes)
    import Camp2CodeDashboard as dashboard
    create_app = dashboard.create_app

    def traced_create_app(*args, **kwargs):
        app = create_app(*args, **kwargs)
        notes.append('Flask-Hooks ' + ('aktiv' if hook_flask(app) else 'NICHT moeglich'))
        for note in notes:
            SINK.emit(k='note', text=note)
        return app
    dashboard.create_app = traced_create_app
    SINK.emit(k='note', text=f'c2c_trace {VERSION}, Projekt {project}, Python {sys.version.split()[0]}, '
                             f'Kerne {os.cpu_count()}, Argumente {rest}')
    threading.Thread(target=sampler, name='c2c-trace-sampler', daemon=True).start()
    port = 8050
    if '--port' in rest:
        try:
            port = int(rest[rest.index('--port') + 1])
        except (IndexError, ValueError):
            pass
    print(f'\n[c2c_trace] Trace-Datei: {path}')
    print(f'[c2c_trace] Netztest:    http://<pi-adresse>:{port}/_c2c_trace/net')
    print(f'[c2c_trace] Live-Report: http://<pi-adresse>:{port}/_c2c_trace/summary')
    print('[c2c_trace] Browser-Seite NACH dem Start neu laden (F5). Dann Simulation abspielen (>= 30 s), danach Ctrl+C und:')
    print(f'[c2c_trace]   python {Path(__file__).name} report\n', flush=True)
    sys.argv = [sys.argv[0]] + rest
    try:
        dashboard.main()
    finally:
        try:
            print('\n' + build_report(load_events(SINK.path)))
        except Exception as exc:
            print(f'[c2c_trace] Auswertung nicht moeglich: {exc}')
        print(f'[c2c_trace] Datei: {SINK.path}')


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        return 0
    command, rest = sys.argv[1], sys.argv[2:]
    if command == 'run':
        cmd_run(rest)
        return 0
    if command == 'report':
        print(build_report(load_events(rest[0] if rest else None)))
        return 0
    if command == 'bench':
        parser = argparse.ArgumentParser(prog='c2c_trace.py bench')
        parser.add_argument('session')
        parser.add_argument('--frames', type=int, default=30)
        parser.add_argument('--width', type=int, default=960)
        parser.add_argument('--quality', type=int, default=70)
        parser.add_argument('--style', default='classic', choices=('classic', 'jetpilot', 'arcade'))
        parser.add_argument('--root', default='Data_AI/sessions')
        return cmd_bench(parser.parse_args(rest))
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
