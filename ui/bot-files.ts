/* The Files section of a bot's right rail (docs/files.md): what the bot created, revised or delivered,
   newest activity first, one row each: a small mark (its kind's icon, or an image's thumbnail) and its name without
   the extension. Three rows; "N more" opens the rest inline, a page at a time, with a search box and the filter button
   (Library kinds) above them; the server narrows the list as it is typed, still newest first, still paged. "Less" folds
   it back to the three newest and forgets the search. No files, no section. A stored file and a Google file open in Tico's review view
   (deps.openFile, ui/app/file-review.ts), any other linked document at its provider in a new tab (the provider decides
   who may open it). Adding a link, promoting and removing
   are the API and the bot's own tools (PATCH /api/v2/files/{id}), not buttons here.
   ui/app/bot-page.ts calls window.botFiles.mount(host, deps) once per bot page; deps keeps this file free
   of the page's globals (get, esc, openFile). */
'use strict';

// The deps mount() takes from its page: the page's own get and esc, and openFileLink (ui/app/viewer.js).
interface BotFilesDeps {
  slug: string;
  get: <T>(path: string) => Promise<T>;
  esc: (s: unknown) => string;
  openFile: (id: string, name: string) => unknown;
}

// What one mounted Files section shows: the rows fetched so far, the total, and where the next page starts.
interface BotFilesState {
  rows: import('./types/api').BotFile[];
  total: number;
  cursor: string | null;
  expanded: boolean;
  error: string;
  q: string;
  kind: string;
  seq: number;
}

interface Window {
  botFiles?: {mount(host: HTMLElement | null, deps: BotFilesDeps): void};
}

