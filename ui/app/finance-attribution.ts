/* ui/app/finance-attribution.ts — Finance attribution (the attribution contract): which teammates and teams a posted line
   counts for, in shares. Transactions shows a row's split as chips; the rail of a selected line lists its "Attributed
   to" parts (source, when, Edit, Undo, History); Edit is a dialog of share rows with a running total. The Attribution
   tab lists the rules (Name, Applies to, Split, On, and a row menu with Edit and Delete) with + Rule.
   Shares are basis points in the API (share_bps, 1..10000 a part, up to 10 parts, total at most 10000); the dialogs
   show percentages and send share_bps = % × 100. Nothing here is gated for approval: the writes are the owner's, or
   anyone holding finance.attribution, and they go straight through.
   Reads: GET /api/v2/finance/lines/{line_id}/attribution, GET /api/v2/finance/attribution-rules.
   Writes (POST /api/v2/finance/actions): attribute, attribute_undo, attribution_rule_set, attribution_rule_delete.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/finance-attribution.js, which the page runs in this file's place. */
'use strict';

// One part of a split as the API sends it: a teammate ("bot:<slug>" / "human:<id>"), a team, or both.
interface FinShare { teammate?: string; team?: string; share_bps: number; amount?: number | null }
// A line's attribution (GET /v2/finance/lines/{line_id}/attribution); history is newest first.
interface FinAttribution {
  splits: FinShare[];
  source: 'tags' | 'rule' | 'person' | 'bot';
  rule_id?: string | null;
  set_by?: string;
  set_at?: string | null;
  can_change: boolean;
  history: {splits: FinShare[]; source: string; set_by?: string; set_at?: string | null}[];
}
// One rule as GET /v2/finance/attribution-rules sends it.
interface FinRule {
  id: string;
  name: string;
  match: {kind: string; id: string; label: string};
  splits: FinShare[];
  enabled: boolean;
  apply_from?: string | null;
}
interface FinPick { id: string; name: string }
// The access read's parts this file uses (GET /api/v2/finance/access), and a rules read.
interface FinAccessLite { teammates?: {teammate?: string}[]; teams?: {id: string; name: string}[] }
interface FinRulesRead { rules?: FinRule[]; can_change?: boolean }
interface FinNamed { id: string; name: string }
interface FinAttrState { rules: FinRule[]; can: boolean; load: number }

const FIN_SHARE_MAX = 10;      // parts in one split (the contract: 1 to 10)
const FIN_SHARE_ALL = 10000;   // basis points in a whole line
// A rule's match: its API kind, the word the table shows, and the choice the rule dialog offers.
const FIN_SHARE_KINDS: [string, string, string][] = [['vendor', 'Vendor', 'Vendor'], ['account', 'Account', 'P&L account'],
  ['money_account', 'Card', 'Card or bank']];
// The lines this session has read, by line id, and the rules' names (for "Rule: <name>"); kept for the visit.
const FIN_SHARE = {at: new Map<string, FinAttribution>(), names: new Map<string, string>()};

// ----------------------------------------------------------------- formats
const finShareEl = <T extends Element = HTMLElement>(root: ParentNode, sel: string): T => root.querySelector(sel) as T;
// 50, 33.33: a percentage of basis points, without trailing zeros.
const finSharePct = (bps: number): string => String(Number((bps / 100).toFixed(2)));
// The percentage a field reads as basis points: "50" → 5000, "33.33" → 3333; NaN for what is not one.
function finShareBps(text: string): number {
  const t = String(text ?? '').trim();
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(t)) return NaN;
  return Math.round(Number(t) * 100);
}
const finShareName = (p: FinShare): string => p.teammate ? finWho(p.teammate) : finTeamName(p.team || '');
// The picker's value for a part, in the form finAttrTags reads back (finance-docs.js): "teammate:bot:x" or "team:id".
const finShareRef = (p: FinShare): string => p.teammate ? 'teammate:' + p.teammate : p.team ? 'team:' + p.team : '';
const finShareSum = (parts: FinShare[]): number => parts.reduce((a, p) => a + p.share_bps, 0);
// A part as the API takes it: only the keys it has.
const finShareOut = (p: FinShare): FinShare => ({...(p.teammate ? {teammate: p.teammate} : {}), ...(p.team ? {team: p.team} : {}), share_bps: p.share_bps});

