/* ui/app/automations-new.js — New inbox and Apply template: an inbox automation made from, or moved onto, one of the
   hub's inbox templates (backend/automation_templates)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   GET /v2/automations/templates lists them; GET /v2/automations/templates/<id>?owner=&inbox= fills one for an owner and
   an inbox (its roles resolved on the org chart, what the owner may not use taken out) and saves nothing, refusing an
   owner the viewer may not make an automation for or one that can't hear the inbox. New inbox POSTs the filled flow;
   Apply template PATCHes an October inbox (one llm step) onto it, a new version that Versions can restore. */
'use strict';

const AU_OWNER_KINDS = [['person', 'Person'], ['bot', 'Bot'], ['group', 'Group'], ['other', 'Other']];
// An email address is heard only by its mailbox's person or bot; a group, or a shared alias with no owner yet ("Other",
// the creator's for now), hears a channel.
const auIsChannel = inbox => !!String(inbox || '').trim() && !String(inbox).includes('@');
const auOwnerKinds = inbox => AU_OWNER_KINDS.filter(([k]) => auIsChannel(inbox) || k === 'person' || k === 'bot');
const auTplUrl = (tid, owner, inbox) => `/v2/automations/templates/${encodeURIComponent(tid)}?` + new URLSearchParams({owner, inbox});

// Apply template is for what the October cutover made: a Message automation whose one step is an llm step with no choose.
const auApplies = a => !!a?.can_edit && !a.readonly && a.trigger?.type === 'message' && (a.steps || []).length === 1
  && a.steps[0]?.type === 'llm' && !a.steps[0].choose;

/* The template's rules, questions and destinations with the old llm step as the LLM: its prompt as the instructions and
   its own fields (id, model, out...) kept, as Sort + draft for a person or a bot; a group has no turn to draft with, so
   its prompt instructs the template's Sort. A switched-off step stays off in what replaces it. Read through the board
   (auBoardShape), so the result is a board unless it is off; null when the filled flow isn't one. A prompt longer than a
   question's instructions may be (clients/judge.py MAX_INSTRUCTIONS) is refused, never cut. */
const AU_MAX_INSTRUCTIONS = 8000;
function auApplyFlow(a, filled) {
  const b = auBoardShape({...filled, trigger: a.trigger});
  if (!b) return null;
  const old = JSON.parse(JSON.stringify(a.steps[0])), as = auOwnerAs(a), L = b.llm;
  const prompt = String(old.prompt || '').trim() ? old.prompt : L.prompt;
  if (as) {
    // Only the rules and the questions stay beside the old step; a Sort it replaces goes.
    const kept = [b.rules.length && b.src.rules, b.questions.length && b.src.qd, b.questions.length && b.src.qf].filter(Boolean);
    if (kept.some(s => s.id === old.id)) old.id = auNewId();
    if (kept.some(s => s.out && s.out === old.out)) delete old.out;
    delete old.choose;
    b.llm = {mode: 'draft', prompt, to: [...L.to], default: L.default, as, ...(old.model ? {model: old.model} : {})};
    b.src = {...b.src, ls: old};
    delete b.src.sd; delete b.src.sf; delete b.src.sq;
  } else {
    if (L.to.length > 1 && [...String(prompt)].length > AU_MAX_INSTRUCTIONS)
      throw new Error(`The prompt is over ${AU_MAX_INSTRUCTIONS.toLocaleString('en-US')} characters. Shorten it first.`);
    b.llm = {...L, prompt};
  }
  const flow = auBoardFlow(b);
  // The LLM's part is last: the llm step, a Sort's choice and its routes, or one-destination Sort's single rule.
  if (old.enabled === false) flow.steps.slice(b.llm.mode === 'sort' && b.llm.to.length > 1 ? -2 : -1).forEach(s => { s.enabled = false; });
  return flow;
}

async function auTemplates() {
  if (!AU.templates) AU.templates = (await get('/v2/automations/templates')).templates || [];
  return AU.templates;
}

