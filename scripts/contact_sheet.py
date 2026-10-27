#!/usr/bin/env python3
"""Kontaktbogen einer Session als PNG (fuer Upload in den Chat).

Je Frame: Rohbild mit ROI und gespeicherten Linien | Farbmaske mit den damaligen Parametern.
Aufruf: python scripts/contact_sheet.py [SESSION] [--from ID] [--to ID] [--n 12]
Ohne SESSION wird die neueste Session verwendet. Liest nur, schreibt nur nach ~/.
"""
import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path('Data_AI/sessions')
W, H = 320, 240


def mask_for(raw, params, top, bottom):
    hue, sat, val = params[0], params[1], params[2]
    hsv = cv2.cvtColor(raw[top:bottom], cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array([hue[0], sat[0], val[0]]), np.array([hue[1], sat[1], val[1]]))
    full = np.zeros(raw.shape[:2], np.uint8)
    full[top:bottom] = m
    return cv2.cvtColor(full, cv2.COLOR_GRAY2BGR)


def draw_lanes(img, lanes, top, bottom):
    colors = ((60, 230, 100), (20, 200, 255), (255, 80, 255))
    for i, lane in enumerate(lanes or []):
        if not lane:
            continue
        m, b = lane
        ok, p0, p1 = cv2.clipLine((0, top, W, bottom - top),
                                  (0, int(b + top)), (W - 1, int(m * (W - 1) + b + top)))
        if ok:
            cv2.line(img, p0, p1, colors[i % 3], 2, cv2.LINE_AA)


def label(img, text, y, color=(255, 255, 255)):
    cv2.putText(img, text, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('session', nargs='?')
    ap.add_argument('--from', dest='lo', type=int)
    ap.add_argument('--to', dest='hi', type=int)
    ap.add_argument('--n', type=int, default=12)
    a = ap.parse_args()
    sessions = sorted(ROOT.glob('session_*'))
    if not sessions:
        raise SystemExit('Keine Session gefunden')
    path = ROOT / a.session if a.session else sessions[-1]
    with (path / 'frames.csv').open(newline='', encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f)
                if (a.lo is None or int(r['frame_id']) >= a.lo) and (a.hi is None or int(r['frame_id']) <= a.hi)]
    if not rows:
        raise SystemExit('Keine Frames im Bereich')
    step = max(1, len(rows) // a.n)
    picked = rows[::step][:a.n]
    tiles = []
    for r in picked:
        raw = cv2.imread(str(path / r['image_filename']))
        if raw is None:
            continue
        raw = cv2.resize(raw, (W, H))
        top, bottom = int(r.get('roi_top_px') or 0), int(r.get('roi_bottom_px') or H)
        lanes = json.loads(r.get('lanes_json') or '[]')
        a_img = raw.copy()
        cv2.rectangle(a_img, (0, top), (W - 1, bottom - 1), (255, 200, 0), 1)
        draw_lanes(a_img, lanes, top, bottom)
        try:
            b_img = mask_for(raw, json.loads(r['processing_parameters_json']), top, bottom)
        except (KeyError, ValueError, TypeError, cv2.error):
            b_img = np.zeros_like(raw)
        draw_lanes(b_img, lanes, top, bottom)
        label(a_img, f"#{r['frame_id']}  {r['data_status']}", 18)
        n = sum(1 for l in lanes if l)
        label(a_img, f"Linien {n}  Stell {r.get('steering_angle_car') or '-'}", H - 10,
              (0, 255, 0) if n == 2 else (0, 200, 255))
        tiles.append(np.hstack([a_img, b_img]))
    cols = 2
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    out = Path.home() / f'c2c-sheet-{path.name}-{picked[0]["frame_id"]}-{picked[-1]["frame_id"]}.png'
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    print(f'{out}  ({len(picked)} Frames, {out.stat().st_size // 1024} KiB)')


if __name__ == '__main__':
    main()
