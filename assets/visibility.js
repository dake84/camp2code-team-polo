// Pausiert das Dashboard-Polling, solange der Browser-Tab nicht sichtbar ist.
// Hintergrund: Alle Tabs eines Browserprofils teilen sich wenige parallele
// HTTP-Verbindungen je Host (Chrome: 6). Alte, noch pollende Tabs koennen das
// Laden eines neuen Tabs blockieren (Verdacht fuer den Browser-Neustart-Fehler).
// 'refresh.disabled' steuert ueber den Callback tab_intervals auch die Ansichtstakte.
//
// Layout-Wettlauf (Version 6.7): 'pageshow' kann vor dem Rendern des Layouts eintreffen; dash_clientside.set_props
// wirft dann "Cannot read properties of undefined". Gesetzt wird daher erst, wenn das Layout steht
// ('main-tabs' im DOM); davor wird alle 200 ms (hoechstens 10 s) erneut versucht.
(function () {
  function apply() {
    if (!window.dash_clientside || !window.dash_clientside.set_props) return false;
    if (!document.getElementById('main-tabs')) return false;
    try {
      window.dash_clientside.set_props('refresh', {disabled: document.hidden});
      return true;
    } catch (error) {
      return false;
    }
  }
  function applyWhenReady() {
    var tries = 0;
    var timer = setInterval(function () {
      if (apply() || ++tries > 50) clearInterval(timer);
    }, 200);
  }
  document.addEventListener('visibilitychange', apply);
  window.addEventListener('pageshow', applyWhenReady);
})();
