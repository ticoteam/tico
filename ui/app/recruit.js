/* Recruiting (backend/recruiting.py): Post a job makes a Recruiting seat with a job description drafted from its title,
   its Applicants page (#/seat/<id>) lists the templates that fit, one Applicant page (#/seat/<id>/<template>) shows
   one in full, and Hire creates the bot in Onboarding in the seat's place. Settings > Bots > Add bot keeps its own
   dialog and posts through the same seat. */
'use strict';
let RECRUIT_HANDOFF = null;
// Page-local state: which load is current, the arrival in progress, each seat's applicants once listed (the sidebar
// shows their count), and the Computer and Model chosen while posting, which the Hire box starts from.
const RECRUIT = {load: 0, arrive: '', run: null, found: {}, counts: {}, prefs: {}};
const RECRUIT_DEPT = {marketing: '#a63d65', sales: '#1769a6', finance: '#2f6b3c', product: '#5946b2', support: '#9a4f19',
  legal: '#4b5563', engineering: '#3f566b', operations: '#0f6e56', hr: '#7a3e9d', leadership: '#3f566b'};
const RECRUIT_MATCH = {strong: 'Strong', good: 'Good'};
const RECRUIT_HOLD = 1600;                         // how long a dot that landed stays in 'done' before it rests
const RECRUIT_ARM = 4000;                          // how long Close job? waits for the second click
const RECRUIT_NEED = {connected: 'Connected', on_hire: 'Installs on hire', not_set: 'Not set'};
const recruitAddress = (id, template = '') => '#/seat/' + encodeURIComponent(id) + (template ? '/' + encodeURIComponent(template) : '');

