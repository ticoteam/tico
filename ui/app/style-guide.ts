/* ui/app/style-guide.ts — the style guide page (#/style-guide): ui/style-guide/guide.html and its stylesheet
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   The guide is a reference page reached by its address, so it is not in the navigation. Its body and stylesheet are
   generated outside the app and served as they are from /tico/ui/style-guide/; the stylesheet is linked the first time
   the page opens, so the other pages never load it. */
'use strict';

const STYLE_GUIDE = '#/style-guide';
const STYLE_GUIDE_FILES = '/tico/ui/style-guide/';
let STYLE_GUIDE_LOAD = 0, STYLE_GUIDE_CSS: Promise<void> | null = null;

function styleGuideRoute(r: string): boolean {
  if ((r.split('?')[0] ?? '') !== STYLE_GUIDE) return false;
  void pageStyleGuide();
  return true;
}

// The stylesheets are linked once; a failed load lets the next open try again.
function styleGuideStyles(): Promise<void> {
  if (STYLE_GUIDE_CSS) return STYLE_GUIDE_CSS;
  STYLE_GUIDE_CSS = styleGuideLink('guide.css');
  STYLE_GUIDE_CSS.catch(() => { STYLE_GUIDE_CSS = null; });
  return STYLE_GUIDE_CSS;
}

function styleGuideLink(file: string): Promise<void> {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = STYLE_GUIDE_FILES + file;
  link.dataset.styleGuide = '';
  return new Promise<void>((resolve, reject) => {
    link.onload = () => resolve();
    link.onerror = () => { link.remove(); reject(new Error('The style guide stylesheet did not load.')); };
    document.head.append(link);
  });
}

async function pageStyleGuide(): Promise<void> {
  const load = ++STYLE_GUIDE_LOAD;
  pageTitle('Style guide');
  $('#main').innerHTML = '<div class="sg-page"><div class="empty">Loading…</div></div>';
  let html: string;
  try {
    const [res] = await Promise.all([fetch(STYLE_GUIDE_FILES + 'guide.html', {credentials: 'same-origin'}), styleGuideStyles()]);
    if (!res.ok) throw new Error(`The style guide did not load (${res.status}).`);
    html = await res.text();
  } catch (e) {
    if (load !== STYLE_GUIDE_LOAD || S.route.split('?')[0] !== STYLE_GUIDE) return;
    $('#main').innerHTML = `<div class="sg-page"><div class="err">${esc(errorMessage(e, 'The style guide did not load.'))}</div></div>`;
    return;
  }
  if (load !== STYLE_GUIDE_LOAD || S.route.split('?')[0] !== STYLE_GUIDE) return;
  const root = document.createElement('div');
  root.className = 'sg-page';
  root.innerHTML = html;
  styleGuideWire(root);
  $('#main').replaceChildren(root);
}

function styleGuideWire(root: HTMLElement): void {
  // Today and Proposed: each [data-rv-tabs] box switches its own panels. A mockup inside a panel may draw tabs of its
  // own, so only the box's own tab bar and panels count.
  for (const box of root.querySelectorAll<HTMLElement>('[data-rv-tabs]')) {
    const tabs = [...box.querySelectorAll<HTMLButtonElement>(':scope > [role=tablist] > [role=tab]')];
    const panels = [...box.querySelectorAll<HTMLElement>(':scope > [role=tabpanel][data-panel]')];
    const show = (tab: HTMLButtonElement): void => {
      for (const t of tabs) { const on = t === tab; t.setAttribute('aria-selected', String(on)); t.classList.toggle('cur', on); }
      for (const p of panels) p.hidden = p.dataset.panel !== tab.dataset.tab;
    };
    const first = tabs.find(t => t.getAttribute('aria-selected') === 'true') ?? tabs[0];
    if (first) show(first);
    for (const tab of tabs) tab.addEventListener('click', () => show(tab));
  }
  root.addEventListener('click', ev => {
    // Section links (#sg-…) scroll within the page: the address stays #/style-guide, which the router owns.
    const link = ev.target instanceof Element ? ev.target.closest<HTMLAnchorElement>('a[href^="#sg-"]') : null;
    if (!link) return;
    ev.preventDefault();
    root.querySelector<HTMLElement>(`[id="${CSS.escape(link.hash.slice(1))}"]`)?.scrollIntoView({block: 'start'});
  });
  // The sample forms show controls only: nothing is sent.
  root.addEventListener('submit', ev => ev.preventDefault());
}
