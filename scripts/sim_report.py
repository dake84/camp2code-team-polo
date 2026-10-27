#!/usr/bin/env python3
"""Batch-Vergleich einer Session mit mehreren Prozessoren als HTML-Report (Punkt 6 aus Paket F).

Laeuft ohne Fahrzeug und ohne Kamera (Pi oder Laptop), im Repository-Root:

    python scripts/sim_report.py SESSION [--proc copilot --proc mock] [--params recorded|studio]
                                 [--from ID] [--to ID] [--warmup 30] [--worst 12]

Ausgabe:
    * Kennzahlen-Tabelle auf der Konsole (zum Einfuegen in den Chat)
    * ~/c2c-sim-<SESSION>.html  (eigenstaendig: Kennzahlen, Diagramm, auffaellige Frames)
    * ~/c2c-sim-<SESSION>.csv   (eine Zeile je Frame und Prozessor)
"""
import argparse
import base64
import csv
import html
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from frame_source import SessionSource  # noqa: E402
from processor_registry import label_for, processor_options  # noqa: E402
from sim_engine import SimEngine, params_from_settings  # noqa: E402

FIELDS = ('proc', 'frame_id', 'warmup', 'lanes_now', 'plausible_now', 'raw_now', 'car_now', 'speed_now',
          'held_now', 'stop_now', 'lanes_then', 'plausible_then', 'raw_then', 'car_then', 'status_then',
          'process_ms')
COLORS = ('#ffc533', '#2fbf8f', '#e5534b', '#c27cff', '#5aa0ff')


def fmt(value, digits=0, unit=''):
    if value is None:
        return '–'
    return f'{value * 100:.0f} %' if unit == '%' else f'{float(value):.{digits}f}{unit}'


def svg_chart(results, width=1000, height=260):
    """Stellwert damals (grau) und jetzt (je Prozessor) ueber der Frame-ID."""
    frames = results[0].frames
    if not frames:
        return ''
    ids = [f.frame_id for f in frames]
    x0, x1 = min(ids), max(ids) or 1

    def px(fid):
        return 40 + (fid - x0) / max(1, x1 - x0) * (width - 60)

    def py(angle):
        return 10 + (140 - angle) / 100 * (height - 40)

    def poly(values, color, dash=''):
        parts, current = [], []
        for fid, val in values:
            if val is None:
                if current:
                    parts.append(current)
                current = []
            else:
                current.append(f'{px(fid):.1f},{py(val):.1f}')
        if current:
            parts.append(current)
        return ''.join(f'<polyline fill="none" stroke="{color}" stroke-width="1.6" {dash} points="{" ".join(p)}"/>'
                       for p in parts if len(p) > 1)

    body = [f'<rect x="0" y="0" width="{width}" height="{height}" fill="#111a2b"/>']
    for angle in (45, 90, 135):
        body.append(f'<line x1="40" x2="{width - 20}" y1="{py(angle):.1f}" y2="{py(angle):.1f}" stroke="#2a3550"/>'
                    f'<text x="4" y="{py(angle) + 4:.1f}" fill="#93a2bd" font-size="11">{angle}°</text>')
    body.append(poly([(f.frame_id, f.then_car_angle) for f in frames], '#93a2bd', 'stroke-dasharray="4 3"'))
    for color, result in zip(COLORS, results):
        body.append(poly([(f.frame_id, f.car_angle) for f in result.frames], color))
        for f in result.frames:
            if f.stop_reason:
                body.append(f'<circle cx="{px(f.frame_id):.1f}" cy="{py(90):.1f}" r="4" fill="{color}"/>')
    legend = ' · '.join(f'<span style="color:{c}">■ {html.escape(label_for(r.processor_key))}</span>'
                        for c, r in zip(COLORS, results))
    return (f'<div class="legend"><span style="color:#93a2bd">┅ damals</span> · {legend} · Punkte = Regler-Stopp</div>'
            f'<svg viewBox="0 0 {width} {height}" width="100%">{"".join(body)}</svg>')


def thumb(image, width=320):
    if image is None:
        return ''
    h, w = image.shape[:2]
    if w > width:
        image = cv2.resize(image, (width, int(h * width / w)))
    ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return f'<img src="data:image/jpeg;base64,{base64.b64encode(data).decode()}"/>' if ok else ''


def worst_frames(results, count):
    """Frames mit den groessten Abweichungen bzw. unplausiblen Linien (ueber alle Prozessoren)."""
    score = {}
    for result in results:
        for pos, f in enumerate(result.measured):
            value = abs(f.angle_delta or 0) + (25 if not f.plausible else 0) + (40 if f.stop_reason else 0)
            if value > score.get(f.frame_id, (0, None))[0]:
                score[f.frame_id] = (value, f.index)
    best = sorted(score.items(), key=lambda item: -item[1][0])[:count]
    return sorted((fid, index) for fid, (value, index) in best if value > 0)