function settingsRecruitHandoff() {
  if (!RECRUIT_HANDOFF || !S.me?.id || !settingsCanCreateBots()) return;
  const seed = RECRUIT_HANDOFF; RECRUIT_HANDOFF = null;
  settingsShow('bots');
  if (seed.scratch) { recruitScratchEditor(seed); return; }
  void settingsRecruitFlow(seed);
}
// From scratch is the bot editor in Settings > Bots, which needs that page's data: go there and open it once loaded.
function recruitScratch(seed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  RECRUIT_HANDOFF = {scratch: true, title: String(seed.title || ''), brief: String(seed.brief || '')};
  SETTINGS_TAB = 'bots';
  if (S.route === SETTINGS) settingsRecruitHandoff(); else location.hash = SETTINGS;
}
function recruitScratchEditor(seed) {
  settingsEditBot();
  const editor = $('#bot-editor');
  if (!editor) return;
  if (seed.title) editor.querySelector('[name=display_name]').value = seed.title.slice(0, 100);
  if (seed.brief) editor.querySelector('[name=description]').value = seed.brief.slice(0, 2000);
}
// Settings > Bots > Add bot: Recruit candidates posts a job; a template, an existing agent or a bot from scratch as before.
async function settingsRecruitFlow(seed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  const dialog = recruitDialog();
  const draft = {title:String(seed.title||'').slice(0,100),brief:String(seed.brief||'').slice(0,2000),template:String(seed.template||'').slice(0,80)};
  let route = draft.template ? 'template' : 'recruit', cards = [], creating = false;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="recruit-title">Add a bot</h2><span class="spacer"></span><button class="ghost" type="button" data-recruit-close aria-label="Close">✕</button></div><div class="bot-editor-body"><div class="row" style="flex-wrap:wrap;margin:0 0 16px">${[['recruit','Post a job'],['template','From a template'],['existing','Existing agent'],['scratch','From scratch']].map(([id,label])=>`<button class="ghost" type="button" data-recruit-route="${id}" aria-pressed="${route===id}">${label}</button>`).join('')}</div><label>Job title<input name="title" maxlength="100" value="${esc(draft.title)}" placeholder="Customer support"></label><label>Job description<textarea name="brief" rows="4" maxlength="2000" placeholder="What should this bot own?">${esc(draft.brief)}</textarea></label><details><summary>More</summary><label>Tools and sources<input name="tools" maxlength="300" placeholder="e.g. Mailbox, team docs"></label><label>Who reviews the work?<input name="reviewer" maxlength="150" placeholder="e.g. Head of support"></label></details><p class="muted" data-recruit-status role="status"></p><div data-recruit-candidates></div></div><div class="bot-editor-actions"><button class="primary" type="submit">Continue</button><button class="ghost" type="button" data-recruit-close>Cancel</button></div></form>`;
  dialog.querySelectorAll('[data-recruit-close]').forEach(b=>b.onclick=()=>dialog.close());
  dialog.querySelectorAll('[data-recruit-route]').forEach(b=>b.onclick=()=>{route=b.dataset.recruitRoute;dialog.querySelectorAll('[data-recruit-route]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));dialog.querySelector('[data-recruit-candidates]').replaceChildren();});
  const form=dialog.querySelector('form'), status=dialog.querySelector('[data-recruit-status]'), results=dialog.querySelector('[data-recruit-candidates]');
  form.onsubmit=e=>e.preventDefault();
  dialog.showModal();
  try { cards=catalogCards(await get('/v2/templates')); } catch(error){status.textContent=error.message;}
  // Only Post a job needs a title and a job description (its seat is made from them); the other ways go on without.
  const recruitMissing=()=>{
    const title=form.elements.title,brief=form.elements.brief;settingsFieldClear(title);settingsFieldClear(brief);
    if(!title.value.trim()){settingsFieldError(title,'Give the job a title.');title.focus();return true;}
    if(brief.value.trim().length<10){settingsFieldError(brief,'Describe the job in a sentence or two (at least 10 characters).');brief.focus();return true;}
    return false;
  };
  const fail=message=>{status.classList.add('err');status.textContent=message;};
  form.onsubmit=async e=>{
    e.preventDefault();if(creating)return;const data=new FormData(form);draft.title=String(data.get('title'));draft.brief=String(data.get('brief'));
    status.classList.remove('err');settingsFieldClear(form.elements.title);settingsFieldClear(form.elements.brief);
    if(route==='existing'){results.innerHTML='<p>Keep your agent where it runs and pair it with Tico using a scoped connection code.</p><a class="linkish" href="https://github.com/ticoteam/tico/blob/main/docs/connect-an-agent.md" target="_blank" rel="noopener noreferrer">Open the connection guide ↗</a>';return;}
    if(route==='scratch'){dialog.close();recruitScratchEditor({title:draft.title,brief:draft.brief});return;}
    if(route==='recruit'){
      // The job is a Recruiting seat; its Applicants page lists the candidates and hires one.
      if(!settingsCanCreateBots()){fail('You no longer have permission to add bots.');return;}
      if(recruitMissing())return;
      creating=true;
      try{const seat=await post('/v2/seats',{title:draft.title.trim(),description:draft.brief.trim()});dialog.close();void refresh(true);await recruitSeat(seat.id,{arrive:true});}
      catch(error){fail(error.message);}
      creating=false;return;
    }
    const terms=new Set(draft.brief.toLowerCase().match(/[a-z]{3,}/g)||[]);
    const score=c=>(draft.template&&c.template===draft.template?100:0)+(c.required?-100:0)+(c.summary||'').toLowerCase().split(/\W+/).filter(w=>terms.has(w)).length;
    const candidates=cards.filter(c=>c.template!=='inbox'&&!c.required).slice().sort((a,b)=>score(b)-score(a)).slice(0,6);
    status.textContent='';
    results.innerHTML=candidates.map(c=>`<article class="cat-card"><h3>${esc(c.name)}</h3><p class="cat-summary">${esc(c.summary||'')}</p>${c.owns?.length?`<p class="muted">Owns: ${esc(c.owns.join('; '))}</p>`:''}<button class="primary" type="button" data-recruit-create="${esc(c.slug)}">Choose & create ${esc(c.name)}</button></article>`).join('');
    results.querySelectorAll('[data-recruit-create]').forEach(b=>b.onclick=async()=>{
      if(creating||!S.me?.id||!settingsCanCreateBots())return;
      const card=cards.find(c=>c.slug===b.dataset.recruitCreate);if(!card)return;
      creating=true;results.querySelectorAll('[data-recruit-create]').forEach(x=>x.disabled=true);status.textContent='Creating a bot that waits for setup…';
      const title=draft.title.trim()||card.name;
      const slugBase=title.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,55)||card.slug;
      let slug=slugBase;for(let n=2;S.emps.some(x=>x.name===slug);n++)slug=slugBase+'-'+n;
      const brief='\n\n## Role brief\n'+(draft.brief.trim()||'Not set')+'\nTools and sources: '+String(data.get('tools')||'Not set')+'\nReviewer: '+String(data.get('reviewer')||'Ask the owner during setup');
      try{await post('/v2/bots',{slug,display_name:title,description:draft.brief,template:card.template,
        instructions:(card.instructions||'')+brief,reports_to:null,status:'planned',repo:'bot-'+slug,thread_mode:'personal',
        model:card.model||'',effort:card.reasoning_effort||'',operator:S.me.id,owners:[S.me.id],runner_id:null});
        dialog.close();await refresh(true);if(S.route===SETTINGS){await loadSettings();settingsShow('bots');}toast('Bot created. Complete its setup conversation next.');
      }catch(error){fail(error.message);creating=false;results.querySelectorAll('[data-recruit-create]').forEach(x=>x.disabled=false);}
    });
  };
}

function recruitDialog() {
  let dialog = $('#recruit-picker');
  if (!dialog) {
    dialog = document.createElement('dialog'); dialog.id = 'recruit-picker'; dialog.className = 'bot-editor catalog-picker';
    dialog.setAttribute('aria-labelledby', 'recruit-title'); document.body.append(dialog);
  }
  return dialog;
}
const recruitMe = () => 'human:' + (mePerson()?.id || S.me?.id || '');
// Where a new seat may hang: you, or (the owner) any person or bot on the chart, or a bot you manage.
function recruitPlaces() {
  const owner = S.me?.role === 'owner', me = recruitMe();
  const people = owner ? (S.people || []).filter(p => !p.hidden).map(p => ['human:' + p.id, p.name || p.id]) : [];
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && !isHelperBot(e) && (owner || e.can_manage))
    .map(e => [e.name, e.display_name || e.name]);
  return [[me, 'You'], ...people.filter(([id]) => id !== me), ...bots];
}
const recruitPlaceName = value => recruitPlaces().find(([id]) => id === value)?.[1]
  || (S.emps || []).find(e => e.name === value)?.display_name
  || (S.people || []).find(p => 'human:' + p.id === value)?.name || value;
const recruitGroupName = id => (S.orgGroups || []).find(g => g.id === id)?.name || '';
const recruitOptions = (rows, value) => rows.map(([id, name]) => `<option value="${esc(id)}"${id === value ? ' selected' : ''}>${esc(name)}</option>`).join('');
// The computers a bot may go on; one that cannot be read leaves the choice to the hire.
let RECRUIT_COMPUTERS = null;
function recruitComputers() {
  RECRUIT_COMPUTERS ||= get('/v2/computers').then(d => (d.computers || []).filter(c => !c.revoked_at).map(c => [c.id, c.label || c.id]),
    () => { RECRUIT_COMPUTERS = null; return []; });
  return RECRUIT_COMPUTERS;
}
// Movement that skips: reduced motion shows the end at once, and a click anywhere jumps there.
const recruitCalm = () => !!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
function recruitStop() {
  RECRUIT.run?.finish(); RECRUIT.run = null;
}

// ------------------------------------------------------------------ Post a job
async function recruitPostJob(seed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  setDrawer(false);
  $('#recruit-post')?.remove();
  const groups = (S.orgGroups || []).map(g => [g.id, g.name]);
  const team = groups.some(([id]) => id === seed.team) ? seed.team : '';
  const boss = recruitPlaces().some(([id]) => id === seed.reports_to) ? seed.reports_to : recruitMe();
  const placed = !!team;                       // from a group's plus: its place is set, folded to one line
  const dialog = document.createElement('dialog');
  dialog.id = 'recruit-post'; dialog.className = 'tmodal tmodal-shell recruit-post';
  dialog.setAttribute('aria-labelledby', 'recruit-post-title');
  dialog.innerHTML = `<form novalidate><div class="tmodal-head"><h2 id="recruit-post-title">Post a job</h2>
      <button class="ghost tmodal-x" type="button" data-post-cancel aria-label="Close">✕</button></div>
    <div class="tmodal-body">
      <label class="rp-field"><span>Job title</span><input name="title" maxlength="120" autocomplete="off" required value="${esc(String(seed.title || '').slice(0, 120))}" placeholder="Rust engineer"></label>
      ${placed ? `<button type="button" class="rp-placed" data-post-place><span>${esc(recruitGroupName(team))} · ${esc(recruitPlaceName(boss))}</span><span class="rp-change">Change</span></button>` : ''}
      <div class="rp-pair" data-post-pair${placed ? ' hidden' : ''}>
        <label class="rp-field"><span>Team</span><select name="team"><option value="">No group</option>${recruitOptions(groups, team)}</select></label>
        <label class="rp-field"><span>Reports to</span><select name="reports_to">${recruitOptions(recruitPlaces(), boss)}</select></label>
      </div>
      <div class="rp-field"><div class="rp-label"><label for="rp-desc">Job description</label>
        <button type="button" class="ghost small rp-redraft" data-post-redraft disabled>${ldotHTML({state: 'idle', size: 20, cls: 'rp-dot'})}Redraft</button></div>
        <textarea id="rp-desc" name="description" rows="11" maxlength="8000"></textarea></div>
      <details class="rp-more"><summary>More</summary><div class="rp-pair">
        <label class="rp-field"><span>Computer</span><select name="runner_id"><option value="">Default</option></select></label>
        <label class="rp-field"><span>Model</span><select name="model"><option value="">Suggested</option></select></label></div></details>
      <p class="err" role="alert" data-post-error hidden></p>
    </div>
    <div class="tmodal-foot rp-foot"><button class="ghost" type="button" data-post-cancel>Cancel</button><button class="primary" type="submit">Post job</button></div></form>`;
  document.body.append(dialog);
  const form = dialog.querySelector('form'), f = form.elements, error = dialog.querySelector('[data-post-error]');
  const redraft = dialog.querySelector('[data-post-redraft]'), dot = redraft.querySelector('.ldot'), text = f.description;
  const leave = () => dialog.close();
  window.addEventListener('hashchange', leave);
  dialog.onclose = () => { window.removeEventListener('hashchange', leave); dialog.remove(); };
  dialog.querySelectorAll('[data-post-cancel]').forEach(b => { b.onclick = () => dialog.close(); });
  dialog.querySelector('[data-post-place]')?.addEventListener('click', event => {
    event.currentTarget.hidden = true; dialog.querySelector('[data-post-pair]').hidden = false; f.team.focus();
  });
  // The description is drafted from the title once it is left (and again on Redraft). Each draft is asked for one
  // title; it lands only if it is still the newest and nobody typed in the description since it was asked for, so it
  // never replaces words written by hand. `draftedFor` is the title the drafted words belong to.
  let typed = false, drafted = '', draftedFor = '', asked = 0, edits = 0, pending = null, pendingTitle = '';
  text.oninput = () => { edits++; typed = text.value.trim() !== '' && text.value !== drafted; };
  const ask = async (title, again, mine, seen) => {
    ldotSet(dot, 'thinking'); redraft.disabled = true; text.setAttribute('aria-busy', 'true');
    try {
      const found = await post('/v2/seats/draft', {title, team: recruitGroupName(f.team.value), reports_to: f.reports_to.value, redraft: again});
      if (mine !== asked || !dialog.open) return 'stale';
      // A title no template fits clears words drafted for an earlier title, but never words typed by hand.
      if (edits === seen && (found.description || text.value === drafted)) {
        text.value = drafted = found.description || ''; draftedFor = title; typed = false;
      }
      ldotSet(dot, 'done');
      return 'ok';
    } catch (e) {
      if (mine !== asked) return 'stale';
      ldotSet(dot, 'idle'); error.textContent = e.message; error.hidden = false;
      return 'error';
    } finally { if (mine === asked) { redraft.disabled = false; text.removeAttribute('aria-busy'); pending = null; } }
  };
  const draftNow = (again = false) => {
    const title = f.title.value.trim();
    if (!title || (!again && typed)) return pending || Promise.resolve('ok');
    if (!again && pending && pendingTitle === title) return pending;
    if (!again && !pending && draftedFor === title) return Promise.resolve('ok');
    pendingTitle = title;
    pending = ask(title, again, ++asked, edits);
    return pending;
  };
  // Before posting, the description must be the person's own words or the draft for the title being posted: wait for
  // that draft (the newest, if one in flight is overtaken), asking for it when none is on its way.
  const needsDraft = () => !typed && draftedFor !== f.title.value.trim();
  const settle = async () => {
    for (let tries = 0; tries < 5 && dialog.open; tries++) {
      if (!needsDraft()) return true;
      if (await draftNow() === 'error') return false;
    }
    return !needsDraft();
  };
  f.title.addEventListener('blur', () => { void draftNow(); });
  f.title.addEventListener('input', () => { redraft.disabled = !f.title.value.trim(); });
  redraft.onclick = () => { void draftNow(true); };
  redraft.disabled = !f.title.value.trim();
  void recruitComputers().then(rows => { if (dialog.open) f.runner_id.insertAdjacentHTML('beforeend', recruitOptions(rows, '')); });
  void taskRunCatalog().then(rows => { if (dialog.open) f.model.insertAdjacentHTML('beforeend', recruitOptions(rows.map(m => [m.id, m.label || m.name || m.id]), '')); });
  let posting = false;
  form.onsubmit = async event => {
    event.preventDefault();
    if (posting) return;
    error.hidden = true;
    const title = f.title.value.trim();
    if (!title) { error.textContent = 'Give the job a title.'; error.hidden = false; f.title.focus(); return; }
    if (!S.me?.id || !settingsCanCreateBots()) { error.textContent = 'You no longer have permission to add bots.'; error.hidden = false; return; }
    const submit = form.querySelector('[type=submit]');
    const unlock = () => { posting = false; submit.disabled = false; f.title.readOnly = false; };
    posting = true; submit.disabled = true; f.title.readOnly = true;      // the title being posted stays put
    if (!await settle()) { unlock(); return; }
    if (!dialog.open) return;
    try {
      const seat = await post('/v2/seats', {title, reports_to: f.reports_to.value, description: text.value.trim(),
        ...(f.team.value ? {team: f.team.value} : {})});
      RECRUIT.prefs[seat.id] = {runner_id: f.runner_id.value, model: f.model.value};
      dialog.close();
      void refresh(true);
      await recruitSeat(seat.id, {arrive: true});
    } catch (e) { error.textContent = e.message; error.hidden = false; unlock(); }
  };
  dialog.showModal();
  // On a phone the dialog itself takes focus, so no field shows a ring and no keyboard opens until a tap.
  if (matchMedia('(max-width:600px)').matches) { dialog.tabIndex = -1; dialog.focus({preventScroll: true}); }
  else f.title.focus();
  if (f.title.value.trim()) void draftNow();
}

// Open a seat's Applicants page: from the org chart, or right after posting (then they arrive one by one).
async function recruitSeat(id, {arrive = false} = {}) {
  if (arrive) RECRUIT.arrive = id;
  location.hash = recruitAddress(id);
}

// ------------------------------------------------------------------ the pages
async function pageSeat(id, template = '') {
  const load = ++RECRUIT.load;
  const main = $('#main');
  main.classList.add('recruit-layout');
  const arrive = RECRUIT.arrive === id && !template; RECRUIT.arrive = '';
  // A seat waiting on a requested cloud agent has that agent's page instead (ui/app/cloud-agents.js).
  const listed = (S.seats || []).find(row => row.id === id);
  if (listed?.agent && typeof window.cloudAgentsSeat === 'function') { recruitAgentSeat(main, listed.id); return; }
  const known = RECRUIT.found[id];
  if (!known || arrive) main.innerHTML = `<div class="recruit-page"><div class="empty">${ldotHTML({state: 'thinking', size: 24})}</div></div>`;
  let found = known;
  try {
    // Listing again is cheap and local; an Applicant page opened from the list uses the list it came from.
    if (!known || !template) found = RECRUIT.found[id] = await post(`/v2/seats/${encodeURIComponent(id)}/recruit`, {});
  } catch (e) {
    if (load !== RECRUIT.load) return;
    main.innerHTML = `<div class="recruit-page"><p class="err">${esc(e.message)}</p></div>`;
    return;
  }
  if (load !== RECRUIT.load || !S.route.startsWith('#/seat/')) return;
  if (found.seat?.agent && typeof window.cloudAgentsSeat === 'function') { recruitAgentSeat(main, found.seat.id); return; }
  if (!arrive) recruitCount(id, found.candidates.length);
  const pick = template && found.candidates.find(c => c.template === template);
  if (template && !pick) { location.replace(recruitAddress(id)); return; }
  if (pick) recruitApplicant(main, found, pick);
  else recruitApplicants(main, found, arrive);
}
function recruitAgentSeat(main, id) {
  main.innerHTML = '<div class="recruit-page"></div>';
  window.cloudAgentsSeat(id, main.firstElementChild);
}
// The sidebar shows the count beside the seat; a count that changes ticks (ui/app/motion.js).
function recruitCount(id, n) {
  RECRUIT.counts[id] = n;
  const badge = document.querySelector(`#tree [data-seat-open="${CSS.escape(id)}"] .cnt`);
  if (badge) countSet(badge, String(n));
  else renderTree();
}
const recruitSeatCount = id => RECRUIT.counts[id];
function recruitAvatar(c, size = 34) {
  const color = RECRUIT_DEPT[c.department_id] || `hsl(${avHue(c.template)} 38% 45%)`;
  const icon = TEMPLATE_ICON.test(c.icon || '') ? c.icon : '';
  return `<span class="rc-av" style="--rc-c:${color};--rc-s:${size}px" aria-hidden="true">${icon
    ? `<span class="av-glyph" style="font-size:${Math.round(size * 0.58)}px">${esc(icon)}</span>` : esc(avInitials(c.name))}</span>`;
}
const recruitCanHire = seat => !!seat.can_edit && settingsCanCreateBots();
function recruitSub(seat) {
  return [recruitGroupName(seat.team), recruitPlaceName(seat.reports_to)].filter(Boolean).map(esc).join(' · ');
}
function recruitRow(c, seat) {
  const href = recruitAddress(seat.id, c.template), hire = recruitCanHire(seat);
  return `<li class="rc-row" data-recruit-row="${esc(c.template)}" data-href="${esc(href)}">
    ${recruitAvatar(c)}
    <div class="rc-who"><div class="rc-name"><a href="${esc(href)}">${esc(c.name)}</a><span class="rc-tag">Template</span></div>
      <p class="rc-line">${esc(c.line)}</p></div>
    <span class="rc-match ${esc(c.match)}">${RECRUIT_MATCH[c.match] || ''}</span>
    <span class="rc-act"><a class="ghost small rc-view" href="${esc(href)}">View</a>${hire ? `<button class="primary small" type="button" data-recruit-hire="${esc(c.template)}">Hire</button>` : ''}</span></li>`;
}

