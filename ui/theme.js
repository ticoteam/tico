/* ui/theme.js — The colour theme, set on <html data-theme> before the page paints (styles/tokens.css).
   Dark unless this browser saved Light or System; Settings changes it through window.ticoTheme. Also the sidebar rows
   that wait on this browser's memory or a server's answer, marked on <html> before the paint (see the end). */
'use strict';
// The first boot timing mark (ui/app/boot-timing.js): the page's HTML has arrived and its head is running.
try { performance.mark('tico:head'); } catch { /* no User Timing */ }
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
// The sidebar as this browser last left it, so the first paint is the rail that stays (index.html): Company and Library
// open unless folded here, Rules folded unless opened here, each open on its own pages (ui/app/redesign.js rdLibSync, the same keys),
// the Team section's fold, and Finance and Finish setup as the last answer for whoever signed in here last said
// (redesign.js rdNavShow, which corrects them once this visit's answer is in, and forgets them for anyone else and on
// sign-out). The app's code keeps each mark with its row from then on.
(() => {
  const root = document.documentElement, hash = location.hash.split('?')[0];
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const under = base => hash === base || hash.startsWith(base + '/');
  root.classList.toggle('nav-co-shut', read('tico.nav.company') === 'closed' && !['#/strategy', '#/growth', '#/finance'].some(under));
  root.classList.toggle('nav-lib-shut', read('tico.nav.library') === 'closed' && !['#/docs', '#/meetings', '#/library'].some(under));
  root.classList.toggle('nav-rules-open', read('tico.nav.rules') === 'open' || under('#/rules'));
  // The Team section folded here (ui/app/sidebar.js renderNavSections).
  let folded = [];
  try { folded = JSON.parse(read('hub.nav.collapsed') || '[]'); } catch { /* unreadable: nothing folded */ }
  root.classList.toggle('nav-team-shut', Array.isArray(folded) && folded.includes('organisation'));
  let kept = null;
  try { kept = JSON.parse(read('tico.nav.kept') || 'null'); } catch { /* unreadable: nothing kept */ }
  const finance = kept?.finance === true, welcome = kept?.welcome === true;
  root.classList.toggle('nav-finance', finance);
  root.classList.toggle('nav-welcome', welcome);
  // Setup's own page shows only Finish setup and the team (notices.js renderOnboardingNav); a new install opens on it.
  const setup = welcome && ['', '#/', '#/welcome'].includes(hash);
  root.classList.toggle('in-setup', setup);
  // Ask's row is away on the Assistant page and in setup (ui/app/ask-panel.js askSync keeps the mark after).
  root.classList.toggle('ask-away', setup || location.hash === '#/assistant');
})();
