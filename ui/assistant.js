/* The Assistant's Confirm / Cancel cards and its off switch (docs/assistant.md). Your private chat with the
   assistant is the Assistant page (ui/app/assistant-page.js), the same thread and composer as a bot's chat.
   Anything with a side effect that matters arrives as a Confirm / Cancel card; only your click runs it.
   deps keeps this file free of the page's globals (get, post, esc, toast). */
'use strict';
(function () {
  const css = `
.asst-card{align-self:flex-start;width:min(560px,100%);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:6px 0;background:var(--surface)}
.asst-card b{display:block;margin-bottom:2px}
.asst-body{margin:4px 0;padding-left:18px;font-size:13px;overflow-wrap:anywhere}
.asst-card code{display:block;font-size:12px;color:var(--muted);overflow-wrap:anywhere;margin:2px 0 8px}
.asst-card .asst-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.asst-state{font-size:12.5px;color:var(--muted)}
.asst-off{padding:14px 0}
.asst-err{color:var(--fail);font-size:12.5px}
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
  function actionCard(action, esc) {
    // A bulk card that ran with some steps refused is done, and says which did not go through.
    const done = {done: 'Done.' + (action.result?.error ? ' ' + action.result.error : ''), failed: 'It did not go through' + (action.result?.error ? ': ' + action.result.error : '.'),
      cancelled: 'Cancelled.', expired: 'Expired: ask again.', running: 'Running…'}[action.status];
    // Never cut a value: a long one is folded behind "Show more", whole.
    const shown = v => { const t = typeof v === 'string' ? v : JSON.stringify(v); return t.length > 160
      ? `<details class="asst-long"><summary>${esc(t.slice(0, 120))}… <span>Show more</span></summary>${esc(t)}</details>` : esc(t); };
    const changes = action.diff?.length
      ? `<ul class="asst-body" aria-label="Changes">${action.diff.map(d => `<li><strong>${esc(d.field)}</strong>: ${'old' in d ? shown(d.old ?? '') + ' → ' : ''}${shown(d.new)}</li>`).join('')}</ul>`
      : Object.keys(action.body || {}).length
        ? `<ul class="asst-body" aria-label="Details">${Object.entries(action.body).map(([k, v]) => `<li><strong>${esc(k)}</strong>: ${shown(v)}</li>`).join('')}</ul>` : '';
    const who = {assistant: 'The assistant says: ', botops: 'BotOps says: '}[action.proposer || action.proposed_via] || '';
    return `<div class="asst-card" data-action="${esc(action.id)}" data-status="${esc(action.status)}">
      <b data-what>${esc(action.description || action.method + ' ' + action.path)}</b>
      <span class="asst-state">${esc(who)}“${esc(action.summary)}”</span>
      ${changes}
      <code>${esc(action.method)} ${esc(action.path)}</code>
      ${action.status === 'pending'
        ? `<div class="asst-row"><button class="primary" type="button" data-confirm>Confirm</button>
            <button class="ghost" type="button" data-cancel>Cancel</button>
            <span class="asst-state">Runs as you, only when you confirm.</span></div>`
        : `<div class="asst-state">${esc(done || action.status)}</div>`}</div>`;
  }

  // The Confirm cards left in a chat (the Assistant's, and BotOps' for what a person asked it for that always needs
  // their click): each message with `refs.action` has a host element, filled from the action.
  const seenActions = new Map();
  async function cards(root, deps) {
    if (!root) return;
    style();
    const {get, post, esc, toast, reload} = deps;
    const paint = (host, action) => {
      seenActions.set(action.id, action);
      host.innerHTML = actionCard(action, esc);
      const decide = async verb => {
        host.querySelectorAll('button').forEach(b => { b.disabled = true; });
        try { await post(`/v2/assistant/actions/${encodeURIComponent(action.id)}/${verb}`, {}); }
        catch (e) { toast?.(e.message, true); }
        seenActions.delete(action.id);
        await cards(root, deps);
        reload?.();
      };
      host.querySelector('[data-confirm]')?.addEventListener('click', () => decide('confirm'));
      host.querySelector('[data-cancel]')?.addEventListener('click', () => decide('cancel'));
    };
    for (const host of root.querySelectorAll('[data-action-host]')) {
      const id = host.dataset.actionHost, kept = seenActions.get(id);
      if (kept && kept.status !== 'pending') { paint(host, kept); continue; }
      try { paint(host, (await get(`/v2/assistant/actions/${encodeURIComponent(id)}`)).action); }
      catch { host.innerHTML = '<div class="asst-state">This card is not yours to see.</div>'; }
    }
  }

  // The Assistant page when the assistant is off: the owner is offered the switch, anyone else is told whom to ask.
  // `after` runs once it is on.
  function off(host, info, deps) {
    if (!host) return;
    style();
    const {post, esc, after} = deps;
    host.innerHTML = `<div class="asst-off"><p><strong>The Assistant is off.</strong> ${info.can_turn_on
      ? `Turn it on to get a private assistant that finds things in ${esc(document.title || 'Tico')} and does them for you.`
      : `Ask the owner of this team to turn it on.`}</p>
      ${info.can_turn_on ? '<button class="primary" type="button" data-turn-on>Turn on the Assistant</button> <span class="asst-state" data-turn-status></span>' : ''}</div>`;
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
    host.innerHTML = `<section class="card" data-assistant-off><header><h2>The Assistant is off</h2></header>
      <p>Everyone gets a private Assistant once it is on. It ${info.state === 'archived' ? 'was set aside when this team was set up; turning it on brings it back with its history.' : 'has not been added to this team yet; turning it on adds it from the template.'}</p>
      <div class="asst-row"><button class="primary" type="button" data-turn-on>Turn on Assistant</button><span class="asst-state" data-turn-status role="status"></span></div></section>`;
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