function recruitApplicants(main, found, arrive) {
  const seat = found.seat, list = found.candidates, cloud = typeof window.cloudAgentsTab === 'function';
  const tabs = [['all', 'All', list.length], ['templates', 'Templates', list.length], ...(cloud ? [['cloud', 'Cloud agents', null]] : [])];
  main.innerHTML = `<div class="recruit-page rc-applicants">
    <header class="rc-head">
      ${seat.team ? `<a class="rc-back rc-phone" href="${TEAM_PAGE}${esc(encodeURIComponent(seat.team))}">← ${esc(recruitGroupName(seat.team))}</a>` : ''}
      <div class="rc-titles"><h1>${esc(seat.title)}<span class="rc-hdot">${arrive ? ldotHTML({state: 'thinking', size: 20}) : ''}</span></h1>
        <p class="muted">${recruitSub(seat)}</p></div>
      ${seat.can_edit ? `<div class="rc-tools"><button class="ghost small" type="button" data-recruit-post>Job post</button>
        <button class="ghost small" type="button" data-recruit-close>Close job</button></div>` : ''}
    </header>
    <div class="rc-tabs" role="tablist" aria-label="Applicants">${tabs.map(([key, label, n], i) =>
      `<button type="button" role="tab" id="rc-tab-${key}" aria-selected="${i === 0}" aria-controls="rc-panel" data-recruit-tab="${key}">${label}${n === null ? '' : ` <span class="rc-n" data-count="${arrive ? 0 : n}">${arrive ? 0 : n}</span>`}</button>`).join('')}</div>
    <div id="rc-panel" role="tabpanel" aria-labelledby="rc-tab-all">
      <ul class="rc-list" aria-live="polite"${list.length ? '' : ' hidden'}>${arrive ? '' : list.map(c => recruitRow(c, seat)).join('')}</ul>
      <p class="rc-empty muted"${list.length ? ' hidden' : ''}>No applicants yet. Widen the job description.</p>
      <div class="rc-cloud" hidden></div>
    </div>
    ${settingsCanCreateBots() ? '<p class="rc-foot"><button class="linkish" type="button" data-recruit-scratch>Build one from scratch</button></p>' : ''}
  </div>`;
  const page = main.querySelector('.rc-applicants'), ul = page.querySelector('.rc-list');
  page.querySelectorAll('[data-recruit-tab]').forEach(tab => { tab.onclick = () => {
    page.querySelectorAll('[data-recruit-tab]').forEach(t => t.setAttribute('aria-selected', String(t === tab)));
    page.querySelector('#rc-panel').setAttribute('aria-labelledby', tab.id);
    const cloudTab = tab.dataset.recruitTab === 'cloud', box = page.querySelector('.rc-cloud');
    ul.hidden = cloudTab || !list.length; page.querySelector('.rc-empty').hidden = cloudTab || !!list.length; box.hidden = !cloudTab;
    if (cloudTab && !box.childElementCount) window.cloudAgentsTab?.(box, seat);
  }; });
  page.onclick = event => {
    if (event.target.closest('[data-recruit-post]')) { recruitJobPost(found); return; }
    if (event.target.closest('[data-recruit-close]')) { void recruitClose(seat, event.target.closest('[data-recruit-close]')); return; }
    if (event.target.closest('[data-recruit-scratch]')) { recruitScratch({title: seat.title, brief: seat.description}); return; }
    const hire = event.target.closest('[data-recruit-hire]');
    if (hire) { const c = list.find(x => x.template === hire.dataset.recruitHire); if (c) void recruitHire(found, c, hire.closest('.rc-row'), {}); return; }
    // A phone's card is one tap target: anywhere off its buttons opens the applicant.
    const row = event.target.closest('.rc-row');
    if (row && !event.target.closest('a,button')) location.hash = row.dataset.href;
  };
  if (arrive) recruitArrive(page, found);
}
// Applicants arrive one by one, best first, with the count ticking in the tab and the sidebar; the header dot works
// until the last lands. A click anywhere (or reduced motion) puts them all there at once.
function recruitArrive(page, found) {
  const seat = found.seat, list = found.candidates, ul = page.querySelector('.rc-list');
  const dot = page.querySelector('.rc-hdot .ldot'), counts = page.querySelectorAll('.rc-n');
  let shown = 0, timer = 0, over = false;
  const land = (c, animate) => {
    ul.insertAdjacentHTML('beforeend', recruitRow(c, seat));
    shown++;
    counts.forEach(n => countSet(n, String(shown)));
    recruitCount(seat.id, shown);
    if (animate && ul.lastElementChild.animate) ul.lastElementChild.animate(
      [{opacity: 0, transform: 'translateY(8px)'}, {opacity: 1, transform: 'none'}], {duration: 120, easing: 'cubic-bezier(.2,0,0,1)'});
  };
  const finish = () => {
    if (over) return;
    over = true; clearTimeout(timer); document.removeEventListener('pointerdown', finish, true);
    while (shown < list.length) land(list[shown], false);
    recruitCount(seat.id, list.length);
    ldotSet(dot, 'done');
    // The dot hops once, holds, then rests.
    if (dot) setTimeout(() => { if (dot.isConnected) ldotSet(dot, 'idle'); }, RECRUIT_HOLD);
    if (RECRUIT.run === run) RECRUIT.run = null;
  };
  const run = {finish};
  RECRUIT.run = run;
  if (!list.length || recruitCalm()) { finish(); return; }
  document.addEventListener('pointerdown', finish, true);
  const next = () => {
    if (over) return;
    land(list[shown], true);
    if (shown >= list.length) { finish(); return; }
    timer = setTimeout(next, 350 + Math.round(Math.random() * 150));
  };
  timer = setTimeout(next, 450);
}

