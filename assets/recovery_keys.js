// Tastaturzustand nur bei bewusst aktiviertem Bergungsbereich weitergeben.
(function () {
  const pressed = new Set();
  function publish() {
    if (!window.dash_clientside || !window.dash_clientside.set_props) return;
    const panel = document.getElementById('recovery-panel');
    if (!panel || !panel.classList.contains('show')) return;
    window.dash_clientside.set_props('recovery-keys', {data: {
      w: pressed.has('w'), s: pressed.has('s'), a: pressed.has('a'),
      d: pressed.has('d'), deadman: pressed.has(' ')
    }});
  }
  window.addEventListener('keydown', function (event) {
    if (event.target.closest('input,textarea,select,[contenteditable=true]')) return;
    if (['w','s','a','d',' '].includes(event.key.toLowerCase())) {
      event.preventDefault(); pressed.add(event.key.toLowerCase()); publish();
    }
  });
  window.addEventListener('keyup', function (event) {
    pressed.delete(event.key.toLowerCase()); publish();
  });
  window.addEventListener('blur', function () { pressed.clear(); publish(); });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { pressed.clear(); publish(); }
  });
})();
