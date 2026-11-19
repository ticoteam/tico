/* ui/theme.ts — The colour theme, set on <html data-theme> before the page paints (styles/tokens.css).
   Dark unless this browser saved Light or System; Settings changes it through window.ticoTheme. Also the Sheets look
   (the class `sheets` on <html>, off unless this browser saved it on; window.ticoSheets), and the sidebar rows
   that wait on this browser's memory or a server's answer, marked on <html> before the paint (see the end). */
'use strict';
// The first boot timing mark (ui/app/boot-timing.js): the page's HTML has arrived and its head is running.
try { performance.mark('tico:head'); } catch { /* no User Timing */ }
// Until every stylesheet and the app have loaded, the page's own recovery card covers the unfinished shell.
// This script is outside the app bundle: a failed bundle still leaves a working Reload and support link.
(() => {
  let appLoaded = false, failed = false;
  const check = (): void => {
    const styles = [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')];
    const ready = appLoaded && !failed && styles.length > 0 && styles.every(link => link.sheet !== null);
    const card = document.getElementById('asset-recovery');
    if (card) card.hidden = ready;
    if (!ready && document.readyState === 'complete') {
      const title = document.getElementById('asset-recovery-h');
      if (title) title.textContent = 'Tico couldn’t finish loading';
    }
  };
  // Only Tico's own files count. A script the network adds (Cloudflare's analytics beacon, which the page's
  // script-src 'self' blocks) or an extension's script failing says nothing about whether Tico loaded.
  const own = (url: string): boolean => { try { return new URL(url, location.href).origin === location.origin; } catch { return false; } };
  addEventListener('error', event => {
    const target = event.target;
    // A page part fetched later (data-part, ui/app/parts.ts) that fails shows Retry in its page, not this card.
    if (target instanceof HTMLScriptElement && own(target.src) && !target.dataset.part
        || target instanceof HTMLLinkElement && target.rel === 'stylesheet' && own(target.href)
        || !appLoaded && event instanceof ErrorEvent && /\/tico\/ui\//.test(event.filename)) {
      failed = true; check();
    }
  }, true);
  addEventListener('tico:app-loaded', () => { appLoaded = true; check(); });
  addEventListener('load', check, {once: true});
})();
(() => {
  const KEY = 'tico.theme', CHOICES = ['dark', 'light', 'system'];
  // The Sheets look (styles/tokens.css, html.sheets): off unless this browser saved it on. It has no control in
  // Settings: ?sheets=1 or ?sheets=0 in the page's address saves the choice, and window.ticoSheets changes it.
  const LOOK = 'tico.sheets';
  // The browser's own bar in the Sheets look: its ground in each theme (tokens.css --ground). The stylesheets are not
  // in yet when this runs, so the two values are repeated here.
  const GROUND = {dark: '#0B0B0B', light: '#E9E9E9'};
  const root = document.documentElement;
  const bar = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  const plainBar = bar?.content ?? '';
  const lightOs = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: light)') : null;
  const saved = () => { try { const v = localStorage.getItem(KEY) ?? 'dark'; return CHOICES.includes(v) ? v : 'dark'; } catch { return 'dark'; } };
  const savedLook = (): boolean => { try { return localStorage.getItem(LOOK) === '1'; } catch { return false; } };
  // The theme as it shows, with System resolved to the computer's own: <html data-scheme>. The Sheets tokens are
  // written once for dark and once for light against it, so System needs no copy of the light values.
  const show = () => {
    const theme = root.dataset.theme;
    const scheme = theme === 'light' || theme === 'system' && lightOs?.matches === true ? 'light' : 'dark';
    root.dataset.scheme = scheme;
    if (bar) bar.content = root.classList.contains('sheets') ? GROUND[scheme] : plainBar;
  };
  const apply = (v: string) => { root.dataset.theme = v; show(); };
  const applyLook = (on: boolean) => { root.classList.toggle('sheets', on); show(); };
  const keepLook = (on: boolean) => {
    try { if (on) localStorage.setItem(LOOK, '1'); else localStorage.removeItem(LOOK); } catch { /* private window: it holds until the tab closes */ }
    applyLook(on);
  };
  apply(saved());
  let asked: string | null = null;
  try { asked = new URLSearchParams(location.search).get('sheets'); } catch { /* an address that does not parse asks for nothing */ }
  if (asked === '1' || asked === '0') keepLook(asked === '1'); else applyLook(savedLook());
  // Another tab of the same browser changed it: follow.
  addEventListener('storage', ev => {
    if (ev.key === KEY) apply(saved());
    if (ev.key === LOOK) applyLook(savedLook());
  });
  // The computer changed between light and dark while the theme is System.
  lightOs?.addEventListener?.('change', show);
  window.ticoTheme = {
    choices: CHOICES,
    get: saved,
    set(v: string) {
      if (!CHOICES.includes(v)) return;
      try { localStorage.setItem(KEY, v); } catch { /* private window: it holds until the tab closes */ }
      apply(v);
    },
  };
  window.ticoSheets = {
    get: () => root.classList.contains('sheets'),
    set(on: boolean) { keepLook(on === true); },
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
// folded unless opened here, Rules folded unless opened here, each open on its own pages (ui/app/redesign.ts rdLibSync, the same keys),
// the Team section's fold, and Finance and Finish setup as the last answer for whoever signed in here last said
// (redesign.js rdNavShow, which corrects them once this visit's answer is in, and forgets them for anyone else and on
// sign-out). The app's code keeps each mark with its row from then on.
(() => {
  const root = document.documentElement, hash = location.hash.split('?')[0] ?? '';
  const read = (key: string) => { try { return localStorage.getItem(key); } catch { return null; } };
  const under = (base: string) => hash === base || hash.startsWith(base + '/');
  root.classList.toggle('nav-co-shut', read('tico.nav.company') !== 'open' && !['#/strategy', '#/growth', '#/finance', '#/automations', '#/listening', '#/rules'].some(under));
  const inLibrary = ['#/docs', '#/tools', '#/library'].some(under);
  root.classList.toggle('nav-lib-shut', read('tico.nav.library') !== 'open' && !inLibrary);
  // The Team section folded here (ui/app/sidebar.ts renderNavSections).
  let folded = [];
  try { folded = JSON.parse(read('hub.nav.collapsed') || '[]'); } catch { /* unreadable: nothing folded */ }
  root.classList.toggle('nav-team-shut', Array.isArray(folded) && folded.includes('organisation'));
  let kept = null;
  try { kept = JSON.parse(read('tico.nav.kept') || 'null'); } catch { /* unreadable: nothing kept */ }
  const finance = kept?.finance === true, welcome = kept?.welcome === true;
  root.classList.toggle('nav-finance', finance);
  root.classList.toggle('nav-welcome', welcome);
  // Setup is a modal over the app (ui/app/welcome.ts), with no tab bar or Ask under it; a new install opens on it.
  const setup = welcome && ['', '#/', '#/welcome'].includes(hash);
  root.classList.toggle('in-setup', setup);
  // Ask's row is away on the Assistant page and in setup (ui/app/ask-panel.ts askSync keeps the mark after).
  root.classList.toggle('ask-away', setup || location.hash === '#/assistant');
})();
