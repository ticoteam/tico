/* ui/app/parts.ts — Page parts: a page's code, fetched the first time a person goes there
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.

   A cold open downloads only the shell; the files ui/parts.json puts in a page part are served as that part alone
   (backend/ui_bundle.py), and the page lists each part's address in a JSON block (#ui-parts). The shell's first script
   declares a stand-in for each name a part lets others call: a page or a call comes here, which loads the part (with
   the parts it needs) and calls the real function, the part's own declaration having taken the stand-in's place. A part that
   does not load leaves the page with Retry in its main area (the sidebar keeps working), or, when a release changed
   the app since this page was opened (its old part address is gone), reloads to the current release. Served as
   separate files (TICO_UI_BUNDLE=off) the page has no list: every file is already loaded and nothing here runs. */
'use strict';

interface UiPartsTable { build: string; parts: Record<string, {url: string; load: string[]}>; }
const UI_PARTS: UiPartsTable = (() => {
  try {
    const text = document.getElementById('ui-parts')?.textContent;
    if (text) return JSON.parse(text) as UiPartsTable;
  } catch { /* no list: the files are all loaded */ }
  return {build: '', parts: {}};
})();
const UI_PART_SCRIPTS = new Map<string, Promise<void>>();
// The build a failed part reloaded the page for: once per build, so a part that never loads cannot loop.
const UI_PART_RELOAD_KEY = 'tico.parts.reloaded';

// One part's script, asked for once however many callers want it; a failure is forgotten, so Retry asks again.
// Scripts added here run in the order they were added (async off), so a part runs after the parts it lists first.
function uiPartScript(name: string): Promise<void> {
  const part = UI_PARTS.parts[name];
  if (!part) return Promise.resolve();
  let loading = UI_PART_SCRIPTS.get(name);
  if (loading) return loading;
  loading = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = part.url;
    script.async = false;
    script.dataset.part = name;
    script.onload = () => { resolve(); };
    script.onerror = () => { script.remove(); reject(new Error(`Tico's ${name} code did not load`)); };
    document.head.appendChild(script);
  });
  loading.catch(() => { UI_PART_SCRIPTS.delete(name); });
  UI_PART_SCRIPTS.set(name, loading);
  return loading;
}

// A part with every part it needs (`load`: its deps, theirs, and itself, in ui/parts.json's order).
function uiPartLoad(name: string): Promise<void> {
  const part = UI_PARTS.parts[name];
  return part ? Promise.all(part.load.map(uiPartScript)).then(() => undefined) : Promise.resolve();
}

// What a stand-in calls (backend/ui_bundle.py stubs): `fn` of `part`, once that is loaded. A page (`page`, called by
// the router) is drawn only while the person is still on the route it was asked for.
function uiPartCall(part: string, fn: string, args: unknown[], page: boolean): Promise<unknown> {
  const route = typeof S === 'undefined' ? '' : S.route;
  return uiPartLoad(part).then(() => {
    if (page && typeof S !== 'undefined' && S.route !== route) return undefined;
    const real = (window as unknown as Record<string, unknown>)[fn];
    if (typeof real !== 'function' || (typeof UI_PART_STUBS !== 'undefined' && UI_PART_STUBS.has(real))) {
      throw new Error(`${fn} is not in Tico's ${part} code`);
    }
    return (real as (...a: unknown[]) => unknown)(...args);
  }, async (error: unknown) => {
    await uiPartFailed(part, page, route, error);
    return undefined;
  });
}

// A part that did not load: a release since this page was opened reloads it (its route opens on the new release);
// otherwise a page shows Retry where it would be, and a call says so in a toast.
async function uiPartFailed(part: string, page: boolean, route: string, error: unknown): Promise<void> {
  let build = '';
  try { build = String((await get<AppConfig>('/v2/config', {fresh: true})).ui_build || ''); } catch { /* offline: Retry below */ }
  let reloaded = '';
  try { reloaded = sessionStorage.getItem(UI_PART_RELOAD_KEY) || ''; } catch { /* no storage */ }
  if (build && UI_PARTS.build && build !== UI_PARTS.build && reloaded !== build) {
    try { sessionStorage.setItem(UI_PART_RELOAD_KEY, build); } catch { /* no storage */ }
    location.reload();
    return;
  }
  console.warn(errorMessage(error, `Tico's ${part} code did not load`));
  if (!page) { toast("Couldn't load this part of Tico. Check your connection and try again.", true); return; }
  if (typeof S !== 'undefined' && S.route !== route) return;
  const main = $('#main') as HTMLElement | null;
  if (!main) return;
  // The page's empty state (ui/styles/components.css), in the main area only: the sidebar keeps working.
  main.innerHTML = `<section class="empty-state part-error" role="alert"><b>Couldn't load this page.</b>`
    + `<span>Check your connection and try again.</span><button class="primary" type="button" data-part-retry>Retry</button></section>`;
}

document.addEventListener('click', ev => {
  if (!eventElement(ev)?.closest('[data-part-retry]')) return;
  const button = eventElement(ev)?.closest<HTMLButtonElement>('[data-part-retry]');
  if (button) button.disabled = true;
  route();
});
