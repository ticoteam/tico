/* ui/app/growth-sources.js — Growth › Sources (#/growth/sources, brand-and-gtm spec §5): one source of truth per kind
   of number (GET /api/v2/growth/sources), one row each: what it is the truth for, the system, how bots reach it, what
   they may do and who owns it. Two sources for one truth are flagged, and "Use X" keeps X and retires the others. The
   owner or an admin adds, edits and retires one; they change without a publish.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GT_REACH = [['mcp', 'MCP'], ['credential', 'Credential'], ['url', 'URL'], ['person', 'Person']];
const GT_MAY = [['read', 'Read'], ['write', 'Write notes'], ['act', 'Act within rules']];
const gtMayList = s => String(s || '').split(',').map(x => x.trim()).filter(Boolean);
const gtMayText = s => gtMayList(s).map(x => GT_MAY.find(m => m[0] === x)?.[1] || x).join(' · ');

function pageGrowthSources(state) { void gsrcRead(state); }
async function gsrcRead(state) {
  let r;
  try { r = await get('/v2/growth/sources', {fresh: true}); }
  catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = {src: r.sources || [], conflicts: r.conflicts || [], may: r.may_edit ?? grAdmin()};
  gsrcDraw(state);
}
function gsrcDraw(state) {
  const body = $('#gr-body'), d = state.data;
  if (!body || !d) return;
  const may = d.may, list = d.src.filter(s => !s.retired_at).sort((a, b) => String(a.truth_for).localeCompare(String(b.truth_for)));
  patchHTML($('#strat-actions'), may ? '<button class="primary" type="button" data-src-add>+ Source</button>' : '');
  const clash = new Set(d.conflicts.map(t => String(t).toLowerCase()));
  const banners = d.conflicts.map(t => {
    const rows = list.filter(s => String(s.truth_for).toLowerCase() === String(t).toLowerCase());
    return `<div class="gr-banner gr-amber" data-clash="${esc(t)}"><span><b>${esc(t)}</b> · ${esc(rows.map(s => s.system).join(', '))}</span><span class="spacer"></span>
      ${may ? rows.map(s => `<button class="ghost small" type="button" data-use="${esc(s.id)}">Use ${esc(s.system)}</button>`).join('') : ''}</div>`;
  }).join('');
  const reach = s => `${esc(GT_REACH.find(r => r[0] === s.reach_kind)?.[1] || s.reach_kind || '')}${s.reach_ref ? ` <code>${esc(s.reach_ref)}</code>` : ''}`;
  const row = s => {
    const inner = `<span class="gsrc-t">${esc(s.truth_for)}</span><span class="gsrc-s">${esc(s.system)}</span><span class="muted gsrc-r">${reach(s)}</span>
      <span class="muted gsrc-m">${esc(gtMayText(s.may))}</span><span class="muted gsrc-o">${esc(grWho(s.owner_ref))}</span>`;
    return `<li class="${clash.has(String(s.truth_for).toLowerCase()) ? 'gsrc-clash' : ''}">${may
      ? `<button type="button" class="gsrc-row" data-src-edit="${esc(s.id)}">${inner}</button>` : `<div class="gsrc-row">${inner}</div>`}</li>`;
  };
  body.innerHTML = banners + (list.length ? `<section class="card gsrc-list" aria-label="Sources"><ul class="gsrc-ul">${list.map(row).join('')}</ul></section>` : '');
  const add = $('#strat-actions [data-src-add]');
  if (add) add.onclick = () => void gsrcDialog(state);
  body.querySelectorAll('[data-src-edit]').forEach(b => { b.onclick = () => void gsrcDialog(state, d.src.find(s => s.id === b.dataset.srcEdit)); });
  // "Use X" keeps X and retires the other sources for that truth.
  body.querySelectorAll('[data-use]').forEach(b => {
    b.onclick = async () => {
      const t = b.closest('[data-clash]').dataset.clash.toLowerCase();
      const others = d.src.filter(s => !s.retired_at && s.id !== b.dataset.use && String(s.truth_for).toLowerCase() === t);
      b.closest('[data-clash]').querySelectorAll('button').forEach(x => { x.disabled = true; });
      try { for (const s of others) await post(`/v2/growth/sources/${encodeURIComponent(s.id)}/retire`, {}); toast('Fixed'); }
      catch (e) { toast(e.message || 'Not fixed', true); }
      void gsrcRead(state);
    };
  });
}
async function gsrcDialog(state, s = null) {
  // What reach_ref can name: connected MCP tools and stored credentials, else free text.
  const [tools, creds] = await Promise.all([get('/v2/tools').then(r => r.integrations || []).catch(() => []),
    S.me?.credential_access ? get('/v2/credentials').then(r => r.credentials || []).catch(() => []) : []]);
  if (GR !== state) return;
  const opts = [...tools.map(t => t.service || t.title).filter(Boolean), ...creds.map(c => c.name).filter(Boolean)];
  const truths = [...new Set(state.data.src.map(x => x.truth_for))];
  const may = gtMayList(s?.may || 'read');
  const body = `${grField('Truth for', `<input name="truth_for" maxlength="80" required list="gt-truths" value="${esc(s?.truth_for || '')}"><datalist id="gt-truths">${truths.map(t => `<option value="${esc(t)}">`).join('')}</datalist>`)}
    ${grField('Source', `<input name="system" maxlength="80" required value="${esc(s?.system || '')}">`)}
    <div class="gr-pair">${grField('Reached by', `<select name="reach_kind">${grOpts(GT_REACH, s?.reach_kind || 'mcp', null)}</select>`)}
    ${grField('Which', `<input name="reach_ref" maxlength="300" list="gt-reach" value="${esc(s?.reach_ref || '')}"><datalist id="gt-reach">${opts.map(o => `<option value="${esc(o)}">`).join('')}</datalist>`)}</div>
    <fieldset class="gr-fs"><legend>Bots may</legend>${grChecks('may', GT_MAY, may)}</fieldset>
    ${grField('Owner', `<select name="owner_ref">${grOwnerOpts(s?.owner_ref)}</select>`)}
    ${s ? '<button type="button" class="ghost danger gr-retire" data-retire>Retire</button>' : ''}`;
  const dlg = grDialog(s ? `${s.truth_for} · ${s.system}` : 'New source', body, s ? 'Save' : 'Add', async form => {
    const v = n => form.elements[n].value.trim();
    const out = {truth_for: v('truth_for'), system: v('system'), reach_kind: v('reach_kind'), reach_ref: v('reach_ref'), may: grChecked(form, 'may').join(','), owner_ref: v('owner_ref') || null};
    if (!out.truth_for || !out.system) throw new Error('Truth for and source');
    if (s) await patch(`/v2/growth/sources/${encodeURIComponent(s.id)}`, out); else await post('/v2/growth/sources', out);
    toast('Saved'); void gsrcRead(state);
  });
  const retire = dlg.querySelector('[data-retire]');
  if (retire) grRetireButton(retire, dlg, `/v2/growth/sources/${encodeURIComponent(s.id)}/retire`, () => void gsrcRead(state));
}
