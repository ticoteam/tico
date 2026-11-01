/* The Assistant's Confirm / Cancel cards and its off switch (docs/assistant.md). Your private chat with the
   assistant is the Assistant page (ui/app/assistant-page.js), the same thread and composer as a bot's chat.
   Anything with a side effect that matters arrives as a Confirm / Cancel card; only your click runs it.
   deps keeps this file free of the page's globals (get, post, esc, toast). */
'use strict';
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
.asst-off{padding:14px 0}
.asst-err{color:var(--fail);font-size:12.5px}
.asst-card .asst-once code{color:var(--ink);user-select:all;font-size:13px}
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
  const shownOnce = new Map();
  function onceHTML(id, esc) {
    const once = shownOnce.get(id);
    if (!once?.code) return '';
    const until = once.expires_at ? new Date(once.expires_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';
    return `<div class="asst-once" data-shown-once><span class="asst-state">One-time code for a new computer${until ? `, until ${esc(until)}` : ''}. It is shown only here and works once: use it where the computer's setup asks for a code (Settings &gt; Computers &gt; Add computer shows the commands).</span><code>${esc(once.code)}</code></div>`;
  }
  const SENT = new Set(['message', 'body', 'text', 'comment']);
  function actionCard(action, esc, open = false, assistantName = '') {
    // A bulk card that ran with some steps refused is done, and says which did not go through.
    const done = {done: 'Done.' + (action.result?.error ? ' ' + action.result.error : ''), failed: 'It did not go through' + (action.result?.error ? ': ' + action.result.error : '.'),
      cancelled: 'Cancelled.', expired: 'Expired: ask again.', running: 'Running…'}[action.status];
    // Never cut a value: a long one is folded behind "Show more", whole.
    const shown = v => { const t = typeof v === 'string' ? v : JSON.stringify(v); return t.length > 160
      ? `<details class="asst-long"><summary>${esc(t.slice(0, 120))}… <span>Show more</span></summary>${esc(t)}</details>` : esc(t); };
    const by = action.proposer || action.proposed_via;
    // A card the Strategy Bot put in the owner's earlier chat with it names it (`by_name`), never "the Assistant".
    const name = action.by_name || {assistant: assistantName || 'The Assistant', botops: 'BotOps'}[by] || 'A bot';
    const what = String(action.description || '').trim(), plain = what && !RAW_TITLE.test(what);
    // What the card does is always in view before the click: with no sentence from the server, the bot's own summary
    // is the title, and with neither, the fields stay on the card.
    const summary = String(action.summary || '').trim();
    const title = plain ? what : summary || 'A change for you';
    // A task change (old → new) and a bulk card's one line per step read as words; any other card's fields are the
    // request itself, so they go under Details with the method and path.
    const readable = action.path === '/api/v2/assistant/batch' || (action.diff || []).some(d => 'old' in d || d.group) || !plain && !summary;
    const fields = action.diff?.length
      ? `<ul class="asst-body" aria-label="Changes">${action.diff.map(d => `<li><strong>${esc(d.field)}</strong>: ${'old' in d ? shown(d.old ?? '') + ' → ' : ''}${shown(d.new)}</li>`).join('')}</ul>`
      : Object.keys(action.body || {}).length
        ? `<ul class="asst-body" aria-label="Fields">${Object.entries(action.body).map(([k, v]) => `<li><strong>${esc(k)}</strong>: ${shown(v)}</li>`).join('')}</ul>` : '';
    // The words a send carries (a message, a comment, a support request) are what the person approves: shown whole,
    // never only under Details.
    const sends = Object.entries(action.body || {}).filter(([k, v]) => SENT.has(k) && typeof v === 'string' && v.trim())
      .map(([, v]) => `<blockquote class="asst-said" aria-label="What is sent">${shown(v)}</blockquote>`).join('');
    const who = action.by_name ? action.by_name + ' says: ' : {assistant: 'The assistant says: ', botops: 'BotOps says: '}[by] || '';
    // The same sentence, or its first 300 characters (a summary is cut there), repeats the title.
    const said = summary, repeats = said === title || said.length >= 300 && title.startsWith(said);
    return `<div class="asst-card" tabindex="-1" data-action="${esc(action.id)}" data-status="${esc(action.status)}">
      <div class="asst-who">${esc(name.replace(/^The /, ''))} asks</div>
      <details class="asst-raw"${open ? ' open' : ''}><summary title="Details"><b data-what>${esc(title)}</b><span class="nav-icon" aria-hidden="true">expand_more</span></summary>
        ${said && !repeats ? `<p class="asst-state">${esc(who)}“${esc(said)}”</p>` : ''}${readable ? '' : fields}${action.original_summary
        ? `<p class="asst-state">First written as: “${esc(action.original_summary)}”</p>` : ''}<code>${esc(action.method)} ${esc(action.path)}</code></details>
      ${sends}${readable ? fields : ''}
      ${action.status === 'pending'
        ? `<div class="asst-row"><button class="primary" type="button" data-confirm title="Runs as you, only when you confirm">Confirm</button>
            <button class="ghost" type="button" data-cancel>Cancel</button></div>`
        : `<div class="asst-state">${esc(done || action.status)}</div>${onceHTML(action.id, esc)}`}</div>`;
  }

  // The Confirm cards left in a chat (the Assistant's, and BotOps' for what a person asked it for that always needs
  // their click): each message with `refs.action` has a host element, filled from the action.
  // A chat rebuilds its whole thread when a message arrives (the result line a Confirm writes, too), so every card seen
  // is kept here and put back at once, inside that rebuild: the same element when it would draw the same, so a "Show
  // more" opened on it stays open and it keeps its height and place, instead of every card falling back to "Loading the
  // card…" and growing back one fetch at a time. A Confirm repaints only its card.
  const seenActions = new Map();
  const openRaw = new Set();             // cards whose Details is open, kept open across a rebuild
  const painted = new WeakMap();         // host -> the card HTML it shows
  const drawn = new Map();               // action id -> {card, html}: the element last drawn for it
  const UNSETTLED = new Set(['pending', 'running']);
  const deciding = new Set();            // cards whose Confirm / Cancel is on its way: a fetch never repaints them
  async function cards(root, deps) {
    if (!root) return;
    style();
    const {get, post, esc, toast, reload, assistantName} = deps;
    const hostsOf = id => [...root.querySelectorAll('[data-action-host]')].filter(h => h.dataset.actionHost === id);
    const paint = (host, action) => {
      seenActions.set(action.id, action);
      const html = actionCard(action, esc, openRaw.has(action.id), assistantName);
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
      drawn.set(action.id, {card, html});
      // The card moves between hosts, so what it does is found from the card, never from the host it was drawn in.
      card.querySelector('details.asst-raw')?.addEventListener('toggle', ev => {
        openRaw[ev.target.open ? 'add' : 'delete'](action.id);
        const now = actionCard(action, esc, ev.target.open, assistantName);
        drawn.set(action.id, {card, html: now});
        if (card.parentElement) painted.set(card.parentElement, now);
      });
      const decide = async verb => {
        const host = card.parentElement || host0;
        const focused = card.contains(document.activeElement);
        card.querySelectorAll('button').forEach(b => { b.disabled = true; });
        // A thread rebuilt while this runs draws the card as running, not with its buttons live again.
        seenActions.set(action.id, {...action, status: 'running'});
        deciding.add(action.id);
        let next = null;
        try {
          const answer = await post(`/v2/assistant/actions/${encodeURIComponent(action.id)}/${verb}`, {});
          if (answer?.shown_once) shownOnce.set(action.id, answer.shown_once);
          next = answer.action;
        }
        catch (e) { toast?.(e.message, true); }
        if (!next) try { next = (await get(`/v2/assistant/actions/${encodeURIComponent(action.id)}`)).action; } catch { /* kept as it was */ }
        deciding.delete(action.id);
        // Only this card is drawn again, wherever it is now; one still waiting gets its buttons back.
        for (const h of new Set([...hostsOf(action.id), host])) { painted.delete(h); paint(h, next || action); }
        // The button pressed is gone with the old card: focus stays on this card, not thrown back to the page.
        if (focused || document.activeElement === document.body) drawn.get(action.id)?.card.focus({preventScroll: true});
        reload?.();
      };
      const host0 = host;
      card.querySelector('[data-confirm]')?.addEventListener('click', () => decide('confirm'));
      card.querySelector('[data-cancel]')?.addEventListener('click', () => decide('cancel'));
    };
    const hosts = [...root.querySelectorAll('[data-action-host]')];
    // At once, before anything is fetched: every card already seen.
    for (const host of hosts) { const kept = seenActions.get(host.dataset.actionHost); if (kept) paint(host, kept); }
    // Then, side by side, what may have changed: new cards, and ones still waiting (decided in another tab, expired).
    await Promise.all(hosts.filter(h => !seenActions.has(h.dataset.actionHost) || UNSETTLED.has(seenActions.get(h.dataset.actionHost).status))
      .map(async host => {
        const id = host.dataset.actionHost;
        try {
          const action = (await get(`/v2/assistant/actions/${encodeURIComponent(id)}`)).action;
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
  function off(host, info, deps) {
    if (!host) return;
    style();
    const {post, esc, after} = deps;
    host.innerHTML = `<div class="asst-off asst-row"><span>The Assistant is off.${info.can_turn_on ? '' : ' Ask the owner to turn it on.'}</span>
      ${info.can_turn_on ? '<button class="primary" type="button" data-turn-on>Turn on</button><span class="asst-state" data-turn-status></span>' : ''}</div>`;
    const on = host.querySelector('[data-turn-on]');
    if (on) on.onclick = async () => {
      on.disabled = true;
      try { const r = await post('/v2/assistant/turn-on', {}); if (r.state !== 'active') host.querySelector('[data-turn-status]').textContent = 'Restored, but it needs a computer before it can answer (Settings).'; else await after?.(); }
      catch (e) { on.disabled = false; host.querySelector('[data-turn-status]').innerHTML = `<span class="asst-err">${esc(e.message)}</span>`; }
    };
  }

  // Settings > Bots, owner only: when the team has no Assistant (archived at setup, or never added),
  // one button restores it or adds it from the template, and everyone's Assistant page starts working.
  async function settingsStrip(host, deps) {
    if (!host) return;
    style();
    const {get, post, esc, toast, after} = deps;
    let info;
    try { info = await get('/v2/assistant'); } catch { host.innerHTML = ''; return; }
    if (info.available || !info.can_turn_on) { host.innerHTML = ''; return; }
    host.innerHTML = `<section class="card" data-assistant-off><div class="asst-row"><strong>The Assistant is off</strong>
      <button class="primary" type="button" data-turn-on>Turn on</button><span class="asst-state" data-turn-status role="status"></span></div></section>`;
    const button = host.querySelector('[data-turn-on]'), status = host.querySelector('[data-turn-status]');
    button.onclick = async () => {
      button.disabled = true; status.textContent = 'Turning it on…';
      try {
        const r = await post('/v2/assistant/turn-on', {});
        status.textContent = r.state === 'active' ? 'On.' : 'Restored, but it needs a computer before it can answer: add one under Computers.';
        toast?.(r.state === 'active' ? 'The Assistant is on' : 'The Assistant is restored and waiting for a computer');
        await after?.();
        if (r.state === 'active') host.innerHTML = '';
      } catch (e) { button.disabled = false; status.innerHTML = `<span class="asst-err">${esc(e.message)}</span>`; }
    };
  }

  window.assistantChat = {cards, off, settingsStrip};
})();