// Chips for a split on a row: two parts, a "+n" chip for the rest, and the part left unattributed.
function finShareChips(parts: FinShare[]): string {
  const shown = parts.slice(0, 2), rest = parts.length - shown.length, free = FIN_SHARE_ALL - finShareSum(parts);
  const all = parts.map(p => `${finShareName(p)} ${finSharePct(p.share_bps)}%`).join(' · ');
  return `<span class="fin-chips" title="${esc(all)}">${shown.map(p => `<span class="fin-chip">${esc(finShareName(p))} ${finSharePct(p.share_bps)}%</span>`).join('')}${rest > 0 ? `<span class="fin-chip">+${rest}</span>` : ''}${free > 0 ? `<span class="fin-chip fin-chip-muted">${finSharePct(free)}% unattributed</span>` : ''}</span>`;
}

// ----------------------------------------------------------------- the share rows (a dialog's part)
function finShareOpts(selected: string): string {
  const acc = (FIN?.acc || FIN_ACCESS) as FinAccessLite | null;
  const mates: FinPick[] = (acc?.teammates || []).filter(t => t.teammate).map(t => ({id: 'teammate:' + t.teammate, name: finWho(t.teammate)}));
  const teams: FinPick[] = (acc?.teams || []).map(t => ({id: 'team:' + t.id, name: t.name}));
  const all = [...mates, ...teams];
  // A part that is no longer on the list (a teammate who left) still shows, so it can be changed.
  if (selected && !all.some(o => o.id === selected)) all.unshift({id: selected, name: finWho(selected.replace(/^(teammate|team):/, ''))});
  const opt = (o: FinPick) => `<option value="${esc(o.id)}"${o.id === selected ? ' selected' : ''}>${esc(o.name)}</option>`;
  return `<option value=""${selected ? '' : ' selected'}>Teammate or team</option>${mates.length ? `<optgroup label="Teammates">${mates.sort((a, b) => a.name.localeCompare(b.name)).map(opt).join('')}</optgroup>` : ''}${teams.length ? `<optgroup label="Teams">${teams.sort((a, b) => a.name.localeCompare(b.name)).map(opt).join('')}</optgroup>` : ''}${all.some(o => o.id === selected) && !mates.some(o => o.id === selected) && !teams.some(o => o.id === selected) ? opt(all.find(o => o.id === selected)!) : ''}`;
}
function finShareRowHtml(who: string, pct: string): string {
  return `<div class="fin-share-row" data-share-row><select aria-label="Teammate or team">${finShareOpts(who)}</select>
    <input inputmode="decimal" autocomplete="off" aria-label="Share, %" value="${esc(pct)}" placeholder="%"><button type="button" class="ghost" data-share-x aria-label="Remove this share">✕</button></div>`;
}
// The rows of a split with its "+" and running total. Empty parts start with one blank row.
function finShareRowsHtml(parts: FinShare[]): string {
  const rows = parts.length ? parts.map(p => finShareRowHtml(finShareRef(p), finSharePct(p.share_bps))).join('') : finShareRowHtml('', '');
  return `<div class="fin-share-rows" data-share-rows>${rows}</div>
    <div class="fin-share-foot"><button type="button" class="ghost" data-share-add aria-label="Add a share">+ Share</button><span class="muted fin-small" data-share-total aria-live="polite"></span></div>`;
}
// Keeps the total line true as rows change: unattributed under 100%, a refusal over it.
function finShareWire(root: ParentNode): void {
  const rows = finShareEl<HTMLElement>(root, '[data-share-rows]'), total = finShareEl<HTMLElement>(root, '[data-share-total]'),
    add = finShareEl<HTMLButtonElement>(root, '[data-share-add]');
  const sync = () => {
    const inputs = [...rows.querySelectorAll<HTMLInputElement>('[data-share-row] input')];
    const sum = inputs.reduce((a, i) => a + (Number.isNaN(finShareBps(i.value)) ? 0 : finShareBps(i.value)), 0);
    add.disabled = rows.querySelectorAll('[data-share-row]').length >= FIN_SHARE_MAX;
    total.textContent = sum > FIN_SHARE_ALL ? `${finSharePct(sum)}% · over 100%` : `Unattributed ${finSharePct(FIN_SHARE_ALL - sum)}%`;
    total.classList.toggle('fin-bad', sum > FIN_SHARE_ALL);
  };
  rows.onclick = ev => {
    const x = (ev.target as HTMLElement).closest('[data-share-x]');
    if (x) { x.closest('[data-share-row]')?.remove(); sync(); }
  };
  rows.oninput = sync;
  rows.onchange = sync;
  add.onclick = () => { rows.insertAdjacentHTML('beforeend', finShareRowHtml('', '')); sync(); };
  sync();
}
// The split the rows hold, validated: each row a teammate or team, a share from 0.01 to 100, no repeats, total ≤ 100%.
function finShareRead(root: ParentNode): FinShare[] {
  const out: FinShare[] = [], seen = new Set<string>();
  let total = 0;
  for (const row of root.querySelectorAll<HTMLElement>('[data-share-row]')) {
    const who = finShareEl<HTMLSelectElement>(row, 'select').value;
    const text = finShareEl<HTMLInputElement>(row, 'input').value;
    if (!who) throw new Error('Pick a teammate or team');
    const bps = finShareBps(text);
    if (!(bps >= 1 && bps <= FIN_SHARE_ALL)) throw new Error('A share from 0.01 to 100%, like 50');
    if (seen.has(who)) throw new Error('Each teammate or team once');
    seen.add(who);
    total += bps;
    out.push({...finAttrTags(who), share_bps: bps} as FinShare);
  }
  if (!out.length) throw new Error('Add a share');
  if (total > FIN_SHARE_ALL) throw new Error(`The shares add to ${finSharePct(total)}%, over 100%`);
  return out;
}

