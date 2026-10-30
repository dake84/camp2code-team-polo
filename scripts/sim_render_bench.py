#!/usr/bin/env python3
"""Misst die Anzeigezeit der Simulations-Wiedergabe: bisheriger Weg gegen den neuen (PAKET-I2).

Aufruf (im Projektordner, mit aktivierter .venv, Dashboard darf laufen):

    python scripts/sim_render_bench.py SESSION [--frames 30] [--root Data_AI/sessions]

Es wird NICHTS gespeichert (kein Run, keine Aenderung an der Session). Zur Erzeugung von Ergebnisdaten rechnet das
Skript zuerst ``--frames`` Frames mit dem Copilot-Prozessor (dauert auf dem Pi einige Sekunden) und misst dann je Frame:

    alt        ``SimLab.render_frame`` + zwei Kodierungen (zwei Dekodierungen, HUD auf dem vollen Bild)
    neu kalt   ``SimLab.render_uris`` ohne Cache, je Bildgroesse 640 / 960 / 1280
    neu warm   dieselben Frames noch einmal (Cache-Treffer)

Die Ausgabe in ms je Anzeigeframe; bei 150 ms Anzeigetakt (PLAY_TICK_MS) ist alles unter ca. 100 ms "fluessig".
"""
import argparse
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def timed(function, items):
    values = []
    for item in items:
        t0 = time.perf_counter()
        function(item)
        values.append((time.perf_counter() - t0) * 1000)
    return values


def line(label, values):
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
    mean = statistics.fmean(values)
    print(f'  {label:<28} Ø {mean:7.1f} ms   Median {statistics.median(values):7.1f} ms   P90 {p90:7.1f} ms'
          f'   (max {1000 / mean:5.1f} Frames/s)')
    return mean


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('session')
    parser.add_argument('--frames', type=int, default=30)
    parser.add_argument('--root', default='Data_AI/sessions')
    args = parser.parse_args()

    from dashboard.helpers import uri
    from frame_source import SessionSource
    from sim_engine import SimEngine
    from sim_lab import DISPLAY_WIDTHS, SimLab

    source = SessionSource(args.session, args.root)
    print(f'Session {args.session}: {len(source.frames)} Frames; rechne {args.frames} Frames fuer die Messung ...')
    result = SimEngine().run(source, 'copilot', None, None, None, 0, max_frames=args.frames)
    positions = list(range(len(result.frames)))
    if not positions:
        raise SystemExit('keine verwertbaren Frames')
    raw = source.image(result.frames[0].index)
    print(f'Bildgroesse in der Session: {raw.shape[1]}x{raw.shape[0]}; {len(positions)} Frames gemessen\n')

    lab = SimLab(SimpleNamespace(running=lambda: False), SimpleNamespace(no_update=None), uri, args.root)
    print('Ein Anzeigeframe (damals + jetzt, kodiert):')
    old = line('alt (voll, 2x dekodieren)',
               timed(lambda p: [uri(image) for image in lab.render_frame(result, source, p)], positions))
    for width, label in DISPLAY_WIDTHS:
        def cold(position, width=width):
            lab._frames.clear()
            lab.render_uris(1, result, source, position, width)
        new = line(f'neu kalt, {label}', timed(cold, positions))
        print(f'      -> {old / new:4.1f}x schneller als alt')
    lab._frames.clear()
    for p in positions:
        lab.render_uris(1, result, source, p, 960)
    line('neu warm (Cache), 960', timed(lambda p: lab.render_uris(1, result, source, p, 960), positions))
    print('\nHinweis: Die Wiedergabe laeuft ueber zwei Dash-Callbacks je Takt (Takt 150 ms); zur Anzeigezeit kommen '
          'Netzwerk und Browser.')


if __name__ == '__main__':
    main()
