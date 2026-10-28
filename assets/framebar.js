// PAKET-H1B: Frame-Balken fuer Diagramme (Replay Lab, Simulation).
//
// Ein senkrechter, ziehbarer Balken liegt als eigenes DOM-Element ueber dem Plotly-Diagramm (nicht im Plotly-
// Diagramm). Dadurch bleiben Zoom, Legendenzustand und Bereichsauswahl unberuehrt, es entstehen keine
// relayoutData-Anfragen und das Diagramm wird beim Ziehen nicht neu gebaut.
//
//   Server -> Balken: clientseitiger Dash-Callback (c2c_framebar.move) setzt die Position aus dem Index
//                     (x-Wert = data[0].x[index]); bei Wiedergabe laeuft der Balken also mit.
//   Balken -> Server: beim Ziehen wird an dcc.Store (data-framebar am Diagrammblock) {frame, nonce} gemeldet,
//                     gedrosselt (SEND_MS) und beim Loslassen sofort. Das programmatische Setzen des Balkens
//                     meldet nichts zurueck (keine Rueckkopplung).
(function () {
  'use strict';

  var GRAB_W = 14;      // Breite des Greifbereichs in px
  var SEND_MS = 150;    // Drosselung der Meldungen an den Server beim Ziehen
  var HOLD_MS = 1200;   // so lange bleibt der gezogene Wert stehen, bis der Server den Index meldet
  var states = {};
  var seq = 0;

  function stateFor(boxId) {
    if (!states[boxId]) states[boxId] = {index: null, drag: null, hold: null, retry: 0, hooked: null};
    return states[boxId];
  }

  function parts(boxId) {
    var box = document.getElementById(boxId);
    if (!box) return null;
    var graphId = boxId.replace(/-box$/, '');
    var host = document.getElementById(graphId);
    if (!host) return null;
    return {boxId: boxId, box: box, host: host, gd: host.querySelector('.js-plotly-plot'),
            store: box.getAttribute('data-framebar')};
  }

  function ready(p) {
    var gd = p && p.gd;
    return !!(gd && gd._fullLayout && gd._fullLayout.xaxis && gd._fullLayout._size && gd.data);
  }

  // x-Werte (Frame-IDs) der ersten Kurve; Position im Array = Index des Replays bzw. der Simulation
  function xValues(gd) {
    var trace = (gd.data || [])[0];
    return trace && trace.x && trace.x.length ? trace.x : [];
  }

  function finite(v) { return typeof v === 'number' && isFinite(v); }

  // Pixel (relativ zum Diagramm-Container) -> naechster vorhandener Frame innerhalb des sichtbaren Bereichs
  function snap(gd, px) {
    var xa = gd._fullLayout.xaxis, xs = xValues(gd);
    var value = xa.p2d(px - xa._offset), range = xa.range || [];
    var lo = Math.min(range[0], range[1]), hi = Math.max(range[0], range[1]);
    if (finite(lo) && finite(hi)) value = Math.max(lo, Math.min(hi, value));
    var best = null, bestDist = Infinity;
    for (var i = 0; i < xs.length; i++) {
      if (!finite(xs[i])) continue;
      if (finite(lo) && finite(hi) && (xs[i] < lo || xs[i] > hi)) continue;
      var dist = Math.abs(xs[i] - value);
      if (dist < bestDist) { bestDist = dist; best = xs[i]; }
    }
    return best;
  }

  function currentValue(p, st) {
    if (st.drag) return st.drag.value;
    if (st.hold && Date.now() - st.hold.t < HOLD_MS) return st.hold.value;
    var xs = xValues(p.gd);
    return st.index !== null && finite(xs[st.index]) ? xs[st.index] : null;
  }

  function ensureBar(p) {
    var bar = p.host.querySelector('.c2c-framebar');
    if (bar) return bar;
    bar = document.createElement('div');
    bar.className = 'c2c-framebar';
    bar.title = 'Frame-Balken: ziehen = Frame wählen';
    bar.innerHTML = '<div class="c2c-framebar-line"></div><div class="c2c-framebar-grip"></div>' +
                    '<div class="c2c-framebar-tag"></div>';
    p.host.appendChild(bar);
    attachDrag(p.boxId, bar);
    return bar;
  }

  function hook(p) {
    var st = stateFor(p.boxId);
    if (st.hooked === p.gd || !p.gd) return;
    st.hooked = p.gd;
    if (typeof p.gd.on === 'function') {
      ['plotly_afterplot', 'plotly_relayout', 'plotly_relayouting', 'plotly_restyle', 'plotly_redraw']
        .forEach(function (name) { p.gd.on(name, function () { render(p.boxId); }); });
    }
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () { render(p.boxId); }).observe(p.host);
    }
  }

  function render(boxId) {
    var p = parts(boxId);
    if (!p || !ready(p)) return false;
    hook(p);
    var st = stateFor(boxId), bar = ensureBar(p);
    var fl = p.gd._fullLayout, xa = fl.xaxis, size = fl._size;
    var value = currentValue(p, st);
    if (value === null || !(size.w > 0)) { bar.style.display = 'none'; return true; }
    var px = xa._offset + xa.d2p(value);
    if (!finite(px) || px < xa._offset - 0.5 || px > xa._offset + xa._length + 0.5) {
      bar.style.display = 'none';    // ausserhalb des sichtbaren Bereichs (Zoom)
      return true;
    }
    bar.style.display = 'block';
    bar.style.left = (px - GRAB_W / 2) + 'px';
    bar.style.top = size.t + 'px';
    bar.style.height = size.h + 'px';
    bar.setAttribute('data-frame', String(value));
    bar.querySelector('.c2c-framebar-tag').textContent = st.drag ? '#' + value : '';
    return true;
  }

  function schedule(boxId) {
    var st = stateFor(boxId);
    if (render(boxId)) { st.retry = 0; return; }
    if (st.retry++ < 60) setTimeout(function () { schedule(boxId); }, 100);   // Diagramm noch nicht gezeichnet
  }

  function send(boxId, final) {
    var st = stateFor(boxId), drag = st.drag, p = parts(boxId);
    if (!drag || !p || !p.store || !window.dash_clientside || !window.dash_clientside.set_props) return;
    function flush() {
      drag.timer = null;
      if (drag.value === drag.sent) return;
      drag.sent = drag.value;
      drag.last = Date.now();
      window.dash_clientside.set_props(p.store, {data: {frame: drag.value, nonce: ++seq}});
    }
    if (final) { if (drag.timer) clearTimeout(drag.timer); flush(); return; }
    var wait = SEND_MS - (Date.now() - drag.last);
    if (wait <= 0) flush();
    else if (!drag.timer) drag.timer = setTimeout(flush, wait);
  }

  function attachDrag(boxId, bar) {
    bar.addEventListener('pointerdown', function (ev) {
      if (ev.button !== undefined && ev.button !== 0) return;
      var p = parts(boxId), st = stateFor(boxId);
      if (!p || !ready(p)) return;
      var value = currentValue(p, st);
      if (value === null) return;
      ev.preventDefault();
      ev.stopPropagation();
      st.drag = {value: value, sent: null, last: 0, timer: null};
      st.hold = null;
      bar.classList.add('c2c-dragging');
      try { bar.setPointerCapture(ev.pointerId); } catch (e) { /* Zeiger nicht mehr aktiv */ }
      render(boxId);
    });
    bar.addEventListener('pointermove', function (ev) {
      var st = stateFor(boxId), p = parts(boxId);
      if (!st.drag || !p || !ready(p)) return;
      var value = snap(p.gd, ev.clientX - p.host.getBoundingClientRect().left);
      if (value === null || value === st.drag.value) return;
      st.drag.value = value;
      render(boxId);
      send(boxId, false);
    });
    function finish() {
      var st = stateFor(boxId);
      if (!st.drag) return;
      send(boxId, true);
      st.hold = {value: st.drag.value, t: Date.now()};
      st.drag = null;
      bar.classList.remove('c2c-dragging');
      render(boxId);
    }
    bar.addEventListener('pointerup', finish);
    bar.addEventListener('pointercancel', finish);
    // Mausrad ueber dem Balken zoomt weiter das Diagramm (die Greifflaeche verdeckt sonst die Zeichenflaeche).
    bar.addEventListener('wheel', function (ev) {
      var p = parts(boxId), target = p && p.gd && p.gd.querySelector('.nsewdrag');
      if (!target) return;
      ev.preventDefault();
      target.dispatchEvent(new WheelEvent('wheel', {bubbles: true, cancelable: true, clientX: ev.clientX,
        clientY: ev.clientY, deltaX: ev.deltaX, deltaY: ev.deltaY, deltaMode: ev.deltaMode,
        ctrlKey: ev.ctrlKey, shiftKey: ev.shiftKey}));
    }, {passive: false});
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    c2c_framebar: {
      // Input: Index (Slider), Figur (nach einem Neuaufbau); State: id des Diagrammblocks
      move: function (index, figure, boxId) {
        if (!boxId) return window.dash_clientside.no_update;
        var st = stateFor(boxId);
        st.index = typeof index === 'number' && isFinite(index) ? index : null;
        st.hold = null;                 // der Server hat den Index gemeldet: er gilt
        schedule(boxId);
        return window.dash_clientside.no_update;
      }
    }
  });
})();
