// PAKET-I4: Vollbild fuer den Browser-Spieler der Simulation (assets/sim_player.js, Paket I3).
// Reines Zusatz-Skript: es aendert sim_player.js nicht, sondern haengt einen Vollbild-Knopf an dessen Oberflaeche
// (Wurzel ".c2c-sp") und nutzt nur die oeffentliche Schnittstelle window.c2cSimPlayer (play, pause, step, stats).
// Taste F = Vollbild, Leertaste = Play/Pause, Pfeile (Einzelschritte) macht sim_player.js selbst; Esc beendet das Vollbild.
// Beim Abspielen blendet sich die Bedienleiste nach 2,5 s ohne Mausbewegung aus.
(function () {
  'use strict';
  var idleTimer = null;

  function player() { return window.c2cSimPlayer || null; }

  function playing() {
    var p = player();
    try { return !!(p && p.stats().playing); } catch (e) { return false; }
  }

  // Bedienleiste nach 2,5 s ohne Aktivitaet ausblenden - nur im Vollbild und nur beim Abspielen
  function armIdle(root) {
    if (idleTimer || root.classList.contains('c2c-sp-idle')) return;
    if (document.fullscreenElement !== root || !playing()) return;
    idleTimer = setTimeout(function () { idleTimer = null; root.classList.add('c2c-sp-idle'); }, 2500);
  }

  function idleReset(root) {            // Aktivitaet: Leiste zeigen und neu anlaufen lassen
    root.classList.remove('c2c-sp-idle');
    if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
    armIdle(root);
  }

  function toggle(root) {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    if (root.requestFullscreen) root.requestFullscreen().catch(function () { /* vom Browser abgelehnt */ });
  }

  function attach(root) {
    if (root.getAttribute('data-fs-ready')) return;
    root.setAttribute('data-fs-ready', '1');
    var controls = root.querySelector('.c2c-sp-controls');
    if (!controls) return;
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-outline-info c2c-sp-fs';
    button.textContent = '⛶ Vollbild';
    button.title = 'Vollbild (Taste F, Doppelklick aufs Bild)';
    button.addEventListener('click', function () { toggle(root); });
    controls.appendChild(button);
    root.addEventListener('dblclick', function (ev) { if (ev.target && ev.target.tagName === 'IMG') toggle(root); });
    root.addEventListener('keydown', function (ev) {
      var tag = ev.target && ev.target.tagName;
      if (tag === 'SELECT' || (tag === 'INPUT' && ev.target.type !== 'range' && ev.target.type !== 'radio')) return;
      var p = player();
      if (ev.key === 'f' || ev.key === 'F') { ev.preventDefault(); toggle(root); }
      else if ((ev.key === ' ' || ev.key === 'Spacebar') && p) {
        ev.preventDefault();
        if (playing()) p.pause(); else p.play();
      }
      idleReset(root);
    });
    ['mousemove', 'pointerdown', 'touchstart'].forEach(function (name) { root.addEventListener(name, function () { idleReset(root); }); });
    document.addEventListener('fullscreenchange', function () {
      var on = document.fullscreenElement === root;
      button.textContent = on ? '⮌ Vollbild beenden' : '⛶ Vollbild';
      if (on) root.focus();
      idleReset(root);
    });
  }

  function scan() {
    Array.prototype.forEach.call(document.querySelectorAll('.c2c-sp'), attach);
    var root = document.fullscreenElement;
    if (root && root.classList && root.classList.contains('c2c-sp')) armIdle(root);   // Abspielen hat begonnen (Taste, Knopf)
  }

  setInterval(scan, 700);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
})();