// ----------------------------------------------------------------- the rail's "Attributed to"
// The line ids a Transactions row holds (a bank row's lines, or an entry's own line) and the rail's section for each.
async function finShareRailLoad(key: string, lines: {line_id: string; account?: string}[]): Promise<void> {
  const box = document.getElementById('fin-tx-share');
  if (!box || !lines.length) return;
  const reads = await Promise.all(lines.map(l => get(`/v2/finance/lines/${encodeURIComponent(l.line_id)}/attribution${finAs('?')}`, {fresh: true})
    .then((r: unknown) => { const a = r as FinAttribution | null; return a && Array.isArray(a.splits) && Array.isArray(a.history) ? a : null; }, () => null)));
  if (FIN?.tx?.sel !== key || document.getElementById('fin-tx-share') !== box) return;
  const found = reads.map((a, i) => a ? {line: lines[i], a} : null).filter(Boolean) as {line: {line_id: string; account?: string}; a: FinAttribution}[];
  if (!found.length) { box.innerHTML = ''; return; }
  // A rule's name, for "Rule: <name>": read once, when a line shows one (none when the viewer may not read the rules).
  if (found.some(f => f.a.source === 'rule') && !FIN_SHARE.names.size) {
    try { for (const r of ((await get('/v2/finance/attribution-rules' + finAs('?'), {fresh: true})) as FinRulesRead | null)?.rules || []) FIN_SHARE.names.set(r.id, r.name); }
    catch { /* the rule shows as "Rule" */ }
  }
  for (const f of found) FIN_SHARE.at.set(f.line.line_id, f.a);
  box.innerHTML = found.map(f => finShareSection(f.line, f.a, found.length > 1)).join('');
  box.onclick = ev => {
    const t = ev.target as HTMLElement;
    const edit = t.closest<HTMLElement>('[data-share-edit]');
    if (edit) { finShareLineDialog(edit.dataset.shareEdit || ''); return; }
    const undo = t.closest<HTMLElement>('[data-share-undo]');
    if (undo) void finShareUndo(undo.dataset.shareUndo || '');
  };
}
function finShareSection(line: {line_id: string; account?: string}, a: FinAttribution, many: boolean): string {
  const parts = a.splits || [], free = FIN_SHARE_ALL - finShareSum(parts);
  const rows = parts.map(p => finKv(finShareName(p), `${finSharePct(p.share_bps)}%${p.amount != null ? ' · ' + finUsd(p.amount) : ''}`)).join('')
    + (free > 0 ? finKv('Unattributed', `${finSharePct(free)}%`, 'muted') : '');
  return `<div class="fin-rail-sec" data-share-line="${esc(line.line_id)}"><h3 class="fin-h">Attributed to${many && line.account ? ' · ' + esc(line.account) : ''}</h3>
    ${rows}
    <p class="muted fin-small">${esc(finShareSource(a))}</p>
    <div class="fin-share-actions">${a.can_change ? `<button type="button" class="ghost" data-share-edit="${esc(line.line_id)}">Edit</button>` : ''}${a.history.length > 1 ? `<button type="button" class="ghost" data-share-undo="${esc(line.line_id)}">Undo</button>` : ''}</div>
    ${a.history.length ? `<details class="fin-share-hist"><summary>History</summary>${a.history.map(h => finKv(`${h.set_at ? finDayLabel(h.set_at.slice(0, 10)) : ''} · ${finShareWhoOf(h)}`,
      esc(h.splits.length ? h.splits.map(p => `${finShareName(p)} ${finSharePct(p.share_bps)}%`).join(' · ') : 'No split'))).join('')}</details>` : ''}</div>`;
}
// Where a line's split came from, and when it was set: "Tags", "Rule: <name>", "Set by <name>".
function finShareSource(a: FinAttribution): string {
  const when = a.set_at && a.source !== 'tags' ? ' · ' + finDayLabel(a.set_at.slice(0, 10)) : '';
  if (a.source === 'rule') return `Rule${a.rule_id ? ': ' + (FIN_SHARE.names.get(a.rule_id) || 'Rule') : ''}${when}`;
  if (a.source === 'person' || a.source === 'bot') return `Set by ${finWho(a.set_by || '')}${when}`;
  return 'Tags';
}
const finShareWhoOf = (h: {source: string; set_by?: string}): string => h.source === 'rule' ? 'Rule' : h.source === 'tags' ? 'Tags' : finWho(h.set_by || '');

