/* ui/app/tools-key-drop.ts — Keys dropped in the Tools owner's chat (spec tools-chat-key-drop). Only the composer of
   that chat (thread-view.ts with opts.keyDrop, mounted by page-chat.ts on Tools) takes this; no other chat scans.
   A paste or dragged-in text is caught before it reaches the text box and scanned in the page (secret-drop.ts, no
   network, no model). Each secret becomes a locked item and the box gets its name as `[key:NAME]`; the rest goes in as
   words. Typed words are scanned 300 ms after the last key, and a certain match is locked at once. Values live only in
   this file's memory (KD): never the box, a DOM attribute, storage, a URL, a toast or the console. On Send each value
   goes straight to Credentials (POST /v2/credentials, source tools-chat, one request id per item, so a retry never
   saves twice); only when every save succeeded does the message go, with `[saved NAME]` in each item's place, and the
   values are dropped. The server checks the message again (backend/secret_drop.py) and refuses one that holds a key.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type KdStatus = 'locked' | 'saving' | 'saved' | 'failed';
interface KdItem extends SdItem {
  id: string;                // the item's own id: its token's key and its save's request id
  status: KdStatus;
  error: string;
  forTool: string;           // the tool it is for, pre-filled from the service or the tools in Problem
  existing: {id: string; revision: number} | null;  // a credential already has this name
  replace: boolean;          // the person chose Replace for an existing name
}
interface KdState {
  rules: SdRules | null;
  items: KdItem[];
  unmarked: string[];        // values the person said are not keys: sent as words, their digests let them through
  names: Map<string, {id: string; revision: number}>;
  admin: boolean | null;     // null until the inventory is read
  denied: string;            // why this person can't save keys (the server's words, naming the admins)
  tools: string[];           // the tools in Problem on the page, by name
  timer: number;
  sent: string;              // the proof line after Send: the names that went
}
const KD = new WeakMap<ThvView, KdState>();
const KD_TOKEN = /\[key:([A-Z0-9_]+)\]/g;
const KD_LOCK = '<span class="kd-ico" aria-hidden="true">🔒</span>';
const kdId = (): string => 'kd-' + newRequestId();
const kdBox = (view: ThvView): HTMLTextAreaElement | null => view.composer?.el.querySelector<HTMLTextAreaElement>('.p-text') || null;

/** Wires a composer for keys: the box's opt-outs, the paste, drop and typing scans, the tray and Mark as secret. */
function kdWire(view: ThvView, tools: () => string[]): void {
  const C = view.composer, box = kdBox(view);
  if (!C || !box) return;
  const state: KdState = {rules: null, items: [], unmarked: [], names: new Map(), admin: null, denied: '', tools: tools(), timer: 0, sent: ''};
  KD.set(view, state);
  // The credential card's opt-outs (ui/credential-card.ts): no password manager, spellcheck or autocorrect reads it.
  for (const [k, v] of [['autocomplete', 'off'], ['autocorrect', 'off'], ['autocapitalize', 'off'], ['spellcheck', 'false'],
    ['data-lpignore', 'true'], ['data-1p-ignore', ''], ['data-form-type', 'other']]) box.setAttribute(k!, v!);
  C.el.querySelector<HTMLElement>('.thv-attach-wrap')?.setAttribute('hidden', '');
  const pill = C.el.querySelector<HTMLElement>('.p-pill');
  pill?.insertAdjacentHTML('beforebegin', '<div class="kd-tray" data-kd-tray hidden></div>');
  pill?.querySelector('.p-send')?.insertAdjacentHTML('beforebegin', '<button type="button" class="ghost small kd-mark" data-kd-mark title="Lock the selected or last word as a key">Mark as secret</button>');
  pill?.insertAdjacentHTML('afterend', '<p class="kd-proof muted" data-kd-proof aria-live="polite"></p>');
  // Capture, so the paste never reaches the box or the shared paste handler (pill.ts pillPasteWire).
  box.addEventListener('paste', ev => {
    ev.preventDefault(); ev.stopImmediatePropagation();
    const files = pastedFiles(ev.clipboardData);
    if (files.length) { kdNote(view, 'Files can’t go in this chat. Paste the key as text.'); return; }
    void kdInsert(view, ev.clipboardData?.getData('text/plain') || '');
  }, true);
  C.el.addEventListener('dragover', ev => { ev.preventDefault(); if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'copy'; }, true);
  C.el.addEventListener('drop', ev => {
    ev.preventDefault(); ev.stopImmediatePropagation();
    if (ev.dataTransfer?.files.length) { kdNote(view, 'Files can’t go in this chat. Paste the key as text.'); return; }
    void kdInsert(view, ev.dataTransfer?.getData('text/plain') || '');
  }, true);
  box.addEventListener('input', () => {
    clearTimeout(state.timer);
    state.timer = window.setTimeout(() => void kdScanTyped(view), 300);
    kdForgetRemoved(view);
  });
  C.el.addEventListener('click', ev => kdClick(view, ev));
  C.el.addEventListener('change', ev => kdChange(view, ev));
  void kdLoad(view);
}

