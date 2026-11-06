/* ui/app/meetings-sources.js — Sources on the Meetings page: every meeting source in one dialog (docs/meetings.md)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Connected sources come first, each with its status, last sync and why it fails; the rest offer Connect.
// The viewer's own Granola account (state.granola, ui/app/meetings-granola.js) is per person; importers are the
// owner's (GET /meetings/sources rows, set up with ui/meeting-importers.js); Close is managed on its tool page.

// What needs a look: an importer that errors or lags, or the viewer's Granola signed out or failing.
function meetSourcesTrouble(state) {
  const g = state.granola;
  return (state.sourceRows || []).some(r => r.status === 'error' || r.status === 'delayed')
    || !!(g && (g.needs_signin || (g.connected && (g.last_error || state.gmsg))));
}
// The dot on the tab bar's Sources link.
function meetSourcesDot(state) {
  const button = $('#meet-sources .meet-src-btn');
  if (!button) return;
  const trouble = meetSourcesTrouble(state);
  button.querySelector('.dot')?.remove();
  if (trouble) button.insertAdjacentHTML('afterbegin', '<i class="dot" aria-hidden="true"></i>');
  button.setAttribute('aria-label', trouble ? 'Sources, needs a look' : 'Sources');
}

function meetSourceItems(state) {
  const owner = S.me?.role === 'owner', g = state.granola, rows = state.sourceRows || [];
  const on = [], off = [];
  const btn = (act, word, cls = 'ghost', extra = '') => `<button type="button" class="${cls} small" data-act="${act}"${extra}>${word}</button>`;
  const ownerOnly = owner ? '' : ' disabled title="The owner connects sources"';
  if (g) {
    const err = state.gflow ? '' : state.gmsg || ((g.connected || g.needs_signin) ? g.last_error || '' : '');
    if (g.needs_signin) on.push({id: 'granola', act: 'g', name: 'Granola', key: 'error', word: 'Signed out', err,
      facts: g.email ? `<span class="mg-email" title="${esc(g.email)}">${esc(g.email)}</span>` : '',
      actions: btn('g-connect', 'Sign in again', 'primary') + btn('g-remove', 'Remove', 'ghost danger-text')});
    else if (g.connected) on.push({id: 'granola', act: 'g', name: 'Granola', key: err ? 'error' : 'on', word: err ? 'Error' : 'Connected', err,
      facts: granolaFacts(g, state.gSyncing),
      actions: btn('g-sync', 'Sync now', 'ghost', state.gSyncing ? ' disabled' : '') + btn('g-remove', 'Remove', 'ghost danger-text')});
    else off.push({id: 'granola', act: 'g', name: 'Granola', err, actions: btn('g-connect', 'Connect', 'primary')});
  }
  for (const source of MEET_SOURCES) {
    // With account sign-in, a Granola importer row is the owner's API key.
    const name = source.id === 'granola' && g ? 'Granola API key' : source.name;
    const row = rows.find(r => r.id === source.id);
    if (source.id === 'close') {
      const link = word => `<a class="${row && row.status !== 'needs_setup' ? 'ghost' : 'primary'} small" href="#/tools/close-crm" data-act="close-open">${word}</a>`;
      if (row && row.status !== 'needs_setup') on.push({id: 'close', name, row, actions: link('Manage')});
      else off.push({id: 'close', name, actions: link('Connect')});
      continue;
    }
    if (row) {
      const st = meetSourceState(row, source.id);
      on.push({id: source.id, name, row, actions: owner ? (st.key === 'error' || st.key === 'delayed' ? btn('imp-connect', 'Reconnect', 'primary') : '')
        + btn('imp-remove', 'Remove', 'ghost danger-text') : ''});
    } else if (source.id !== 'granola' || !g) off.push({id: source.id, name, actions: btn('imp-connect', 'Connect', 'primary', ownerOnly)});
  }
  for (const item of on.filter(i => i.row)) {
    const st = meetSourceState(item.row, item.id);
    const when = item.row.last_succeeded_at;
    Object.assign(item, {key: st.key, word: st.word, err: item.row.error || (st.key === 'error' ? 'Sync failed' : ''),
      facts: `<span>${when ? 'Synced ' + esc(ago(when)) : 'Not synced yet'}</span>`});
  }
  return {on, off};
}

function meetSourceRowHTML(item, connected) {
  return `<li class="msrc-row" data-src="${esc(item.id)}"${connected ? ` data-state="${esc(item.key)}"` : ''}>${meetLogo(item.id, 28)}
    <div class="msrc-body"><div class="msrc-line"><b>${esc(item.name)}</b>${connected ? `<span class="msrc-st"><i class="dot" aria-hidden="true"></i>${esc(item.word)}</span>${item.facts || ''}` : ''}</div>
      ${item.err ? `<p class="msrc-err" role="alert">${esc(item.err)}</p>` : ''}</div>
    <div class="msrc-actions">${item.actions}</div></li>`;
}

function meetSourcesOpen(state, focus) {
  let dialog = $('#meet-src-dialog');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'tmodal msrc-dialog'; dialog.id = 'meet-src-dialog'; dialog.setAttribute('aria-label', 'Sources');
    dialog.onclick = e => { if (e.target === dialog) dialog.close(); };
    dialog.addEventListener('close', () => { dialog.remove(); $('#meet-sources .meet-src-btn')?.focus(); });
    document.body.append(dialog);
  }
  if (!dialog.open) dialog.showModal();
  meetSourcesPaint(state, focus);
  if (!focus) dialog.querySelector('[data-act=close-panel]')?.focus();
  meetSources(state);
}

// Re-render in place while open, keeping focus on the same control when it is still there.
function meetSourcesPaint(state, focus) {
  const dialog = $('#meet-src-dialog');
  if (!dialog || MEET !== state) return;
  const was = dialog.contains(document.activeElement) ? document.activeElement : null;
  const keep = was && {act: was.dataset.act, src: was.closest('[data-src]')?.dataset.src};
  const {on, off} = meetSourceItems(state);
  dialog.innerHTML = `<header class="mg-dhead"><h2>Sources</h2><button type="button" class="ghost" data-act="close-panel" aria-label="Close">✕</button></header>
    ${on.length ? `<ul class="msrc-list" aria-label="Connected">${on.map(i => meetSourceRowHTML(i, true)).join('')}</ul>` : ''}
    ${off.length ? `<ul class="msrc-list" aria-label="Not connected">${off.map(i => meetSourceRowHTML(i, false)).join('')}</ul>` : ''}`;
  dialog.querySelectorAll('[data-act]').forEach(el => {
    const act = el.dataset.act, id = el.closest('[data-src]')?.dataset.src;
    const source = MEET_SOURCES.find(s => s.id === id);
    if (act === 'close-open') { el.onclick = () => dialog.close(); return; }
    el.onclick = async () => {
      if (act === 'close-panel') dialog.close();
      else if (act === 'g-connect') granolaConnect(state);
      else if (act === 'g-sync') granolaSync(state, true);   // the button: may retry inside a backoff
      else if (act === 'g-remove') { if (confirm('Remove Granola?')) granolaDisconnect(state); }
      else if (act === 'imp-connect') window.openMeetingImporter?.(id, source.name, () => meetSources(state));
      else if (act === 'imp-remove') {
        const name = el.closest('.msrc-row').querySelector('b').textContent;
        if (!confirm(`Remove ${name}?`)) return;
        el.disabled = true;
        try { await post('/v2/meeting-importers/' + encodeURIComponent(id), {enabled: false, runner_id: ''}); }
        catch (error) { el.disabled = false; toast(error.message, true); return; }
        meetSources(state);
      }
    };
  });
  const target = focus === 'connect' ? dialog.querySelector('[data-src=granola] [data-act=g-connect]')
    : focus === 'more' ? dialog.querySelector('[data-src=granola] [data-act]:not(:disabled)')
    : keep ? dialog.querySelector(`[data-src="${keep.src}"] [data-act="${keep.act}"]`) || (keep.act === 'close-panel' ? dialog.querySelector('[data-act=close-panel]') : null)
    : null;
  // A control that just turned disabled (Sync now while syncing) hands focus to the next one on its row.
  const usable = target?.disabled ? target.closest('[data-src]')?.querySelector('[data-act]:not(:disabled)') : target;
  if (focus === 'connect' || focus === 'more' || keep) (usable || dialog.querySelector('[data-act=close-panel]'))?.focus({preventScroll: true});
}
