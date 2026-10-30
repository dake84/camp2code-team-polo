// PAKET-I4: Wiedergabe im Replay Lab im Browser (Server-Teil: replay_player.py).
//
// Der Browser spielt selbst ab: Play, Pause, Slider, Tempo, Einzelschritte, Bildgroesse, HUD-Stil und Vollbild wirken sofort
// und brauchen waehrend der Wiedergabe keinen Dash-Callback. Der Server liefert nur Zustand (state.json), Meta-Daten
// (meta.json) und fertige JPEG-Anzeigebilder mit HUD (img/<i>.jpg); die naechsten Bilder werden im Voraus geladen.
//
// Verbindung zu Dash (kein eigener Callback):
//   * Welche Session/welchen Bereich/welchen Index das Replay Lab gerade hat, meldet der Server ueber state.json (Abfrage
//     alle 0,7 s, nur bei sichtbarer Ansicht). Aendert er sich (Session gewaehlt, Bereich gezoomt, Ereignissprung ...),
//     laedt der Spieler neu bzw. springt.
//   * Der Spieler schreibt seine Position mit dash_clientside.set_props('replay-index', ...) zurueck, wenn er pausiert oder
//     der Nutzer springt (nie waehrend der Wiedergabe). So stimmen "Frame im Pipeline Studio analysieren", der Frame-Balken
//     und die bisherige Anzeige mit dem Spieler ueberein.
//   * Der Frame-Balken im Diagramm folgt lokal (c2c_framebar.move); sein Ziehen (set_props 'replay-bar') und ein Klick ins
//     Diagramm (plotly_click) werden als lokaler Sprung ausgefuehrt.
// Live-Ringbuffer: nicht abspielbar im Browser (fluechtig) - dort bleibt die bisherige Anzeige sichtbar.
(function () {
  'use strict';

  var BASE = '/_c2c/replay/';
  var SPEEDS = [[0.25, '0,25x'], [0.5, '0,5x'], [1, '1x'], [2, '2x'], [4, '4x']];
  var WIDTH_LABELS = {0: 'Original', 640: 'Schnell (640)', 960: 'Standard (960)', 1280: 'Hoch (1280)'};
  var TICK_MS = 25;          // Takt der Wiedergabeschleife (lokal)
  var MAX_LOADING = 4;       // gleichzeitig ladende Bilder (Verbindungslimit je Host im Browser: 6)
  var KEEP_BEHIND = 24;
  var KEEP_AHEAD = 100;
  var MAX_LOOKAHEAD = 60;
  var HORIZON_MS = 3000;     // so weit (Medienzeit x Tempo) wird vorausgeladen
  var POLL_MS = 700;
  var PUSH_DELAY_MS = 300;
  var KEY = {mode: 'c2c.rp.mode', width: 'c2c.rp.width', speed: 'c2c.rp.speed', timing: 'c2c.rp.timing', hud: 'c2c.rp.hud'};

  var P = {
    mode: 'client', serverLive: true, session: null, lo: null, hi: null, rev: -1, viewKey: null, failed: false, polling: false,
    meta: null, n: 0, idx: 0, shown: -1, playing: false, speed: 1, timing: 'realtime', width: 960, hud: 'follow',
    anchor: null, entries: {}, inflight: 0, gen: 0, loadId: 0, timer: null, root: null, el: {}, dragging: false,
    stalled: false, barTimer: null, pushTimer: null, idleTimer: null, lastPump: 0, lastPushed: null, dashIndex: null,
    pushedRecent: [], lastLocal: 0,
    stats: null
  };
  function freshStats() { return {skipped: 0, stalls: 0, errors: 0, ms: [], kb: [], shown: [], jumps: 0}; }
  P.stats = freshStats();

  function $(id) { return document.getElementById(id); }
  function now() { return performance.now(); }
  function ls(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, String(value));
    } catch (e) { /* Speicher nicht verfuegbar */ }
    return null;
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function bisectRight(values, x) {
    var lo = 0, hi = values.length;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (x < values[mid]) hi = mid; else lo = mid + 1; }
    return lo;
  }
  function esc(text) {
    return String(text).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; });
  }

  // ------------------------------------------------------------------ Oberflaeche
  function radios(name, options, current) {
    return options.map(function (o) {
      var id = 'c2c-rp-' + name + '-' + String(o[0]).replace('.', '_');
      return '<div class="form-check form-check-inline"><input class="form-check-input" type="radio" name="c2c-rp-' + name +
        '" id="' + id + '" value="' + o[0] + '"' + (String(o[0]) === String(current) ? ' checked' : '') +
        '><label class="form-check-label" for="' + id + '">' + o[1] + '</label></div>';
    }).join('');
  }

  function build() {
    var root = $('replay-player-root');
    if (!root) return false;
    if (root._c2c) { P.root = root; return true; }
    root._c2c = true;
    P.root = root;
    root.innerHTML =
      '<div class="c2c-rp" tabindex="0" data-r="box">' +
      '<div class="c2c-rp-top"><span class="fw-semibold" data-r="label">Keine Session gewählt</span>' +
      '<button type="button" class="btn btn-sm btn-outline-info ms-2" data-r="fs" title="Vollbild (Taste F, Doppelklick aufs Bild)">⛶ Vollbild</button>' +
      '<span class="small ms-2 text-secondary" data-r="hint"></span></div>' +
      '<div class="c2c-rp-stage"><img class="c2c-rp-img" data-r="img" alt=""></div>' +
      '<div class="c2c-rp-controls c2c-rp-bar">' +
      '<div class="btn-group btn-group-sm" role="group">' +
      '<button type="button" class="btn btn-secondary" data-r="prev" title="ein Frame zurück (Pfeil links)">◀</button>' +
      '<button type="button" class="btn btn-primary c2c-rp-play" data-r="play" title="Leertaste">▶ Play</button>' +
      '<button type="button" class="btn btn-secondary" data-r="next" title="ein Frame vor (Pfeil rechts)">▶|</button>' +
      '</div>' +
      '<input type="range" class="form-range c2c-rp-slider" min="0" max="1" step="1" value="0" data-r="slider">' +
      '<span class="small c2c-rp-count" data-r="count"></span></div>' +
      '<div class="c2c-rp-opts c2c-rp-bar">' +
      '<div><div class="c2c-kpi-label">Tempo</div>' + radios('speed', SPEEDS, P.speed) + '</div>' +
      '<div><div class="c2c-kpi-label">Wiedergabe</div>' + radios('timing', [['realtime', 'Echtzeit'], ['every', 'Jeder Frame']], P.timing) + '</div>' +
      '<div><div class="c2c-kpi-label">Bildgröße</div><select class="form-select form-select-sm c2c-rp-select" data-r="width"></select></div>' +
      '<div><div class="c2c-kpi-label">HUD</div><select class="form-select form-select-sm c2c-rp-select" data-r="hudsel"></select></div>' +
      '</div>' +
      '<div class="small mt-2 c2c-rp-details" data-r="details"></div>' +
      '<div class="small mt-1 c2c-rp-diag" data-r="diag"></div></div>';
    var el = P.el = {};
    Array.prototype.forEach.call(root.querySelectorAll('[data-r]'), function (node) { el[node.getAttribute('data-r')] = node; });
    fillSelects(null);
    bind();
    return true;
  }

  function fillSelects(meta) {
    var el = P.el;
    var widths = meta ? meta.widths : [0, 640, 960, 1280];
    el.width.innerHTML = widths.map(function (w) {
      return '<option value="' + w + '"' + (w === P.width ? ' selected' : '') + '>' + (WIDTH_LABELS[w] || w) + '</option>';
    }).join('');
    var styles = meta ? meta.styles : [{value: 'follow', label: 'HUD wie im Cockpit'}];
    el.hudsel.innerHTML = styles.map(function (s) {
      return '<option value="' + s.value + '"' + (s.value === P.hud ? ' selected' : '') + '>' + esc(s.label) + '</option>';
    }).join('');
  }

  function bind() {
    var el = P.el;
    el.play.addEventListener('click', function () { if (P.playing) pause(); else play(); });
    el.prev.addEventListener('click', function () { step(-1); });
    el.next.addEventListener('click', function () { step(1); });
    el.fs.addEventListener('click', toggleFullscreen);
    el.img.addEventListener('dblclick', toggleFullscreen);
    el.slider.addEventListener('pointerdown', function () { P.dragging = true; });
    var release = function () { P.dragging = false; renderText(); schedulePush(true); };
    el.slider.addEventListener('pointerup', release);
    el.slider.addEventListener('pointercancel', release);
    el.slider.addEventListener('change', release);
    el.slider.addEventListener('input', function () { seek(Number(el.slider.value)); });
    el.box.addEventListener('change', function (ev) {
      var t = ev.target;
      if (!t) return;
      if (t.name === 'c2c-rp-speed') setSpeed(Number(t.value));
      else if (t.name === 'c2c-rp-timing') setTiming(t.value === 'every' ? 'every' : 'realtime');
    });
    el.width.addEventListener('change', function () { setWidth(Number(el.width.value)); });
    el.hudsel.addEventListener('change', function () { setHud(el.hudsel.value); });
    el.box.addEventListener('keydown', function (ev) {
      if (ev.target && (ev.target.tagName === 'SELECT' || ev.target.tagName === 'INPUT' && ev.target.type !== 'range' && ev.target.type !== 'radio')) return;
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); step(-1); }
      else if (ev.key === 'ArrowRight') { ev.preventDefault(); step(1); }
      else if (ev.key === ' ' || ev.key === 'Spacebar') { ev.preventDefault(); if (P.playing) pause(); else play(); }
      else if (ev.key === 'f' || ev.key === 'F') { ev.preventDefault(); toggleFullscreen(); }
      idleReset();
    });
    ['mousemove', 'pointerdown', 'touchstart'].forEach(function (name) { el.box.addEventListener(name, idleReset); });
  }

  function buildSwitch() {
    var host = $('replay-player-switch');
    if (!host || host._c2c) return;
    host._c2c = true;
    host.innerHTML = '<span class="c2c-kpi-label me-2">Wiedergabe</span>' +
      radios('mode', [['client', 'Im Browser (schnell)'], ['legacy', 'Über den Server (alt)']], P.mode) +
      '<span class="ms-2 text-secondary" id="replay-player-note"></span>';
    host.addEventListener('change', function (ev) {
      if (ev.target && ev.target.name === 'c2c-rp-mode') setMode(ev.target.value === 'legacy' ? 'legacy' : 'client');
    });
  }

  function effectiveClient() { return P.mode === 'client' && !P.serverLive; }

  function applyMode() {
    var legacy = $('replay-legacy-view'), wrap = $('replay-player-wrap'), controls = $('replay-legacy-controls');
    if (!legacy || !wrap) return;
    var client = effectiveClient();
    legacy.style.display = client ? 'none' : '';
    wrap.style.display = client ? '' : 'none';
    if (controls) controls.style.display = client ? 'none' : '';
    var radio = document.querySelector('input[name="c2c-rp-mode"][value="' + P.mode + '"]');
    if (radio && !radio.checked) radio.checked = true;
    var note = $('replay-player-note');
    if (note) {
      note.textContent = P.serverLive ? 'Live-Ringbuffer: Anzeige über den Server (die Browser-Wiedergabe gilt für gespeicherte Sessions)' : '';
    }
    if (!client) pause();
  }

  function setMode(mode) {
    P.mode = mode;
    ls(KEY.mode, mode);
    applyMode();
    // Server ueber den Wechsel informieren; wechselt man zurueck auf "alt", die bisherige Anzeige auf die Position bringen.
    // eigene Anfrage (unabhaengig von einer gerade laufenden Zustandsabfrage), damit der Herzschlag sicher vor dem Neuzeichnen umgestellt ist
    fetch(stateUrl(mode === 'client'), {cache: 'no-store'}).then(function () {
      if (mode === 'legacy' && P.mode === 'legacy' && P.meta) refreshLegacy();
    }).catch(function () { /* der naechste Takt meldet es erneut */ });
  }

  // Die bisherige Anzeige wurde waehrend der Browser-Wiedergabe nicht aktualisiert (der Bild-Callback hat nichts gerendert).
  // set_props loest den Bild-Callback auch bei gleichem Wert aus (Dash prueft nicht auf Gleichheit): einmal den aktuellen Index setzen.
  // Dash wertet bei mehreren Anfragen fuer dieselbe Ausgabe nur die letzte aus; ein Takt-Aufruf (liefert fuer Sessions "nichts zu tun")
  // kann das Bild daher ueberholen. Aendert sich die Beschriftung nicht, wird es deshalb bis zu 3-mal erneut versucht.
  function refreshLegacy(attempt) {
    var dc = window.dash_clientside;
    attempt = attempt || 0;
    if (!P.meta || !dc || typeof dc.set_props !== 'function') return;
    var label = $('replay-frame-label');
    var before = label ? label.textContent : null;
    P.dashIndex = P.idx;
    P.lastLocal = now();
    P.pushedRecent.push([P.idx, now()]);
    try { dc.set_props('replay-index', {value: P.idx}); } catch (e) { /* Layout noch nicht bereit */ }
    setTimeout(function () {
      var current = $('replay-frame-label');
      if (attempt < 3 && P.mode === 'legacy' && P.meta && current && current.textContent === before &&
          before !== 'Frame #' + P.meta.ids[P.idx]) refreshLegacy(attempt + 1);
    }, 1500);
  }

  // ------------------------------------------------------------------ Zustand vom Server
  function viewKey(session, lo, hi) { return session + '|' + lo + '|' + hi; }
  function viewQuery() {
    return 's=' + encodeURIComponent(P.session) + '&lo=' + (P.lo === null ? '' : P.lo) + '&hi=' + (P.hi === null ? '' : P.hi);
  }

  // q = Zeitstempel: der Server ordnet die Herzschlag-Meldungen danach (eine aeltere Anfrage darf eine neuere nicht uebersteuern)
  function stateUrl(active) { return BASE + 'state.json?a=' + (active ? 1 : 0) + '&q=' + Date.now(); }

  function pollState(done) {
    if (P.polling) return;
    P.polling = true;
    fetch(stateUrl(P.mode === 'client'), {cache: 'no-store'}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (st) {
      P.polling = false;
      var live = !!st.live || !st.session;
      if (live !== P.serverLive) { P.serverLive = live; applyMode(); }
      if (!live) onState(st);
      if (done) done();
    }).catch(function () { P.polling = false; });
  }

  function onState(st) {
    if (P.mode !== 'client') return;
    var key = viewKey(st.session, st.lo, st.hi);
    if (key !== P.viewKey) {                       // andere Session oder anderer Bereich: neu laden
      P.rev = st.rev;
      loadView(st.session, st.lo, st.hi, st.index);
      return;
    }
    if (st.rev === P.rev) return;
    P.rev = st.rev;
    if (!P.meta) {
      if (P.failed) loadView(st.session, st.lo, st.hi, st.index);       // frueherer Ladefehler: bei neuer Meldung erneut versuchen
      return;
    }
    // Eigene Meldungen kommen verzoegert zurueck (auch mehrere nacheinander) und die Position in Dash hinkt der lokalen nach:
    // Index, den der Spieler selbst kuerzlich geschrieben hat, und Aenderungen kurz nach einer lokalen Bedienung gelten nicht
    // als Sprung des Servers. Ein echter Sprung (Ereignisliste, Zoom ...) aendert meist den Bereich und laedt ohnehin neu.
    var t = now();
    var own = P.pushedRecent.some(function (e) { return e[0] === st.index && t - e[1] < 5000; });
    if (own || t - P.lastLocal < 1500 || st.index === P.idx) return;
    seek(st.index, true);
  }

  function pollTimer() {
    if (document.hidden || !P.root || !P.root.isConnected) return;
    var box = $('replay-viewer-host');
    if (!box || box.getClientRects().length === 0) return;           // Tab nicht sichtbar
    pollState();
  }

  // ------------------------------------------------------------------ Ansicht laden
  function clearEntries() {
    P.gen++;
    Object.keys(P.entries).forEach(function (key) { dropEntry(Number(key)); });
    P.entries = {};
    P.inflight = 0;
  }

  function dropEntry(i) {
    var e = P.entries[i];
    if (!e) return;
    if (e.state === 'loading' && e.ctrl) { try { e.ctrl.abort(); } catch (x) { /* schon beendet */ } }
    if (e.url) { try { URL.revokeObjectURL(e.url); } catch (x) { /* ignorieren */ } }
    delete P.entries[i];
  }

  function loadView(session, lo, hi, index) {
    pause();
    var mine = ++P.loadId;
    clearEntries();
    P.viewKey = viewKey(session, lo, hi); P.failed = false;
    P.session = session; P.lo = lo; P.hi = hi;
    P.meta = null; P.n = 0; P.idx = 0; P.shown = -1; P.stats = freshStats(); P.lastPushed = null; P.pushedRecent = [];
    if (P.el.img) P.el.img.removeAttribute('src');
    if (P.el.label) P.el.label.textContent = 'Lade Session …';
    fetch(BASE + 'meta.json?' + viewQuery(), {cache: 'no-store'}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (meta) {
      if (mine !== P.loadId) return;
      P.meta = meta;
      P.n = meta.n;
      P.idx = clamp(Number(index) || 0, 0, meta.n - 1);
      P.dashIndex = P.idx;
      var width = ls(KEY.width) === null ? meta.default_width : Number(ls(KEY.width));
      P.width = meta.widths.indexOf(width) >= 0 ? width : meta.default_width;
      var hud = ls(KEY.hud) || 'follow';
      P.hud = meta.styles.some(function (s) { return s.value === hud; }) ? hud : 'follow';
      fillSelects(meta);
      P.el.slider.max = String(Math.max(1, meta.n - 1));
      P.el.slider.value = String(P.idx);
      renderText();
      requestFrame(P.idx, true);
      syncBarSoon(true);
    }).catch(function (err) {
      if (mine !== P.loadId) return;
      P.failed = true;
      if (P.el.label) P.el.label.textContent = 'Session nicht ladbar: ' + err.message;
    });
  }

  // ------------------------------------------------------------------ Bilder laden
  function imageUrl(i) {
    return BASE + 'img/' + i + '.jpg?' + viewQuery() + '&w=' + P.width + '&st=' + encodeURIComponent(P.hud) + '&v=' + P.meta.v;
  }

  function requestFrame(i, urgent) {
    if (!P.meta || i < 0 || i >= P.n) return;
    var existing = P.entries[i];
    if (existing && !(urgent && existing.state === 'error')) return;
    if (existing) dropEntry(i);
    var e = {i: i, state: 'loading', ctrl: new AbortController(), t0: now(), url: null, blob: null, gen: P.gen};
    P.entries[i] = e;
    P.inflight++;
    fetch(imageUrl(i), {signal: e.ctrl.signal}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.blob();
    }).then(function (blob) {
      if (e.gen !== P.gen || P.entries[i] !== e) return;
      e.blob = blob;
      e.state = 'ready';
      P.stats.ms.push(now() - e.t0); if (P.stats.ms.length > 40) P.stats.ms.shift();
      P.stats.kb.push(blob.size / 1024); if (P.stats.kb.length > 40) P.stats.kb.shift();
    }).catch(function (err) {
      if (e.gen !== P.gen || P.entries[i] !== e) return;
      if (err && err.name === 'AbortError') { delete P.entries[i]; return; }
      e.state = 'error';
      e.error = String(err && err.message || err);
      P.stats.errors++;
    }).then(function () {
      if (e.gen !== P.gen) return;
      P.inflight = Math.max(0, P.inflight - 1);
      if (P.entries[i] === e && P.idx === i && e.state !== 'loading') { showEntry(i); renderText(); }
      pump();
    });
  }

  function mediaNow() { return P.anchor.media + (now() - P.anchor.wall) * P.speed; }
  function targetIndex() { return clamp(bisectRight(P.meta.t, mediaNow()) - 1, P.idx, P.n - 1); }

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
    for (var k = 0; k < order.length && P.inflight < MAX_LOADING; k++) {
      var i = order[k];
      if (i >= 0 && i < P.n && !P.entries[i]) requestFrame(i, false);
    }
    var low = P.idx - KEEP_BEHIND, high = P.idx + KEEP_AHEAD;
    if (order.length) high = Math.max(high, order[order.length - 1] + 2);
    Object.keys(P.entries).forEach(function (key) {
      var n = Number(key);
      if (n < low || n > high) dropEntry(n);
    });
  }

  // ------------------------------------------------------------------ Anzeige
  function showEntry(i) {
    var e = P.entries[i];
    if (!e) return false;
    if (e.state === 'ready') {
      if (!e.url) e.url = URL.createObjectURL(e.blob);
      P.el.img.src = e.url;
      P.shown = i;
      return true;
    }
    if (e.state === 'error') { P.shown = i; return true; }
    return false;
  }

  function renderText() {
    var m = P.meta, el = P.el;
    if (!el.label) return;
    if (!m) { if (el.count) el.count.textContent = ''; if (el.details) el.details.textContent = ''; return; }
    var i = P.idx, e = P.entries[i];
    var text = 'Frame #' + m.ids[i];
    if (e && e.state === 'error') text += ' · Bild nicht verfügbar (' + e.error + ')';
    else if (!e || e.state === 'loading') text += ' · lädt …';
    el.label.textContent = text;
    el.count.textContent = (i + 1) + ' / ' + m.n;
    if (!P.dragging && Number(el.slider.value) !== i) el.slider.value = String(i);
    el.slider.disabled = m.n < 2;
    var d = function (v) { return v === '' || v === undefined ? '–' : v; };
    el.details.textContent = 'Status ' + d(m.status[i]) + ' · Stellwert ' + d(m.sc[i]) + ' · Soll ' + d(m.rq[i]) + ' · Roh ' + d(m.rw[i]) +
      ' · Speed ' + d(m.sp[i]) + ' · Linien ' + d(m.ln[i]) + ' · Ereignisse ' + d(m.ev[i]);
  }

  function renderDiag() {
    var el = P.el;
    if (!el.diag) return;
    if (!P.meta) { el.diag.textContent = ''; return; }
    var s = P.stats, shown = s.shown, parts = [];
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
      parts.push('Laden Ø ' + avg.toFixed(0) + ' ms je Bild (' + kb.toFixed(0) + ' KB)');
    }
    if (s.skipped) parts.push('übersprungen ' + s.skipped);
    if (s.stalls) parts.push('gewartet ' + s.stalls + '×');
    if (s.errors) parts.push('Bildfehler ' + s.errors);
    el.diag.textContent = parts.join(' · ');
  }

  function displayFrame(i) {
    P.idx = i;
    showEntry(i);
    renderText();
    var s = P.stats.shown, t = now();
    s.push([t, P.meta.t[i]]);
    while (s.length > 40 || (s.length > 2 && t - s[0][0] > 6000)) s.shift();
    syncBarSoon(false);
  }

  function syncBarSoon(delayed) {
    if (P.barTimer) return;
    P.barTimer = setTimeout(function () {
      P.barTimer = null;
      var fb = window.dash_clientside && window.dash_clientside.c2c_framebar;
      if (fb && fb.move && P.meta) {
        try { fb.move(P.idx, null, 'replay-chart-box'); } catch (e) { /* Diagramm noch nicht bereit */ }
      }
    }, delayed ? 450 : 80);
  }

  // Position nach Dash zurueckschreiben (nur wenn nicht abgespielt wird)
  function pushIndex() {
    var dc = window.dash_clientside;
    if (!P.meta || P.dashIndex === P.idx || !dc || typeof dc.set_props !== 'function') return;
    P.dashIndex = P.idx;
    P.lastPushed = P.idx;
    P.pushedRecent.push([P.idx, now()]);
    while (P.pushedRecent.length > 12) P.pushedRecent.shift();
    try { dc.set_props('replay-index', {value: P.idx}); } catch (e) { /* Layout noch nicht bereit */ }
  }

  function schedulePush(immediate) {
    if (P.pushTimer) { clearTimeout(P.pushTimer); P.pushTimer = null; }
    if (P.playing) return;
    if (immediate) { pushIndex(); return; }
    P.pushTimer = setTimeout(function () { P.pushTimer = null; if (!P.playing) pushIndex(); }, PUSH_DELAY_MS);
  }

  // ------------------------------------------------------------------ Vollbild
  function toggleFullscreen() {
    var box = P.el.box;
    if (!box) return;
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    if (box.requestFullscreen) box.requestFullscreen().catch(function () { /* vom Browser abgelehnt */ });
  }

  function idleReset() {
    var box = P.el.box;
    if (!box) return;
    box.classList.remove('c2c-rp-idle');
    if (P.idleTimer) { clearTimeout(P.idleTimer); P.idleTimer = null; }
    if (document.fullscreenElement === box && P.playing) {
      P.idleTimer = setTimeout(function () { box.classList.add('c2c-rp-idle'); }, 2500);
    }
  }

  document.addEventListener('fullscreenchange', function () {
    var box = P.el.box;
    if (!box) return;
    var on = document.fullscreenElement === box;
    box.classList.toggle('c2c-rp-full', on);
    if (P.el.fs) P.el.fs.textContent = on ? '⮌ Vollbild beenden' : '⛶ Vollbild';
    if (on) box.focus();
    idleReset();
  });

  // ------------------------------------------------------------------ Steuerung
  function anchorHere() { P.anchor = {media: P.meta.t[P.idx], wall: now()}; }

  function play() {
    if (!P.meta || P.playing) return;
    P.lastLocal = now();
    if (P.idx >= P.n - 1) seek(0);
    P.stats.skipped = 0; P.stats.stalls = 0; P.stats.shown = [];
    P.stalled = false;
    anchorHere();
    P.playing = true;
    P.el.play.textContent = '⏸ Pause';
    if (P.timer) clearInterval(P.timer);
    P.timer = setInterval(tick, TICK_MS);
    idleReset();
    pump();
  }

  function pause() {
    if (P.timer) { clearInterval(P.timer); P.timer = null; }
    var was = P.playing;
    if (was) P.lastLocal = now();
    P.playing = false;
    if (P.el.play) P.el.play.textContent = '▶ Play';
    idleReset();
    if (was && P.meta) { schedulePush(true); syncBarSoon(false); pump(); renderDiag(); }
  }

  function seek(i, fromServer) {
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
    if (fromServer) P.dashIndex = i; else { P.lastLocal = now(); schedulePush(false); }
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

  function setSpeed(v) { if (v > 0) { P.speed = v; ls(KEY.speed, v); if (P.playing && P.meta) anchorHere(); } }
  function setTiming(v) { P.timing = v; ls(KEY.timing, v); if (P.playing && P.meta) anchorHere(); }

  function reloadImages() {
    var keep = P.idx;
    clearEntries();
    requestFrame(keep, true);
    pump();
  }
  function setWidth(w) {
    if (!P.meta || P.meta.widths.indexOf(w) < 0) return;
    P.width = w; ls(KEY.width, w);
    reloadImages();
  }
  function setHud(value) {
    if (!P.meta) return;
    P.hud = value; ls(KEY.hud, value);
    reloadImages();
  }

  function tick() {
    var m = P.meta;
    if (!m || !P.playing) return;
    if (!P.root || P.root.getClientRects().length === 0) { pause(); return; }   // Ansicht verborgen (Tabwechsel, Live)
    var t = m.t, last = P.n - 1, t0 = now();
    if (P.timing === 'every') {
      if (P.idx < last && mediaNow() >= t[P.idx + 1]) {
        var next = P.entries[P.idx + 1];
        if (next && (next.state === 'ready' || next.state === 'error')) {
          P.anchor = {media: t[P.idx + 1], wall: t0};
          P.stalled = false;
          displayFrame(P.idx + 1);
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
          displayFrame(chosen);
        } else if (!P.stalled) { P.stalled = true; P.stats.stalls++; }
      }
    }
    if (P.idx >= last) { pause(); return; }
    if (t0 - P.lastPump >= 100) { P.lastPump = t0; pump(); }
  }

  // ------------------------------------------------------------------ Einbindung in Dash
  function patchSetProps() {
    var dc = window.dash_clientside;
    if (!dc || typeof dc.set_props !== 'function' || dc.set_props._c2cReplay) return;
    var original = dc.set_props;
    var wrapped = function (id, props) {
      if (id === 'replay-bar' && effectiveClient() && P.meta && props && props.data && typeof props.data.frame === 'number') {
        seekToFrameId(props.data.frame);              // Frame-Balken gezogen: lokal springen, keine Serveranfrage
        return undefined;
      }
      return original.apply(this, arguments);
    };
    wrapped._c2cReplay = true;
    // Kette erhalten: ein anderes Skript (sim_player.js) kann set_props schon umhuellt haben
    for (var key in original) { if (Object.prototype.hasOwnProperty.call(original, key)) wrapped[key] = original[key]; }
    wrapped._c2cReplay = true;
    try { dc.set_props = wrapped; } catch (e) { /* nicht ueberschreibbar: der Server-Weg bleibt */ }
  }

  function hookChart() {
    var gd = document.querySelector('#replay-chart .js-plotly-plot');
    if (!gd || gd._c2cReplay || typeof gd.on !== 'function') return;
    gd._c2cReplay = true;
    gd.on('plotly_click', function (ev) {
      var point = ev && ev.points && ev.points[0];
      if (effectiveClient() && P.meta && point && typeof point.customdata === 'number') seekToFrameId(point.customdata);
    });
  }

  function ensure() {
    if (!$('replay-viewer-host')) return;
    build();
    buildSwitch();
    applyMode();
    patchSetProps();
    hookChart();
  }

  function boot() {
    P.mode = ls(KEY.mode) === 'legacy' ? 'legacy' : 'client';
    var s = Number(ls(KEY.speed));
    if (s > 0) P.speed = s;
    P.timing = ls(KEY.timing) === 'every' ? 'every' : 'realtime';
    var w = ls(KEY.width);
    if (w !== null && !isNaN(Number(w))) P.width = Number(w);
    P.hud = ls(KEY.hud) || 'follow';
    setInterval(ensure, 700);
    setInterval(pollTimer, POLL_MS);
    setInterval(function () { if (P.root && P.root.getClientRects().length) renderDiag(); }, 500);
    ensure();
  }

  window.c2cReplayPlayer = {
    state: P,
    stats: function () {
      return {idx: P.idx, n: P.n, playing: P.playing, speed: P.speed, timing: P.timing, width: P.width, hud: P.hud,
        mode: P.mode, live: P.serverLive, session: P.session, lo: P.lo, hi: P.hi, inflight: P.inflight,
        ready: Object.keys(P.entries).filter(function (k) { return P.entries[k].state === 'ready'; }).length,
        skipped: P.stats.skipped, stalls: P.stats.stalls, errors: P.stats.errors, jumps: P.stats.jumps,
        fullscreen: document.fullscreenElement === P.el.box};
    },
    play: play, pause: pause, seek: seek, step: step, fullscreen: toggleFullscreen
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