async function kdLoad(view: ThvView): Promise<void> {
  const state = KD.get(view);
  if (!state) return;
  state.rules = await sdRules();
  try {
    const inv = await get<{credentials?: {id: string; name: string; env?: string; revision?: number}[]; can_manage?: boolean}>('/v2/credentials', {fresh: true});
    for (const row of inv.credentials || []) {
      for (const n of [row.name, row.env]) if (n) state.names.set(n.toUpperCase(), {id: row.id, revision: Number(row.revision) || 1});
    }
    state.admin = !!inv.can_manage;
    if (!state.admin) state.denied = 'Only a credential administrator can save keys here.';
  } catch (e) {
    state.admin = false;
    state.denied = errorMessage(e, 'Only a credential administrator can save keys here.');
  }
  for (const item of state.items) kdExisting(state, item);
  kdDraw(view);
}

/** One line under the composer, never holding a value. */
function kdNote(view: ThvView, text: string): void {
  const line = view.composer?.el.querySelector<HTMLElement>('[data-kd-proof]');
  if (line) line.textContent = text;
}

function kdExisting(state: KdState, item: KdItem): void {
  item.existing = state.names.get(item.name) || null;
  if (!item.existing) item.replace = false;
}

function kdForTool(state: KdState, service: string): string {
  if (service) {
    const match = state.tools.find(t => t.toLowerCase().includes(service.toLowerCase()));
    return match || service;
  }
  return state.tools.length === 1 ? state.tools[0]! : '';
}

/** A unique, valid variable name: the box's tokens are keyed by it. */
function kdUniqueName(state: KdState, wanted: string, self?: KdItem): string {
  let base = sdEnvName(wanted) || 'SECRET';
  if (/^[0-9]/.test(base)) base = 'K_' + base;
  let name = base, n = 1;
  while (state.items.some(i => i !== self && i.name === name)) name = base + '_' + (++n);
  return name;
}

function kdItem(state: KdState, found: SdItem): KdItem {
  const item: KdItem = {...found, id: kdId(), status: 'locked', error: '', forTool: kdForTool(state, found.service), existing: null, replace: false};
  item.name = kdUniqueName(state, found.name, item);
  kdExisting(state, item);
  state.items.push(item);
  return item;
}

/** `text` with each secret replaced by its token: the items are made, the words come back. */
function kdLock(state: KdState, text: string, tiers: SdItem['tier'][]): string {
  if (!state.rules) return text;
  const t = sdNormalize(state.rules, text);
  const found = sdScan(state.rules, t).filter(f => tiers.includes(f.tier) && !state.unmarked.includes(f.value) && !kdInToken(t, f));
  let out = '', at = 0;
  for (const f of found) {
    const item = kdItem(state, f);
    out += t.slice(at, f.start) + '[key:' + item.name + ']';
    at = f.end;
  }
  return found.length ? out + t.slice(at) : text;
}
function kdInToken(text: string, f: SdItem): boolean {
  for (const m of text.matchAll(KD_TOKEN)) if (f.start < m.index + m[0].length && m.index < f.end) return true;
  return false;
}

async function kdInsert(view: ThvView, text: string): Promise<void> {
  const state = KD.get(view), box = kdBox(view);
  if (!state || !box || !text) return;
  state.rules ||= await sdRules();
  if (!state.rules) { kdNote(view, 'Can’t check this paste for keys right now, so it wasn’t added. Try again in a moment.'); return; }
  state.sent = '';
  const words = kdLock(state, text, ['certain', 'suspect']);
  box.setRangeText(words, box.selectionStart, box.selectionEnd, 'end');
  box.dispatchEvent(new Event('input', {bubbles: true}));
  kdDraw(view);
}

