"""Schnellere Wiedergabe (Paket I2): begrenzter Bildcache mit Vorausberechnung.

Problem: Jeder Anzeigetakt der Simulation dekodierte das JPEG zweimal, zeichnete zwei HUDs auf das VOLLE Bild und kodierte
zwei JPEGs - auf dem Raspberry Pi dauert das laenger als der Takt (150 ms), die Wiedergabe wird zur Zeitlupe.

``FrameRenderCache`` haelt die fertigen Anzeigebilder (Data-URIs) in einem kleinen LRU-Cache (Standard 48 Eintraege) und
berechnet waehrend der Wiedergabe die naechsten Frames in wenigen (Standard 2) Hintergrund-Threads voraus. Der Anzeige-Callback holt dann
im besten Fall nur noch einen fertigen Eintrag. Bereits berechnete Frames (Scrubben, zweiter Durchlauf, Pause) kosten
nichts mehr. Der Speicher ist begrenzt (keine unbeschraenkte Bildablage).

Dieses Modul kennt weder Dash noch OpenCV noch die Simulation: ``factory()`` liefert den Wert zu einem Schluessel.
"""
import logging
import threading
from collections import OrderedDict, deque

log = logging.getLogger(__name__)

DEFAULT_CAPACITY = 48
DEFAULT_WORKERS = 2      # OpenCV gibt beim Rechnen die GIL frei: zwei Threads nutzen zwei Kerne des Pi


class FrameRenderCache:
    """LRU-Cache mit Duplikatschutz (derselbe Schluessel wird nie doppelt berechnet) und einem Vorausrechner-Thread."""

    def __init__(self, capacity=DEFAULT_CAPACITY, workers=DEFAULT_WORKERS):
        self.capacity = max(2, int(capacity))
        self.workers = max(1, int(workers))
        self._cond = threading.Condition()
        self._items = OrderedDict()      # Schluessel -> Wert
        self._inflight = {}              # Schluessel -> Event (wird gerade berechnet)
        self._queue = deque()            # (Schluessel, factory) fuer den Hintergrund-Thread
        self._epoch = 0                  # wird mit clear() erhoeht; Ergebnisse alter Epochen werden verworfen
        self._threads = []
        self._closed = False
        self.hits = self.misses = self.prefetched = 0

    # ---- Lesen / Berechnen
    def get(self, key):
        """Wert aus dem Cache oder None (zaehlt nicht als Treffer)."""
        with self._cond:
            return self._items.get(key)

    def render(self, key, factory):
        """Cache-Treffer, sonst ``factory()`` (Fehler werden weitergereicht). Laeuft der Schluessel gerade im
        Hintergrund, wird darauf gewartet statt doppelt gerechnet."""
        for _attempt in range(2):
            with self._cond:
                if key in self._items:
                    self._items.move_to_end(key)
                    self.hits += 1
                    return self._items[key]
                event = self._inflight.get(key)
                owner = event is None
                if owner:
                    event = self._inflight[key] = threading.Event()
                    epoch = self._epoch
            if owner:
                try:
                    value = factory()
                except BaseException:
                    with self._cond:
                        self._inflight.pop(key, None)
                    event.set()
                    raise
                with self._cond:
                    self.misses += 1
                    self._store(key, value, epoch)
                    self._inflight.pop(key, None)
                event.set()
                return value
            event.wait(30)       # Hintergrund-Thread rechnet denselben Frame
        return factory()         # Hintergrund-Thread hat versagt: selbst rechnen (Fehler erreicht den Aufrufer)

    def _store(self, key, value, epoch):
        if epoch != self._epoch:
            return
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    # ---- Vorausberechnung
    def prefetch(self, entries):
        """Ersetzt die Warteschlange durch ``entries`` = [(Schluessel, factory), ...] (veraltete Auftraege entfallen)."""
        with self._cond:
            if self._closed:
                return
            self._queue = deque((key, factory) for key, factory in entries
                                if key not in self._items and key not in self._inflight)
            if self._queue:
                self._threads = [thread for thread in self._threads if thread.is_alive()]
                while len(self._threads) < self.workers:
                    thread = threading.Thread(target=self._work, name=f'sim-frame-prefetch-{len(self._threads)}',
                                              daemon=True)
                    self._threads.append(thread)
                    thread.start()
                self._cond.notify_all()

    def _work(self):
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                key, factory = self._queue.popleft()
                if key in self._items or key in self._inflight:
                    continue
                event = self._inflight[key] = threading.Event()
                epoch = self._epoch
            try:
                value = factory()
            except Exception:     # der Vordergrund versucht es erneut und zeigt den Fehler an
                log.debug('Vorausberechnung %r fehlgeschlagen', key, exc_info=True)
                value = None
            with self._cond:
                if value is not None:
                    self.prefetched += 1
                    self._store(key, value, epoch)
                self._inflight.pop(key, None)
            event.set()

    # ---- Verwaltung
    def clear(self):
        """Verwirft Eintraege und offene Auftraege (z. B. anderer Run)."""
        with self._cond:
            self._epoch += 1
            self._items.clear()
            self._queue.clear()
            self.hits = self.misses = self.prefetched = 0

    def close(self):
        with self._cond:
            self._closed = True
            self._queue.clear()
            self._cond.notify_all()

    def __len__(self):
        with self._cond:
            return len(self._items)

    def stats(self):
        with self._cond:
            return {'entries': len(self._items), 'hits': self.hits, 'misses': self.misses,
                    'prefetched': self.prefetched, 'queued': len(self._queue)}