// Edit: a dialog of the line's shares; Save sends them, Clear removes the override (splits: []).
function finShareLineDialog(lineId: string): void {
  const a = FIN_SHARE.at.get(lineId);
  if (!a) return;
  const refresh = () => { if (FIN?.tx) void finTxLoad(false); };
  const dlg = finDialog('Attributed to', `${finShareRowsHtml(a.splits)}
    <div class="fin-share-clear"><button type="button" class="ghost" data-share-clear>Clear</button></div>`, 'Save', async (form: HTMLFormElement) => {
    const splits = finShareRead(form);
    await finAct('attribute', {line_id: lineId, splits});
    refresh();
  });
  finShareWire(dlg);
  finShareEl<HTMLButtonElement>(dlg, '[data-share-clear]').onclick = async () => {
    try { await finAct('attribute', {line_id: lineId, splits: []}); dlg.close(); refresh(); }
    catch (e) { const err = finShareEl<HTMLElement>(dlg, '[data-error]'); err.textContent = (e as Error).message; err.hidden = false; }
  };
}
async function finShareUndo(lineId: string): Promise<void> {
  try { await finAct('attribute_undo', {line_id: lineId}); toast('Undone'); }
  catch (e) { toast((e as Error).message, true); return; }
  if (FIN?.tx) void finTxLoad(false);
}

