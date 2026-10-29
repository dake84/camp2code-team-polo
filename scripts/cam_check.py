#!/usr/bin/env python3
"""Kamera-Pruefung auf dem Pi (Paket H3): Sensor-Modi und Dual-Stream, OHNE zu fahren.

Aufruf (Dashboard vorher beenden, sonst ist die Kamera belegt):
    python scripts/cam_check.py                       # 1280x960 gross + 320x240 klein
    python scripts/cam_check.py --main 640x480 --small 320x240 --frames 90
    python scripts/cam_check.py --main 1296x972 --fps 30 --save

Was geprueft wird (nur Kamera, keine Motoren):
    1. Kameras und Sensor-Modi, wie Picamera2 sie meldet (nichts ist festgeschrieben).
    2. Je Testgroesse: Nimmt die Kamera main (RGB888) + lores (YUV420) an? Tatsaechliche Groessen, Zeilen-Stride.
    3. Eine Aufnahme: Stammen beide Bilder aus derselben Szene (Abweichung lores gegen verkleinertes main)?
       Kamera-Metadaten (Belichtungszeit, Verstaerkung, Bilddauer, Lux).
    4. Gemessene Bildrate und Zeit je Bild: Dual-Stream gegen Software-Verkleinern (Messung dieses Laufs, nicht
       uebertragbar auf Fahren mit laufendem Dashboard).
Am Ende steht ein KOPIERBLOCK; bitte die komplette Ausgabe zurueckgeben.
"""
import argparse
import os
import statistics
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from basisklassen_cam import Camera  # noqa: E402

SUMMARY = []


def size_arg(text):
    width, height = text.lower().split('x')
    return int(width), int(height)


def note(line):
    print(line)
    SUMMARY.append(line)


def small_for(main, width=320):
    """Kleine Groesse mit dem Seitenverhaeltnis des grossen Bildes (gerade Seiten)."""
    height = int(round(width * main[1] / main[0] / 2)) * 2
    return width, height


def header(title):
    print()
    print('=' * 78)
    print(title)
    print('=' * 78)


def list_cameras():
    header('1. Kameras und Sensor-Modi (Picamera2)')
    try:
        from picamera2 import Picamera2
    except ImportError as exc:
        note(f'Picamera2 nicht importierbar: {exc}')
        return
    infos = Picamera2.global_camera_info()
    note(f'Kameras: {len(infos)}')
    for index, info in enumerate(infos):
        note(f'  [{index}] Modell {info.get("Model")} | Id {info.get("Id")}')
    if not infos:
        return
    camera = Picamera2(0)
    try:
        for mode in camera.sensor_modes:
            note(f'  Modus {tuple(mode.get("size", ()))}  {mode.get("fps")} fps  {mode.get("bit_depth")} bit  '
                 f'{mode.get("format")}  crop_limits {tuple(mode.get("crop_limits", ()))}')
    finally:
        camera.close()


def try_config(main, small):
    """Dual-Stream fuer eine Groesse konfigurieren (ohne Frames): akzeptiert, abgelehnt, tatsaechliche Groessen."""
    try:
        with Camera(width=main[0], height=main[1], small_size=small, small_mode='camera') as camera:
            info = camera.get_stream_info()
            note(f'  main {main[0]}x{main[1]} + small {small[0]}x{small[1]}: Herkunft {info["small_source"]}, '
                 f'main erhalten {info["main_size"]}, small erhalten {info["small_size"]}, '
                 f'lores-Stride {info["lores_stride"]}' + (f', Hinweis: {info["dual_note"]}' if info['dual_note'] else ''))
            return info
    except Exception as exc:
        note(f'  main {main[0]}x{main[1]} + small {small[0]}x{small[1]}: FEHLER {type(exc).__name__}: {exc}')
        return None


def check_capture(main, small, frames, fps, save):
    header(f'3. Aufnahme {main[0]}x{main[1]} + {small[0]}x{small[1]} (Dual-Stream)')
    with Camera(width=main[0], height=main[1], small_size=small, small_mode='camera', frame_rate=fps or None) as camera:
        info = camera.get_stream_info()
        note(f'  Herkunft des kleinen Bildes: {info["small_source"]} (Soll: camera); Kamera-Bildrate eingestellt: '
             f'{info["frame_rate_set"] or "Automatik"}')
        time.sleep(1.0)                                    # Belichtung einschwingen lassen
        image, small_image = camera.get_frames()
        meta = camera.get_metadata()
        note(f'  Bilder: gross {image.shape}, klein {small_image.shape}')
        reference = cv2.resize(image, (small_image.shape[1], small_image.shape[0]), interpolation=cv2.INTER_AREA)
        difference = float(np.abs(reference.astype(int) - small_image.astype(int)).mean())
        note(f'  Mittlere Abweichung klein (Kamera) gegen gross (verkleinert): {difference:.1f} von 255 '
             '(klein = gleicher Bildausschnitt; gross = anderer Ausschnitt oder Farbe)')
        note('  Metadaten: ' + ', '.join(f'{key} {value}' for key, value in meta.items()))
        if save:
            cv2.imwrite('cam_check_gross.jpg', image)
            cv2.imwrite('cam_check_klein.jpg', small_image)
            cv2.imwrite('cam_check_vergleich.jpg', np.hstack([reference, small_image]))
            note('  Bilder: cam_check_gross.jpg, cam_check_klein.jpg, cam_check_vergleich.jpg')
    return info