function recruitApplicant(main, found, c) {
  const seat = found.seat, hire = recruitCanHire(seat), prefs = RECRUIT.prefs[seat.id] || {};
  const days = (c.owns || []).slice(0, 3), needs = c.prerequisites || [];
  const models = found.models || [], model = models.some(m => m.id === prefs.model) ? prefs.model : '';
  const more = [
    c.summary && c.summary !== c.line ? `<p>${esc(c.summary)}</p>` : '',
    c.never?.length ? `<h3>Never</h3><ul>${c.never.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '',
    c.onboarding?.length ? `<h3>Asks you on day one</h3><ul>${c.onboarding.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '',
    `<h3>Model</h3><p>${esc(c.model_name || '')}</p>`].join('');
  main.innerHTML = `<div class="recruit-page rc-applicant${hire ? ' can-hire' : ''}">
    <a class="rc-back" href="${esc(recruitAddress(seat.id))}">← ${esc(seat.title)} · ${found.candidates.length}</a>
    <div class="rc-cols"><article class="rc-main">
      <header class="rc-who-big">${recruitAvatar(c, 48)}<div><h1>${esc(c.name)}</h1><p class="muted">Template · runs on your computer</p></div></header>
      ${c.tags?.length ? `<div class="rc-chips">${c.tags.slice(0, 6).map(t => `<span>${esc(t)}</span>`).join('')}</div>` : ''}
      ${days.length ? `<section><h2>Day one</h2><ul class="rc-days">${days.map(x => `<li>${esc(x)}</li>`).join('')}</ul></section>` : ''}
      ${needs.length ? `<section><h2>Needs</h2><div class="rc-needs">${needs.map(n => `<div class="rc-need ${esc(n.state)}"><b>${esc(n.label)}</b><span>${RECRUIT_NEED[n.state] || ''}</span></div>`).join('')}</div></section>` : ''}
      <details class="rc-more"><summary>More</summary><div>${more}</div></details>
    </article>
    ${hire ? `<aside class="rc-hire"><form data-recruit-hire-form><h2>Hire</h2>
      <label class="rp-field"><span>Name</span><input name="name" maxlength="100" value="${esc(seat.title)}"></label>
      <label class="rp-field"><span>Reports to</span><select name="reports_to">${recruitOptions(recruitPlaces().some(([id]) => id === seat.reports_to) ? recruitPlaces() : [[seat.reports_to, recruitPlaceName(seat.reports_to)], ...recruitPlaces()], seat.reports_to)}</select></label>
      <label class="rp-field"><span>Computer</span><select name="runner_id"><option value="">Default</option></select></label>
      <details class="rc-settings"><summary>More settings</summary>
        <label class="rp-field"><span>Model</span><select name="model"><option value="">Suggested: ${esc(c.model_name || '')}</option>${recruitOptions(models.filter(m => m.id !== c.model).map(m => [m.id, m.name]), model)}</select></label></details>
      <p class="err" role="alert" data-hire-error hidden></p>
      <button class="primary rc-hire-go" type="submit">Hire</button></form></aside>` : ''}
    </div>
    ${hire ? `<div class="rc-hirebar"><span>${esc(c.name)}</span><button class="primary" type="button" data-recruit-hirebar>Hire</button></div>` : ''}
  </div>`;
  const page = main.querySelector('.rc-applicant'), form = page.querySelector('[data-recruit-hire-form]');
  if (!form) return;
  void recruitComputers().then(rows => {
    if (!form.isConnected) return;
    form.elements.runner_id.insertAdjacentHTML('beforeend', recruitOptions(rows, prefs.runner_id || ''));
  });
  form.onsubmit = event => {
    event.preventDefault();
    const f = form.elements;
    void recruitHire(found, c, page.querySelector('.rc-who-big'), {name: f.name.value.trim(), reports_to: f.reports_to.value,
      runner_id: f.runner_id.value, model: f.model.value}, form.querySelector('[data-hire-error]'));
  };
  page.querySelector('[data-recruit-hirebar]').onclick = () => form.requestSubmit();
}

// Hire: the bot is created in Onboarding in the seat's place; a HIRED stamp lands on its avatar, then its page opens.
async function recruitHire(found, c, card, choice, errorEl = null) {
  const seat = found.seat;
  if (RECRUIT.hiring || !S.me?.id || !recruitCanHire(seat)) return;
  RECRUIT.hiring = true;
  const buttons = [...document.querySelectorAll('[data-recruit-hire],.rc-hire-go,[data-recruit-hirebar]')];
  buttons.forEach(b => { b.disabled = true; });
  const fail = message => {
    if (errorEl) { errorEl.textContent = message; errorEl.hidden = false; } else toast(message, true);
    buttons.forEach(b => { b.disabled = false; }); RECRUIT.hiring = false;
  };
  try {
    // A new manager is saved first and kept, so a retry after a failed hire does not save it again.
    if (choice.reports_to && choice.reports_to !== seat.reports_to) {
      await patch(`/v2/seats/${encodeURIComponent(seat.id)}`, {reports_to: choice.reports_to});
      seat.reports_to = choice.reports_to;
    }
    const prefs = RECRUIT.prefs[seat.id] || {};
    const runner = choice.runner_id ?? prefs.runner_id ?? '';
    const model = choice.model ?? ((found.models || []).some(m => m.id === prefs.model) ? prefs.model : '');
    const hired = await post(`/v2/seats/${encodeURIComponent(seat.id)}/hire`,
      {template: c.template, model: model || '', name: choice.name || seat.title, ...(runner ? {runner_id: runner} : {})});
    await recruitStamp(card);
    delete RECRUIT.found[seat.id]; delete RECRUIT.counts[seat.id]; delete RECRUIT.prefs[seat.id];
    RECRUIT.hiring = false;
    await refresh(true);
    const where = recruitGroupName(seat.team);
    toast(`${hired.name || choice.name || seat.title} joined ${where || 'the team'}.`);
    location.hash = '#/bot/' + encodeURIComponent(hired.slug);
  } catch (e) { fail(e.message); }
}
function recruitStamp(card) {
  const av = card?.querySelector('.rc-av');
  if (!av || recruitCalm()) return Promise.resolve();
  av.insertAdjacentHTML('afterend', '<span class="rc-stamp" aria-hidden="true">Hired</span>');
  const stamp = av.nextElementSibling;
  stamp.style.left = av.offsetLeft + av.offsetWidth / 2 + 'px';
  stamp.style.top = av.offsetTop + av.offsetHeight / 2 + 'px';
  card.classList.add('rc-lift');
  const motion = stamp.animate([{opacity: 0, transform: 'translate(-50%,-50%) rotate(-8deg) scale(1.3)'},
    {opacity: 1, transform: 'translate(-50%,-50%) rotate(-8deg) scale(1)'}], {duration: 180, easing: 'cubic-bezier(.2,0,0,1)', fill: 'forwards'});
  // It holds a moment so the stamp is seen; a click cuts it short.
  return new Promise(done => {
    const end = () => { document.removeEventListener('pointerdown', end, true); clearTimeout(hold); motion.finish(); done(); };
    const hold = setTimeout(end, 520);
    document.addEventListener('pointerdown', end, true);
  });
}

// Job post: the description, editable by whoever may change the seat.
function recruitJobPost(found) {
  const seat = found.seat;
  $('#recruit-jobpost')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'recruit-jobpost'; dialog.className = 'tmodal tmodal-shell recruit-post';
  dialog.setAttribute('aria-labelledby', 'recruit-jobpost-title');
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="recruit-jobpost-title">${esc(seat.title)}</h2>
      <button class="ghost tmodal-x" type="button" data-post-cancel aria-label="Close">✕</button></div>
    <div class="tmodal-body"><label class="rp-field"><span>Job description</span>
      <textarea name="description" rows="14" maxlength="8000"${seat.can_edit ? '' : ' readonly'}>${esc(seat.description)}</textarea></label>
      <p class="err" role="alert" data-post-error hidden></p></div>
    ${seat.can_edit ? '<div class="tmodal-foot rp-foot"><button class="ghost" type="button" data-post-cancel>Cancel</button><button class="primary" type="submit">Save</button></div>' : ''}</form>`;
  document.body.append(dialog);
  dialog.onclose = () => dialog.remove();
  dialog.querySelectorAll('[data-post-cancel]').forEach(b => { b.onclick = () => dialog.close(); });
  const form = dialog.querySelector('form'), error = dialog.querySelector('[data-post-error]');
  form.onsubmit = async event => {
    event.preventDefault();
    const text = form.elements.description.value.trim();
    if (text === seat.description) { dialog.close(); return; }
    form.querySelector('[type=submit]').disabled = true;
    try {
      await patch(`/v2/seats/${encodeURIComponent(seat.id)}`, {description: text});
      dialog.close(); delete RECRUIT.found[seat.id]; route();
    } catch (e) { error.textContent = e.message; error.hidden = false; form.querySelector('[type=submit]').disabled = false; }
  };
  dialog.showModal();
}
// Close job asks twice on the same button: the first click arms it ("Close job?"), a second within a few seconds closes;
// waiting or moving away puts it back.
async function recruitClose(seat, button) {
  if (!button.dataset.armed) {
    const label = button.textContent;
    button.dataset.armed = '1'; button.textContent = 'Close job?'; button.classList.add('danger');
    const disarm = () => {
      clearTimeout(timer); button.removeEventListener('blur', disarm);
      if (!button.dataset.armed) return;
      delete button.dataset.armed; button.textContent = label; button.classList.remove('danger');
    };
    const timer = setTimeout(disarm, RECRUIT_ARM);
    button.addEventListener('blur', disarm);
    return;
  }
  delete button.dataset.armed;
  button.disabled = true;
  try {
    await post(`/v2/seats/${encodeURIComponent(seat.id)}/close`, {});
    delete RECRUIT.found[seat.id]; delete RECRUIT.counts[seat.id];
    await refresh(true);
    toast(`${seat.title} closed.`);
    location.hash = seat.team ? TEAM_PAGE + encodeURIComponent(seat.team) : OVERVIEW;
  } catch (e) { button.disabled = false; toast(e.message, true); }
}