def render_now(source, result, index):
    from dashboard_overlay import render_calibration_overlay, render_overlay
    from models import CameraData, DrivingState
    import numpy as np
    frame = next((f for f in result.frames if f.index == index), None)
    if frame is None:
        return None
    camera = CameraData(process_image=np.empty(0), raw_image=source.image(index), frame_id=frame.frame_id,
                        crop=frame.crop, lanes=frame.lanes)
    if frame.car_angle is None:
        return render_calibration_overlay(camera)
    return render_overlay(camera, DrivingState(frame.frame_id, 0, frame.raw_angle or 90, frame.speed or 0,
                                               lanes=frame.lanes, steering_angle_car=frame.car_angle))


def main():
    keys = [o['value'] for o in processor_options()]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session')
    ap.add_argument('--proc', action='append', choices=keys)
    ap.add_argument('--params', choices=('recorded', 'studio'), default='recorded')
    ap.add_argument('--from', dest='lo', type=int)
    ap.add_argument('--to', dest='hi', type=int)
    ap.add_argument('--warmup', type=int, default=30)
    ap.add_argument('--worst', type=int, default=12)
    ap.add_argument('--root', default='Data_AI/sessions')
    ap.add_argument('--out', default=str(Path.home()))
    a = ap.parse_args()
    procs = a.proc or ['copilot']
    source = SessionSource(a.session, a.root)
    override = None
    if a.params == 'studio':
        from pipeline_config import load
        override = params_from_settings(load())
    engine = SimEngine()
    results = [engine.run(source, key, override, a.lo, a.hi, a.warmup) for key in procs]

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f'c2c-sim-{a.session}.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for r in results:
            for f in r.frames:
                writer.writerow([r.processor_key, f.frame_id, int(f.warmup), f.lane_count, int(f.plausible),
                                 f.raw_angle, f.car_angle, f.speed, int(f.held), f.stop_reason or '',
                                 f.then_lane_count, '' if f.then_plausible is None else int(f.then_plausible),
                                 f.then_raw_angle, f.then_car_angle, f.then_status or '', f'{f.process_ms:.1f}'])

    rows = [('2 Linien', 'two_lanes', '%'), ('plausibel', 'plausible', '%'), ('längste Lücke', 'longest_gap', '')]
    stats = [r.stats() for r in results]
    head = (f"{'Kennzahl':24s}{'damals':>10s}" + ''.join(f'{r.processor_key[:12]:>14s}' for r in results))
    lines = [f'Session {a.session} · Parameter {a.params} · {stats[0]["frames"]} Frames ohne Vorlauf', head]
    for title, key, unit in rows:
        lines.append(f'{title:24s}{fmt(stats[0][key + "_then"], 0, unit):>10s}'
                     + ''.join(f'{fmt(s[key + "_now"], 0, unit):>14s}' for s in stats))
    for title, key, digits, unit in (('Regler-Stopps', 'stops_now', 0, ''), ('Vorwert gehalten', 'held_now', 0, ''),
                                     ('Ø |Δ Stellwert|', 'mean_abs_delta', 1, '°'),
                                     ('Abweichung ≥5°', 'differs_5deg', 0, ''),
                                     ('Rechenzeit Median', 'process_ms_median', 1, ' ms'),
                                     ('übersprungen', 'errors', 0, '')):
        lines.append(f'{title:24s}{"":>10s}' + ''.join(f'{fmt(s[key], digits, unit):>14s}' for s in stats))
    table_text = '\n'.join(lines)
    print(table_text)

    gallery = []
    for fid, index in worst_frames(results, a.worst):
        cells = [f'<td><b>#{fid}</b><br>{thumb(source.then_image(index))}<br>damals</td>']
        for r in results:
            f = next((x for x in r.frames if x.index == index), None)
            note = '' if f is None else (f'{f.lane_count} Lin · {fmt(f.car_angle, 0, "°")}'
                                         + (f' · STOPP {f.stop_reason}' if f.stop_reason else ''))
            cells.append(f'<td>{thumb(render_now(source, r, index))}<br>{html.escape(r.processor_key)} · {note}</td>')
        gallery.append('<tr>' + ''.join(cells) + '</tr>')
    html_path = out_dir / f'c2c-sim-{a.session}.html'
    html_path.write_text(f"""<!doctype html><html lang="de"><meta charset="utf-8">
<title>C2C Simulation {html.escape(a.session)}</title>
<style>body{{background:#0e1424;color:#e9f1ff;font-family:system-ui,sans-serif;margin:16px}}
pre{{background:#172235;padding:10px;border-radius:8px}}td{{vertical-align:top;padding:4px;font-size:12px}}
img{{max-width:320px;border-radius:6px}}.legend{{font-size:13px;margin:6px 0}}</style>
<h2>Simulation · {html.escape(a.session)}</h2>
<p>Erstellt {datetime.now():%d.%m.%Y %H:%M} · Parameter: {a.params} · Vorlauf {a.warmup} Frames ·
keine Motoransteuerung (ReplayCar) · Uhr = aufgezeichnete Zeitstempel</p>
<pre>{html.escape(table_text)}</pre>
{svg_chart(results)}
<h3>Auffällige Frames (größte Abweichung, unplausibel oder Stopp)</h3>
<table>{''.join(gallery) or '<tr><td>keine</td></tr>'}</table>
</html>""", encoding='utf-8')
    print(f'Report: {html_path}\nDetail: {csv_path}')


if __name__ == '__main__':
    main()
