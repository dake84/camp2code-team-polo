// PAKET-I5: Livebild im Browser (Server-Teil: live_player.py).
//
// Der Browser holt sich die Livebilder selbst: nach dem Eintreffen eines Bildes fragt er das naechste an
// (/_c2c/live/frame.jpg?after=<id>), der Server antwortet, sobald ein neueres Bild vorliegt (Long-Poll bis 0,5 s). Dadurch gibt es
// keinen Stau und keinen Verzoegerungsaufbau; die Bildrate ist begrenzt (Standard 10 pro Sekunde, einstellbar), um die Regelschleife
// des Fahrzeugs nicht zu belasten. Das Bild wird in das vorhandene <img id="drive-overlay"> des Cockpits gesetzt (dessen Vollbild-Knopf und
// die HUD-Auswahl "cockpit-hud" bleiben wirksam). Kein Dash-Callback: solange Bilder geholt werden, rendert der Cockpit-Callback keine
// eigenen Bilder (der Server merkt sich den Herzschlag jeder Anfrage 3 s lang).
//
// Der Spieler arbeitet nur, wenn das Livebild sichtbar ist (Cockpit-Tab, Modus "Live", Seite im Vordergrund). Zurueck zur bisherigen
// Anzeige: Umschalter "Über den Server (alt)".
(function () {
  'use strict';

  var BASE = '/_c2c/live/';
  var IMG_ID = 'drive-overlay';
  var WIDTH_LABELS = {0: 'Original', 640: 'Schnell (640)', 960: 'Standard (960)', 1280: 'Hoch (1280)'};
  var FPS_CHOICES = [5, 10, 15, 25];
  var KEY = {mode: 'c2c.lp.mode', width: 'c2c.lp.width', fps: 'c2c.lp.fps'};
  var CHECK_MS = 500;

  var P = {mode: 'client', width: 640, fps: 10, running: false, token: 0, ctrl: null, last: -1, url: null, oldUrl: null,
           el: null, bar: null, stats: {shown: [], rtt: [], render: [], age: [], kb: [], errors: 0, empty: 0, state: ''},
           timer: null, lastStart: 0, built: false};

  function $(id) { return document.getElementById(id); }
  function now() { return performance.now(); }
  function ls(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, String(value));
    } catch (e) { /* Speicher nicht verfuegbar */ }
    return null;
  }
  function avg(values) { return values.length ? values.reduce(function (a, b) { return a + b; }, 0) / values.length : null; }
  function keep(list, value, max) { list.push(value); while (list.length > max) list.shift(); }
  function esc(text) { return String(text).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }

  // ------------------------------------------------------------------ Bedienleiste
  function radio(name, value, label, checked) {
    var id = 'c2c-lp-' + name + '-' + value;
    return '<div class="form-check form-check-inline"><input class="form-check-input" type="radio" name="c2c-lp-' + name + '" id="' + id +
      '" value="' + value + '"' + (checked ? ' checked' : '') + '><label class="form-check-label" for="' + id + '">' + label + '</label></div>';
  }

  function buildBar() {
    var bar = $('live-player-bar');
    if (!bar) return false;
    P.bar = bar;
    if (bar._c2c) return true;
    bar._c2c = true;
    bar.innerHTML =
      '<span class="c2c-kpi-label me-2">Livebild</span>' +
      radio('mode', 'client', 'Im Browser (schnell)', P.mode === 'client') +
      radio('mode', 'legacy', 'Über den Server (alt)', P.mode === 'legacy') +
      '<span class="ms-3 c2c-lp-opt"><span class="c2c-kpi-label me-1">Größe</span><select class="form-select form-select-sm d-inline-block c2c-lp-select" data-r="width">' +
      Object.keys(WIDTH_LABELS).map(function (w) {
        return '<option value="' + w + '"' + (Number(w) === P.width ? ' selected' : '') + '>' + WIDTH_LABELS[w] + '</option>';
      }).join('') + '</select></span>' +
      '<span class="ms-3 c2c-lp-opt"><span class="c2c-kpi-label me-1">max. Bilder/s</span><select class="form-select form-select-sm d-inline-block c2c-lp-select" data-r="fps">' +
      FPS_CHOICES.map(function (f) { return '<option value="' + f + '"' + (f === P.fps ? ' selected' : '') + '>' + f + '</option>'; }).join('') + '</select></span>' +
      '<div class="c2c-lp-diag mt-1" data-r="diag"></div>';
    bar.addEventListener('change', function (ev) {
      var t = ev.target;
      if (!t) return;
      if (t.name === 'c2c-lp-mode') setMode(t.value === 'legacy' ? 'legacy' : 'client');
      else if (t.getAttribute('data-r') === 'width') { P.width = Number(t.value); ls(KEY.width, P.width); P.last = -1; }
      else if (t.getAttribute('data-r') === 'fps') { P.fps = Number(t.value); ls(KEY.fps, P.fps); }
    });
    return true;
  }

  function renderDiag() {
    var el = P.bar && P.bar.querySelector('[data-r=diag]');
    if (!el) return;
    var s = P.stats;
    if (P.mode !== 'client') { el.textContent = 'Anzeige über den Server (Dash-Takt, ca. 1,4 Bilder/s)'; return; }
    if (!P.running) { el.textContent = 'Livebild im Browser: wartet auf sichtbares Livebild'; return; }
    var parts = [];
    var shown = s.shown;
    if (shown.length >= 2 && shown[shown.length - 1] > shown[0]) {
      parts.push(((shown.length - 1) / ((shown[shown.length - 1] - shown[0]) / 1000)).toFixed(1) + ' Bilder/s (im Browser gemessen)');
    } else {
      parts.push(s.state === 'no-camera' ? 'kein Kamerabild' : 'wartet auf Bilder');
    }
    var rtt = avg(s.rtt), render = avg(s.render), age = avg(s.age), kb = avg(s.kb);
    if (rtt !== null) parts.push('Rundlauf Ø ' + rtt.toFixed(0) + ' ms');
    if (render !== null) parts.push('Rendern (Server) Ø ' + render.toFixed(0) + ' ms');
    if (age !== null) parts.push('Datenalter beim Senden Ø ' + age.toFixed(0) + ' ms');
    if (kb !== null) parts.push(kb.toFixed(0) + ' KB je Bild');
    if (s.errors) parts.push('Fehler ' + s.errors);
    el.textContent = parts.join(' · ');
  }

  // ------------------------------------------------------------------ Bildholen
  function visible() {
    var img = $(IMG_ID);
    return !!img && !document.hidden && img.getClientRects().length > 0;
  }

  function setImage(blob) {
    var img = $(IMG_ID);
    if (!img) return;
    var url = URL.createObjectURL(blob);
    if (P.oldUrl) { try { URL.revokeObjectURL(P.oldUrl); } catch (e) { /* ignorieren */ } }
    P.oldUrl = P.url;
    P.url = url;
    img.src = url;
  }

  function loop(token) {
    if (!P.running || token !== P.token) return;
    var t0 = now();
    P.lastStart = t0;
    var ctrl = P.ctrl = new AbortController();
    var url = BASE + 'frame.jpg?after=' + P.last + '&w=' + P.width + '&st=follow&q=' + Date.now();
    var delay = 0;
    fetch(url, {signal: ctrl.signal, cache: 'no-store'}).then(function (r) {
      var state = r.headers.get('X-C2C-State') || '';
      if (r.status === 200) {
        var id = Number(r.headers.get('X-C2C-Frame'));
        return r.blob().then(function (blob) {
          if (token !== P.token) return;
          if (id === id) P.last = id;
          setImage(blob);
          var s = P.stats, t = now();
          keep(s.shown, t, 30);
          keep(s.rtt, t - t0, 30);
          var render = Number(r.headers.get('X-C2C-Render-Ms')), age = Number(r.headers.get('X-C2C-Age-Ms'));
          if (render === render) keep(s.render, render, 30);
          if (age === age && r.headers.get('X-C2C-Age-Ms') !== null) keep(s.age, age, 30);
          keep(s.kb, blob.size / 1024, 30);
          s.state = 'ok';
        });
      }
      if (r.status === 204) { P.stats.empty++; P.stats.state = state; return; }
      throw new Error('HTTP ' + r.status);
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      P.stats.errors++;
      delay = 500;                                     // Fehler: nicht hammern
    }).then(function () {
      if (!P.running || token !== P.token) return;
      var minInterval = 1000 / Math.max(1, P.fps);
      var wait = Math.max(delay, minInterval - (now() - t0));
      setTimeout(function () { loop(token); }, wait);
    });
  }

  function start() {
    if (P.running) return;
    P.running = true;
    P.token++;
    P.last = -1;
    P.stats = {shown: [], rtt: [], render: [], age: [], kb: [], errors: 0, empty: 0, state: ''};
    loop(P.token);
  }

  function stop(release) {
    if (!P.running && !release) return;
    P.running = false;
    P.token++;
    if (P.ctrl) { try { P.ctrl.abort(); } catch (e) { /* schon beendet */ } P.ctrl = null; }
    if (release) {
      // Zeitstempel ordnet die Meldung: eine aeltere, noch laufende Bildanfrage darf den Herzschlag nicht erneuern
      fetch(BASE + 'release?q=' + (Date.now() + 1), {cache: 'no-store'}).catch(function () { /* der Herzschlag laeuft ohnehin ab */ });
    }
  }

  function setMode(mode) {
    P.mode = mode;
    ls(KEY.mode, mode);
    if (mode === 'legacy') stop(true);
    check();
  }

  function check() {
    var shouldRun = P.mode === 'client' && visible();
    if (shouldRun && !P.running) start();
    else if (!shouldRun && P.running) stop(false);
    renderDiag();
  }

  function ensure() {
    buildBar();
    check();
  }

  function boot() {
    P.mode = ls(KEY.mode) === 'legacy' ? 'legacy' : 'client';
    var w = Number(ls(KEY.width));
    if (ls(KEY.width) !== null && !isNaN(w) && WIDTH_LABELS[w] !== undefined) P.width = w;
    var f = Number(ls(KEY.fps));
    if (FPS_CHOICES.indexOf(f) >= 0) P.fps = f;
    P.timer = setInterval(ensure, CHECK_MS);
    ensure();
  }

  window.c2cLivePlayer = {
    state: P,
    stats: function () {
      return {mode: P.mode, running: P.running, last: P.last, width: P.width, fps: P.fps, shown: P.stats.shown.length,
        errors: P.stats.errors, empty: P.stats.empty, state: P.stats.state, hasSrc: !!($(IMG_ID) && $(IMG_ID).getAttribute('src'))};
    },
    start: start, stop: stop
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
