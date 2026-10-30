/* ui/theme.js — The colour theme, set on <html data-theme> before the page paints (styles/tokens.css).
   Dark unless this browser saved Light or System; Settings changes it through window.ticoTheme. */
'use strict';
(() => {
  const KEY = 'tico.theme', CHOICES = ['dark', 'light', 'system'];
  const saved = () => { try { const v = localStorage.getItem(KEY); return CHOICES.includes(v) ? v : 'dark'; } catch { return 'dark'; } };
  const apply = v => { document.documentElement.dataset.theme = v; };
  apply(saved());
  // Another tab of the same browser changed it: follow.
  addEventListener('storage', ev => { if (ev.key === KEY) apply(saved()); });
  window.ticoTheme = {
    choices: CHOICES,
    get: saved,
    set(v) {
      if (!CHOICES.includes(v)) return;
      try { localStorage.setItem(KEY, v); } catch { /* private window: it holds until the tab closes */ }
      apply(v);
    },
  };
})();
// The icon font swaps in rather than blocking, so its ligature names would show as words while it loads: base.css hides
// icon glyphs until this marks the page. They show once the font settles (loaded, or failed), or after 10s: a slow first
// load leaves holes rather than words ("bolt" read "ol" in a 20px box), and the glyphs swap in when the font lands.
(() => {
  const ready = () => document.documentElement.classList.add('icons-ready');
  if (!document.fonts?.load) { ready(); return; }
  setTimeout(ready, 10000);
  // the @font-face rule exists once the stylesheets are parsed; the font itself is preloaded by index.html. A load that
  // found no face (the stylesheet not in yet) asks again once the page has loaded.
  const load = () => document.fonts.load('20px "Material Symbols Outlined"', 'bolt');
  const check = () => load().then(faces => {
    if (faces.length || document.readyState === 'complete') ready();
    else addEventListener('load', () => load().then(ready, ready), {once: true});
  }, ready);
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', check, {once: true}); else check();
})();