// One dialog at a time, inside the page (so it goes when the page does, and reads the page's colors). Its clicks and
// keys stay in it: the page's own handlers read data-* attributes the dialog has no business with.
function auDialog(cls, title, icon, body, foot) {
  AU.root.querySelectorAll('dialog.au-nd').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal tmodal-shell au-nd ' + cls;
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<form novalidate><div class="tmodal-head"><span class="nav-icon" aria-hidden="true">${icon}</span><h2>${esc(title)}</h2>
      <button type="button" class="ghost tmodal-x" data-x aria-label="Close">✕</button></div>
    <div class="tmodal-body au-ndb">${body}</div>
    <div class="tmodal-foot au-ndf">${foot}</div></form>`;
  AU.root.appendChild(dlg);
  for (const ev of ['click', 'change', 'keydown', 'input']) dlg.addEventListener(ev, e => e.stopPropagation());
  dlg.querySelectorAll('[data-x]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  dlg.showModal();
  return dlg;
}
const auTplCards = (list, tid) => list.length ? list.map(t => `<button type="button" class="au-tp" role="radio" aria-checked="${t.id === tid}" data-tid="${esc(t.id)}">
    <b>${esc(t.name)}</b><span>${esc((t.destinations || []).join(' · '))}</span></button>`).join('') : '<p class="muted">Loading…</p>';
function auNdError(dlg, e) {
  const err = dlg.querySelector('[data-error]');
  err.textContent = e ? e.message || String(e) : '';
  err.hidden = !e;
}

// New inbox: the inbox, whose it is, the template and Turn on now; Create fills the template for that owner and inbox,
// saves it with the switch's on or off, and opens it (a board).
function auNewInbox() {
  auNewMenu(false);
  const me = S.me || {}, state = AU;
  const n = {inbox: me.email || '', kind: 'person', person: 'person:' + me.id, bot: auBots()[0]?.[0] || '',
    group: AU.groups[0] ? 'group:' + AU.groups[0].id : '', tid: '', on: false, list: AU.templates || []};
  n.tid = n.list[0]?.id || '';
  const dlg = auDialog('au-nd-new', 'New inbox', 'mail', `
      <label class="au-f"><span class="au-lb">Inbox</span><input type="text" data-n="inbox" value="${esc(n.inbox)}" placeholder="name@acme.example" autocomplete="off" spellcheck="false"></label>
      <div class="au-f" data-who></div>
      <div class="au-f"><span class="au-lb" id="au-nd-tl">Template</span><div class="au-tps" role="radiogroup" aria-labelledby="au-nd-tl" data-tpls>${auTplCards(n.list, n.tid)}</div></div>
      <p class="err" data-error hidden></p>`,
    `<label class="au-nd-on">${auSwitch(false, 'Turn on now', 'data-n="on"')}<span>Turn on now</span></label>
      <button type="button" class="ghost" data-x>Cancel</button><button type="submit" class="primary" data-go>Create</button>`);
  const who = () => {
    const kinds = auOwnerKinds(n.inbox);
    if (!kinds.some(([k]) => k === n.kind)) n.kind = 'person';
    const pick = {person: () => [auPeople(), n.person], bot: () => [auBots(), n.bot],
      group: () => [AU.groups.map(g => ['group:' + g.id, g.name || g.id]), n.group]}[n.kind];
    const [list, v] = pick ? pick() : [null, ''];
    dlg.querySelector('[data-who]').innerHTML = `<span class="au-lb" id="au-nd-wl">Whose inbox</span>
      <div class="theme-seg au-nd-seg" role="radiogroup" aria-labelledby="au-nd-wl">${kinds.map(([k, l]) =>
        `<button type="button" role="radio" data-kind="${k}" aria-checked="${k === n.kind}">${esc(l)}</button>`).join('')}</div>
      ${list ? `<select data-n="${n.kind}" aria-label="${esc(auCap(n.kind))}">${auOpt(auWithCur(list, v, v), v)}</select>` : ''}`;
  };
  who();
  const owner = () => n.kind === 'other' ? 'person:' + me.id : n[n.kind];
  dlg.addEventListener('input', e => {
    const f = e.target.dataset?.n;
    if (f === 'inbox') { const was = auOwnerKinds(n.inbox).length; n.inbox = e.target.value; if (auOwnerKinds(n.inbox).length !== was) who(); }
  });
  dlg.addEventListener('change', e => {
    const f = e.target.dataset?.n;
    if (f === 'on') n.on = e.target.checked;
    else if (f && f !== 'inbox') n[f] = e.target.value;
  });
  dlg.addEventListener('click', e => {
    const k = e.target.closest('[data-kind]'), t = e.target.closest('[data-tid]');
    if (k) { n.kind = k.dataset.kind; who(); dlg.querySelector(`[data-kind="${k.dataset.kind}"]`)?.focus(); }
    if (t) { n.tid = t.dataset.tid; dlg.querySelectorAll('[data-tid]').forEach(x => x.setAttribute('aria-checked', String(x === t))); }
  });
  if (!n.list.length) void auTemplates().then(list => {
    if (!dlg.isConnected) return;
    n.list = list; n.tid ||= list[0]?.id || '';
    dlg.querySelector('[data-tpls]').innerHTML = auTplCards(list, n.tid);
  }, e => { if (dlg.isConnected) auNdError(dlg, e); });
  const go = dlg.querySelector('[data-go]');
  dlg.querySelector('form').onsubmit = async e => {
    e.preventDefault();
    if (go.disabled) return;
    auNdError(dlg, null);
    if (!n.inbox.trim()) { auNdError(dlg, new Error('Add an inbox')); dlg.querySelector('[data-n="inbox"]').focus(); return; }
    if (!n.tid) { auNdError(dlg, new Error('Pick a template')); return; }
    if (!owner()) { auNdError(dlg, new Error('Pick whose inbox it is')); return; }
    go.disabled = true;
    try {
      const f = await get(auTplUrl(n.tid, owner(), n.inbox.trim()), {fresh: true});
      const out = auNormal(await post('/v2/automations', {name: f.name, owner: f.owner, ...(f.group ? {group: f.group} : {}),
        enabled: n.on, builtin: false, trigger: f.trigger, steps: f.steps || [], ...(Array.isArray(f.destinations) ? {destinations: f.destinations} : {})}));
      dlg.close();
      if (AU !== state) return;
      await auLoadList(state);
      if (AU === state) location.hash = auHref(out.id);
    } catch (err) { if (dlg.isConnected) { auNdError(dlg, err); go.disabled = false; } }
  };
  dlg.querySelector('[data-n="inbox"]').focus();
}

// Apply template: pick one, see the destinations it fills in for this automation's owner and inbox, then Apply saves
// the new flow as a new version (name, trigger and on/off as they were).
function auApplyTemplate() {
  const a = AU.cur, state = AU;
  if (!auApplies(a)) return;
  const owner = a.owner_id || a.owner, inbox = a.trigger.inbox;
  const t = {tid: '', list: AU.templates || [], filled: null, seq: 0};
  const dlg = auDialog('au-nd-apply', 'Apply template', 'dashboard_customize', `
      <div class="au-f"><span class="au-lb" id="au-nd-al">Template</span><div class="au-tps" role="radiogroup" aria-labelledby="au-nd-al" data-tpls>${auTplCards(t.list, t.tid)}</div></div>
      <div class="au-f" data-dests hidden></div>
      <p class="err" data-error hidden></p>`,
    `<span class="au-sp"></span><button type="button" class="ghost" data-x>Cancel</button><button type="submit" class="primary" data-go disabled>Apply</button>`);
  const go = dlg.querySelector('[data-go]'), box = dlg.querySelector('[data-dests]');
  const pick = async tid => {
    const seq = ++t.seq;
    t.tid = tid; t.filled = null; go.disabled = true; box.hidden = true; auNdError(dlg, null);
    dlg.querySelectorAll('[data-tid]').forEach(x => x.setAttribute('aria-checked', String(x.dataset.tid === tid)));
    try {
      const f = await get(auTplUrl(tid, owner, inbox), {fresh: true});
      if (seq !== t.seq || !dlg.isConnected) return;
      const flow = auApplyFlow(a, f);
      if (!flow) throw new Error('This template doesn’t fit this inbox');
      t.filled = flow;
      box.innerHTML = `<span class="au-lb">Destinations</span><ul class="au-nd-dests">${flow.destinations.map((d, i) =>
        `<li style="--c:${auDestColor(d, i)}"><i aria-hidden="true"></i><b>${esc(d.name || d.id)}</b><span>${esc(auActLine((d.then || [])[0]))}</span></li>`).join('')}</ul>`;
      box.hidden = false; go.disabled = false;
    } catch (e) { if (seq === t.seq && dlg.isConnected) auNdError(dlg, e); }
  };
  dlg.addEventListener('click', e => { const c = e.target.closest('[data-tid]'); if (c) void pick(c.dataset.tid); });
  if (!t.list.length) void auTemplates().then(list => {
    if (!dlg.isConnected) return;
    t.list = list;
    dlg.querySelector('[data-tpls]').innerHTML = auTplCards(list, t.tid);
  }, e => { if (dlg.isConnected) auNdError(dlg, e); });
  dlg.querySelector('form').onsubmit = async e => {
    e.preventDefault();
    if (go.disabled || !t.filled) return;
    go.disabled = true; auNdError(dlg, null);
    try {
      const out = auNormal(await patch(auApi(a.id), {...t.filled, summary: 'Applied a template'}));
      dlg.close();
      if (AU !== state) return;
      auAdopt(out);
      toast(`Template applied · v${out.version}`);
    } catch (err) { if (dlg.isConnected) { auNdError(dlg, err); go.disabled = false; } }
  };
  dlg.querySelector('[data-tid]')?.focus();
}
