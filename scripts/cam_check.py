#!/usr/bin/env python3
"""Kamera-Pruefung auf dem Pi (Paket H3, Fassung 2): Sensor-Modi und Dual-Stream, OHNE zu fahren.

Aufruf (Dashboard vorher beenden, sonst ist die Kamera belegt):
    python scripts/cam_check.py                       # 1280x960 gross + 320x240 klein
    python scripts/cam_check.py --main 1296x972 --small 320x240 --save
    python scripts/cam_check.py --main 1296x972 --fps 30

Was geprueft wird (nur Kamera, keine Motoren):
    1. Kameras und Sensor-Modi, wie Picamera2 sie meldet (nichts ist festgeschrieben).
    2. Je Testgroesse: Nimmt die Kamera main (RGB888) + lores (YUV420) an? Tatsaechliche Groessen, Zeilen-Stride.
    3. Eine Aufnahme: Abweichung lores gegen verkleinertes main; Kamera-Metadaten (Belichtungszeit, Verstaerkung,
       Bilddauer, Lux). Mit --save entstehen Bilder zur Sichtpruefung (cam_check_vergleich.jpg).
    4. Zeitmessung je Modus (camera/software): Wanduhr, CPU-Zeit und Rate aus den Zeitstempeln.
       ACHTUNG: Die Wanduhr-Zeit je Bild wird von der Kamera-Bildrate bestimmt (die Schleife wartet auf das naechste
       Bild) und taugt NICHT fuer den Vergleich der Modi; massgeblich ist die CPU-Zeit.
    5. Mikro-Benchmark der beiden Rechenwege auf diesem Pi: Software-Verkleinern (INTER_AREA) gegen
       YUV420->BGR-Umwandlung. Die Skalierung im Kamera-ISP selbst ist darin nicht enthalten.
Alle Werte gelten fuer diesen Lauf (ohne Dashboard, ohne Fahren), nicht fuer den Fahrbetrieb.
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


def check_capture(main, small, fps, save):
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
        note(f'  Mittlere Abweichung lores (Kamera) gegen main (per Software verkleinert): {difference:.1f} von 255 '
             '(kleiner Wert = aehnliches Bild; ob der Bildausschnitt gleich ist, zeigt nur cam_check_vergleich.jpg '
             'mit --save)')
        note('  Metadaten: ' + ', '.join(f'{key} {value}' for key, value in meta.items()))
        if save:
            cv2.imwrite('cam_check_gross.jpg', image)
            cv2.imwrite('cam_check_klein.jpg', small_image)
            cv2.imwrite('cam_check_vergleich.jpg', np.hstack([reference, small_image]))
            note('  Bilder: cam_check_gross.jpg, cam_check_klein.jpg, cam_check_vergleich.jpg '
                 '(Vergleich: links main verkleinert, rechts lores)')
    return info


def measure(main, small, mode, frames, fps):
    """Wanduhr-Zeit, CPU-Zeit (alle Threads des Prozesses) und Rate fuer small_mode 'camera' oder 'software'."""
    with Camera(width=main[0], height=main[1], small_size=small, small_mode=mode, frame_rate=fps or None) as camera:
        time.sleep(1.0)
        camera.get_frames()                                # Aufwaermen
        wall, cpu, stamps, durations, exposures, gains, luxes = [], [], [], [], [], [], []
        for _ in range(frames):
            start, cpu_start = time.perf_counter(), time.process_time()
            camera.get_frames()
            wall.append((time.perf_counter() - start) * 1000)
            cpu.append((time.process_time() - cpu_start) * 1000)
            meta = camera.get_metadata()
            stamps.append(meta.get('sensor_timestamp_ns'))
            for values, key in ((durations, 'frame_duration_us'), (exposures, 'exposure_us'), (gains, 'gain'),
                                (luxes, 'lux')):
                if meta.get(key) is not None:
                    values.append(meta[key])
        source = camera.small_source
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if a is not None and b is not None and b > a]
    rate = (f'{1e9 / statistics.median(gaps):.1f} Hz (Zeitstempel)' if gaps
            else f'{frames / (sum(wall) / 1000):.1f} Hz (Wanduhr)')
    note(f'  {mode:8s} (Herkunft {source}): Wanduhr {statistics.median(wall):6.1f} ms (Median, max {max(wall):.1f}) | '
         f'CPU {statistics.median(cpu):5.1f} ms (Median, max {max(cpu):.1f}) | Rate {rate}')
    if durations:
        note(f'           Bilddauer (Kamera) Median {statistics.median(durations):.0f} us = '
             f'{1e6 / statistics.median(durations):.1f} fps | Belichtung Median {statistics.median(exposures):.0f} us '
             f'| Verstaerkung Median {statistics.median(gains):.2f}'
             + (f' | Lux Median {statistics.median(luxes):.0f}' if luxes else ''))
    return source


def bench(function, repeat):
    function()                                             # Aufwaermen
    start = time.perf_counter()
    for _ in range(repeat):
        function()
    return (time.perf_counter() - start) * 1000 / repeat


def micro_benchmark(main, small, repeat=100):
    """Rechenzeit der beiden Wege zum kleinen Bild auf diesem Pi (ohne Kamera-ISP, ohne Drehen und Farbraum)."""
    from basisklassen_cam import yuv420_to_bgr
    with Camera(width=main[0], height=main[1]) as camera:
        time.sleep(0.5)
        image = camera.get_frame()
    reference = cv2.resize(image, small, interpolation=cv2.INTER_AREA)
    yuv = cv2.cvtColor(reference, cv2.COLOR_BGR2YUV_I420)
    software = bench(lambda: cv2.resize(image, small, interpolation=cv2.INTER_AREA), repeat)
    convert = bench(lambda: yuv420_to_bgr(yuv, small[0], small[1]), repeat)
    note(f'  Mikro-Benchmark ({repeat}x, {main[0]}x{main[1]} -> {small[0]}x{small[1]}): '
         f'Software-Verkleinern (INTER_AREA) {software:.2f} ms | YUV420->BGR (lores) {convert:.2f} ms '
         '(ohne Skalierung im Kamera-ISP)')


def main():
    parser = argparse.ArgumentParser(description='Kamera- und Dual-Stream-Pruefung (ohne Fahren)')
    parser.add_argument('--main', type=size_arg, default=(1280, 960), help='Groesse des grossen Bildes, z. B. 1296x972')
    parser.add_argument('--small', type=size_arg, default=None, help='Groesse des kleinen Bildes (Standard 320 breit)')
    parser.add_argument('--frames', type=int, default=60, help='Bilder fuer die Zeitmessung (Standard 60)')
    parser.add_argument('--fps', type=float, default=0.0, help='feste Kamera-Bildrate (0 = Automatik)')
    parser.add_argument('--save', action='store_true', help='Bilder als JPEG speichern')
    parser.add_argument('--sizes', default='640x480,1296x972,1920x1080',
                        help='weitere grosse Bilder, die nur auf Annahme geprueft werden (Komma-getrennt)')
    args = parser.parse_args()
    main_size = args.main
    small_size = args.small or small_for(main_size)
    print('Camp2Code cam_check (Paket H3, Fassung 2) - nur Kamera, keine Motoren. Dashboard vorher beenden!')
    list_cameras()
    header('2. Dual-Stream-Konfiguration je Groesse (ohne Bilder)')
    sizes = [main_size] + [s for s in (size_arg(x) for x in args.sizes.split(',') if x) if s != main_size]
    for size in sizes:
        try_config(size, small_size if size == main_size else small_for(size))
    check_capture(main_size, small_size, args.fps, args.save)
    header(f'4. Zeitmessung ({args.frames} Bilder, Kamera-Bildrate {args.fps or "Automatik"})')
    note('  Hinweis: Dieser Lauf ohne Dashboard und ohne Fahren. Die Wanduhr-Zeit wird von der Kamera-Bildrate '
         'bestimmt (Warten auf das naechste Bild) und taugt nicht fuer den Vergleich; massgeblich ist die CPU-Zeit.')
    for mode in ('camera', 'software'):
        try:
            measure(main_size, small_size, mode, args.frames, args.fps)
        except Exception as exc:
            note(f'  {mode}: FEHLER {type(exc).__name__}: {exc}')
    header('5. Mikro-Benchmark der Rechenwege')
    try:
        micro_benchmark(main_size, small_size)
    except Exception as exc:
        note(f'  FEHLER {type(exc).__name__}: {exc}')
    header('KOPIERBLOCK (bitte zurueckgeben)')
    for line in SUMMARY:
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
