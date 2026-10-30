// PAKET-I3: Wiedergabe der Simulation im Browser.
//
// Der Browser spielt selbst ab: Play, Pause, Slider, Tempo und Einzelschritte wirken sofort und brauchen keine
// Serveranfrage. Der Server liefert nur Meta-Daten (/_c2c/sim/<token>/meta.json) und fertige Anzeigebilder
// (/_c2c/sim/<token>/<then|now>/<index>.jpg?w=<breite>). Die naechsten Bilder werden im Voraus geladen.
//
// Zeitverhalten wie die bisherige Server-Wiedergabe:
//   Echtzeit    Medienzeit = Anker + Wanduhr * Tempo; gezeigt wird der letzte bereits geladene Frame bis zu dieser
//               Zeit, dazwischenliegende (oder noch nicht geladene) Frames werden uebersprungen.
//   Jeder Frame hoechstens ein Frame weiter, nie ueberspringen; der Anker wandert mit dem gezeigten Frame.
//
// Einbindung in Dash: Es gibt keinen eigenen Dash-Callback. Welches Ergebnis geladen ist, erfaehrt der Spieler ueber
// /_c2c/sim/current.json (Abfrage ca. alle 0,7 s, nur bei sichtbarer Ansicht). Der Frame-Balken (framebar.js) folgt der Wiedergabe ueber c2c_framebar.move; sein Ziehen wird hier abgefangen
// (dash_clientside.set_props fuer 'sim-bar') und als lokaler Sprung ausgefuehrt. Ein Klick ins Diagramm springt lokal
// (plotly_click). Die alte Server-Wiedergabe bleibt ueber den Umschalter "Server (alt)" erhalten.
(function () {
  'use strict';

  var BASE = '/_c2c/sim/';
  var SPEEDS = [[0.25, '0,25x'], [0.5, '0,5x'], [1, '1x'], [2, '2x'], [4, '4x']];
  var WIDTH_LABELS = {640: 'Schnell (640)', 960: 'Standard (960)', 1280: 'Hoch (1280)'};
  var TICK_MS = 25;          // Takt der Wiedergabeschleife (nur lokal, keine Serveranfrage)
  var MAX_PAIRS = 3;         // gleichzeitig ladende Bildpaare = 6 Verbindungen (Limit je Host im Browser)
  var KEEP_BEHIND = 24;      // zurueckliegende Frames, die fuer Einzelschritte im Speicher bleiben
  var KEEP_AHEAD = 100;
  var MAX_LOOKAHEAD = 60;
  var HORIZON_MS = 3000;     // so weit (in Medienzeit je Tempo) wird vorausgeladen
  var KEY = {mode: 'c2c.sim.mode', width: 'c2c.sim.width', speed: 'c2c.sim.speed', timing: 'c2c.sim.timing'};

  var P = {
    mode: 'client', token: null, wantToken: undefined, failedToken: undefined, polling: false, meta: null, n: 0, idx: 0, shown: -1,
    playing: false, speed: 1, timing: 'realtime', width: 960, anchor: null, entries: {}, inflight: 0,
    timer: null, root: null, el: {}, dragging: false, stalled: false, loadId: 0, gen: 0, barTimer: null, lastPump: 0,
    stats: {skipped: 0, stalls: 0, errors: 0, ms: [], kb: [], shown: [], jumps: 0}
  };

  function $(id) { return document.getElementById(id); }
  function now() { return performance.now(); }
  function ls(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, String(value));
    } catch (e) { /* Speicher nicht verfuegbar */ }
    return null;
  }
  function fmt(value, digits, unit) {
    if (value === null || value === undefined) return '–';
    var number = Number(value);
    if (!isFinite(number)) return String(value);
    return number.toFixed(digits || 0) + (unit || '');
  }
  function esc(text) {
    return String(text).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
    });
  }
  function bisectRight(values, x) {
    var lo = 0, hi = values.length;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (x < values[mid]) hi = mid; else lo = mid + 1; }
    return lo;
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  // ------------------------------------------------------------------ Aufbau der Oberflaeche
  function radios(name, options, current) {
    return options.map(function (o) {
      var id = 'c2c-sp-' + name + '-' + String(o[0]).replace('.', '_');
      return '<div class="form-check form-check-inline"><input class="form-check-input" type="radio" name="c2c-sp-' + name +
        '" id="' + id + '" value="' + o[0] + '"' + (String(o[0]) === String(current) ? ' checked' : '') +
        '><label class="form-check-label" for="' + id + '">' + o[1] + '</label></div>';
    }).join('');
  }

  function build() {
    var root = $('sim-player-root');
    if (!root) return false;
    if (root._c2c) { P.root = root; return true; }
    root._c2c = true;
    P.root = root;
    root.innerHTML =
      '<div class="c2c-sp" tabindex="0">' +
      '<div class="c2c-sp-label fw-semibold mb-2" data-r="label">Noch keine Simulation geladen</div>' +
      '<div class="c2c-sp-grid">' +
      '<div><div class="c2c-kpi-label">DAMALS (Aufzeichnung)</div><img class="c2c-sp-img" data-r="then" alt=""></div>' +
      '<div><div class="c2c-kpi-label">JETZT (Simulation)</div><img class="c2c-sp-img" data-r="now" alt=""></div>' +
      '</div>' +
      '<div class="c2c-sp-controls mt-2">' +
      '<div class="btn-group btn-group-sm" role="group">' +
      '<button type="button" class="btn btn-secondary" data-r="prev" title="ein Frame zurück (Pfeil links)">◀</button>' +
      '<button type="button" class="btn btn-primary c2c-sp-play" data-r="play">▶ Play</button>' +
      '<button type="button" class="btn btn-secondary" data-r="next" title="ein Frame vor (Pfeil rechts)">▶|</button>' +
      '</div>' +
      '<input type="range" class="form-range c2c-sp-slider" min="0" max="1" step="1" value="0" data-r="slider">' +
      '<span class="small c2c-sp-count" data-r="count"></span>' +
      '</div>' +
      '<div class="c2c-sp-opts mt-2">' +
      '<div><div class="c2c-kpi-label">Tempo</div>' + radios('speed', SPEEDS, P.speed) + '</div>' +
      '<div><div class="c2c-kpi-label">Wiedergabe</div>' +
      radios('timing', [['realtime', 'Echtzeit'], ['every', 'Jeder Frame']], P.timing) + '</div>' +
      '<div><div class="c2c-kpi-label">Bildgröße</div><select class="form-select form-select-sm c2c-sp-width" data-r="width">' +
      Object.keys(WIDTH_LABELS).map(function (w) {
        return '<option value="' + w + '"' + (Number(w) === P.width ? ' selected' : '') + '>' + WIDTH_LABELS[w] + '</option>';
      }).join('') + '</select></div>' +
      '</div>' +
      '<div class="small mt-2 c2c-sp-diag" data-r="diag"></div>' +
      '</div>';
    var el = P.el = {};
    Array.prototype.forEach.call(root.querySelectorAll('[data-r]'), function (node) { el[node.getAttribute('data-r')] = node; });
    bind(root);
    return true;
  }

  function bind(root) {
    var el = P.el;
    el.play.addEventListener('click', function () { if (P.playing) pause(); else play(); });
    el.prev.addEventListener('click', function () { step(-1); });
    el.next.addEventListener('click', function () { step(1); });
    el.slider.addEventListener('pointerdown', function () { P.dragging = true; });
    var release = function () { P.dragging = false; renderText(); };
    el.slider.addEventListener('pointerup', release);
    el.slider.addEventListener('pointercancel', release);
    el.slider.addEventListener('change', release);
    el.slider.addEventListener('input', function () { seek(Number(el.slider.value)); });
    root.addEventListener('change', function (ev) {
      var t = ev.target;
      if (!t || !t.name) return;
      if (t.name === 'c2c-sp-speed') { setSpeed(Number(t.value)); }
      else if (t.name === 'c2c-sp-timing') { setTiming(t.value === 'every' ? 'every' : 'realtime'); }
    });
    el.width.addEventListener('change', function () { setWidth(Number(el.width.value)); });
    root.addEventListener('keydown', function (ev) {
      if (ev.target && (ev.target.tagName === 'SELECT' || ev.target.tagName === 'INPUT')) return;
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); step(-1); }
      else if (ev.key === 'ArrowRight') { ev.preventDefault(); step(1); }
    });
  }

  function buildSwitch() {
    var host = $('sim-player-switch');
    if (!host || host._c2c) return;
    host._c2c = true;
    host.innerHTML = '<span class="c2c-kpi-label me-2">Wiedergabe</span>' +
      radios('mode', [['client', 'Im Browser (schnell)'], ['legacy', 'Über den Server (alt)']], P.mode);
    host.addEventListener('change', function (ev) {
      if (ev.target && ev.target.name === 'c2c-sp-mode') setMode(ev.target.value === 'legacy' ? 'legacy' : 'client');
    });
  }

  function applyMode() {
    var legacy = $('sim-legacy-view'), wrap = $('sim-player-wrap');
    if (!legacy || !wrap) return;
    var client = P.mode === 'client';
    legacy.style.display = client ? 'none' : '';
    wrap.style.display = client ? '' : 'none';
    document.body.classList.toggle('c2c-sim-client', client);
    var radio = document.querySelector('input[name="c2c-sp-mode"][value="' + P.mode + '"]');
    if (radio && !radio.checked) radio.checked = true;
    if (!client) pause();
    ensureLiveCompare();
  }

  function setMode(mode) {
    P.mode = mode;
    ls(KEY.mode, mode);
    applyMode();
    if (mode === 'client') pollToken();
  }

  // Vergleichstabelle: eigenes Element neben der (vom Server gefuellten) Tabelle, damit React nichts ueberschreibt
  function ensureLiveCompare() {
    var host = $('sim-compare');
    if (!host || !host.parentNode) return null;
    var live = $('sim-live-compare');
    if (!live) {
      live = document.createElement('div');
      live.id = 'sim-live-compare';
      host.parentNode.insertBefore(live, host);
    }
    P.el.compare = live;
    return live;
  }

  // ------------------------------------------------------------------ Ergebnis laden
  function clearEntries() {
    P.gen++;                               // laufende Anfragen der alten Generation zaehlen nicht mehr mit
    Object.keys(P.entries).forEach(function (key) { dropEntry(Number(key)); });
    P.entries = {};
    P.inflight = 0;
  }

  function dropEntry(i) {
    var e = P.entries[i];
    if (!e) return;
    if (e.state === 'loading' && e.ctrl) { try { e.ctrl.abort(); } catch (x) { /* schon beendet */ } }
    if (e.urls) e.urls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (x) { /* ignorieren */ } });
    delete P.entries[i];
  }

  function onToken(token) {
    P.wantToken = token;
    if (P.mode !== 'client') return;
    if (token === P.token && (P.meta || token === null)) return;
    if (token === P.failedToken) return;                  // Laden schlug fehl: erst bei neuem Ergebnis erneut versuchen
    loadToken(token);
  }

  // Welches Ergebnis ist geladen? Kleine Abfrage statt Dash-Callback; nur bei sichtbarer Ansicht im Modus "Browser".
  function pollToken() {
    if (P.polling || document.hidden || P.mode !== 'client') return;
    if (!P.root || !P.root.isConnected || P.root.getClientRects().length === 0) return;
    P.polling = true;
    fetch(BASE + 'current.json', {cache: 'no-store'}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (data) {
      P.polling = false;
      onToken(data && data.token !== undefined ? data.token : null);
    }).catch(function () { P.polling = false; });
  }

  function loadToken(token) {
    pause();
    var myLoad = ++P.loadId;
    clearEntries();
    P.failedToken = undefined;
    P.token = token;
    P.meta = null;
    P.n = 0;
    P.idx = 0;
    P.shown = -1;
    P.stats = {skipped: 0, stalls: 0, errors: 0, ms: [], kb: [], shown: [], jumps: 0};
    if (P.el.then) { P.el.then.removeAttribute('src'); P.el.now.removeAttribute('src'); }
    if (token === null || token === undefined) {
      if (P.el.slider) P.el.slider.max = '1';
      renderText();
      return;
    }
    if (P.el.label) P.el.label.textContent = 'Lade Simulation ...';
    fetch(BASE + token + '/meta.json', {cache: 'no-store'}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (meta) {
      if (myLoad !== P.loadId) return;
      P.meta = meta;
      P.n = meta.n;
      P.idx = clamp(meta.first || 0, 0, meta.n - 1);
      if (P.el.slider) { P.el.slider.max = String(Math.max(1, meta.n - 1)); }
      var width = Number(ls(KEY.width)) || meta.default_width || 960;
      if (meta.widths.indexOf(width) < 0) width = meta.default_width || 960;
      P.width = width;
      if (P.el.width) P.el.width.value = String(width);
      renderText();
      requestFrame(P.idx, true);
      syncBarSoon(true);
    }).catch(function (err) {
      if (myLoad !== P.loadId) return;
      P.failedToken = token;
      if (P.el.label) P.el.label.textContent = 'Simulation nicht ladbar: ' + err.message;
    });
  }

  // ------------------------------------------------------------------ Bilder laden
  function url(i, kind) {
    return BASE + P.token + '/' + kind + '/' + i + '.jpg?w=' + P.width + '&s=' + encodeURIComponent(P.meta.style);
  }

  function fetchBlob(address, signal) {
    return fetch(address, {signal: signal}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.blob();
    });
  }

  function requestFrame(i, urgent) {
    if (!P.meta || i < 0 || i >= P.n) return;
    var existing = P.entries[i];
    if (existing && !(urgent && existing.state === 'error')) return;
    if (existing) dropEntry(i);
    var e = {i: i, state: 'loading', ctrl: new AbortController(), t0: now(), urls: null, then: null, now: null, gen: P.gen};
    P.entries[i] = e;
    P.inflight++;
    Promise.all([fetchBlob(url(i, 'then'), e.ctrl.signal), fetchBlob(url(i, 'now'), e.ctrl.signal)]).then(function (blobs) {
      if (e.gen !== P.gen || P.entries[i] !== e) return;
      e.then = blobs[0];
      e.now = blobs[1];
      e.state = 'ready';
      var ms = now() - e.t0;
      P.stats.ms.push(ms); if (P.stats.ms.length > 40) P.stats.ms.shift();
      P.stats.kb.push((blobs[0].size + blobs[1].size) / 1024); if (P.stats.kb.length > 40) P.stats.kb.shift();
    }).catch(function (err) {
      if (e.gen !== P.gen || P.entries[i] !== e) return;
      if (err && err.name === 'AbortError') { delete P.entries[i]; return; }
      e.state = 'error';
      e.error = String(err && err.message || err);
      P.stats.errors++;
    }).then(function () {
      if (e.gen !== P.gen) return;
      P.inflight = Math.max(0, P.inflight - 1);
      if (P.entries[i] === e && P.idx === i && e.state !== 'loading') showEntry(i), renderText();
      pump();
    });
  }

  function mediaNow() {
    return P.anchor.media + (now() - P.anchor.wall) * P.speed;
  }

  function targetIndex() {
    var t = P.meta.t;
    return clamp(bisectRight(t, mediaNow()) - 1, P.idx, P.n - 1);
  }

  function pump() {
    if (!P.meta) return;
    var order = [];
    if (!P.entries[P.idx]) order.push(P.idx);
    if (P.playing) {
      var t = P.meta.t, media = mediaNow();
      var base = P.timing === 'realtime' ? Math.max(P.idx + 1, targetIndex()) : P.idx + 1;
      for (var j = base; j < P.n && order.length < MAX_LOOKAHEAD; j++) {
        if (order.length >= 4 && t[j] > media + HORIZON_MS * P.speed) break;
        order.push(j);
      }
    } else {
      order.push(P.idx + 1, P.idx - 1, P.idx + 2);
    }
    for (var k = 0; k < order.length && P.inflight < MAX_PAIRS; k++) {
      var i = order[k];
      if (i >= 0 && i < P.n && !P.entries[i]) requestFrame(i, false);
    }
    evict(order);
  }

  function evict(order) {
    var low = P.idx - KEEP_BEHIND, high = P.idx + KEEP_AHEAD;
    if (order.length) high = Math.max(high, order[order.length - 1] + 2);
    Object.keys(P.entries).forEach(function (key) {
      var i = Number(key);
      if (i < low || i > high) dropEntry(i);
    });
  }

  // ------------------------------------------------------------------ Anzeige
  function showEntry(i) {
    var e = P.entries[i];
    if (!e) return false;
    if (e.state === 'ready') {
      if (!e.urls) e.urls = [URL.createObjectURL(e.then), URL.createObjectURL(e.now)];
      P.el.then.src = e.urls[0];
      P.el.now.src = e.urls[1];
      P.shown = i;
      return true;
    }
    if (e.state === 'error') { P.shown = i; return true; }
    return false;
  }

  function recordShown(i) {
    var s = P.stats.shown, t = now();
    s.push([t, P.meta.t[i]]);
    while (s.length > 40 || (s.length > 2 && t - s[0][0] > 6000)) s.shift();
  }

  function displayFrame(i, auto) {
    P.idx = i;
    showEntry(i);
    renderText();
    if (auto) recordShown(i);
    syncBarSoon(false);
  }

  function renderText() {
    var m = P.meta, el = P.el;
    if (!el.label) return;
    if (!m) {
      el.label.textContent = P.token === null || P.token === undefined ? 'Noch keine Simulation geladen' : el.label.textContent;
      if (el.count) el.count.textContent = '';
      if (el.compare && (P.token === null || P.token === undefined)) el.compare.innerHTML = '';
      return;
    }
    var i = P.idx;
    var text = 'Frame #' + m.ids[i] + (m.warm[i] ? ' · Vorlauf' : '') + (m.stop[i] ? ' · STOPP ' + m.stop[i] : '');
    var entry = P.entries[i];
    if (entry && entry.state === 'error') text += ' · Bild nicht verfügbar (' + entry.error + ')';
    else if (!entry || entry.state === 'loading') text += ' · lädt …';
    el.label.textContent = text;
    el.count.textContent = (i + 1) + ' / ' + m.n;
    if (!P.dragging && Number(el.slider.value) !== i) el.slider.value = String(i);
    el.slider.disabled = m.n < 2;
    var plaus = function (v) { return v === null ? '–' : (v ? 'ja' : 'nein'); };
    var rows = [
      ['Linien', fmt(m.tl[i]), fmt(m.nl[i])],
      ['plausibel', plaus(m.tp[i]), m.np[i] ? 'ja' : 'nein'],
      ['Rohwinkel', fmt(m.tr[i], 0, '°'), fmt(m.nr[i], 0, '°')],
      ['Stellwert', fmt(m.tc[i], 0, '°'), fmt(m.nc[i], 0, '°')],
      ['Speed-Steuerwert', fmt(m.ts[i]), fmt(m.ns[i])],
      ['Status', m.tst[i] || '–', m.stop[i] || (m.held[i] ? 'Vorwert gehalten' : 'Entscheidung')]
    ];
    var compare = ensureLiveCompare();
    if (compare) {
      compare.innerHTML = '<table class="table table-sm table-dark mb-0 small"><thead><tr><th>Frame</th><th>damals</th><th>jetzt</th></tr></thead><tbody>' +
        rows.map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td><td>' + esc(r[2]) + '</td></tr>'; }).join('') +
        '</tbody></table>';
    }
  }

  function renderDiag() {
    var el = P.el;
    if (!el.diag) return;
    if (!P.meta) { el.diag.textContent = ''; return; }
    var parts = [], s = P.stats, shown = s.shown;
    if (P.playing && shown.length >= 2 && shown[shown.length - 1][0] > shown[0][0]) {
      var span = (shown[shown.length - 1][0] - shown[0][0]) / 1000;
      parts.push('Anzeige ' + ((shown.length - 1) / span).toFixed(1) + ' Frames/s (im Browser gemessen)');
      parts.push('effektives Tempo ' + (((shown[shown.length - 1][1] - shown[0][1]) / 1000) / span).toFixed(2) + 'x');
    } else {
      parts.push(P.playing ? 'Anzeige wird gemessen' : 'pausiert');
    }
    var ready = Object.keys(P.entries).filter(function (k) { return P.entries[k].state === 'ready' && Number(k) > P.idx; }).length;
    parts.push('Puffer ' + ready + ' Frames');
    if (s.ms.length) {
      var avg = s.ms.reduce(function (a, b) { return a + b; }, 0) / s.ms.length;
      var kb = s.kb.reduce(function (a, b) { return a + b; }, 0) / s.kb.length;
      parts.push('Laden Ø ' + avg.toFixed(0) + ' ms je Bildpaar (' + kb.toFixed(0) + ' KB)');
    }
    if (s.skipped) parts.push('übersprungen ' + s.skipped);
    if (s.stalls) parts.push('gewartet ' + s.stalls + '×');
    if (s.errors) parts.push('Bildfehler ' + s.errors);
    if (P.meta.fps) parts.push('Aufnahme Median ' + P.meta.fps.toFixed(1) + ' Hz (' + (P.meta.fps_source || '?') + ')');
    el.diag.textContent = parts.join(' · ');
  }

  // Frame-Balken im Diagramm folgt der Anzeige (clientseitig, ohne Server)
  function syncBarSoon(delayed) {
    if (P.barTimer) return;
    P.barTimer = setTimeout(function () {
      P.barTimer = null;
      var fb = window.dash_clientside && window.dash_clientside.c2c_framebar;
      if (fb && fb.move && P.meta) {
        try { fb.move(P.idx, null, 'sim-chart-box'); } catch (e) { /* Diagramm noch nicht bereit */ }
      }
    }, delayed ? 450 : 80);
  }

  // ------------------------------------------------------------------ Steuerung
  function anchorHere() {
    P.anchor = {media: P.meta.t[P.idx], wall: now()};
  }

  function play() {
    if (!P.meta || P.playing) return;
    if (P.idx >= P.n - 1) seek(clamp(P.meta.first || 0, 0, P.n - 1));
    fetch(BASE + P.token + '/meta.json', {cache: 'no-store'}).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (m) {
        if (m && m.token === P.token && m.style !== P.meta.style) {          // HUD-Stil im Cockpit geaendert
          P.meta.style = m.style;
          clearEntries();
          requestFrame(P.idx, true);
        }
      }).catch(function () { /* weiter mit den bisherigen Bildern */ });
    P.stats.skipped = 0;
    P.stats.stalls = 0;
    P.stats.shown = [];
    P.stalled = false;
    anchorHere();
    P.playing = true;
    P.el.play.textContent = '⏸ Pause';
    if (P.timer) clearInterval(P.timer);
    P.timer = setInterval(tick, TICK_MS);
    pump();
  }

  function pause() {
    if (P.timer) { clearInterval(P.timer); P.timer = null; }
    var was = P.playing;
    P.playing = false;
    if (P.el.play) P.el.play.textContent = '▶ Play';
    if (was && P.meta) { syncBarSoon(false); pump(); renderDiag(); }
  }

  function seek(i) {
    if (!P.meta) return;
    i = clamp(Math.round(i), 0, P.n - 1);
    P.idx = i;
    P.stats.jumps++;
    var e = P.entries[i];
    if (!e || e.state === 'error') requestFrame(i, true);
    if (e && e.state !== 'loading') showEntry(i);
    if (P.playing) anchorHere();
    renderText();
    syncBarSoon(false);
    pump();
  }

  function step(delta) {
    if (!P.meta) return;
    if (P.playing) pause();
    seek(P.idx + delta);
  }

  function seekToFrameId(frameId) {
    var ids = P.meta.ids, best = -1, dist = Infinity;
    for (var k = 0; k < ids.length; k++) {
      var d = Math.abs(ids[k] - frameId);
      if (d < dist) { dist = d; best = k; }
    }
    if (best >= 0) seek(best);
  }

  function setSpeed(v) {
    if (!(v > 0)) return;
    P.speed = v;
    ls(KEY.speed, v);
    if (P.playing && P.meta) anchorHere();
  }

  function setTiming(v) {
    P.timing = v;
    ls(KEY.timing, v);
    if (P.playing && P.meta) anchorHere();
  }

  function setWidth(w) {
    if (!P.meta || P.meta.widths.indexOf(w) < 0) return;
    P.width = w;
    ls(KEY.width, w);
    var keep = P.idx;
    clearEntries();
    requestFrame(keep, true);
    pump();
  }

  function tick() {
    var m = P.meta;
    if (!m || !P.playing) return;
    if (!P.root || P.root.getClientRects().length === 0) { pause(); return; }   // Ansicht verborgen (z. B. Live-Modus)
    var t = m.t, last = P.n - 1, t0 = now();
    if (P.timing === 'every') {
      if (P.idx < last && mediaNow() >= t[P.idx + 1]) {
        var next = P.entries[P.idx + 1];
        if (next && (next.state === 'ready' || next.state === 'error')) {
          P.anchor = {media: t[P.idx + 1], wall: t0};
          P.stalled = false;
          displayFrame(P.idx + 1, true);
        } else if (!P.stalled) { P.stalled = true; P.stats.stalls++; }
      }
    } else {
      var target = targetIndex();
      if (target > P.idx) {
        var chosen = -1;
        for (var j = target; j > P.idx; j--) {
          var e = P.entries[j];
          if (e && (e.state === 'ready' || e.state === 'error')) { chosen = j; break; }
        }
        if (chosen >= 0) {
          P.stats.skipped += chosen - P.idx - 1;
          P.stalled = false;
          displayFrame(chosen, true);
        } else if (!P.stalled) { P.stalled = true; P.stats.stalls++; }
      }
    }
    if (P.idx >= last) { pause(); return; }
    if (t0 - P.lastPump >= 100) { P.lastPump = t0; pump(); }
  }

  // ------------------------------------------------------------------ Einbindung in Dash
  function patchSetProps() {
    var dc = window.dash_clientside;
    if (!dc || typeof dc.set_props !== 'function' || dc.set_props._c2cSim) return;
    var original = dc.set_props;
    var wrapped = function (id, props) {
      if (id === 'sim-bar' && P.mode === 'client' && P.meta && props && props.data &&
          typeof props.data.frame === 'number') {
        seekToFrameId(props.data.frame);            // Frame-Balken gezogen: lokal springen, keine Serveranfrage
        return undefined;
      }
      return original.apply(this, arguments);
    };
    wrapped._c2cSim = true;
    try { dc.set_props = wrapped; } catch (e) { /* nicht ueberschreibbar: der Server-Weg bleibt */ }
  }

  function hookChart() {
    var gd = document.querySelector('#sim-chart .js-plotly-plot');
    if (!gd || gd._c2cSim || typeof gd.on !== 'function') return;
    gd._c2cSim = true;
    gd.on('plotly_click', function (ev) {
      var point = ev && ev.points && ev.points[0];
      if (P.mode === 'client' && P.meta && point && typeof point.customdata === 'number') seek(point.customdata);
    });
  }

  function ensure() {
    if (!$('sim-viewer-host')) return;
    var fresh = !(P.root && P.root.isConnected && P.root._c2c);
    build();
    buildSwitch();
    applyMode();
    patchSetProps();
    hookChart();
    ensureLiveCompare();
    if (fresh && P.meta) {                      // Ansicht wurde neu aufgebaut (Tabwechsel): Zustand wiederherstellen
      P.stats.shown = [];
      renderText();
      if (P.shown >= 0 && P.entries[P.idx]) showEntry(P.idx);
      else requestFrame(P.idx, true);
    }
  }

  function boot() {
    P.mode = ls(KEY.mode) === 'legacy' ? 'legacy' : 'client';
    var s = Number(ls(KEY.speed));
    if (s > 0) P.speed = s;
    P.timing = ls(KEY.timing) === 'every' ? 'every' : 'realtime';
    var w = Number(ls(KEY.width));
    if (w > 0) P.width = w;
    setInterval(ensure, 700);
    setInterval(pollToken, 700);
    setInterval(function () { if (P.root && P.root.getClientRects().length) renderDiag(); }, 500);
    ensure();
  }

  window.c2cSimPlayer = {
    state: P,
    stats: function () { return {idx: P.idx, n: P.n, playing: P.playing, speed: P.speed, timing: P.timing, width: P.width,
      mode: P.mode, inflight: P.inflight, ready: Object.keys(P.entries).filter(function (k) { return P.entries[k].state === 'ready'; }).length,
      skipped: P.stats.skipped, stalls: P.stats.stalls, errors: P.stats.errors, jumps: P.stats.jumps}; },
    play: play, pause: pause, seek: seek, step: step
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