(function () {
  const SHORT = 3, PAGE = 20, TYPING_MS = 250;
  let mounted = 0;

  // The mark for each kind of file (clients/bot_files.py KINDS), a page and a link; anything else is a plain file.
  const BF_ICONS: Record<string, string> = {html: 'article', document: 'description', spreadsheet: 'table_chart', image: 'image',
    video: 'movie', audio: 'graphic_eq', remote_link: 'link'};

  // One row as the rail's page row (ui/styles/pages.css .pg-row): 32px, a 24px mark, the name on one line.
  const css = `
.bot-files .bf-row{display:flex;align-items:center;gap:8px;min-height:32px;padding:0 6px;margin:0 -6px;border-radius:6px;font-size:13px;line-height:18px;color:var(--ink);text-decoration:none}
.bot-files a.bf-row:hover{background:var(--hover);color:var(--ink);text-decoration:none}
.bot-files a.bf-row:focus-visible{outline:var(--focus-ring);outline-offset:var(--focus-inset)}
.bot-files .bf-mark{flex:none;display:inline-grid;place-items:center;width:24px;height:24px}
.bot-files .bf-mark .nav-icon{font-size:16px;width:16px;height:16px;color:var(--muted)}
.bot-files .bf-thumb{display:block;width:24px;height:24px;object-fit:cover;border-radius:4px;box-shadow:inset 0 0 0 1px var(--line)}
.bot-files .bf-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink)}
.bot-files .bf-foot{display:flex;gap:12px;align-items:center;padding-top:2px}
.bot-files .bf-err{color:var(--fail);font-size:12.5px}
.bot-files .bf-tools{display:flex;gap:6px;align-items:center;margin:2px 0 6px}
.bot-files .bf-tools[hidden]{display:none}
.bot-files .bf-tools .lf-search{flex:1;margin-left:0}
.bot-files .bf-none{font-size:12.5px;color:var(--muted);padding:6px 0}
.bf-pop{width:200px}
.bf-pop [aria-checked=false] .tl-mi-on{visibility:hidden}
@media (max-width:760px){.bot-files .bf-row{min-height:44px}}`;

  // The name a row shows: a link's title as written; a stored file's title without its folder when the title is the
  // stored file's path, and without its extension when that is the stored file's own (reports/ticket-topics.csv named
  // ticket-topics.csv reads "ticket-topics"). A title a bot gave ("Q3 plan / draft") is never split at its slash.
  const shownName = (f: import('./types/api').BotFile): string => {
    const title = String(f.title || f.name || '');
    if (f.locator === 'remote_link') return title;
    const name = String(f.name || ''), dot = name.lastIndexOf('.');
    const base = name && title.endsWith('/' + name) ? name : title;
    const ext = dot > 0 ? name.slice(dot).toLowerCase() : '';
    return ext && base.toLowerCase().endsWith(ext) && base.length > ext.length ? base.slice(0, -ext.length) : base;
  };
  const markKey = (f: import('./types/api').BotFile): string => f.locator === 'remote_link' ? 'remote_link'
    : (f.mime || '').split(';')[0]!.trim().toLowerCase() === 'text/html' || /\.html?$/i.test(f.name || '') ? 'html' : f.kind;
  const iconHTML = (name: string) => `<span class="nav-icon" aria-hidden="true">${name}</span>`;

  const GOOGLE_FILE = /^https:\/\/(docs|drive)\.google\.com\//;
  const httpsOnly = (url: unknown): string => { try { const text = String(url); return new URL(text).protocol === 'https:' ? text : ''; } catch { return ''; } };

  function mount(host: HTMLElement | null, deps: BotFilesDeps): void {
    if (!host) return;
    if (!document.getElementById('bot-files-style')) {
      const style = document.createElement('style');
      style.id = 'bot-files-style';
      style.textContent = css;
      document.head.append(style);
    }
    const {slug, get, esc, openFile} = deps;
    const state: BotFilesState = {rows: [], total: 0, cursor: null, expanded: false, error: '', q: '', kind: '', seq: 0};
    const base = `/v2/bots/${encodeURIComponent(slug)}/files`;
    const narrowed = (): boolean => !!(state.q || state.kind);

    const load = async (more?: boolean): Promise<void> => {
      const seq = ++state.seq;
      try {
        const query = new URLSearchParams({limit: String(state.expanded ? PAGE : SHORT)});
        if (state.expanded && state.q) query.set('q', state.q);
        if (state.expanded && state.kind) query.set('kind', state.kind);
        if (more && state.cursor) query.set('cursor', state.cursor);
        const page = await get<import('./types/api').BotFileList>(`${base}?${query}`);
        // An answer to an older search or filter is not drawn over the newer one's.
        if (!host.isConnected || seq !== state.seq) return;
        const rows = page.files || [];
        state.rows = more ? state.rows.concat(rows) : rows;
        state.total = page.total || 0; state.cursor = page.next_cursor; state.error = '';
      } catch (e) { state.error = errorMessage(e, 'Could not load the files.'); }
      render();
    };

    function row(f: import('./types/api').BotFile): string {
      const link = f.locator === 'remote_link';
      const url = link ? httpsOnly(f.open?.url) : f.open?.url;
      const icon = BF_ICONS[markKey(f)] || 'draft';
      const mark = f.kind === 'image' && f.thumb_blob_id && !link
        ? `<img class="bf-thumb" alt="" src="/api/v2/files/${encodeURIComponent(f.id)}/thumb?v=${encodeURIComponent(String(f.version))}">`
        : iconHTML(icon);
      const inner = `<span class="bf-mark" data-bf-icon="${icon}">${mark}</span><span class="bf-name">${esc(shownName(f))}</span>`;
      const tip = `data-file="${esc(f.id)}" title="${esc(f.title)}"`;
      // A file that never synced has nothing to open: a plain row.
      return !url ? `<div class="bf-row" ${tip}>${inner}</div>` : link
        ? `<a class="bf-row" ${tip} href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-bf-open="external"${GOOGLE_FILE.test(String(url)) ? ` data-bf-id="${esc(f.id)}" data-bf-file="${esc(f.title)}"` : ''}>${inner}</a>`
        : `<a class="bf-row" ${tip} href="${esc(url)}" data-bf-open="tico" data-bf-id="${esc(f.id)}" data-bf-file="${esc(f.name || f.title)}">${inner}</a>`;
    }

    // The parts stay put: the search box keeps its focus and what is typed while the rows under it are drawn again.
    // The search mark, the filter mark and the kinds are Library › Files' own (task-list-menus.ts, core.ts, pages.ts).
    const popId = `bf-pop-${++mounted}`;
    host.innerHTML = `<h2 class="rail-h">Files</h2>
      <div class="bf-tools" hidden><div class="lf-search">${TL_ICON.search}<input class="bf-q" type="search" placeholder="Search" aria-label="Search files" autocomplete="off" spellcheck="false" maxlength="200"></div>
        <button type="button" class="lf-fbtn bf-fbtn" popovertarget="${popId}" aria-haspopup="true" aria-expanded="false" aria-label="Filters" title="Filters">${filterIcon(16)}</button>
        <div class="tl-pop bf-pop" id="${popId}" popover></div></div>
      <div class="bf-body"></div>`;
    const tools = host.querySelector<HTMLElement>('.bf-tools')!, body = host.querySelector<HTMLElement>('.bf-body')!;
    const search = host.querySelector<HTMLInputElement>('.bf-q')!, filterBtn = host.querySelector<HTMLElement>('.bf-fbtn')!;
    const pop = host.querySelector<HTMLElement>('.bf-pop')!;
    // The filter's kinds as a menu of Library's own: one is in force, "All kinds" is none.
    const popHTML = (): string => `<div class="tl-menu" role="menu" aria-label="Kind">${LF_KINDS.map(([value = '', label = '']) =>
      `<button type="button" class="tl-mi" role="menuitemradio" aria-checked="${state.kind === value}" data-bf-kind="${esc(value)}">${esc(label)}<span class="tl-mi-on" aria-hidden="true">✓</span></button>`).join('')}</div>`;

    let drawn = '';
    const render = (): void => {
      const shown = state.expanded ? state.rows : state.rows.slice(0, SHORT);
      const hasMore = state.expanded && state.cursor;
      const left = Math.max(0, state.total - shown.length);
      // Nothing to list is no section at all; an error still says so, and a search that finds nothing keeps its box.
      host.hidden = !shown.length && !state.error && !narrowed();
      tools.hidden = !state.expanded;
      filterBtn.classList.toggle('active', !!state.kind);
      filterBtn.setAttribute('aria-label', state.kind ? 'Filters, 1 active' : 'Filters');
      const html = `${state.error ? `<div class="bf-err" role="alert">${esc(state.error)}</div>` : ''}
        ${shown.map(row).join('')}
        ${!shown.length && narrowed() && !state.error ? '<div class="bf-none">Nothing matches.</div>' : ''}
        ${state.total > SHORT || hasMore || state.expanded ? `<div class="bf-foot">${state.expanded
          ? `${hasMore ? `<button class="rail-more" type="button" data-bf-more>${left} more</button>` : ''}<button class="rail-more" type="button" data-bf-less>Less</button>`
          : `<button class="rail-more" type="button" data-bf-all>${left} more</button>`}</div>` : ''}`;
      // The minute's reload draws only what changed, so a thumbnail is not asked for again each time
      // (its route answers private, no-store).
      if (html === drawn) return;
      drawn = html;
      body.innerHTML = html;
      // A thumbnail that does not load (gone, 404) shows its kind's icon instead.
      for (const img of body.querySelectorAll<HTMLImageElement>('img.bf-thumb')) {
        const fallback = () => { img.replaceWith(document.createRange().createContextualFragment(iconHTML('image'))); };
        img.addEventListener('error', fallback, {once: true});
        if (img.complete && !img.naturalWidth) fallback();
      }
    };

    let typing = 0;
    search.oninput = () => {
      clearTimeout(typing);
      typing = window.setTimeout(() => { state.q = search.value.trim(); void load(); }, TYPING_MS);
    };
    pop.addEventListener('beforetoggle', (ev: Event) => { if ('newState' in ev && ev.newState === 'open') pop.innerHTML = popHTML(); });
    pop.addEventListener('toggle', (ev: Event) => {
      const open = 'newState' in ev && ev.newState === 'open';
      filterBtn.setAttribute('aria-expanded', String(open));
      if (!open) return;
      // Under the button, inside the window; a phone's popover is the bottom sheet the stylesheet makes it.
      if (!matchMedia('(max-width:760px)').matches) {
        const r = filterBtn.getBoundingClientRect();
        pop.style.top = `${Math.max(8, Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8))}px`;
        pop.style.left = `${Math.max(8, Math.min(r.right - pop.offsetWidth, innerWidth - pop.offsetWidth - 8))}px`;
      }
      pop.querySelector<HTMLElement>('[aria-checked="true"]')?.focus({preventScroll: true});
    });
    pop.onclick = (event: MouseEvent) => {
      const pick = eventElement(event)?.closest<HTMLElement>('[data-bf-kind]');
      if (!pick) return;
      state.kind = pick.dataset.bfKind ?? '';
      pop.hidePopover();
      void load();
    };

    host.onclick = async event => {
      const hit = (selector: string): HTMLElement | null => eventElement(event)?.closest<HTMLElement>(selector) ?? null;
      const stored = hit('[data-bf-open=tico]');
      if (stored) { event.preventDefault(); openFile(stored.dataset.bfId ?? '', stored.dataset.bfFile ?? ''); return; }
      // A Google file opens in the app's review view, in Google's own preview; a modified click still opens a tab.
      const google = hit('[data-bf-open=external][data-bf-id]');
      if (google && !event.metaKey && !event.ctrlKey && !event.shiftKey) { event.preventDefault(); openFile(google.dataset.bfId ?? '', google.dataset.bfFile ?? ''); return; }
      try {
        if (hit('[data-bf-all]')) { state.expanded = true; await load(); }
        else if (hit('[data-bf-less]')) {
          state.expanded = false; state.rows = state.rows.slice(0, SHORT); state.q = state.kind = ''; search.value = '';
          clearTimeout(typing); await load();
        }
        else if (hit('[data-bf-more]')) { await load(true); }
      } catch (e) { state.error = errorMessage(e, 'Could not load the files.'); render(); }
    };
    load();
    // Background tabs used to keep hitting the Files list every minute; that endpoint is
    // statement-heavy (#1659) and multiplies across open bot pages. Resume when the tab is shown again.
    // A tab shown again reloads only when its last load is about an interval old (a little under, so timer jitter
    // never skips a minute): switching tabs often must not
    // ask more than the minute timer did.
    let loadedAt = Date.now();
    const due = () => { if (!document.hidden && Date.now() - loadedAt >= 55000) { loadedAt = Date.now(); load(); } };
    const onVis = () => {
      if (!host.isConnected) { document.removeEventListener('visibilitychange', onVis); return; }
      due();
    };
    const timer = setInterval(() => {
      if (!host.isConnected) {
        clearInterval(timer); document.removeEventListener('visibilitychange', onVis); return;
      }
      due();
    }, 60000);
    document.addEventListener('visibilitychange', onVis);
  }

  window.botFiles = {mount};
})();