// ----------------------------------------------------------------- the Attribution tab
// Shown to the owner and anyone holding finance.attribution (or finance.admin): the rules, and + Rule for those who may change them.
function finAttrPage(): void {
  const state: FinAttrState = {rules: [], can: false, load: 0};
  FIN.attr = state;
  $('#fin-body')!.innerHTML = `<div class="fin-bar"><span class="spacer"></span><button type="button" class="primary" data-fin-rule-add hidden>+ Rule</button></div>
    <div class="fin-scroll" id="fin-rules">${skelRows(4, {dot: false})}</div>`;
  const body = $('#fin-body')!;
  body.onclick = (ev: MouseEvent) => {
    const t = ev.target as HTMLElement;
    if (t.closest('[data-fin-rule-add]')) { finRuleDialog(null); return; }
    const menu = t.closest<HTMLElement>('[data-fin-rule-menu]');
    if (menu) { finPop(menu, menu.nextElementSibling as HTMLElement); return; }
    const edit = t.closest<HTMLElement>('[data-fin-rule-edit]');
    if (edit) { finRuleDialog(state.rules[Number(edit.dataset.finRuleEdit)] || null); return; }
    const del = t.closest<HTMLElement>('[data-fin-rule-del]');
    if (del) {
      // The delete is confirmed in the menu itself (no browser dialog).
      const rule = state.rules[Number(del.dataset.finRuleDel)], pop = del.closest('.fin-pop') as HTMLElement;
      if (rule && pop) pop.innerHTML = `<span class="muted fin-small">Delete ${esc(rule.name)}? Its lines go back to their tags.</span>
        <div class="fin-confirm"><button type="button" class="ghost" data-fin-rule-keep>Keep</button><button type="button" class="primary" data-fin-rule-yes="${Number(del.dataset.finRuleDel)}">Delete</button></div>`;
      return;
    }
    if (t.closest('[data-fin-rule-keep]')) { finAttrDraw(); return; }
    const yes = t.closest<HTMLElement>('[data-fin-rule-yes]');
    if (yes) void finRuleDelete(state.rules[Number(yes.dataset.finRuleYes)]);
  };
  body.onchange = (ev: Event) => {
    const on = (ev.target as HTMLElement).closest<HTMLInputElement>('input[data-fin-rule-on]');
    if (on) void finRuleToggle(on, state.rules[Number(on.dataset.finRuleOn)]);
  };
  void finAttrLoad();
}
async function finAttrLoad(): Promise<void> {
  const state = FIN.attr as FinAttrState | undefined;
  if (!state) return;
  const load = ++state.load;
  let d: FinRulesRead | null;
  try { d = await get('/v2/finance/attribution-rules' + finAs('?'), {fresh: true}); }
  catch (e) { if (FIN?.attr === state && $('#fin-rules')) $('#fin-rules')!.innerHTML = finFail(e); return; }
  if (FIN?.attr !== state || state.load !== load || !$('#fin-rules')) return;
  state.rules = d?.rules || [];
  state.can = !!d?.can_change;
  for (const r of state.rules) FIN_SHARE.names.set(r.id, r.name);
  const add = $('[data-fin-rule-add]') as HTMLElement | null;
  if (add) add.hidden = !state.can;
  finAttrDraw();
}
function finAttrDraw(): void {
  const state = FIN.attr as FinAttrState | undefined, box = $('#fin-rules');
  if (!box || !state) return;
  if (!state.rules.length) { box.innerHTML = finNote('No rules.'); return; }
  box.innerHTML = `<table class="fin-t"><thead><tr><th>Name</th><th>Applies to</th><th>Split</th><th>On</th><th aria-label="Actions"></th></tr></thead><tbody>${state.rules.map((r, i) => {
    const word = FIN_SHARE_KINDS.find(k => k[0] === r.match.kind)?.[1] || '';
    const menu = state.can ? `<span class="fin-pop-at"><button type="button" class="ghost" data-fin-rule-menu aria-label="${esc(r.name)}: actions" aria-haspopup="menu" aria-expanded="false">⋯</button>
        <div class="fin-pop fin-menu" role="menu" hidden><button type="button" role="menuitem" data-fin-rule-edit="${i}">Edit</button><button type="button" role="menuitem" data-fin-rule-del="${i}">Delete</button></div></span>` : '';
    return `<tr data-fin-rule-row="${i}"><td>${esc(r.name)}</td><td><span class="muted">${esc(word)}</span> ${esc(r.match.label || '')}</td><td>${finShareChips(r.splits)}</td>
      <td><input type="checkbox" role="switch" class="people-switch" aria-label="${esc(r.name)} on"${r.enabled ? ' checked' : ''}${state.can ? '' : ' disabled'} data-fin-rule-on="${i}"></td><td>${menu}</td></tr>`;
  }).join('')}</tbody></table>`;
}
// The rule's splits and match as its set action takes them.
const finRuleBody = (r: FinRule) => ({id: r.id, name: r.name, match: {kind: r.match.kind, id: r.match.id}, splits: r.splits.map(finShareOut)});
async function finRuleToggle(input: HTMLInputElement, rule: FinRule | undefined): Promise<void> {
  if (!rule) return;
  const on = input.checked;
  try { await finAct('attribution_rule_set', {...finRuleBody(rule), enabled: on}); rule.enabled = on; }
  catch (e) { input.checked = !on; toast((e as Error).message, true); }
}
async function finRuleDelete(rule: FinRule | undefined): Promise<void> {
  if (!rule) return;
  try { await finAct('attribution_rule_delete', {id: rule.id}); toast('Deleted'); }
  catch (e) { toast((e as Error).message, true); }
  void finAttrLoad();
}
// The lines a rule can apply to, by kind: vendors (contacts), P&L accounts (the chart) or the bank and card accounts.
async function finRulePicks(kind: string): Promise<FinPick[]> {
  try {
    if (kind === 'vendor') return ((await finContacts('vendor')) as FinNamed[]).map(c => ({id: c.id, name: c.name}));
    if (kind === 'account') return ((await finChart('journal')) as FinNamed[]).map(a => ({id: a.id, name: a.name}));
    return await finPayFrom();
  } catch { return []; }
}
// + Rule, or Edit on one: a name, what it applies to (a kind and one of its lines), the split, and from when it applies.
function finRuleDialog(rule: FinRule | null): void {
  const kind0 = rule?.match.kind || 'vendor', match0 = rule?.match.id || '';
  const dlg = finDialog(rule ? 'Rule' : 'New rule', `<label>Name<input name="name" required maxlength="80" autocomplete="off" value="${esc(rule?.name || '')}"></label>
    <label>Applies to<select name="kind">${FIN_SHARE_KINDS.map(([v, , l]) => `<option value="${v}"${v === kind0 ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
    <label>Match<select name="match" required></select></label>
    <div class="fin-share-label muted fin-small">Split</div>${finShareRowsHtml(rule?.splits || [])}
    <label>Apply to lines from<input type="date" name="apply_from" value="${esc(rule?.apply_from || '')}"></label>`,
  rule ? 'Save' : 'Add', async (form: HTMLFormElement) => {
    const f = (n: string) => form.elements.namedItem(n) as HTMLInputElement;
    const name = f('name').value.trim();
    if (!name) throw new Error('Name the rule');
    const match = (form.elements.namedItem('match') as HTMLSelectElement).value;
    if (!match) throw new Error('Pick what it applies to');
    const splits = finShareRead(form);
    const from = f('apply_from').value;
    await finAct('attribution_rule_set', {...(rule ? {id: rule.id} : {}), name, match: {kind: f('kind').value, id: match}, splits,
      enabled: rule ? rule.enabled : true, ...(from ? {apply_from: from} : {})});
    void finAttrLoad();
  });
  finShareWire(dlg);
  const form = dlg.querySelector('form') as HTMLFormElement;
  const kind = form.elements.namedItem('kind') as HTMLSelectElement, match = form.elements.namedItem('match') as HTMLSelectElement;
  const lists = new Map<string, FinPick[]>();
  const fill = async () => {
    const k = kind.value;
    if (!lists.has(k)) lists.set(k, await finRulePicks(k));
    const keep = k === kind0 ? match0 : '';
    match.innerHTML = `<option value="">Pick…</option>${(lists.get(k) || []).map(p => `<option value="${esc(p.id)}"${p.id === keep ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}`;
  };
  kind.onchange = () => void fill();
  void fill();
}