/** Typed words: a certain match is locked now; a suspect waits for Send, which scans again. */
async function kdScanTyped(view: ThvView): Promise<void> {
  const state = KD.get(view), box = kdBox(view);
  if (!state?.rules || !box) return;
  const before = box.value, after = kdLock(state, before, ['certain']);
  if (after === before) return;
  const caret = box.selectionEnd + (after.length - before.length);
  box.value = after;
  box.setSelectionRange(Math.max(0, caret), Math.max(0, caret));
  box.dispatchEvent(new Event('input', {bubbles: true}));
  kdDraw(view);
}

/** An item whose token was deleted from the box is gone, value and all. */
function kdForgetRemoved(view: ThvView): void {
  const state = KD.get(view), box = kdBox(view);
  if (!state || !box) return;
  const names = new Set(Array.from(box.value.matchAll(KD_TOKEN), m => m[1]));
  const kept = state.items.filter(i => names.has(i.name) || i.status === 'saved');
  if (kept.length !== state.items.length) { state.items = kept; kdDraw(view); }
}

function kdReplaceToken(box: HTMLTextAreaElement, from: string, to: string): void {
  box.value = box.value.split('[key:' + from + ']').join(to);
}

function kdClick(view: ThvView, ev: MouseEvent): void {
  const state = KD.get(view), box = kdBox(view), target = eventElement(ev);
  if (!state || !box || !target) return;
  if (target.closest('[data-kd-mark]')) { ev.preventDefault(); kdMark(view); return; }
  const row = target.closest<HTMLElement>('[data-kd-item]'), item = row && state.items.find(i => i.id === row.dataset.kdItem);
  if (!item) return;
  const act = target.closest<HTMLElement>('[data-kd]')?.dataset.kd;
  if (act === 'remove') {
    kdReplaceToken(box, item.name, '');
    state.items = state.items.filter(i => i !== item);
  } else if (act === 'unmark') {
    // Not a key: the words go back in the box, and the server lets exactly this one through from here.
    state.unmarked.push(item.value);
    kdReplaceToken(box, item.name, item.value);
    state.items = state.items.filter(i => i !== item);
  } else if (act === 'replace') item.replace = true;
  else if (act === 'rename') { row.querySelector<HTMLInputElement>('[data-kd-name]')?.focus(); return; }
  else return;
  ev.preventDefault();
  box.dispatchEvent(new Event('input', {bubbles: true}));
  kdDraw(view);
}

function kdChange(view: ThvView, ev: Event): void {
  const state = KD.get(view), box = kdBox(view), target = eventElement(ev);
  const row = target?.closest<HTMLElement>('[data-kd-item]'), item = row && state?.items.find(i => i.id === row.dataset.kdItem);
  if (!state || !box || !item || item.status === 'saved') return;
  const input = target as HTMLInputElement | HTMLSelectElement;
  if (input.matches('[data-kd-name]')) {
    const name = kdUniqueName(state, input.value, item);
    kdReplaceToken(box, item.name, '[key:' + name + ']');
    item.name = name;
    kdExisting(state, item);
  } else if (input.matches('[data-kd-for]')) item.forTool = input.value;
  kdDraw(view);
}

/** Mark as secret: the selected words, or the last word typed, become an item. */
function kdMark(view: ThvView): void {
  const state = KD.get(view), box = kdBox(view);
  if (!state || !box) return;
  let start = box.selectionStart, end = box.selectionEnd;
  if (start === end) {
    const left = box.value.slice(0, end).replace(/\s+$/, '');
    end = left.length;
    start = left.search(/\S+$/);
  }
  const value = box.value.slice(start, end).trim();
  if (start < 0 || !value || /^\[key:[A-Z0-9_]+\]$/.test(value)) { kdNote(view, 'Select the key, or type it last, then Mark as secret.'); return; }
  const service = state.rules ? sdServiceNear(state.rules, box.value, start) : '';
  const item = kdItem(state, {tier: 'certain', rule: 'marked', kind: 'Key', service, name: service ? sdPrefixed(service, 'API_KEY') : 'SECRET',
    type: value.length >= 12 ? 'api_key' : 'password', username: '', start, end, value});
  box.setRangeText('[key:' + item.name + ']', start, end, 'end');
  box.dispatchEvent(new Event('input', {bubbles: true}));
  kdDraw(view);
}