def measure(main, small, mode, frames, fps):
    """Zeit je Bild und gemessene Rate fuer small_mode 'camera' oder 'software'."""
    with Camera(width=main[0], height=main[1], small_size=small, small_mode=mode, frame_rate=fps or None) as camera:
        time.sleep(1.0)
        camera.get_frames()                                # Aufwaermen
        times, stamps, durations, exposures, gains, luxes = [], [], [], [], [], []
        for _ in range(frames):
            start = time.perf_counter()
            camera.get_frames()
            times.append((time.perf_counter() - start) * 1000)
            meta = camera.get_metadata()
            stamps.append(meta.get('sensor_timestamp_ns'))
            for values, key in ((durations, 'frame_duration_us'), (exposures, 'exposure_us'), (gains, 'gain'),
                                (luxes, 'lux')):
                if meta.get(key) is not None:
                    values.append(meta[key])
        source = camera.small_source
    stamp_gaps = [b - a for a, b in zip(stamps, stamps[1:]) if a is not None and b is not None and b > a]
    measured = 1e9 / statistics.median(stamp_gaps) if stamp_gaps else None
    total = sum(times) / 1000
    note(f'  {mode:8s} (Herkunft {source}): {statistics.mean(times):6.1f} ms je Bild (Median {statistics.median(times):.1f}, '
         f'max {max(times):.1f}) | Rate aus Zeitstempeln {measured:.1f} Hz' if measured else
         f'  {mode:8s} (Herkunft {source}): {statistics.mean(times):6.1f} ms je Bild | Rate: {frames / total:.1f} Hz (Wanduhr)')
    if durations:
        note(f'           Bilddauer (Kamera) Median {statistics.median(durations):.0f} us = '
             f'{1e6 / statistics.median(durations):.1f} fps | Belichtung Median {statistics.median(exposures):.0f} us '
             f'| Verstaerkung Median {statistics.median(gains):.2f}'
             + (f' | Lux Median {statistics.median(luxes):.0f}' if luxes else ''))
    return measured


def main():
    parser = argparse.ArgumentParser(description='Kamera- und Dual-Stream-Pruefung (ohne Fahren)')
    parser.add_argument('--main', type=size_arg, default=(1280, 960), help='Groesse des grossen Bildes, z. B. 1280x960')
    parser.add_argument('--small', type=size_arg, default=None, help='Groesse des kleinen Bildes (Standard 320 breit)')
    parser.add_argument('--frames', type=int, default=60, help='Bilder fuer die Zeitmessung (Standard 60)')
    parser.add_argument('--fps', type=float, default=0.0, help='feste Kamera-Bildrate (0 = Automatik)')
    parser.add_argument('--save', action='store_true', help='Bilder als JPEG speichern')
    parser.add_argument('--sizes', default='640x480,1296x972,1920x1080',
                        help='weitere grosse Bilder, die nur auf Annahme geprueft werden (Komma-getrennt)')
    args = parser.parse_args()
    main_size = args.main
    small_size = args.small or small_for(main_size)
    print('Camp2Code cam_check (Paket H3) - nur Kamera, keine Motoren. Dashboard vorher beenden!')
    list_cameras()
    header('2. Dual-Stream-Konfiguration je Groesse (ohne Bilder)')
    for text in [f'{main_size[0]}x{main_size[1]}'] + [s for s in args.sizes.split(',') if s]:
        size = size_arg(text)
        try_config(size, small_for(size) if size != main_size or args.small is None else small_size)
    check_capture(main_size, small_size, args.frames, args.fps, args.save)
    header(f'4. Zeitmessung ({args.frames} Bilder, Kamera-Bildrate {args.fps or "Automatik"})')
    note('  Hinweis: gemessen wird dieser Lauf ohne Dashboard und ohne Fahren (nur Aufnahme + Umwandlung/Verkleinern).')
    for mode in ('camera', 'software'):
        try:
            measure(main_size, small_size, mode, args.frames, args.fps)
        except Exception as exc:
            note(f'  {mode}: FEHLER {type(exc).__name__}: {exc}')
    header('KOPIERBLOCK (bitte zurueckgeben)')
    for line in SUMMARY:
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
