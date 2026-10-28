"""Hilfetexte fuer die Info-Buttons (ⓘ) im Dashboard (Paket G).

Ein Text je Schluessel: Titel, Herkunft (gemessen/berechnet/Befehlswert/Einstellung) und genau EIN
Satz (PAKET-H1): was der Wert bedeutet, Einheit, wichtigste Einflussfaktoren.
Neue Texte nur hier ergaenzen; ``info(key, dbc, html)`` erzeugt Button und Popover.
"""

# Herkunft: gemessen | berechnet | Befehlswert | Einstellung
ORIGINS = ('gemessen', 'berechnet', 'Befehlswert', 'Einstellung')

# PAKET-H1: Jeder Text ist genau EIN Satz (was der Wert bedeutet, Einheit, wichtigste Einflussfaktoren).
HELP = {
    'stell': ('Stellwert (Fahrzeug)', 'Befehlswert',
              'Lenkwinkel in Grad (45–135, 90 = geradeaus), der zuletzt an den Servo gesendet wurde und von '
              'Rohwinkel, steering_gain und der Begrenzung abhängt, aber keine gemessene Radstellung ist.'),
    'speed': ('Speed-Steuerwert', 'Befehlswert',
              'PWM-Wert der Motoren (Betrag 0–100, rückwärts negativ), je nach Einstellung fest 30 oder adaptiv '
              'nach Lenkwinkel, aber keine gemessene Geschwindigkeit.'),
    'soll': ('Sollwinkel (Regler)', 'Befehlswert',
             'Lenkwinkel in Grad, den der Regler anfordert (90 + (Rohwinkel − 90) · steering_gain, begrenzt auf '
             '45–135) und der bei Autofahrt praktisch dem Stellwert entspricht.'),
    'roh': ('Rohwinkel (Linien)', 'berechnet',
            'Richtung in Grad (90 = geradeaus) von der Bildmitte zur Mitte der beiden erkannten Linien an der '
            'ROI-Oberkante, abhängig von Linienerkennung und ROI, wobei der Regler ohne zwei Linien den Vorwert hält.'),
    'diag': ('Controllerdiagnose', 'berechnet',
             'Letzte Entscheidung des CameraMode mit Linienanzahl, Rohwinkel, gehaltenem Vorwert, Soll- und '
             'Stellwert und Stoppursache, abhängig von der Linienerkennung des jeweiligen Frames.'),
    'hud': ('HUD-Darstellung', 'Einstellung',
            'Blendet Frame, Steuer-Speed, Roh-, Stell- und gegebenenfalls Sollwinkel sowie den Fahrzustand '
            '(GESTOPPT, BERGUNG, MANUELL, AUTONOM, STEHT) ins Bild ein, ohne das Rohbild zu verändern.'),
    'rate': ('Regelrate', 'gemessen',
             'Fahrentscheidungen pro Sekunde in Hz, nach oben begrenzt durch Kamerarate und Bildverarbeitung '
             '(grün ab 80 %, gelb ab 50 % des Solls, sonst rot).'),
    'lat': ('Reaktionszeit', 'gemessen',
            'Zeit in Millisekunden von der Bildaufnahme bis zum Fahrbefehl, die mit der Bildverarbeitung, der '
            'Wartezeit bis zur nächsten Regler-Abtastung (100 Hz = bis 10 ms) und der CPU-Last steigt.'),
    'qual': ('Spurqualität', 'berechnet',
             'Anteil der Frames der letzten rund 4 Sekunden mit zwei plausiblen Linien (links vor rechts, '
             'Spurbreite 25–98 % der Bildbreite), abhängig von Beleuchtung, HSV-Grenzen und ROI.'),
    'freq': ('Soll / Ist Frequenz', 'gemessen',
             'Kamerabilder und Fahrentscheidungen pro Sekunde in Hz im Vergleich zum Soll '
             '(cam_sensor.scan_frequency), wobei Ist = 0 gilt, wenn seit 2 Sekunden kein Wert kam.'),
    'freqhist': ('Frequenzverlauf', 'gemessen',
                 'Kamera- und Entscheidungsfrequenz in Hz über die letzten 60 Sekunden, abhängig von '
                 'Bildverarbeitungszeit und CPU-Last und ohne Fahrt bei 0.'),
    'drivehist': ('Lenkwinkel / Speed / Linien', 'gemessen',
                  'Rohwinkel und Stellwert in Grad, Speed-Steuerwert und Linienanzahl je Fahrentscheidung der '
                  'letzten 60 Sekunden, abhängig von Linienerkennung und Regler.'),
    'dataage': ('Datenalter, Latenzen, System', 'gemessen',
                'Alter des letzten Kamerabilds, Dauer der Bildverarbeitung, Reaktionszeit, CPU je Thread und '
                'Renderzeiten des Dashboards in Millisekunden beziehungsweise Prozent, abhängig von der Systemlast.'),
    'crop': ('ROI', 'Einstellung',
             'Bildausschnitt als Anteil der Bildhöhe von unten (0) bis oben (1), der allein ausgewertet wird '
             'und das Fahrzeug im unteren Bildrand ausschließen sollte.'),
    'hue': ('Hue', 'Einstellung',
            'Farbton-Bereich 0–179 (OpenCV) der Linienmaske, wobei blaues Klebeband typisch bei 100–130 liegt.'),
    'sat': ('Saturation', 'Einstellung',
            'Sättigungs-Bereich 0–255 der Linienmaske, bei dem ein zu hohes Minimum Linien im Schatten verliert.'),
    'val': ('Value', 'Einstellung',
            'Helligkeits-Bereich 0–255 der Linienmaske, dessen Minimum sehr dunkle Bereiche ausfiltert.'),
    'blur': ('Blur-Kernel', 'Einstellung',
             'Kantenlänge des Weichzeichners in Pixeln, der Maskenränder glättet und Rauschen unterdrückt, '
             'wobei hohe Werte schmale Linien unter die Binärschwelle von 50 % drücken können.'),
    'blurgauss': ('Blur-Kernel (Gauß)', 'Einstellung',
                  'Kantenlänge des Gauß-Weichzeichners in Pixeln (gerade Werte werden um 1 erhöht), der das '
                  'Bildrauschen vor dem Blackhat-Filter dämpft, bei hohen Werten aber schmale Linien verwischt.'),
    'morph': ('Morph-Kernel', 'Einstellung',
              'Kantenlänge des Schließens (Dilatation und Erosion) in Pixeln, die kleine Lücken in der Maske '
              'schließt, aber bei hohen Werten Linien verbreitert.'),
    'canny': ('Canny-Schwellen', 'Einstellung',
              'Untere und obere Schwelle (0–255) der Kantenerkennung, die bei aktivem Schritt statt der '
              'Flächen Kanten an die Linienerkennung gibt.'),
    'speedmode': ('Geschwindigkeitsmodus', 'Einstellung',
                  'Fest fährt immer mit demselben Steuerwert, adaptiv mit v_max auf Geraden und linear bis '
                  'v_min bei vollem Einschlag oder gehaltenem Vorwert, nur im Stillstand änderbar und dauerhaft '
                  'in config.json gespeichert.'),
    'speedfixed': ('Fester Speed-Steuerwert', 'Einstellung',
                   'PWM-Steuerwert zwischen 30 und 60 für den Modus fest, wobei das Auto unter 30 nicht '
                   'anfährt.'),
    'speedvmin': ('v_min (Kurve)', 'Einstellung',
                  'Speed-Steuerwert (mindestens 30) im Modus adaptiv bei vollem Lenkeinschlag oder fehlender '
                  'Linie, höchstens so groß wie v_max.'),
    'speedvmax': ('v_max (Gerade)', 'Einstellung',
                  'Speed-Steuerwert (höchstens 60) im Modus adaptiv bei Geradeausfahrt, mindestens so groß wie '
                  'v_min.'),
    'recq': ('JPEG-Qualität der Aufnahme', 'Einstellung',
             'Qualität 50–100 der gespeicherten Rohbilder, bei der höhere Werte schärfere Bilder, größere '
             'Dateien und mehr SD-Schreiblast bedeuten und die ab der nächsten Aufnahme gilt.'),
    'simspeed': ('Wiedergabetempo', 'Einstellung',
                 'Faktor von 0,25 bis 4, mit dem die aufgezeichneten Zeitstempel der Frames abgespielt werden, '
                 'ohne dass neu berechnet werden muss.'),
    'simmode': ('Echtzeit / Jeder Frame', 'Einstellung',
                'Echtzeit hält das Tempo nach den Zeitstempeln und überspringt Anzeigeframes, Jeder Frame '
                'überspringt nichts und läuft langsamer, wenn die Anzeige nicht hinterherkommt.'),
    # PAKET-H1B: Kamera, Regler und Fahrzeug (Settings)
    'camfreq': ('Kamera-Abtastung', 'Einstellung',
                'Soll-Bildrate der Kameraschleife in Hertz, die nur erreicht wird, wenn Aufnahme und Bildverarbeitung '
                'schnell genug sind, und die unter 5 Hz die Kamera-Überwachung des Reglers auslösen kann.'),
    'ctlfreq': ('Regler-Abtastung', 'Einstellung',
                'Wie oft der CameraMode pro Sekunde nach einem neuen Kamerabild schaut, wobei nur neue Bilder '
                'ausgewertet werden und die Entscheidungsrate daher nie über der Kamerarate liegt.'),
    'losttimeout': ('Linien-Timeout', 'Einstellung',
                    'Sekunden ohne zwei erkannte Linien oder ohne neues Kamerabild, nach denen der Regler stoppt '
                    '(LANE_TIMEOUT beziehungsweise CAMERA_STALE), wobei kleine Werte früher abbrechen.'),
    'steergain': ('Lenkverstärkung', 'Einstellung',
                  'Faktor, mit dem die Abweichung des Rohwinkels von 90 Grad in den Sollwinkel übersetzt wird '
                  '(Standard 2,0), wobei größere Werte stärker lenken und der Stellwert auf 45 bis 135 Grad begrenzt bleibt.'),
    'vmin': ('Minimaler Steuerwert', 'Einstellung',
             'Untergrenze des PWM-Steuerwerts für jede Fahrt (Standard 20), auf die kleinere Werte angehoben werden, '
             'wobei das Auto laut Erfahrung erst ab etwa 30 anfährt.'),
    'vmax': ('Maximaler Steuerwert', 'Einstellung',
             'Obergrenze des PWM-Steuerwerts für jede Fahrt (Standard 100), die auch die adaptive Geschwindigkeit '
             'und die Bergung begrenzt und nicht kleiner als der minimale Steuerwert sein darf.'),
    # PAKET-H2: Auslastung, Speicherplatz, Telemetrie-Diagramm, Ereignisprotokoll
    'cpu': ('Auslastung (CPU)', 'gemessen',
            'CPU-Last des Pi in Prozent als Mittel über alle vier Kerne, die bis 60 Prozent grün, bis 85 Prozent gelb '
            'und darüber oder ab 80 Grad Celsius rot angezeigt wird und mit Bildverarbeitung, Aufnahme und '
            'Dashboard steigt.'),
    'ram': ('Arbeitsspeicher', 'gemessen',
            'Belegter Anteil des Arbeitsspeichers in Prozent, der mit Ringbuffer, Aufnahme und offenen '
            'Browser-Tabs steigt.'),
    'temp': ('Temperatur', 'gemessen',
             'CPU-Temperatur in Grad Celsius, ab etwa 80 Grad taktet der Pi 4 herunter, weshalb ab diesem Wert '
             'die Anzeige immer rot ist.'),
    'disk': ('Freier Speicher', 'gemessen',
             'Freier Platz der SD-Karte unter Data_AI in Gigabyte und Prozent, der nur etwa alle 30 Sekunden '
             'neu bestimmt wird.'),
    'diskest': ('Aufnahmedauer', 'berechnet',
                'Geschätzte Minuten Aufnahme, die auf den freien Speicher passen, berechnet aus der mittleren '
                'JPEG-Größe der neuesten Session und der Kamera-fps, ohne vorhandene Session nicht bestimmbar.'),
    'decisions': ('Entscheidungen', 'gemessen',
                  'Wie oft der Regler pro Sekunde einen neuen Kameraframe auswertet, höchstens so hoch wie die '
                  'Kamerarate.'),
    'telechart': ('Telemetrie-Diagramm', 'gemessen',
                  'Vier Spuren (Frequenzen, Latenzen, Fahren, System) mit gemeinsamer Zeitachse der letzten '
                  '60 Sekunden, in denen im Stillstand Lücken entstehen, weil nichts fortgeschrieben wird.'),
    'eventlog': ('Ereignisprotokoll', 'gemessen',
                 'Zustandsänderungen und Meldungen des Fahrzeugs mit dem Frame, zu dem sie gehören, wobei der '
                 'Filter Wichtig die häufigen Linien- und Lenkereignisse ausblendet.'),
    'simrate': ('Anzeigerate', 'gemessen',
                'Vom Server ausgelieferte Anzeigeframes pro Sekunde, begrenzt durch den Anzeigetakt von 150 ms '
                '(höchstens etwa 6 bis 7 pro Sekunde) sowie Bildkodierung und Übertragung, ohne Messung der '
                'Darstellung im Browser.'),
}


def info(key, dbc, html, suffix=None):
    """ⓘ-Symbol mit Klick-Popover. Liefert eine Liste (in children einsetzen).

    Bei Mehrfachverwendung desselben Schluessels in einem Layout ``suffix`` angeben (eindeutige IDs).
    """
    title, origin, text = HELP[key]
    target = f'info-{key}' + (f'-{suffix}' if suffix else '')
    return [html.Span('ⓘ', id=target, className='c2c-info', title=title, role='button', tabIndex='0'),
            dbc.Popover([dbc.PopoverHeader(f'{title} · {origin}'), dbc.PopoverBody(text)],
                        target=target, trigger='legacy', placement='bottom')]


def label(text, key, dbc, html, className='c2c-kpi-label', suffix=None):
    """Beschriftung mit angehaengtem ⓘ."""
    return html.Div([text, ' ', *info(key, dbc, html, suffix)], className=className)

def tip(key):
    """PAKET-H2: Kurztext fuer ``title``-Attribute (Tooltip ohne Popover): ``Titel (Herkunft): Satz``."""
    title, origin, text = HELP[key]
    return f'{title} ({origin}): {text}'
