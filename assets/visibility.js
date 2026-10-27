// Pausiert das Dashboard-Polling, solange der Browser-Tab nicht sichtbar ist.
// Hintergrund: Alle Tabs eines Browserprofils teilen sich wenige parallele
// HTTP-Verbindungen je Host (Chrome: 6). Alte, noch pollende Tabs koennen das
// Laden eines neuen Tabs blockieren (Verdacht fuer den Browser-Neustart-Fehler).
// 'refresh.disabled' steuert ueber den Callback tab_intervals auch die Ansichtstakte.
(function () {
  function apply() {
    if (!window.dash_clientside || !window.dash_clientside.set_props) return;
    window.dash_clientside.set_props('refresh', {disabled: document.hidden});
  }
  document.addEventListener('visibilitychange', apply);
  window.addEventListener('pageshow', apply);
})();
