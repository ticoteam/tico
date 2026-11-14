/* The Assistant's Confirm / Cancel cards and its off switch (docs/assistant.md). Your private chat with the
   assistant is the Assistant page (ui/app/assistant-page.ts), the same thread and composer as a bot's chat.
   Anything with a side effect that matters arrives as a Confirm / Cancel card; only your click runs it.
   deps keeps this file free of the page's globals (get, post, esc, toast). */
'use strict';

// The deps cards() takes from its page. assistantName is the Assistant's name, shown on its own cards.
interface AssistantCardsDeps {
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, body?: unknown) => Promise<T>;
  esc: (s: unknown) => string;
  toast?: (msg: string, isErr?: boolean) => unknown;
  reload?: () => unknown;
  assistantName?: string;
}

// The deps off() takes. after runs once the Assistant is on again.
interface AssistantOffDeps {
  post: <T>(path: string, body?: unknown) => Promise<T>;
  esc: (s: unknown) => string;
  after?: () => unknown;
}

// The deps settingsStrip() takes. after runs once the Assistant is on again.
interface AssistantSettingsDeps {
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, body?: unknown) => Promise<T>;
  esc: (s: unknown) => string;
  toast?: (msg: string, isErr?: boolean) => unknown;
  after?: () => unknown;
}

// One line of a card's changes: old → new for a task change, or a plain field.
interface AssistantCardDiff {
  field: string;
  new: unknown;
  old?: unknown;
  routine?: boolean;
  group?: unknown;
}

// One Confirm / Cancel card's action as GET and POST /v2/assistant/actions/{id} send it (api.d.ts has only a shell for it).
interface AssistantCardAction {
  id: string;
  status: string;
  method: string;
  path: string;
  description?: string | null;
  summary?: string | null;
  proposer?: string | null;
  proposed_via?: string | null;
  by_name?: string | null;
  original_summary?: string | null;
  body?: Record<string, unknown> | null;
  diff?: AssistantCardDiff[] | null;
  result?: {error?: string | null} | null;
}

// What a Confirm or a Cancel answers: the action as it is now, and a one-time code it made, if any.
interface AssistantCardAnswer {
  action: AssistantCardAction;
  shown_once?: Record<string, unknown>;
}

interface Window {
  assistantChat?: {
    cards(root: ParentNode | null, deps: AssistantCardsDeps): Promise<void>;
    off(host: HTMLElement | null, info: {can_turn_on?: boolean | undefined}, deps: AssistantOffDeps): void;
    settingsStrip(host: HTMLElement | null, deps: AssistantSettingsDeps): Promise<void>;
  };
}

