"""Hilfetexte fuer die Info-Buttons (ⓘ) im Dashboard (Paket G).

Ein Text je Schluessel: Titel, Beschreibung, Herkunft (gemessen/berechnet/Steuerwert/Einstellung).
Neue Texte nur hier ergaenzen; ``info(key, dbc, html)`` erzeugt Button und Popover.
"""

HELP = {
    'stell': ('Stellwert (Fahrzeug)', 'Steuerwert', 'Lenkwinkel, der an den Servo gesendet wurde, nach Begrenzung '
              'auf 45–135°. 90° = geradeaus. Kein Messwert der tatsächlichen Radstellung.'),
    'speed': ('Speed-Steuerwert', 'Steuerwert', 'PWM-Wert für die Motoren (0–100), keine gemessene Geschwindigkeit. '
              'Fest (Standard 30) oder adaptiv, einstellbar unter Settings.'),
    'soll': ('Sollwinkel (Regler)', 'berechnet', 'Rohwinkel × steering_gain um 90° gespiegelt, begrenzt auf 45–135°.'),
    'roh': ('Rohwinkel (Linien)', 'berechnet', 'Richtung zur Mitte der beiden erkannten Linien an der ROI-Oberkante. '
            'Fehlt eine Linie, hält der Regler den Vorwert.'),
    'diag': ('Controllerdiagnose', 'berechnet', 'Letzte Entscheidung des CameraMode: Linien, Rohwinkel, gehaltener '
             'Vorwert, Soll- und Stellwert, Stoppursache.'),
    'hud': ('HUD-Darstellung', 'Anzeige', 'Zeigt das Livebild im Stil des Replay-HUD (Kopfzeile mit Frame, Stell-, '
            'Soll- und Rohwinkel, Speed, Status, Richtungspfeil). Rohbild bleibt unverändert.'),
    'rate': ('Regelrate', 'gemessen', 'Entscheidungen pro Sekunde: wie oft CameraMode einen neuen Kameraframe '
             'auswertet. Höchstens so hoch wie die Kamerarate (Soll = cam_sensor.scan_frequency). '
             'Gelb unter 80 % des Solls, rot unter 50 %.'),
    'lat': ('Reaktionszeit', 'gemessen', 'Zeit von der Bildaufnahme bis zum Fahrbefehl des letzten vollständigen '
            'Frames (Bildverarbeitung + Wartezeit bis zur Regler-Abtastung). Grün unter 80 ms.'),
    'qual': ('Spurqualität', 'berechnet', 'Anteil der Frames im Ringbuffer (ca. 4 s) mit zwei plausiblen Linien: '
             'links < rechts an ROI-Unter- und -Oberkante, Spurbreite 25–98 % der Bildbreite. '
             'Frühindikator für LANE_TIMEOUT. Rot unter 70 %.'),
    'freq': ('Soll / Ist Frequenz', 'gemessen', 'Kamera: veröffentlichte Frames/s. Entscheidungen: ausgewertete '
             'Frames/s. Ist = 0, wenn seit 2 s kein Wert kam.'),
    'freqhist': ('Frequenzverlauf', 'gemessen', 'Verlauf der letzten 60 s. Ohne Fahrt fallen die Entscheidungen '
                 'auf 0 (vor Paket G wurde der letzte Wert fortgeschrieben).'),
    'drivehist': ('Lenkwinkel / Speed / Linien', 'gemessen', 'Rohwinkel, Stellwert, Speed-Steuerwert und '
                  'Linienanzahl je Entscheidung der letzten 60 s.'),
    'dataage': ('Datenalter, Latenzen, System', 'gemessen', 'Alter des letzten Kamerabilds, Dauer der '
                'Bildverarbeitung, Reaktionszeit, CPU je Thread, Renderzeiten des Dashboards.'),
    'crop': ('ROI', 'Einstellung', 'Bildausschnitt als Anteil von unten (0) bis oben (1). Nur dieser Streifen wird '
             'ausgewertet. Unten das Fahrzeug (LED, Platine) ausschließen.'),
    'hue': ('Hue', 'Einstellung', 'Farbton 0–179 (OpenCV). Blaues Klebeband liegt typisch bei 100–130.'),
    'sat': ('Saturation', 'Einstellung', 'Sättigung 0–255. Zu hohes Minimum verliert Linien im Schatten.'),
    'val': ('Value', 'Einstellung', 'Helligkeit 0–255. Minimum filtert sehr dunkle Bereiche.'),
    'blur': ('Blur-Kernel', 'Einstellung', 'Weichzeichner in Pixeln. Glättet Maskenränder; danach Schwelle 50 %.'),
    'morph': ('Morph-Kernel', 'Einstellung', 'Schließen (Dilatation + Erosion) in Pixeln. Schließt kleine Lücken.'),
    'canny': ('Canny-Schwellen', 'Einstellung', 'Kantenerkennung. Bei aktivem Schritt werden Kanten statt Flächen '
              'an die Linienerkennung gegeben.'),
    'speedmode': ('Geschwindigkeitsmodus', 'Einstellung', 'Fest: immer der feste Wert. Adaptiv: v_max auf Geraden, '
                  'linear bis v_min bei vollem Lenkeinschlag; v_min auch bei gehaltenem Vorwert. '
                  'Änderung nur im Stillstand, gilt bis zum Neustart.'),
    'speedfixed': ('Fester Speed-Steuerwert', 'Einstellung', '30–60. Unter 30 fährt das Auto nicht an.'),
    'speedvmin': ('v_min (Kurve)', 'Einstellung', 'Speed-Steuerwert bei vollem Einschlag oder fehlender Linie, '
                  'mindestens 30.'),
    'speedvmax': ('v_max (Gerade)', 'Einstellung', 'Speed-Steuerwert bei Geradeausfahrt, höchstens 60.'),
    'recq': ('JPEG-Qualität der Aufnahme', 'Einstellung', 'Höher = schärfere Bilder, größere Dateien und mehr '
             'SD-Schreiblast. Gilt ab der nächsten Aufnahme. Die Auflösung legt die Kamera fest '
             '(cam_sensor.width/height, Neustart nötig).'),
}


def info(key, dbc, html):
    """ⓘ-Symbol mit Klick-Popover. Liefert eine Liste (in children einsetzen)."""
    title, origin, text = HELP[key]
    target = f'info-{key}'
    return [html.Span('ⓘ', id=target, className='c2c-info', title=title, role='button', tabIndex='0'),
            dbc.Popover([dbc.PopoverHeader(f'{title} · {origin}'), dbc.PopoverBody(text)],
                        target=target, trigger='legacy', placement='bottom')]


def label(text, key, dbc, html, className='c2c-kpi-label'):
    """Beschriftung mit angehaengtem ⓘ."""
    return html.Div([text, ' ', *info(key, dbc, html)], className=className)
