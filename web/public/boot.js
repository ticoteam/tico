// Before the first paint: set the theme and motion the person chose, so the page never flashes the other theme.
// A file, not an inline script: the server's page policy refuses inline scripts. Keys match src/prefs/prefs.ts.
(function () {
  var root = document.documentElement;
  function read(key) {
    try {
      var raw = localStorage.getItem('tico-next:' + key);
      return raw == null ? null : JSON.parse(raw);
    } catch {
      return null;
    }
  }
  // Dark unless the person chose light, or chose to follow the system and the system is light.
  var theme = read('theme.v1');
  if (theme === 'system') theme = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  if (theme !== 'light') theme = 'dark';
  root.setAttribute('data-theme', theme);
  // Motion as src/motion/level.ts resolves it: ?motion= wins, then the person's level, else the system's (reduced = off).
  var levels = ['more', 'less', 'off'];
  var motion = new URLSearchParams(location.search).get('motion');
  if (levels.indexOf(motion) < 0) motion = read('motion.v1');
  if (levels.indexOf(motion) < 0) motion = matchMedia('(prefers-reduced-motion: reduce)').matches ? 'off' : 'more';
  root.setAttribute('data-motion', motion);
  // Phone cards or sheets, as src/shell/layout.ts modeFor decides (CARDS_AT, SHEETS_AT): every phone style switches on
  // this one attribute, so it is set before the first paint and kept on resize for screens with no frame (sign-in,
  // the desktop's call window). The frame writes the same value from the same rule.
  var CARDS_AT = 760, SHEETS_AT = 800;
  function layout() {
    var w = innerWidth, was = root.getAttribute('data-layout');
    root.setAttribute('data-layout', w <= CARDS_AT || (was === 'cards' && w < SHEETS_AT) ? 'cards' : 'sheets');
  }
  layout();
  addEventListener('resize', layout);
})();
