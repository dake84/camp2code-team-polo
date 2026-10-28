#!/usr/bin/env python3
"""Test: Picamera2 mit zwei Streams (main + lores) - Vorstufe fuer Paket H3.

Prueft auf dem Pi, ohne das Projekt zu aendern:
  1. Lassen sich main (gross, RGB888) und lores (klein, YUV420) gleichzeitig konfigurieren?
  2. Kommen beide Bilder aus DERSELBEN Aufnahme (gleicher Bildausschnitt)?
  3. Was kostet ein Frame: Dual-Stream vs. nur main + Software-Verkleinern?

Aufruf (Dashboard vorher beenden, sonst ist die Kamera belegt):
    python dual_stream_test.py                       # 1280x960 + 320x240
    python dual_stream_test.py 640x480 320x240
Ergebnis: dual_main.jpg, dual_lores.jpg, dual_vergleich.jpg im aktuellen Ordner.
Keine Motoren, nur Kamera.
"""
import sys
import time

import cv2
import numpy as np
from picamera2 import Picamera2


def size(text):
    width, height = text.lower().split('x')
    return int(width), int(height)


def lores_to_bgr(yuv, width, height):
    """YUV420 (I420, Zeilen evtl. mit Stride aufgefuellt) -> BGR, auf width x height zugeschnitten."""
    bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
    return bgr[:height, :width]


def timed(function, count=60):
    function()                                    # Aufwaermen
    start = time.perf_counter()
    for _ in range(count):
        function()
    return (time.perf_counter() - start) / count * 1000


main_size = size(sys.argv[1]) if len(sys.argv) > 1 else (1280, 960)
lores_size = size(sys.argv[2]) if len(sys.argv) > 2 else (320, 240)
flip = True                                       # wie basisklassen_cam (180 Grad)

cam = Picamera2(0)
try:
    config = cam.create_preview_configuration(
        main={'format': 'RGB888', 'size': main_size},
        lores={'format': 'YUV420', 'size': lores_size},   # Pi 4: lores muss YUV420 sein
        buffer_count=2, queue=False)
    cam.configure(config)
    actual = cam.camera_configuration()
    real_main = tuple(actual['main']['size'])
    real_lores = tuple(actual['lores']['size'])
    print('angefordert: main', main_size, 'lores', lores_size)
    print('erhalten:    main', real_main, 'lores', real_lores,
          '| Sensor-Modus', actual.get('raw', {}).get('size'))
    cam.start()
    time.sleep(1.0)                               # Belichtung einschwingen lassen

    # --- 1+2: beide Bilder aus derselben Anfrage
    (main, yuv), meta = cam.capture_arrays(['main', 'lores'])
    lores = lores_to_bgr(yuv, *real_lores)
    if flip:
        main, lores = cv2.flip(main, -1), cv2.flip(lores, -1)
    print('Arrays:      main', main.shape, main.dtype, '| lores roh', yuv.shape, '-> BGR', lores.shape)
    print('Zeitstempel:', meta.get('SensorTimestamp'))

    # Gleicher Bildausschnitt? main per Software verkleinern und mit lores vergleichen.
    soft = cv2.resize(main, real_lores, interpolation=cv2.INTER_AREA)
    diff = cv2.absdiff(soft, lores).mean()
    print(f'Mittlere Abweichung main(verkleinert) vs. lores: {diff:.1f} von 255 '
          f'(klein, ca. < 15 = gleicher Ausschnitt; gross = anderer Ausschnitt/Farbe)')
    cv2.imwrite('dual_main.jpg', main)
    cv2.imwrite('dual_lores.jpg', lores)
    label = np.hstack([soft, lores])
    cv2.putText(label, 'main verkleinert', (5, 18), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
    cv2.putText(label, 'lores (Kamera)', (real_lores[0] + 5, 18), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
    cv2.imwrite('dual_vergleich.jpg', label)

    # --- 3: Kosten je Frame (inklusive Warten auf die Kamera)
    def dual():
        (m, y), _ = cam.capture_arrays(['main', 'lores'])
        lores_to_bgr(y, *real_lores)

    def main_only():
        m = cam.capture_array('main')
        cv2.resize(m, real_lores, interpolation=cv2.INTER_AREA)

    def convert_only():                           # reine CPU-Kosten ohne Kamera-Wartezeit
        lores_to_bgr(yuv, *real_lores)

    def resize_only():
        cv2.resize(main, real_lores, interpolation=cv2.INTER_AREA)

    print(f'Dual-Stream je Frame:        {timed(dual):6.1f} ms (inkl. Warten auf Kamera)')
    print(f'Nur main + verkleinern:      {timed(main_only):6.1f} ms (inkl. Warten auf Kamera)')
    print(f'CPU nur lores->BGR:          {timed(convert_only, 300):6.2f} ms')
    print(f'CPU nur Software-Verkleinern:{timed(resize_only, 300):6.2f} ms')
    print('Bilder: dual_main.jpg, dual_lores.jpg, dual_vergleich.jpg')
finally:
    try:
        cam.stop()
    finally:
        cam.close()
