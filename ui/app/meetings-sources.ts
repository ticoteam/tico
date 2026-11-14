/* ui/app/meetings-sources.ts — Sources on the Meetings page: every meeting source in one dialog (docs/meetings.md)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Connected sources come first, each with its status, last sync and why it fails; the rest offer Connect.
// The viewer's own Granola account (state.granola, ui/app/meetings-granola.ts) is per person; importers are the
// owner's (GET /meetings/sources rows, set up with ui/meeting-importers.ts); Close is managed on its tool page.

// A sources row: MeetSourceRow (meetings.ts) plus the error an importer reports.
interface MsrcRow extends MeetSourceRow { error?: string | null }
// One line of the dialog: a connected source (row and status) or one that offers Connect.
interface MsrcItem { id: string; name: string; act?: string; key?: string; word?: string; err?: string; facts?: string; actions: string; row?: MsrcRow }
// An action control: a button can be disabled, a link cannot.
type MsrcControl = HTMLElement & {disabled?: boolean};
// The fields ui/app/meetings-granola.ts adds to MeetState.
interface MeetSourcesState extends MeetState { gmsg?: string; gflow?: GranolaFlow | null; gSyncing?: boolean }

// What needs a look: an importer that errors or lags, or the viewer's Granola signed out or failing.
function meetSourcesTrouble(state: MeetSourcesState) {
  const g = state.granola;
  return (state.sourceRows || []).some(r => r.status === 'error' || r.status === 'delayed')
    || !!(g && (g.needs_signin || (g.connected && (g.last_error || state.gmsg))));
}
// Focus goes back to Sources, or to the header's ⋯ that holds it once its menu has closed.
function meetSourcesFocusBack(): void {
  const button: HTMLElement | null = $('#meet-sources .meet-src-btn');
  (button?.checkVisibility() ? button : $('.notes-head > .ph-more'))?.focus();
}
// The dot on Sources, in the header's ⋯ menu.
function meetSourcesDot(state: MeetSourcesState) {
  const button: HTMLElement | null = $('#meet-sources .meet-src-btn');
  if (!button) return;
  const trouble = meetSourcesTrouble(state);
  button.querySelector('.dot')?.remove();
  if (trouble) button.insertAdjacentHTML('afterbegin', '<i class="dot" aria-hidden="true"></i>');
  button.setAttribute('aria-label', trouble ? 'Sources, needs a look' : 'Sources');
  // Sources sits in the header's ⋯ (task #338), so the ⋯ carries the same dot while the menu is closed.
  button.closest('.ph')?.querySelector(':scope > .ph-more')?.classList.toggle('meet-src-trouble', !!trouble && !button.closest('[hidden]'));
}

function meetSourceItems(state: MeetSourcesState) {
  const owner = S.me?.role === 'owner', g = state.granola, rows: MsrcRow[] = state.sourceRows || [];
  const on: MsrcItem[] = [], off: MsrcItem[] = [];
  const btn = (act: string, word: string, cls = 'ghost', extra = '') => `<button type="button" class="${cls} small" data-act="${act}"${extra}>${word}</button>`;
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
      const link = (word: string) => `<a class="${row && row.status !== 'needs_setup' ? 'ghost' : 'primary'} small" href="#/tools/close-crm" data-act="close-open">${word}</a>`;
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
    const row = item.row;
    if (!row) throw new Error(`Source ${item.id} has no row`);
    const st = meetSourceState(row, item.id);
    const when = row.last_succeeded_at;
    Object.assign(item, {key: st.key, word: st.word, err: row.error || (st.key === 'error' ? 'Sync failed' : ''),
      facts: `<span>${when ? 'Synced ' + esc(ago(when)) : 'Not synced yet'}</span>`});
  }
  return {on, off};
}

function meetSourceRowHTML(item: MsrcItem, connected: boolean) {
  return `<li class="msrc-row" data-src="${esc(item.id)}"${connected ? ` data-state="${esc(item.key)}"` : ''}>${meetLogo(item.id, 28)}
    <div class="msrc-body"><div class="msrc-line"><b>${esc(item.name)}</b>${connected ? `<span class="msrc-st"><i class="dot" aria-hidden="true"></i>${esc(item.word)}</span>${item.facts || ''}` : ''}</div>
      ${item.err ? `<p class="msrc-err" role="alert">${esc(item.err)}</p>` : ''}</div>
    <div class="msrc-actions">${item.actions}</div></li>`;
}

function meetSourcesOpen(state: MeetSourcesState, focus?: string) {
  let dialog: HTMLDialogElement | null = $('#meet-src-dialog');
  if (!dialog) {
    const made = document.createElement('dialog');
    made.className = 'tmodal msrc-dialog'; made.id = 'meet-src-dialog'; made.setAttribute('aria-label', 'Sources');
    made.onclick = e => { if (e.target === made) made.close(); };
    made.addEventListener('close', () => { made.remove(); meetSourcesFocusBack(); });
    document.body.append(made);
    dialog = made;
  }
  if (!dialog.open) dialog.showModal();
  meetSourcesPaint(state, focus);
  if (!focus) dialog.querySelector<HTMLElement>('[data-act=close-panel]')?.focus();
  meetSources(state);
}

// Re-render in place while open, keeping focus on the same control when it is still there.
function meetSourcesPaint(state: MeetSourcesState, focus?: string) {
  const dialog: HTMLDialogElement | null = $('#meet-src-dialog');
  if (!dialog || MEET !== state) return;
  const was = dialog.contains(document.activeElement) ? document.activeElement as HTMLElement : null;
  const keep = was && {act: was.dataset.act, src: was.closest<HTMLElement>('[data-src]')?.dataset.src};
  const {on, off} = meetSourceItems(state);
  dialog.innerHTML = `<header class="mg-dhead"><h2>Sources</h2><button type="button" class="ghost" data-act="close-panel" aria-label="Close">✕</button></header>
    ${on.length ? `<ul class="msrc-list" aria-label="Connected">${on.map(i => meetSourceRowHTML(i, true)).join('')}</ul>` : ''}
    ${off.length ? `<ul class="msrc-list" aria-label="Not connected">${off.map(i => meetSourceRowHTML(i, false)).join('')}</ul>` : ''}`;
  dialog.querySelectorAll<MsrcControl>('[data-act]').forEach(el => {
    const act = el.dataset.act, id = el.closest<HTMLElement>('[data-src]')?.dataset.src ?? '';
    const source = MEET_SOURCES.find(s => s.id === id);
    if (act === 'close-open') { el.onclick = () => dialog.close(); return; }
    el.onclick = async () => {
      if (act === 'close-panel') dialog.close();
      else if (act === 'g-connect') granolaConnect(state);
      else if (act === 'g-sync') granolaSync(state, true);   // the button: may retry inside a backoff
      else if (act === 'g-remove') { if (await askConfirm('Remove Granola?', el)) granolaDisconnect(state); }
      else if (act === 'imp-connect') {
        if (!source) throw new Error(`Unknown source ${id}`);
        window.openMeetingImporter?.(id, source.name, () => meetSources(state));
      }
      else if (act === 'imp-remove') {
        const label = el.closest('.msrc-row')?.querySelector('b');
        if (!label) throw new Error('Source row has no name');
        const name = label.textContent;
        if (!(await askConfirm(`Remove ${name}?`, el))) return;
        el.disabled = true;
        try { await post('/v2/meeting-importers/' + encodeURIComponent(id), {enabled: false, runner_id: ''}); }
        catch (error) { el.disabled = false; toast(errorMessage(error, 'Could not remove the source'), true); return; }
        meetSources(state);
      }
    };
  });
  const target = focus === 'connect' ? dialog.querySelector<MsrcControl>('[data-src=granola] [data-act=g-connect]')
    : focus === 'more' ? dialog.querySelector<MsrcControl>('[data-src=granola] [data-act]:not(:disabled)')
    : keep ? dialog.querySelector<MsrcControl>(`[data-src="${keep.src}"] [data-act="${keep.act}"]`) || (keep.act === 'close-panel' ? dialog.querySelector<MsrcControl>('[data-act=close-panel]') : null)
    : null;
  // A control that just turned disabled (Sync now while syncing) hands focus to the next one on its row.
  const usable = target?.disabled ? target.closest<HTMLElement>('[data-src]')?.querySelector<MsrcControl>('[data-act]:not(:disabled)') : target;
  if (focus === 'connect' || focus === 'more' || keep) (usable || dialog.querySelector<HTMLElement>('[data-act=close-panel]'))?.focus({preventScroll: true});
}
