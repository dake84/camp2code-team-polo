// Paket G: Vollbild fuer Bilder/Streams. Ein Button mit Klasse "c2c-fs" und
// data-target="<id>" schaltet das Element mit dieser id in den Vollbildmodus.
(function () {
  document.addEventListener('click', function (event) {
    var button = event.target.closest('.c2c-fs');
    if (!button) return;
    var target = document.getElementById(button.getAttribute('data-target'));
    if (!target) return;
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    if (target.requestFullscreen) target.requestFullscreen();
  });
})();
