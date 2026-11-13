/* ui/app/growth-sources.ts — Sources (#/growth/sources, behind a link on the Growth page, which its head links back to): one source of truth per kind
   of number (GET /api/v2/growth/sources), one row each: what it is the truth for, the system, how bots reach it, what
   they may do and who owns it. A synced source (backend/growth_sync.py) also says what it fills and its health: Fresh,
   Stale or Failing, and since when. Two sources for one truth are flagged, and "Use X" keeps X and retires the others.
   The owner or an admin adds, edits and retires one, and connects a synced one: Connect a source maps a file's columns
   to Growth's numbers once, and its first file fills up to 12 months. They change without a publish.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface GrSourcesResponse { sources?: GrSource[]; conflicts?: string[]; may_edit?: boolean | null }
interface GrToolsResponse { integrations?: Array<{ service?: string | null; title?: string | null }> }
interface GrCredentialsResponse { credentials?: Array<{ name?: string | null }> }
interface GrSourceControls { truth_for: HTMLInputElement; system: HTMLInputElement; reach_kind: HTMLSelectElement; reach_ref: HTMLInputElement; owner_ref: HTMLSelectElement }

const GT_REACH: Array<[string, string]> = [['mcp', 'MCP'], ['credential', 'Credential'], ['url', 'URL'], ['person', 'Person']];
const GT_MAY: Array<[string, string]> = [['read', 'Read'], ['write', 'Write notes'], ['act', 'Act within rules']];
const gtMayList = (s: string | null | undefined): string[] => String(s || '').split(',').map(x => x.trim()).filter(Boolean);
const gtMayText = (s: string | null | undefined) => gtMayList(s).map(x => GT_MAY.find(m => m[0] === x)?.[1] || x).join(' · ');

function pageGrowthSources(state: GrState) { void gsrcRead(state); }
async function gsrcRead(state: GrState) {
  let r: GrSourcesResponse;
  try { r = await get<GrSourcesResponse>('/v2/growth/sources', {fresh: true}); }
  catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = {src: r.sources || [], conflicts: r.conflicts || [], may: r.may_edit ?? grAdmin()};
  gsrcDraw(state);
}
function gsrcDraw(state: GrState) {
  const body = $('#gr-body'), d = state.data;
  if (!body || !d) return;
  const may = d.may, list = d.src.filter(s => !s.retired_at).sort((a, b) => String(a.truth_for).localeCompare(String(b.truth_for)));
  patchHTML($('#strat-actions'), may ? '<button class="ghost" type="button" data-src-add>+ Source</button><button class="primary" type="button" data-src-connect>Connect a source</button>' : '');
  const banners = d.conflicts.map(t => {
    const rows = list.filter(s => String(s.truth_for).toLowerCase() === String(t).toLowerCase());
    return `<div class="gr-banner gr-amber" data-clash="${esc(t)}"><span><b>${esc(t)}</b> · ${esc(rows.map(s => s.system).join(', '))}</span><span class="spacer"></span>
      ${may ? rows.map(s => `<button class="ghost small" type="button" data-use="${esc(s.id)}">Use ${esc(s.system)}</button>`).join('') : ''}</div>`;
  }).join('');
  const reach = (s: GrSource) => `${esc(GT_REACH.find(r => r[0] === s.reach_kind)?.[1] || s.reach_kind || '')}${s.reach_ref ? ` <code>${esc(s.reach_ref)}</code>` : ''}`;
  const row = (s: GrSource) => {
    const inner = `<span class="gsrc-t">${esc(s.truth_for)}</span><span class="gsrc-s">${esc(s.system)}</span><span class="muted gsrc-r">${reach(s)}</span>
      <span class="muted gsrc-m">${esc(gtMayText(s.may))}</span><span class="muted gsrc-o">${esc(grWho(s.owner_ref))}</span>${gsrcSync(s)}`;
    return `<li data-src="${esc(s.id)}" data-health="${esc(s.health || 'not_synced')}">${may
      ? `<button type="button" class="gsrc-row" data-src-edit="${esc(s.id)}">${inner}</button>` : `<div class="gsrc-row">${inner}</div>`}</li>`;
  };
  body.innerHTML = banners + (list.length ? `<section class="card gsrc-list" aria-label="Sources"><ul class="gsrc-ul">${list.map(row).join('')}</ul></section>` : '');
  const add = $('#strat-actions [data-src-add]');
  if (add) add.onclick = () => void gsrcDialog(state);
  const connect = $('#strat-actions [data-src-connect]');
  if (connect) connect.onclick = () => gsrcConnect(state);
  body.querySelectorAll('[data-src-edit]').forEach((b: HTMLElement) => {
    b.onclick = () => { const s = d.src.find(x => x.id === b.dataset.srcEdit); if (s?.kind) gsrcConnect(state, s); else void gsrcDialog(state, s); };
  });
  // "Use X" keeps X and retires the other sources for that truth.
  body.querySelectorAll('[data-use]').forEach((b: HTMLElement) => {
    b.onclick = async () => {
      const clash = b.closest<HTMLElement>('[data-clash]');
      if (!clash) return;
      const t = String(clash.dataset.clash).toLowerCase();
      const others = d.src.filter(s => !s.retired_at && s.id !== b.dataset.use && String(s.truth_for).toLowerCase() === t);
      clash.querySelectorAll<HTMLButtonElement>('button').forEach(x => { x.disabled = true; });
      try { for (const s of others) await post(`/v2/growth/sources/${encodeURIComponent(s.id)}/retire`, {}); toast('Fixed'); }
      catch (e) { toast(errorMessage(e, 'Not fixed'), true); }
      void gsrcRead(state);
    };
  });
}
async function gsrcDialog(state: GrState, s: GrSource | null | undefined = null) {
  // What reach_ref can name: connected MCP tools and stored credentials, else free text.
  const [tools, creds] = await Promise.all([get<GrToolsResponse>('/v2/tools').then(r => r.integrations || []).catch(() => []),
    S.me?.credential_access ? get<GrCredentialsResponse>('/v2/credentials').then(r => r.credentials || []).catch(() => []) : []]);
  if (GR !== state) return;
  const data = state.data;
  if (!data) return;
  const opts = [...tools.map(t => t.service || t.title).filter(Boolean), ...creds.map(c => c.name).filter(Boolean)];
  const truths = [...new Set(data.src.map(x => x.truth_for))];
  const may = gtMayList(s?.may || 'read');
  const body = `${grField('Truth for', `<input name="truth_for" maxlength="80" required list="gt-truths" value="${esc(s?.truth_for || '')}"><datalist id="gt-truths">${truths.map(t => `<option value="${esc(t)}">`).join('')}</datalist>`)}
    ${grField('Source', `<input name="system" maxlength="80" required value="${esc(s?.system || '')}">`)}
    <div class="gr-pair">${grField('Reached by', `<select name="reach_kind">${grOpts(GT_REACH, s?.reach_kind || 'mcp', null)}</select>`)}
    ${grField('Which', `<input name="reach_ref" maxlength="300" list="gt-reach" value="${esc(s?.reach_ref || '')}"><datalist id="gt-reach">${opts.map(o => `<option value="${esc(o)}">`).join('')}</datalist>`)}</div>
    <fieldset class="gr-fs"><legend>Bots may</legend>${grChecks('may', GT_MAY, may)}</fieldset>
    ${grField('Owner', `<select name="owner_ref">${grOwnerOpts(s?.owner_ref)}</select>`)}
    ${s ? '<button type="button" class="ghost danger gr-retire" data-retire>Retire</button>' : ''}`;
  const dlg = grDialog(s ? `${s.truth_for} · ${s.system}` : 'New source', body, s ? 'Save' : 'Add', async form => {
    const v = (n: keyof GrSourceControls) => (form.elements as HTMLFormControlsCollection & GrSourceControls)[n].value.trim();
    const out = {truth_for: v('truth_for'), system: v('system'), reach_kind: v('reach_kind'), reach_ref: v('reach_ref'), may: grChecked(form, 'may').join(','), owner_ref: v('owner_ref') || null};
    if (!out.truth_for || !out.system) throw new Error('Truth for and source');
    if (s) await patch(`/v2/growth/sources/${encodeURIComponent(s.id)}`, out); else await post('/v2/growth/sources', out);
    toast('Saved'); void gsrcRead(state);
  });
  const retire = dlg.querySelector<HTMLButtonElement>('[data-retire]');
  if (retire && s) grRetireButton(retire, dlg, `/v2/growth/sources/${encodeURIComponent(s.id)}/retire`, () => void gsrcRead(state));
}

// ----------------------------------------------------------------- synced sources (backend/growth_sync.py)
// What a source's row says about syncing: what it fills and its health, or that it is typed by hand.
const GSRC_HEALTH: Record<string, string> = {fresh: 'Fresh', stale: 'Stale', failing: 'Failing', waiting: 'Waiting'};
function gsrcSync(s: GrSource) {
  if (!s.kind) return '<span class="muted gsrc-sync">Typed by hand · not synced</span>';
  const h = s.health || 'waiting', fills = grList((s.fills || []).map(grFieldWord));
  const when = h === 'failing' ? `since ${fmt(s.health_since)}${s.last_error ? ` · ${s.last_error}` : ''}`
    : h === 'stale' ? (s.last_good_run ? `last good run ${grDay(s.last_good_run)}` : '')
    : h === 'fresh' ? `synced ${ago(s.last_good_run)}` : (s.has_file ? 'its file did not run yet' : 'waiting for its first file');
  return `<span class="gsrc-sync"><span class="muted">${esc(fills)}</span> <span class="pill ${h === 'fresh' ? 'ok' : h === 'failing' ? 'fail' : 'waiting'}">${esc(GSRC_HEALTH[h] || h)}</span> <span class="muted">${esc(when)}</span></span>`;
}
// The numbers a synced source can fill, in the order Growth shows them.
const GSRC_FIELDS = ['spend', 'bot_cost', 'new_customers', 'revenue_month', 'margin_pct', 'kept_d30', 'kept_d90', 'kept_d180'];
interface GsrcRun { ok?: boolean; error?: string | null; written?: number; held?: number; not_written?: Array<{row?: number; channel?: string; month?: string; reason?: string}>; not_written_count?: number }
// A CSV's first line, as column names (quotes taken off).
const gsrcHeader = (text: string) => (text.split(/\r?\n/, 1)[0] || '').split(',').map(c => c.trim().replace(/^"(.*)"$/, '$1').trim()).filter(Boolean);
// Connect a source, or change a connected one: its name and owner, the file's month and channel columns, and the column
// for each number it fills (blank: not filled). Picking a file reads its columns and matches them by name; saving with
// a file runs it at once (the first run fills up to 12 months back) and says what it wrote, held and could not write.
function gsrcConnect(state: GrState, s: GrSource | null = null) {
  const map = s?.map || {}, fields = map.fields || {};
  const col = (name: string, value: string) => `<input name="${esc(name)}" maxlength="80" list="gsrc-cols" autocapitalize="off" value="${esc(value)}">`;
  const body = `<p class="muted gr-note">${s ? `${esc(GSRC_HEALTH[s.health || 'waiting'] || '')}${s.last_error ? `: ${esc(s.last_error)}` : ''}` : 'From a file: upload a CSV (a sheet’s export works), or a script or bot posts the same rows. One row per channel per month.'}</p>
    ${grField('Name', `<input name="system" maxlength="80" required value="${esc(s?.system || '')}" placeholder="Signups sheet">`)}
    ${grField(s ? 'New file (CSV)' : 'File (CSV)', '<input name="file" type="file" accept=".csv,text/csv">')}
    <datalist id="gsrc-cols"></datalist>
    <fieldset class="gr-fs"><legend class="gr-k">Columns in the file</legend>
      <div class="gr-pair">${grField('Month (YYYY-MM)', col('col_month', map.month || 'month'))}${grField('Channel', col('col_channel', map.channel || 'channel'))}</div>
      <p class="muted gr-note">Which column holds each number. Leave it blank when the file does not have it.</p>
      <div class="gsrc-map">${GSRC_FIELDS.map(f => grField(GR_METRIC[f] || f, col('f_' + f, fields[f] || ''))).join('')}</div>
    </fieldset>
    ${grField('Owner', `<select name="owner_ref">${grOwnerOpts(s?.owner_ref)}</select>`)}
    <p class="muted gr-note">Rows for a channel Growth does not have are not written; the result lists them. A number someone typed as actual is never written over: both are kept, and the owner chooses on Growth.</p>
    ${s?.has_file ? '<button type="button" class="ghost small" data-src-run>Run its last file again</button>' : ''}
    ${s ? '<button type="button" class="ghost danger gr-retire" data-retire>Retire</button>' : ''}`;
  const label = s ? 'Save' : 'Connect and fill 12 months';
  const dlg = grDialog(s ? s.system : 'Connect a source', body, label, async form => {
    const el = form.elements as HTMLFormControlsCollection & Record<string, HTMLInputElement>;
    const v = (n: string) => String(el[n]?.value || '').trim();
    const mapped: Record<string, string> = {};
    for (const f of GSRC_FIELDS) if (v('f_' + f)) mapped[f] = v('f_' + f);
    if (!v('system')) throw new Error('A name');
    if (!Object.keys(mapped).length) throw new Error('Name the column for at least one number');
    const file = el.file?.files?.[0];
    if (!s && !file) throw new Error('A CSV file to fill from');
    const text = file ? await file.text() : null;
    const out = {system: v('system'), truth_for: grList(Object.keys(mapped).map(grFieldWord)).slice(0, 80), owner_ref: v('owner_ref') || null,
      sync_map: {month: v('col_month') || 'month', channel: v('col_channel') || 'channel', fields: mapped}};
    const saved = s ? await patch(`/v2/growth/sources/${encodeURIComponent(s.id)}`, out) as GrSource
      : await post('/v2/growth/sources', {...out, sync_kind: 'file'}) as GrSource;
    void gsrcRead(state);
    if (text == null) { toast('Saved'); return; }
    gsrcResult(saved.system || out.system, await post(`/v2/growth/sources/${encodeURIComponent(saved.id)}/rows`, {csv: text}) as GsrcRun);
    void gsrcRead(state);
  });
  // A picked file offers its columns and fills each blank one whose name matches.
  const pick = dlg.querySelector<HTMLInputElement>('[name=file]');
  if (pick) pick.onchange = async () => {
    const f = pick.files?.[0];
    if (!f) return;
    const cols = gsrcHeader(await f.slice(0, 65536).text());
    patchHTML(dlg.querySelector<HTMLElement>('#gsrc-cols')!, cols.map(c => `<option value="${esc(c)}">`).join(''));
    const norm = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, '');
    for (const f2 of GSRC_FIELDS) {
      const input = dlg.querySelector<HTMLInputElement>(`[name=f_${f2}]`), hit = cols.find(c => [norm(f2), norm(GR_METRIC[f2] || '')].includes(norm(c)));
      if (input && !input.value && hit) input.value = hit;
    }
  };
  const run = dlg.querySelector<HTMLButtonElement>('[data-src-run]');
  if (run && s) run.onclick = async () => {
    run.disabled = true;
    try { gsrcResult(s.system, await post(`/v2/growth/sources/${encodeURIComponent(s.id)}/sync`, {}) as GsrcRun); }
    catch (e) { toast(errorMessage(e, 'Did not run'), true); run.disabled = false; return; }
    void gsrcRead(state);
  };
  const retire = dlg.querySelector<HTMLButtonElement>('[data-retire]');
  if (retire && s) grRetireButton(retire, dlg, `/v2/growth/sources/${encodeURIComponent(s.id)}/retire`, () => void gsrcRead(state));
}
// A run's result: what it wrote, what it held beside a typed number, and the rows it could not write; or why it failed
// (nothing it wrote before changed).
function gsrcResult(name: string, r: GsrcRun) {
  const rows = r.not_written || [], more = (r.not_written_count || 0) - rows.length;
  const body = r.ok
    ? `<p data-run-ok>Wrote ${esc(r.written ?? 0)} number${r.written === 1 ? '' : 's'}.${r.held ? ` ${esc(r.held)} differ${r.held === 1 ? 's' : ''} from a number someone typed as actual: both are kept, and the owner chooses on Growth.` : ''}</p>
      ${rows.length ? `<p class="muted gr-note">Not written:</p><ul class="gsrc-skip">${rows.map(x => `<li>Row ${esc(x.row ?? '')}${x.channel ? ` · ${esc(x.channel)}` : ''}${x.month ? ` · ${esc(x.month)}` : ''}: ${esc(x.reason || '')}</li>`).join('')}${more > 0 ? `<li class="muted">and ${esc(more)} more</li>` : ''}</ul>` : ''}`
    : `<p data-run-failed>Failing: ${esc(r.error || 'the run failed')}. Nothing changed.</p>`;
  grDialog(`${name} · ${r.ok ? 'Synced' : 'Not synced'}`, body, '', null);
}