(function () {
  const css = `
.asst-card{align-self:flex-start;width:min(560px,100%);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:6px 0;background:var(--surface)}
.asst-card b{display:block;margin-bottom:2px}
.asst-who{font-size:var(--t-xs);font-weight:600;color:var(--muted);margin-bottom:2px}
.asst-card>.asst-raw{font-size:inherit;margin:0 0 6px}.asst-card>.asst-raw>:not(summary){font-size:12.5px}
.asst-card>.asst-raw>summary{display:flex;align-items:flex-start;gap:2px;color:inherit;width:auto;list-style:none}
.asst-card>.asst-raw>summary::-webkit-details-marker{display:none}
.asst-card>.asst-raw>summary .nav-icon{flex:none;width:18px;height:18px;font-size:18px}
.asst-card>.asst-raw[open]>summary .nav-icon{transform:rotate(180deg)}
.asst-card>.asst-raw:not([open]) [data-what]{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}
.asst-card:focus:not(:focus-visible){outline:none}
.asst-body{margin:4px 0;padding-left:18px;font-size:13px;overflow-wrap:anywhere}
.asst-card code{display:block;font-size:12px;color:var(--muted);overflow-wrap:anywhere;margin:2px 0 8px}
.asst-raw{margin:4px 0 8px;font-size:12.5px}
.asst-raw>summary{cursor:pointer;color:var(--muted);width:max-content}
.asst-said{margin:6px 0;padding:4px 10px;border-left:3px solid var(--line);font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.asst-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.asst-state{font-size:12.5px;color:var(--muted)}
.asst-row button[aria-busy="true"],.asst-row button[aria-busy="true"]:disabled{opacity:1;cursor:progress;display:inline-flex;align-items:center;gap:6px}
.asst-row button[aria-busy="true"]::before{content:"";flex:none;width:12px;height:12px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:p-spin .8s linear infinite}
@media (prefers-reduced-motion:reduce){.asst-row button[aria-busy="true"]::before{animation-duration:2.4s}}
.asst-off{padding:14px 0}
.asst-err{color:var(--fail);font-size:12.5px}
.asst-card .asst-once code{color:var(--ink);user-select:all;font-size:13px}
.pgk-rows{list-style:none;margin:6px 0 10px;padding:0;font-size:13px}
.pgk-row{display:flex;align-items:center;gap:6px;min-height:28px;border-top:1px solid var(--line)}
.pgk-row:first-child{border-top:0}
.pgk-row>span{flex:1;min-width:0;overflow-wrap:anywhere}
.pgk-o{color:var(--muted)}
.pgk-off>span{color:var(--muted);text-decoration:line-through}
button.pgk-x{flex:none;width:28px;height:28px;padding:0;border:0;background:none;color:var(--muted);font-size:16px;line-height:1;border-radius:6px;cursor:pointer}
button.pgk-x:hover{background:var(--surface2);color:var(--ink)}
.pgk .asst-row{justify-content:space-between}
`;

  function style() {
    if (document.getElementById('asst-style')) return;
    const el = document.createElement('style');
    el.id = 'asst-style';
    el.textContent = css;
    document.head.appendChild(el);
  }

  // One proposal as a Confirm / Cancel card. What will really happen comes from the server; the proposing
  // bot's own words are only its summary. Used by the Assistant page and by BotOps' chat (`cards`).
  // A card is who asks, the title and two buttons. The title is always plain words: the server's sentence, or for a card
  // with none (stored before its route had one) a plain line. Three lines of it show until it is tapped, which opens the
  // rest: the bot's own words, the method, path and the fields it sends. What a send carries and a task change's lines
  // are what the person approves, so they stay on the card.
  const RAW_TITLE = /^(GET|POST|PUT|PATCH|DELETE) \//;
  // A one-time value the Confirm answered with (a new computer's code): kept in this page only, never stored anywhere
  // the proposing bot reads, so it shows on its card until the page is reloaded.
  const shownOnce = new Map<string, Record<string, unknown>>();
  function onceHTML(id: string, esc: (s: unknown) => string): string {
    const once = shownOnce.get(id);
    if (!once?.code) return '';
    // The server sends expires_at as an ISO time; api.d.ts types the whole shown_once record as unknown values.
    const until = once.expires_at ? new Date(once.expires_at as string).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';
    return `<div class="asst-once" data-shown-once><span class="asst-state">One-time code for a new computer${until ? `, until ${esc(until)}` : ''}. It is shown only here and works once: use it where the computer's setup asks for a code (Settings &gt; Computers &gt; Add computer shows the commands).</span><code>${esc(once.code)}</code></div>`;
  }
  const SENT = new Set(['message', 'body', 'text', 'comment']);
  // Who says a card's words, by its proposer (a card with `by_name` says its own name instead).
  const SAYS: Record<string, string> = {assistant: 'The assistant says: ', botops: 'BotOps says: '};
  // A page chat's card (backend/page_chat_cards.py, Goals): one row per goal with its owner, × to drop a row (it stays,
  // struck, with ↺ to bring it back), Cancel on the left and one accent button that counts the kept rows. Once decided
  // it lists what ran (the server keeps only the kept rows) with no buttons.
  const dropped = new Map<string, Set<number>>();            // action id -> the rows the person took off before Confirm
  const isPageCard = (action: AssistantCardAction): boolean => String(action.proposer || '').startsWith('page:');
  function pageCard(action: AssistantCardAction, esc: (s: unknown) => string, done: string | undefined, busy: string): string {
    const off = dropped.get(action.id) || new Set<number>(), lines = action.diff || [];
    const open = busy !== '' || action.status === 'pending';
    const kept = lines.length - [...off].filter(i => i < lines.length).length;
    const rows = lines.map((d, i) => {
      const gone = open && off.has(i);
      const toggle = open ? `<button class="pgk-x" type="button" data-drop="${i}" aria-label="${gone ? 'Bring back' : 'Drop'} ${esc(d.field)}"${busy ? ' disabled' : ''}>${gone ? '↺' : '×'}</button>` : '';
      return `<li class="pgk-row${gone ? ' pgk-off' : ''}"><span><span class="pgk-t">${esc(d.field)}</span><span class="pgk-o"> · ${esc(d.new)}</span></span>${toggle}</li>`;
    }).join('');
    const noun = (n: number) => `${n} goal${n === 1 ? '' : 's'}`;
    const button = (verb: string, cls: string, label: string, doing: string, off = false): string => busy === verb
      ? `<button class="${cls}" type="button" data-${verb} disabled aria-busy="true">${doing}</button>`
      : `<button class="${cls}" type="button" data-${verb}${busy || off ? ' disabled' : ''}>${label}</button>`;
    return `<div class="asst-card pgk" tabindex="-1" data-action="${esc(action.id)}" data-status="${esc(action.status)}">
      <div class="asst-who">${esc((action.by_name || 'A bot').replace(/^The /, ''))} asks</div>
      <b data-what>${esc(action.description || action.summary || '')}</b>
      <ul class="pgk-rows" aria-label="Goals">${rows}</ul>
      ${open
        ? `<div class="asst-row">${button('cancel', 'ghost', 'Cancel', 'Canceling…')}${button('confirm', 'primary', 'Add ' + noun(kept), 'Adding…', kept === 0)}</div>`
        : `<div class="asst-state">${esc(done || action.status)}</div>`}</div>`;
  }

  // `busy`: the button pressed ('confirm' / 'cancel') while its answer is on its way: it spins (chat.css p-spin) and says
  // so, the other is off, and a thread rebuilt meanwhile draws the same.
  function actionCard(action: AssistantCardAction, esc: (s: unknown) => string, open = false, assistantName = '', busy = ''): string {
    // A bulk card that ran with some steps refused is done, and says which did not go through.
    const doneWords: Record<string, string> = {done: 'Done.' + (action.result?.error ? ' ' + action.result.error : ''), failed: 'It did not go through' + (action.result?.error ? ': ' + action.result.error : '.'),
      canceled: 'Canceled.', withdrawn: 'Withdrawn: nothing ran.', expired: 'Expired: ask again.', running: 'Confirmed · working on it'};
    const done = doneWords[action.status];
    if (isPageCard(action)) return pageCard(action, esc, done, busy);
    // Never cut a value: a long one is folded behind "Show more", whole.
    const shown = (v: unknown): string => { const t = typeof v === 'string' ? v : JSON.stringify(v); return t.length > 160
      ? `<details class="asst-long"><summary>${esc(t.slice(0, 120))}… <span>Show more</span></summary>${esc(t)}</details>` : esc(t); };
    const by = action.proposer || action.proposed_via || '';
    // A card the Strategy Owner put in the owner's earlier chat with it names it (`by_name`), never "the Assistant".
    const names: Record<string, string> = {assistant: assistantName || 'The Assistant', botops: 'BotOps'};
    const name = action.by_name || names[by] || 'A bot';
    const what = String(action.description || '').trim(), plain = what && !RAW_TITLE.test(what);
    // What the card does is always in view before the click: with no sentence from the server, the bot's own summary
    // is the title, and with neither, the fields stay on the card.
    const summary = String(action.summary || '').trim();
    const title = plain ? what : summary || 'A change for you';
    // A task change (old → new) and a bulk card's one line per step read as words; any other card's fields are the
    // request itself, so they go under Details with the method and path.
    const routine = (action.diff || []).some(d => d.routine);
    const readable = routine || action.path === '/api/v2/assistant/batch' || (action.diff || []).some(d => 'old' in d || d.group) || !plain && !summary;
    const fields = action.diff?.length
      ? `<ul class="asst-body" aria-label="Changes">${action.diff.map(d => `<li><strong>${esc(d.field)}</strong>: ${'old' in d ? shown(d.old ?? '') + ' → ' : ''}${shown(d.new)}</li>`).join('')}</ul>`
      : Object.keys(action.body || {}).length
        ? `<ul class="asst-body" aria-label="Fields">${Object.entries(action.body || {}).map(([k, v]) => `<li><strong>${esc(k)}</strong>: ${shown(v)}</li>`).join('')}</ul>` : '';
    // The words a send carries (a message, a comment, a support request) are what the person approves: shown whole,
    // never only under Details.
    const sends = Object.entries(action.body || {}).filter(([k, v]) => SENT.has(k) && typeof v === 'string' && v.trim())
      .map(([, v]) => `<blockquote class="asst-said" aria-label="What is sent">${shown(v)}</blockquote>`).join('');
    const who = action.by_name ? action.by_name + ' says: ' : SAYS[by] || '';
    // The same sentence, or its first 300 characters (a summary is cut there), repeats the title.
    const said = summary, repeats = said === title || said.length >= 300 && title.startsWith(said);
    const button = (verb: string, cls: string, label: string, doing: string, title = ''): string => busy === verb
      ? `<button class="${cls}" type="button" data-${verb} disabled aria-busy="true">${doing}</button>`
      : `<button class="${cls}" type="button" data-${verb}${busy ? ' disabled' : ''}${title}>${label}</button>`;
    return `<div class="asst-card" tabindex="-1" data-action="${esc(action.id)}" data-status="${esc(action.status)}">
      <div class="asst-who">${esc(name.replace(/^The /, ''))} asks</div>
      <details class="asst-raw"${open ? ' open' : ''}><summary title="Details"><b data-what>${esc(title)}</b><span class="nav-icon" aria-hidden="true">expand_more</span></summary>
        ${said && !repeats ? `<p class="asst-state">${esc(who)}“${esc(said)}”</p>` : ''}${routine ? `<ul class="asst-body" aria-label="Request">${Object.entries(action.body || {}).map(([k, v]) => `<li><strong>${esc(k)}</strong>: ${shown(v)}</li>`).join('')}</ul>` : readable ? '' : fields}${action.original_summary
        ? `<p class="asst-state">First written as: “${esc(action.original_summary)}”</p>` : ''}<code>${esc(action.method)} ${esc(action.path)}</code></details>
      ${sends}${readable ? fields : ''}
      ${busy || action.status === 'pending'
        ? `<div class="asst-row">${button('confirm', 'primary', 'Confirm', 'Confirming…', ' title="Runs as you, only when you confirm"')}
            ${button('cancel', 'ghost', 'Cancel', 'Canceling…')}</div>`
        : `<div class="asst-state">${esc(done || action.status)}</div>${onceHTML(action.id, esc)}`}</div>`;
  }

  // The Confirm cards left in a chat (the Assistant's, and BotOps' for what a person asked it for that always needs
  // their click): each message with `refs.action` has a host element, filled from the action.
  // A chat rebuilds its whole thread when a message arrives (the result line a Confirm writes, too), so every card seen
  // is kept here and put back at once, inside that rebuild: the same element when it would draw the same, so a "Show
  // more" opened on it stays open and it keeps its height and place, instead of every card falling back to "Loading the
  // card…" and growing back one fetch at a time. A Confirm repaints only its card.
  const seenActions = new Map<string, AssistantCardAction>();
  const openRaw = new Set<string>();             // cards whose Details is open, kept open across a rebuild
  const painted = new WeakMap<HTMLElement, string>();         // host -> the card HTML it shows
  const drawn = new Map<string, {card: HTMLElement; html: string}>();               // action id -> {card, html}: the element last drawn for it
  const UNSETTLED = new Set(['pending', 'running']);
  const deciding = new Map<string, string>();            // action id -> 'confirm' / 'cancel' on its way: a fetch never repaints it
  async function cards(root: ParentNode | null, deps: AssistantCardsDeps): Promise<void> {
    if (!root) return;
    style();
    const {get, post, esc, toast, reload, assistantName} = deps;
    const hostsOf = (id: string): HTMLElement[] => [...root.querySelectorAll<HTMLElement>('[data-action-host]')].filter(h => h.dataset.actionHost === id);
    const draw = (action: AssistantCardAction, open: boolean) => actionCard(action, esc, open, assistantName, deciding.get(action.id));
    const paint = (host: HTMLElement, action: AssistantCardAction): void => {
      seenActions.set(action.id, action);
      const html = draw(action, openRaw.has(action.id));
      if (painted.get(host) === html) return;            // nothing changed: leave it alone
      const kept = drawn.get(action.id);
      // A rebuilt thread's new host gets the card it had before, moved, not drawn again (one shown twice is drawn).
      if (kept?.html === html && !kept.card.isConnected) {
        host.replaceChildren(kept.card);
        painted.set(host, html);
        return;
      }
      host.innerHTML = html;
      painted.set(host, html);
      const card = host.firstElementChild;
      if (!(card instanceof HTMLElement)) return;        // the markup starts with the card's div, so this is always one
      drawn.set(action.id, {card, html});
      // The card moves between hosts, so what it does is found from the card, never from the host it was drawn in.
      const details = card.querySelector<HTMLDetailsElement>('details.asst-raw');
      if (details) details.addEventListener('toggle', () => {
        openRaw[details.open ? 'add' : 'delete'](action.id);
        const now = draw(action, details.open);
        drawn.set(action.id, {card, html: now});
        if (card.parentElement) painted.set(card.parentElement, now);
      });
      const decide = async (verb: string): Promise<void> => {
        const host = card.parentElement || host0;
        const focused = card.contains(document.activeElement);
        // At once, the pressed button spins ("Confirming…") and the other is off; a thread rebuilt while this runs (the
        // server runs every step before it answers) puts back the same, never live buttons.
        const waiting = {...action, status: 'running'};
        deciding.set(action.id, verb);
        for (const h of new Set([...hostsOf(action.id), host])) { painted.delete(h); paint(h, waiting); }
        if (focused) drawn.get(action.id)?.card.focus({preventScroll: true});
        let next: AssistantCardAction | null = null;
        try {
          const drop = verb === 'confirm' && isPageCard(action) ? [...(dropped.get(action.id) || [])].sort((a, b) => a - b) : [];
          const answer = await post<AssistantCardAnswer>(`/v2/assistant/actions/${encodeURIComponent(action.id)}/${verb}`, drop.length ? {drop} : {});
          if (answer?.shown_once) shownOnce.set(action.id, answer.shown_once);
          next = answer.action;
        }
        catch (e) { toast?.(errorMessage(e, ''), true); }
        if (!next) try { next = (await get<AssistantCardAnswer>(`/v2/assistant/actions/${encodeURIComponent(action.id)}`)).action; } catch { /* kept as it was */ }
        deciding.delete(action.id);
        // Only this card is drawn again, wherever it is now; one still waiting gets its buttons back.
        for (const h of new Set([...hostsOf(action.id), host])) { painted.delete(h); paint(h, next || action); }
        // The button pressed is gone with the old card: focus stays on this card, not thrown back to the page.
        if (focused || document.activeElement === document.body) drawn.get(action.id)?.card.focus({preventScroll: true});
        reload?.();
      };
      const host0 = host;
      // × / ↺ on a page card's row: redrawn where it is, with the focus kept on that row's button.
      for (const b of card.querySelectorAll<HTMLButtonElement>('[data-drop]')) b.addEventListener('click', () => {
        const i = Number(b.dataset.drop), off = dropped.get(action.id) || new Set<number>();
        off[off.has(i) ? 'delete' : 'add'](i);
        dropped.set(action.id, off);
        const now = seenActions.get(action.id) || action;
        for (const h of new Set([...hostsOf(action.id), card.parentElement || host0])) { painted.delete(h); paint(h, now); }
        drawn.get(action.id)?.card.querySelector<HTMLButtonElement>(`[data-drop="${i}"]`)?.focus();
      });
      card.querySelector('[data-confirm]')?.addEventListener('click', () => decide('confirm'));
      card.querySelector('[data-cancel]')?.addEventListener('click', () => decide('cancel'));
    };
    const hosts = [...root.querySelectorAll<HTMLElement>('[data-action-host]')];
    // At once, before anything is fetched: every card already seen.
    for (const host of hosts) { const kept = seenActions.get(host.dataset.actionHost ?? ''); if (kept) paint(host, kept); }
    // Then, side by side, what may have changed: new cards, and ones still waiting (decided in another tab, expired).
    await Promise.all(hosts.filter(h => {
        const kept = seenActions.get(h.dataset.actionHost ?? '');
        return !kept || UNSETTLED.has(kept.status);
      })
      .map(async host => {
        const id = host.dataset.actionHost ?? '';
        try {
          const action = (await get<AssistantCardAnswer>(`/v2/assistant/actions/${encodeURIComponent(id)}`)).action;
          // A fetch sent before a click comes back late: it never repaints a card being decided, nor makes a decided
          // one pending again.
          const kept = seenActions.get(id);
          if (!host.isConnected || deciding.has(id) || action.status === 'pending' && kept && kept.status !== 'pending') return;
          paint(host, action);
        }
        catch { if (!seenActions.has(id)) host.innerHTML = '<div class="asst-state">This card is not yours to see.</div>'; }
      }));
  }

  // The Assistant page when the assistant is off: one line, with the switch for the owner and whom to ask for anyone
  // else. `after` runs once it is on.
  function off(host: HTMLElement | null, info: import('./types/api').Assistant, deps: AssistantOffDeps): void {
    if (!host) return;
    style();
    const {post, esc, after} = deps;
    host.innerHTML = `<div class="asst-off asst-row"><span>The Assistant is off.${info.can_turn_on ? '' : ' Ask the owner to turn it on.'}</span>
      ${info.can_turn_on ? '<button class="primary" type="button" data-turn-on>Turn on</button><span class="asst-state" data-turn-status></span>' : ''}</div>`;
    // The status line is drawn with the button above; both are looked up once, and the button is wired only when both exist.
    const on = host.querySelector<HTMLButtonElement>('[data-turn-on]');
    const status = host.querySelector<HTMLElement>('[data-turn-status]');
    if (on && status) on.onclick = async () => {
      on.disabled = true;
      try { const r = await post<{state: string}>('/v2/assistant/turn-on', {}); if (r.state !== 'active') status.textContent = 'Restored, but it needs a computer before it can answer (Settings).'; else await after?.(); }
      catch (e) { on.disabled = false; status.innerHTML = `<span class="asst-err">${esc(errorMessage(e, ''))}</span>`; }
    };
  }

  // Settings > Bots, owner only: when the team has no Assistant (archived at setup, or never added),
  // one button restores it or adds it from the template, and everyone's Assistant page starts working.
  async function settingsStrip(host: HTMLElement | null, deps: AssistantSettingsDeps): Promise<void> {
    if (!host) return;
    style();
    const {get, post, esc, toast, after} = deps;
    let info: import('./types/api').Assistant;
    try { info = await get<import('./types/api').Assistant>('/v2/assistant'); } catch { host.innerHTML = ''; return; }
    if (info.available || !info.can_turn_on) { host.innerHTML = ''; return; }
    host.innerHTML = `<section class="card" data-assistant-off><div class="asst-row"><strong>The Assistant is off</strong>
      <button class="primary" type="button" data-turn-on>Turn on</button><span class="asst-state" data-turn-status role="status"></span></div></section>`;
    // Both are drawn by the markup just above.
    const button = host.querySelector<HTMLButtonElement>('[data-turn-on]')!, status = host.querySelector<HTMLElement>('[data-turn-status]')!;
    button.onclick = async () => {
      button.disabled = true; status.textContent = 'Turning it on…';
      try {
        const r = await post<{state: string}>('/v2/assistant/turn-on', {});
        status.textContent = r.state === 'active' ? 'On.' : 'Restored, but it needs a computer before it can answer: add one under Computers.';
        toast?.(r.state === 'active' ? 'The Assistant is on' : 'The Assistant is restored and waiting for a computer');
        await after?.();
        if (r.state === 'active') host.innerHTML = '';
      } catch (e) { button.disabled = false; status.innerHTML = `<span class="asst-err">${esc(errorMessage(e, ''))}</span>`; }
    };
  }

  window.assistantChat = {cards, off, settingsStrip};
})();