function kdStatusHTML(item: KdItem): string {
  if (item.status === 'saving') return '<span class="kd-state">Saving…</span>';
  if (item.status === 'saved') return '<span class="kd-state kd-ok">✓ Saved</span>';
  if (item.status === 'failed') return `<span class="kd-state kd-bad" role="alert">! ${esc(item.error || 'Not saved')}</span>`;
  return item.tier === 'certain' ? `<span class="kd-state">${KD_LOCK} Locked</span>` : '<span class="kd-state">◌ Looks like a key</span>';
}

function kdItemHTML(state: KdState, item: KdItem, wide: boolean): string {
  const tools = Array.from(new Set([...state.tools, ...(item.forTool ? [item.forTool] : [])]));
  const forSelect = `<select data-kd-for aria-label="For which tool"${item.status === 'saved' ? ' disabled' : ''}><option value="">Choose</option>${
    tools.map(t => `<option${t === item.forTool ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  const kind = item.username ? `${esc(item.kind)} · ${esc(item.username)}` : esc(item.kind);
  const replace = item.existing && !item.replace && item.status !== 'saved'
    ? `<span class="kd-replace">Replaces the existing ${esc(item.name)} <button type="button" class="ghost small" data-kd="replace">Replace</button><button type="button" class="ghost small" data-kd="rename">Rename</button></span>` : '';
  return `<div class="kd-item ${item.tier === 'suspect' ? 'kd-suspect' : 'kd-certain'}" data-kd-item="${esc(item.id)}" role="group" aria-label="${esc(item.kind + ' ' + item.name)}">
    <span class="kd-kind">${item.tier === 'certain' ? KD_LOCK : '<span class="kd-ico" aria-hidden="true">◌</span>'} ${kind}</span>
    <input class="kd-name" data-kd-name value="${esc(item.name)}" aria-label="Variable name" autocomplete="off" spellcheck="false"${item.status === 'saved' ? ' disabled' : ''}>
    ${wide ? `<span class="kd-for">${forSelect}</span>` : ''}
    <span class="kd-preview tnum">${esc(sdPreview(item))}</span>
    ${kdStatusHTML(item)}
    ${item.tier === 'suspect' && item.status !== 'saved' ? '<button type="button" class="ghost small" data-kd="unmark">Not a key?</button>' : ''}
    ${item.status !== 'saved' ? `<button type="button" class="ghost small kd-x" data-kd="remove" aria-label="Remove ${esc(item.name)}" title="Remove">✕</button>` : ''}
    ${replace}</div>`;
}

function kdDraw(view: ThvView): void {
  const state = KD.get(view), el = view.composer?.el;
  const tray = el?.querySelector<HTMLElement>('[data-kd-tray]'), proof = el?.querySelector<HTMLElement>('[data-kd-proof]');
  if (!state || !tray || !proof) return;
  const items = state.items.filter(i => i.status !== 'saved');
  const wide = items.length >= 3;
  tray.hidden = !items.length;
  tray.classList.toggle('kd-wide', wide);
  tray.innerHTML = items.length ? (wide ? `<div class="kd-head">${items.length} keys from your paste</div>` : '')
    + items.map(i => kdItemHTML(state, i, wide)).join('')
    + (state.admin === false ? `<p class="kd-denied">${esc(state.denied)} <a href="#/tools/keys">Request a key</a></p>` : '') : '';
  const n = items.length;
  proof.textContent = state.sent || (n ? `${n} ${n === 1 ? 'key' : 'keys'} locked. They go to Credentials, encrypted. The bot only sees their names.` : '');
  const send = el?.querySelector<HTMLButtonElement>('.p-send');
  if (send) send.disabled = !!n && (state.admin === false || items.some(i => i.existing && !i.replace));
}

/** Before Send: scan once more, save every item, and give back the words to send (names only), or null to wait. */
async function kdBeforeSend(view: ThvView): Promise<{text: string; unmarked: string[]} | null> {
  const state = KD.get(view), box = kdBox(view);
  if (!state || !box) return null;
  state.rules ||= await sdRules();
  if (!state.rules) { kdNote(view, 'Can’t check this message for keys right now. Try again in a moment.'); return null; }
  const locked = kdLock(state, box.value, ['certain', 'suspect']);
  if (locked !== box.value) {
    box.value = locked;
    box.dispatchEvent(new Event('input', {bubbles: true}));
    kdDraw(view);
    kdNote(view, 'Some of this looked like a key, so it’s locked now. Check it and press Send again.');
    return null;
  }
  const names = new Set(Array.from(box.value.matchAll(KD_TOKEN), m => m[1]));
  const items = state.items.filter(i => names.has(i.name));
  if (items.length && state.admin !== true) { kdDraw(view); return null; }
  if (items.some(i => i.existing && !i.replace)) { kdDraw(view); return null; }
  for (const item of items.filter(i => i.status !== 'saved')) {
    item.status = 'saving'; item.error = '';
    kdDraw(view);
    try {
      const body = {name: item.name, kind: item.type, env: item.name, username: item.username || '', secret: item.value, source: 'tools-chat'};
      if (item.existing && item.replace) await post('/v2/credentials/' + encodeURIComponent(item.existing.id), {...body, expected_revision: item.existing.revision}, item.id);
      else await post('/v2/credentials', body, item.id);
      item.status = 'saved';
      item.value = '';   // saved: the value is gone from this page
    } catch (e) {
      item.status = 'failed';
      item.error = errorMessage(e, 'Not saved');
    }
  }
  kdDraw(view);
  if (items.some(i => i.status !== 'saved')) return null;
  let text = box.value;
  for (const item of items) text = text.split('[key:' + item.name + ']').join('[saved ' + item.name + (item.forTool ? ' for ' + item.forTool : '') + ']');
  const digests = await Promise.all(state.unmarked.filter(v => text.includes(v)).map(v => sdDigest(state.rules!, v)));
  state.sent = items.length ? 'Sent names only: ' + items.map(i => i.name).join(', ') + '.' : '';
  state.items = state.items.filter(i => !items.includes(i));
  state.unmarked = [];
  kdDraw(view);
  return {text: text.trim(), unmarked: digests};
}

/** The view is gone: every value with it. */
function kdForget(view: ThvView): void {
  const state = KD.get(view);
  if (!state) return;
  clearTimeout(state.timer);
  for (const item of state.items) item.value = '';
  state.items = []; state.unmarked = [];
  KD.delete(view);
}

// ---- the Tools owner's proposal card (backend/key_grants.py): rows the server built, Grant, then each key's check
interface KgBot { id: string; name: string }
interface KgReplaced { key: string; keeps: string[]; loses: string[] }
interface KgRow { key: string; tool_id: string; tool_name: string; level: string; bots: KgBot[]; username?: string; replaces?: KgReplaced | null }
interface KgResult { key: string; tool_id: string; ok: boolean; detail: string }
interface KgCard { id: string; state: string; rows: KgRow[]; results: KgResult[] }
const KG_LEVELS: Record<string, string> = {read: 'Read', read_write: 'Read and write'};
const KG_DROPPED = new Map<string, Set<number>>();   // per card message: the rows the person removed before Grant
const kgCard = (m: ThvMessage): KgCard | null => (m.refs?.card === 'key_grant' && m.refs.key_grant ? m.refs.key_grant as KgCard : null);

function kgCardHTML(m: ThvMessage): string {
  const card = kgCard(m);
  if (!card) return '';
  if (card.state !== 'proposed') {
    const results = card.results.length ? card.results : card.rows.map(r => ({key: r.key, tool_id: r.tool_id, ok: false, detail: 'Checking…'}));
    return `<div class="kg-card" data-kg="${esc(m.id)}" role="group" aria-label="Keys granted">${results.map(r => `<div class="kg-result ${r.ok ? 'kg-ok' : r.detail === 'Checking…' ? 'kg-wait' : 'kg-bad'}">
      <span aria-hidden="true">${r.ok ? '✓' : r.detail === 'Checking…' ? '…' : '!'}</span> <b>${esc(r.key)}</b> ${esc(r.detail)}
      ${r.ok || r.detail === 'Checking…' ? '' : `<span class="kg-fix"><button type="button" class="ghost small" data-kg-paste>Paste a new key</button><a class="ghost small" href="#/tools/api/${encodeURIComponent(r.tool_id)}">Open ${esc(card.rows.find(x => x.key === r.key)?.tool_name || 'the tool')} settings</a></span>`}</div>`).join('')}</div>`;
  }
  const dropped = KG_DROPPED.get(m.id) || new Set<number>();
  const kept = card.rows.map((_, i) => i).filter(i => !dropped.has(i));
  return `<div class="kg-card" data-kg="${esc(m.id)}" role="group" aria-label="Proposed key grants">
    <div class="kg-title">${esc(m.body?.split('\n')[0]?.startsWith('Proposal:') ? 'Proposal' : (m.body || '').split('\n')[0] || 'Proposal')}</div>
    ${card.rows.map((r, i) => dropped.has(i) ? '' : `<div class="kg-row" data-kg-row="${i}"><b>${esc(r.key)}</b> → ${esc(r.tool_name)} · ${esc(KG_LEVELS[r.level] || r.level)} · ${esc(r.bots.map(b => b.name).join(', '))}
      <button type="button" class="ghost small kd-x" data-kg-remove="${i}" aria-label="Remove ${esc(r.key)}" title="Remove">✕</button>${kgReplacesHTML(r)}</div>`).join('')}
    <div class="kg-acts"><button type="button" class="primary small" data-kg-grant${kept.length ? '' : ' disabled'}>Grant ${kept.length}</button></div>
    <p class="kg-err" role="alert" hidden></p></div>`;
}

// A key that takes the tool's place of another says so before Grant: the key replaced, who keeps their access on the
// new key, and who loses it (the server's own list, backend/key_grants.py _replaced).
function kgReplacesHTML(r: KgRow): string {
  const x = r.replaces;
  if (!x) return '';
  return `<span class="kg-replaces">Replaces ${esc(r.tool_name)}'s ${esc(KG_LEVELS[r.level] || r.level)} key ${esc(x.key)}.`
    + (x.keeps.length ? ` ${esc(x.keeps.join(', '))} keep${x.keeps.length === 1 ? 's' : ''} access on the new key.` : '')
    + (x.loses.length ? ` <b>${esc(x.loses.join(', '))} lose${x.loses.length === 1 ? 's' : ''} access.</b>` : '') + '</span>';
}

/** A press on a proposal card: remove a row, Grant, or Paste a new key. True when it was the card's. */
function kgClick(view: ThvView, ev: Event): boolean {
  const target = eventElement(ev), host = target?.closest<HTMLElement>('[data-kg]');
  if (!target || !host) return false;
  const m = view.messages.find(x => x.id === host.dataset.kg), card = m && kgCard(m);
  if (!m || !card) return false;
  const remove = target.closest<HTMLElement>('[data-kg-remove]');
  if (remove) {
    const set = KG_DROPPED.get(m.id) || new Set<number>();
    set.add(Number(remove.dataset.kgRemove));
    KG_DROPPED.set(m.id, set);
    thvDraw(view);
    return true;
  }
  if (target.closest('[data-kg-paste]')) { kdBox(view)?.focus(); return true; }
  const grant = target.closest<HTMLButtonElement>('[data-kg-grant]');
  if (!grant) return false;
  const dropped = KG_DROPPED.get(m.id) || new Set<number>();
  const keep = card.rows.map((_, i) => i).filter(i => !dropped.has(i));
  grant.disabled = true; grant.textContent = 'Granting…';
  m.refs = {...m.refs, key_grant: {...card, state: 'granted', rows: keep.map(i => card.rows[i]!), results: []}};
  void post('/v2/tools-chat/proposals/' + encodeURIComponent(m.id) + '/grant', {keep}).then((done: KgCard) => {
    m.refs = {...m.refs, key_grant: done};
    KG_DROPPED.delete(m.id);
    thvDraw(view);
  }).catch((e: unknown) => {
    m.refs = {...m.refs, key_grant: card};
    thvDraw(view);
    const err = view.el.querySelector<HTMLElement>(`[data-kg="${CSS.escape(m.id)}"] .kg-err`);
    if (err) { err.textContent = errorMessage(e, 'Could not grant'); err.hidden = false; }
  });
  thvDraw(view);
  return true;
}
