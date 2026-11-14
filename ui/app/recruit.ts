/* Recruiting (backend/recruiting.py): Post a job makes a Recruiting seat with a job description drafted from its title,
   its Applicants page (#/seat/<id>) lists the templates that fit, one Applicant page (#/seat/<id>/<template>) shows
   one in full, and Hire creates the bot in Onboarding in the seat's place. Settings > Bots > Add bot keeps its own
   dialog and posts through the same seat. */
'use strict';
// What a way into posting starts from: a title and a brief (Settings > Bots), or a team and manager (a group's plus).
interface RecruitSeed { scratch?: boolean; title?: string; brief?: string; template?: string; team?: string; reports_to?: string }
// A seat as its Applicants page reads it (backend recruiting.view); `agent` is set while a cloud agent is requested.
type RecruitSeatRow = import('../types/api').SeatDetail & { agent?: string };
// A cloud coding agent among a coding job's applicants (backend team_build._cloud_agents).
interface RecruitAgent { id: string; name: string; maker: string; line: string; match: string; source: 'cloud' }
type RecruitCandidate = import('../types/api').SeatCandidate;
type RecruitItem = RecruitCandidate | RecruitAgent;
// POST /v2/seats/<id>/recruit, with the cloud agents and where they go among the templates.
interface RecruitFound extends Omit<import('../types/api').SeatCandidates, 'seat'> {
  seat: RecruitSeatRow; agents?: RecruitAgent[]; agents_at?: number;
}
interface RecruitPrefs { runner_id?: string; model?: string }
interface RecruitState {
  load: number; arrive: string; run: { finish(): void } | null; found: Record<string, RecruitFound>;
  counts: Record<string, number>; prefs: Record<string, RecruitPrefs>; hiring?: boolean;
}
// A catalog card from GET /v2/templates, as Add bot reads it.
interface RecruitCard {
  slug: string; template?: string; name?: string; summary?: string; owns?: string[]; required?: boolean;
  instructions?: string; model?: string; reasoning_effort?: string;
}
// An option row: the value and its label.
type RecruitPlace = [string, string | undefined];
type RecruitFormOf<C> = HTMLFormElement & { readonly elements: HTMLFormControlsCollection & C };
interface RecruitChoice { name?: string; reports_to?: string; runner_id?: string; model?: string; harness?: string; effort?: string; compact_at?: number | null }
type RecruitDraftResult = 'ok' | 'stale' | 'error';
interface RecruitPostFields {
  title: HTMLInputElement; team: HTMLSelectElement; reports_to: HTMLSelectElement; description: HTMLTextAreaElement;
  runner_id: HTMLSelectElement; model: HTMLSelectElement;
}
interface RecruitHireFields { name: HTMLInputElement; reports_to: HTMLSelectElement; runner_id: HTMLSelectElement }
interface RecruitModelRow { id: string; label?: string; name?: string }
const RECRUIT_DEPT: Record<string, string> = {marketing: '#a63d65', sales: '#1769a6', finance: '#2f6b3c', product: '#5946b2', support: '#9a4f19',
  legal: '#4b5563', engineering: '#3f566b', operations: '#0f6e56', hr: '#7a3e9d', leadership: '#3f566b'};
const RECRUIT_MATCH: Record<string, string> = {strong: 'Strong', good: 'Good'};
const RECRUIT_HOLD = 1600;                         // how long a dot that landed stays in 'done' before it rests
const RECRUIT_ARM = 4000;                          // how long Close job? waits for the second click
const RECRUIT_NEED: Record<string, string> = {connected: 'Connected', on_hire: 'Installs on hire', not_set: 'Not set'};

function settingsRecruitHandoff() {
  if (!RECRUIT_HANDOFF || !S.me?.id || !settingsCanCreateBots()) return;
  const seed = RECRUIT_HANDOFF; RECRUIT_HANDOFF = null;
  settingsShow('bots');
  if (seed.scratch) { recruitScratchEditor(seed); return; }
  void settingsRecruitFlow(seed);
}
// From scratch is the bot editor in Settings > Bots, which needs that page's data: go there and open it once loaded.
function recruitScratch(seed: RecruitSeed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  RECRUIT_HANDOFF = {scratch: true, title: String(seed.title || ''), brief: String(seed.brief || '')};
  SETTINGS_TAB = 'bots';
  if (S.route === SETTINGS) settingsRecruitHandoff(); else location.hash = SETTINGS;
}
function recruitScratchEditor(seed: RecruitSeed) {
  settingsEditBot();
  const editor = $('#bot-editor');
  if (!editor) return;
  if (seed.title) editor.querySelector('[name=display_name]').value = seed.title.slice(0, 100);
  if (seed.brief) editor.querySelector('[name=description]').value = seed.brief.slice(0, 2000);
}
// Settings > Bots > Add bot: Recruit candidates posts a job; a template, an existing agent or a bot from scratch as before.
async function settingsRecruitFlow(seed: RecruitSeed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  const dialog = recruitDialog();
  const draft = {title:String(seed.title||'').slice(0,100),brief:String(seed.brief||'').slice(0,2000),template:String(seed.template||'').slice(0,80)};
  let route: string | undefined = draft.template ? 'template' : 'recruit', cards: RecruitCard[] = [], creating = false;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="recruit-title">Add a bot</h2><span class="spacer"></span><button class="ghost" type="button" data-recruit-close aria-label="Close">✕</button></div><div class="bot-editor-body"><div class="row" style="flex-wrap:wrap;margin:0 0 16px">${[['recruit','Post a job'],['template','From a template'],['existing','Existing agent'],['scratch','From scratch']].map(([id,label])=>`<button class="ghost" type="button" data-recruit-route="${id}" aria-pressed="${route===id}">${label}</button>`).join('')}</div><label>Job title<input name="title" maxlength="100" value="${esc(draft.title)}" placeholder="Customer support"></label><label>Job description<textarea name="brief" rows="4" maxlength="2000" placeholder="What should this bot own?">${esc(draft.brief)}</textarea></label><details><summary>More</summary><label>Tools and sources<input name="tools" maxlength="300" placeholder="e.g. Mailbox, team docs"></label><label>Who reviews the work?<input name="reviewer" maxlength="150" placeholder="e.g. Head of support"></label></details><p class="muted" data-recruit-status role="status"></p><div data-recruit-candidates></div></div><div class="bot-editor-actions"><button class="primary" type="submit">Continue</button><button class="ghost" type="button" data-recruit-close>Cancel</button></div></form>`;
  dialog.querySelectorAll<HTMLButtonElement>('[data-recruit-close]').forEach(b=>b.onclick=()=>dialog.close());
  dialog.querySelectorAll<HTMLButtonElement>('[data-recruit-route]').forEach(b=>b.onclick=()=>{route=b.dataset.recruitRoute;dialog.querySelectorAll('[data-recruit-route]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));dialog.querySelector('[data-recruit-candidates]')!.replaceChildren();});
  const form=dialog.querySelector<RecruitFormOf<{title: HTMLInputElement; brief: HTMLTextAreaElement}>>('form'), status=dialog.querySelector('[data-recruit-status]'), results=dialog.querySelector('[data-recruit-candidates]');
  if (!form || !status || !results) return;
  form.onsubmit=e=>e.preventDefault();
  dialog.showModal();
  try { cards=catalogCards(await get('/v2/templates')); } catch(error){status.textContent=errorMessage(error, '');}
  // Only Post a job needs a title and a job description (its seat is made from them); the other ways go on without.
  const recruitMissing=()=>{
    const title=form.elements.title,brief=form.elements.brief;settingsFieldClear(title);settingsFieldClear(brief);
    if(!title.value.trim()){settingsFieldError(title,'Give the job a title.');title.focus();return true;}
    if(brief.value.trim().length<10){settingsFieldError(brief,'Describe the job in a sentence or two (at least 10 characters).');brief.focus();return true;}
    return false;
  };
  const fail=(message: string)=>{status.classList.add('err');status.textContent=message;};
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
      try{const seat: import('../types/api').Seat=await post('/v2/seats',{title:draft.title.trim(),description:draft.brief.trim()});dialog.close();void refresh(true);await recruitSeat(seat.id,{arrive:true});}
      catch(error){fail(errorMessage(error, ''));}
      creating=false;return;
    }
    const terms=new Set(draft.brief.toLowerCase().match(/[a-z]{3,}/g)||[]);
    const score=(c: RecruitCard)=>(draft.template&&c.template===draft.template?100:0)+(c.required?-100:0)+(c.summary||'').toLowerCase().split(/\W+/).filter(w=>terms.has(w)).length;
    const candidates=cards.filter(c=>c.template!=='inbox-manager'&&!c.required).slice().sort((a,b)=>score(b)-score(a)).slice(0,6);
    status.textContent='';
    results.innerHTML=candidates.map(c=>`<article class="cat-card"><h3>${esc(c.name)}</h3><p class="cat-summary">${esc(c.summary||'')}</p>${c.owns?.length?`<p class="muted">Owns: ${esc(c.owns.join('; '))}</p>`:''}<button class="primary" type="button" data-recruit-create="${esc(c.slug)}">Choose & create ${esc(c.name)}</button></article>`).join('');
    results.querySelectorAll<HTMLButtonElement>('[data-recruit-create]').forEach(b=>b.onclick=async()=>{
      if(creating||!S.me?.id||!settingsCanCreateBots())return;
      const card=cards.find(c=>c.slug===b.dataset.recruitCreate);if(!card)return;
      creating=true;results.querySelectorAll<HTMLButtonElement>('[data-recruit-create]').forEach(x=>x.disabled=true);status.textContent='Creating a bot that waits for setup…';
      const title=draft.title.trim()||card.name;
      if(title===undefined)return; // a card with no name and no title typed: the old code threw on title.toLowerCase()
      const slugBase=title.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,55)||card.slug;
      let slug=slugBase;for(let n=2;S.emps.some(x=>x.slug===slug);n++)slug=slugBase+'-'+n;
      const brief='\n\n## Role brief\n'+(draft.brief.trim()||'Not set')+'\nTools and sources: '+String(data.get('tools')||'Not set')+'\nReviewer: '+String(data.get('reviewer')||'Ask the owner during setup');
      try{await post('/v2/bots',{slug,display_name:title,description:draft.brief,template:card.template,
        instructions:(card.instructions||'')+brief,reports_to:null,status:'planned',repo:'bot-'+slug,thread_mode:'personal',
        model:card.model||'',effort:card.reasoning_effort||'',operator:S.me.id,owners:[S.me.id],runner_id:null});
        dialog.close();await refresh(true);if(S.route===SETTINGS){await loadSettings();settingsShow('bots');}toast('Bot created. Complete its setup conversation next.');
      }catch(error){fail(errorMessage(error, ''));creating=false;results.querySelectorAll<HTMLButtonElement>('[data-recruit-create]').forEach(x=>x.disabled=false);}
    });
  };
}

function recruitDialog(): HTMLDialogElement {
  let dialog: HTMLDialogElement | null = $('#recruit-picker');
  if (!dialog) {
    dialog = document.createElement('dialog'); dialog.id = 'recruit-picker'; dialog.className = 'bot-editor catalog-picker';
    dialog.setAttribute('aria-labelledby', 'recruit-title'); document.body.append(dialog);
  }
  return dialog;
}
const recruitMe = () => 'human:' + (mePerson()?.id || S.me?.id || '');
// Where a new seat may hang: you, or (the owner) any person or bot on the chart, or a bot you manage.
function recruitPlaces(): RecruitPlace[] {
  const owner = S.me?.role === 'owner', me = recruitMe();
  const people = owner ? (S.people || []).filter(p => !p.hidden).map((p): RecruitPlace => ['human:' + p.id, p.name || p.id]) : [];
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && !isHelperBot(e) && (owner || e.can_manage))
    .map((e): RecruitPlace => [e.id, e.display_name || e.slug]);
  return [[me, 'You'], ...people.filter(([id]) => id !== me), ...bots];
}
const recruitPlaceName = (value: string | undefined) => recruitPlaces().find(([id]) => id === value)?.[1]
  || botRow(value)?.display_name
  || (S.people || []).find(p => 'human:' + p.id === value)?.name || value;
const recruitGroupName = (id: string | undefined) => (S.orgGroups || []).find(g => g.id === id)?.name || '';
const recruitOptions = (rows: readonly RecruitPlace[], value: string | undefined) => rows.map(([id, name]) => `<option value="${esc(id)}"${id === value ? ' selected' : ''}>${esc(name)}</option>`).join('');
// The computers a bot may go on; one that cannot be read leaves the choice to the hire.
let RECRUIT_COMPUTERS: Promise<RecruitPlace[]> | null = null;
function recruitComputers(): Promise<RecruitPlace[]> {
  RECRUIT_COMPUTERS ||= get<{computers?: {id: string; label?: string; revoked_at?: string | null}[]}>('/v2/computers').then(d => (d.computers || []).filter(c => !c.revoked_at).map((c): RecruitPlace => [c.id, c.label || c.id]),
    () => { RECRUIT_COMPUTERS = null; return []; });
  return RECRUIT_COMPUTERS;
}
// Movement that skips: reduced motion shows the end at once, and a click anywhere jumps there.
const recruitCalm = () => !!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
function recruitStop() {
  RECRUIT.run?.finish(); RECRUIT.run = null;
}

// ------------------------------------------------------------------ Post a job
async function recruitPostJob(seed: RecruitSeed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  setDrawer(false);
  $('#recruit-post')?.remove();
  const groups = (S.orgGroups || []).map((g): RecruitPlace => [g.id, g.name]);
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
  const form = dialog.querySelector<RecruitFormOf<RecruitPostFields>>('form')!, f = form.elements, error = dialog.querySelector<HTMLElement>('[data-post-error]')!;
  const redraft = dialog.querySelector<HTMLButtonElement>('[data-post-redraft]')!, dot = redraft.querySelector('.ldot'), text = f.description;
  const leave = () => dialog.close();
  window.addEventListener('hashchange', leave);
  dialog.onclose = () => { window.removeEventListener('hashchange', leave); dialog.remove(); };
  dialog.querySelectorAll<HTMLButtonElement>('[data-post-cancel]').forEach(b => { b.onclick = () => dialog.close(); });
  dialog.querySelector('[data-post-place]')?.addEventListener('click', event => {
    if (event.currentTarget instanceof HTMLElement) event.currentTarget.hidden = true;
    dialog.querySelector<HTMLElement>('[data-post-pair]')!.hidden = false; f.team.focus();
  });
  // The description is drafted from the title once it is left (and again on Redraft). Each draft is asked for one
  // title; it lands only if it is still the newest and nobody typed in the description since it was asked for, so it
  // never replaces words written by hand. `draftedFor` is the title the drafted words belong to.
  let typed = false, drafted = '', draftedFor = '', asked = 0, edits = 0, pending: Promise<RecruitDraftResult> | null = null, pendingTitle = '';
  text.oninput = () => { edits++; typed = text.value.trim() !== '' && text.value !== drafted; };
  const ask = async (title: string, again: boolean, mine: number, seen: number): Promise<RecruitDraftResult> => {
    ldotSet(dot, 'thinking'); redraft.disabled = true; text.setAttribute('aria-busy', 'true');
    try {
      const found: import('../types/api').SeatDrafted = await post('/v2/seats/draft', {title, team: recruitGroupName(f.team.value), reports_to: f.reports_to.value, redraft: again});
      if (mine !== asked || !dialog.open) return 'stale';
      // A title no template fits clears words drafted for an earlier title, but never words typed by hand.
      if (edits === seen && (found.description || text.value === drafted)) {
        text.value = drafted = found.description || ''; draftedFor = title; typed = false;
      }
      ldotSet(dot, 'done');
      return 'ok';
    } catch (e) {
      if (mine !== asked) return 'stale';
      ldotSet(dot, 'idle'); error.textContent = errorMessage(e, ''); error.hidden = false;
      return 'error';
    } finally { if (mine === asked) { redraft.disabled = false; text.removeAttribute('aria-busy'); pending = null; } }
  };
  const draftNow = (again = false): Promise<RecruitDraftResult> => {
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
  void taskRunCatalog().then((rows: RecruitModelRow[]) => { if (dialog.open) f.model.insertAdjacentHTML('beforeend', recruitOptions(rows.map((m): RecruitPlace => [m.id, m.label || m.name || m.id]), '')); });
  let posting = false;
  form.onsubmit = async event => {
    event.preventDefault();
    if (posting) return;
    error.hidden = true;
    const title = f.title.value.trim();
    if (!title) { error.textContent = 'Give the job a title.'; error.hidden = false; f.title.focus(); return; }
    if (!S.me?.id || !settingsCanCreateBots()) { error.textContent = 'You no longer have permission to add bots.'; error.hidden = false; return; }
    const submit = form.querySelector<HTMLButtonElement>('[type=submit]');
    if (!submit) return;
    const unlock = () => { posting = false; submit.disabled = false; f.title.readOnly = false; };
    posting = true; submit.disabled = true; f.title.readOnly = true;      // the title being posted stays put
    if (!await settle()) { unlock(); return; }
    if (!dialog.open) return;
    try {
      const seat: import('../types/api').Seat = await post('/v2/seats', {title, reports_to: f.reports_to.value, description: text.value.trim(),
        ...(f.team.value ? {team: f.team.value} : {})});
      RECRUIT.prefs[seat.id] = {runner_id: f.runner_id.value, model: f.model.value};
      dialog.close();
      void refresh(true);
      await recruitSeat(seat.id, {arrive: true});
    } catch (e) { error.textContent = errorMessage(e, ''); error.hidden = false; unlock(); }
  };
  dialog.showModal();
  // On a phone the dialog itself takes focus, so no field shows a ring and no keyboard opens until a tap.
  if (matchMedia('(max-width:600px)').matches) { dialog.tabIndex = -1; dialog.focus({preventScroll: true}); }
  else f.title.focus();
  if (f.title.value.trim()) void draftNow();
}

// Open a seat's Applicants page: from the org chart, or right after posting (then they arrive one by one).
async function recruitSeat(id: string, {arrive = false}: {arrive?: boolean} = {}) {
  if (arrive) RECRUIT.arrive = id;
  location.hash = recruitAddress(id);
}

// ------------------------------------------------------------------ the pages
async function pageSeat(id: string, template = '') {
  const load = ++RECRUIT.load;
  const main = $('#main');
  main.classList.add('recruit-layout');
  const arrive = RECRUIT.arrive === id && !template; RECRUIT.arrive = '';
  // A seat waiting on a requested cloud agent has that agent's page instead (ui/app/cloud-agents.ts).
  const listed = (S.seats || []).find(row => row.id === id);
  if (listed?.agent && typeof window.cloudAgentsSeat === 'function') { recruitAgentSeat(main, listed.id); return; }
  const known = RECRUIT.found[id];
  if (!known || arrive) main.innerHTML = `<div class="recruit-page"><div class="empty">${ldotHTML({state: 'thinking', size: 24})}</div></div>`;
  let found: RecruitFound | undefined = known;
  try {
    // Listing again is cheap and local; an Applicant page opened from the list uses the list it came from.
    if (!known || !template) found = RECRUIT.found[id] = await post(`/v2/seats/${encodeURIComponent(id)}/recruit`, {});
  } catch (e) {
    if (load !== RECRUIT.load) return;
    main.innerHTML = `<div class="recruit-page"><p class="err">${esc(errorMessage(e, ''))}</p></div>`;
    return;
  }
  if (load !== RECRUIT.load || !S.route.startsWith('#/seat/')) return;
  if (!found) return;
  if (found.seat?.agent && typeof window.cloudAgentsSeat === 'function') { recruitAgentSeat(main, found.seat.id); return; }
  if (!arrive) recruitCount(id, recruitAll(found).length);
  const pick = template && found.candidates.find(c => c.template === template);
  if (template && !pick) { location.replace(recruitAddress(id)); return; }
  if (pick) recruitApplicant(main, found, pick);
  else recruitApplicants(main, found, arrive);
}
function recruitAgentSeat(main: HTMLElement, id: string) {
  main.innerHTML = '<div class="recruit-page"></div>';
  window.cloudAgentsSeat(id, main.firstElementChild);
}
// The sidebar shows the count beside the seat; a count that changes ticks (ui/app/motion.ts).
function recruitCount(id: string, n: number) {
  RECRUIT.counts[id] = n;
  const badge = document.querySelector(`#tree [data-seat-open="${CSS.escape(id)}"] .cnt`);
  if (badge) countSet(badge, String(n));
  else renderTree();
}
function recruitAvatar(c: RecruitCandidate, size = 34) {
  const color = RECRUIT_DEPT[String(c.department_id)] || `hsl(${avHue(c.template)} 38% 33%)`;
  const icon = TEMPLATE_ICON.test(c.icon || '') ? c.icon : '';
  return `<span class="rc-av" style="--rc-c:${color};--rc-s:${size}px" aria-hidden="true">${icon
    ? `<span class="av-glyph" style="font-size:${Math.round(size * 0.58)}px">${esc(icon)}</span>` : esc(avInitials(c.name))}</span>`;
}
const recruitCanHire = (seat: RecruitSeatRow) => !!seat.can_edit && settingsCanCreateBots();
function recruitSub(seat: RecruitSeatRow) {
  return [recruitGroupName(seat.team), recruitPlaceName(seat.reports_to)].filter(Boolean).map(esc).join(' · ');
}
function recruitRow(c: RecruitCandidate, seat: RecruitSeatRow) {
  const href = recruitAddress(seat.id, c.template), hire = recruitCanHire(seat);
  return `<li class="rc-row" data-recruit-row="${esc(c.template)}" data-href="${esc(href)}">
    ${recruitAvatar(c)}
    <div class="rc-who"><div class="rc-name"><a href="${esc(href)}">${esc(c.name)}</a><span class="rc-tag">Template</span></div>
      <p class="rc-line">${esc(c.line)}</p></div>
    <span class="rc-match ${esc(c.match)}">${RECRUIT_MATCH[c.match] || ''}</span>
    <span class="rc-act"><a class="ghost small rc-view" href="${esc(href)}">View</a>${hire ? `<button class="primary small" type="button" data-recruit-hire="${esc(c.template)}">Hire</button>` : ''}</span></li>`;
}

// A cloud coding agent among a coding job's applicants (backend recruiting.candidates `agents`): Hire adds it in the
// seat (ui/app/cloud-agents.ts), with no applicant page of its own.
function recruitAgentRow(a: RecruitAgent, seat: RecruitSeatRow) {
  const hire = recruitCanHire(seat) && typeof window.cloudAgentHire === 'function';
  return `<li class="rc-row rc-agent" data-recruit-agent="${esc(a.id)}">
    <span class="rc-av rc-av-agent" style="--rc-s:34px" aria-hidden="true">${esc(avInitials(a.name))}</span>
    <div class="rc-who"><div class="rc-name"><span>${esc(a.name)}</span><span class="rc-tag">Cloud agent · ${esc(a.maker)}</span></div>
      <p class="rc-line">${esc(a.line)}</p></div>
    <span class="rc-match ${esc(a.match)}">${RECRUIT_MATCH[a.match] || ''}</span>
    <span class="rc-act">${hire ? `<button class="primary small" type="button" data-recruit-agent-hire="${esc(a.id)}">Hire</button>` : ''}</span></li>`;
}
// Every applicant in the All tab's order: the templates, with a coding job's cloud agents after the ones that are the job.
function recruitAll(found: RecruitFound): RecruitItem[] {
  const list = found.candidates || [], agents = found.agents || [], at = Math.min(found.agents_at || 0, list.length);
  return [...list.slice(0, at), ...agents, ...list.slice(at)];
}
const recruitItemRow = (item: RecruitItem, seat: RecruitSeatRow) => item.source === 'cloud' ? recruitAgentRow(item, seat) : recruitRow(item, seat);

function recruitApplicants(main: HTMLElement, found: RecruitFound, arrive: boolean) {
  const seat = found.seat, list = found.candidates, all = recruitAll(found), cloud = typeof window.cloudAgentsTab === 'function';
  const tabs: [string, string, number | null][] = [['all', 'All', all.length], ['templates', 'Templates', list.length], ...(cloud ? [['cloud', 'Cloud agents', null] satisfies [string, string, null]] : [])];
  main.innerHTML = `<div class="recruit-page rc-applicants">
    <header class="rc-head">
      ${seat.team ? `<a class="rc-back rc-phone" href="${TEAM_PAGE}${esc(encodeURIComponent(seat.team))}">← ${esc(recruitGroupName(seat.team))}</a>` : ''}
      <div class="rc-titles"><h1>${esc(seat.title)}<span class="rc-hdot">${arrive ? ldotHTML({state: 'thinking', size: 20}) : ''}</span></h1>
        <p class="muted">${recruitSub(seat)}</p></div>
      ${seat.can_edit ? `<div class="rc-tools"><button class="ghost small" type="button" data-recruit-post>Job post</button>
        <button class="ghost small" type="button" data-recruit-close>Close job</button></div>` : ''}
    </header>
    <div class="rc-tabs" role="tablist" aria-label="Applicants">${tabs.map(([key, label, n], i) =>
      `<button type="button" role="tab" id="rc-tab-${key}" aria-selected="${i === 0}" aria-controls="rc-panel" data-recruit-tab="${key}">${label}${n === null ? '' : ` <span class="rc-n" data-count="${arrive ? 0 : n}" data-of="${key}">${arrive ? 0 : n}</span>`}</button>`).join('')}</div>
    <div id="rc-panel" role="tabpanel" aria-labelledby="rc-tab-all">
      <ul class="rc-list" aria-live="polite"${all.length ? '' : ' hidden'}>${arrive ? '' : all.map(c => recruitItemRow(c, seat)).join('')}</ul>
      <p class="rc-empty muted"${all.length ? ' hidden' : ''}>No applicants yet. Widen the job description.</p>
      <div class="rc-cloud" hidden></div>
    </div>
    ${settingsCanCreateBots() ? '<p class="rc-foot"><button class="linkish" type="button" data-recruit-scratch>Build one from scratch</button></p>' : ''}
  </div>`;
  const page = main.querySelector<HTMLElement>('.rc-applicants')!, ul = page.querySelector<HTMLUListElement>('.rc-list')!;
  page.querySelectorAll<HTMLButtonElement>('[data-recruit-tab]').forEach(tab => { tab.onclick = () => {
    page.querySelectorAll('[data-recruit-tab]').forEach(t => t.setAttribute('aria-selected', String(t === tab)));
    page.querySelector('#rc-panel')!.setAttribute('aria-labelledby', tab.id);
    const cloudTab = tab.dataset.recruitTab === 'cloud', box = page.querySelector<HTMLElement>('.rc-cloud')!;
    // Templates shows the templates alone; All has the cloud agents among them.
    const templatesTab = tab.dataset.recruitTab === 'templates', shown = templatesTab ? list : all;
    ul.querySelectorAll<HTMLElement>('.rc-agent').forEach(row => { row.hidden = templatesTab; });
    ul.hidden = cloudTab || !shown.length; page.querySelector<HTMLElement>('.rc-empty')!.hidden = cloudTab || !!shown.length; box.hidden = !cloudTab;
    if (cloudTab && !box.childElementCount) window.cloudAgentsTab?.(box, seat);
  }; });
  page.onclick = event => {
    const target = eventElement(event);
    if (!target) return;
    if (target.closest('[data-recruit-post]')) { recruitJobPost(found); return; }
    if (target.closest('[data-recruit-close]')) { void recruitClose(seat, target.closest<HTMLButtonElement>('[data-recruit-close]')!); return; }
    if (target.closest('[data-recruit-scratch]')) { recruitScratch({title: seat.title, brief: seat.description}); return; }
    const hire = target.closest<HTMLElement>('[data-recruit-hire]');
    if (hire) { const c = list.find(x => x.template === hire.dataset.recruitHire); if (c) void recruitHire(found, c, hire.closest('.rc-row'), {}); return; }
    const agentHire = target.closest<HTMLElement>('[data-recruit-agent-hire]');
    if (agentHire) { const a = (found.agents || []).find(x => x.id === agentHire.dataset.recruitAgentHire); if (a) window.cloudAgentHire?.(a, seat.id); return; }
    // A phone's card is one tap target: anywhere off its buttons opens the applicant.
    const row = target.closest<HTMLElement>('.rc-row');
    if (row?.dataset.href && !target.closest('a,button')) location.hash = row.dataset.href;
  };
  if (arrive) recruitArrive(page, found);
}
// Applicants arrive one by one, best first, with the count ticking in the tab and the sidebar; the header dot works
// until the last lands. A click anywhere (or reduced motion) puts them all there at once.
function recruitArrive(page: HTMLElement, found: RecruitFound) {
  const seat = found.seat, list = recruitAll(found), ul = page.querySelector('.rc-list');
  if (!ul) return;
  const dot = page.querySelector('.rc-hdot .ldot'), counts = page.querySelectorAll<HTMLElement>('.rc-n');
  let shown = 0, templates = 0, timer = 0, over = false;
  const land = (c: RecruitItem | undefined, animate: boolean) => {
    if (!c) return;
    ul.insertAdjacentHTML('beforeend', recruitItemRow(c, seat));
    const row = ul.lastElementChild;
    if (!(row instanceof HTMLElement)) return;
    // A cloud agent landing while Templates is open stays out of it, as the tab's own switch leaves it.
    if (c.source === 'cloud') row.hidden = page.querySelector('[data-recruit-tab="templates"]')?.getAttribute('aria-selected') === 'true';
    shown++;
    if (c.source !== 'cloud') templates++;
    counts.forEach(n => countSet(n, String(n.dataset.of === 'templates' ? templates : shown)));
    recruitCount(seat.id, shown);
    if (animate && row.animate) row.animate(
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

function recruitApplicant(main: HTMLElement, found: RecruitFound, c: RecruitCandidate) {
  const seat = found.seat, hire = recruitCanHire(seat), prefs = RECRUIT.prefs[seat.id] || {};
  const days = (c.owns || []).slice(0, 3), needs = c.prerequisites || [];
  const models = found.models || [], model = models.some(m => m.id === prefs.model) ? prefs.model : '';
  // Under a seat that is still open, the bot reports above it: the one line that says who, until that seat is hired.
  const manager = seat.reports_now, managerLabel = manager ? (manager.until ? `${manager.until}, not hired yet` : manager.name || 'Seat above') : '';
  const more = [
    c.summary && c.summary !== c.line ? `<p>${esc(c.summary)}</p>` : '',
    c.never?.length ? `<h3>Never</h3><ul>${c.never.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '',
    c.onboarding?.length ? `<h3>Asks you on day one</h3><ul>${c.onboarding.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '',
    `<h3>Model</h3><p>${esc(c.model_name || '')}</p>`].join('');
  main.innerHTML = `<div class="recruit-page rc-applicant${hire ? ' can-hire' : ''}">
    <a class="rc-back" href="${esc(recruitAddress(seat.id))}">← ${esc(seat.title)} · ${recruitAll(found).length}</a>
    <div class="rc-cols"><article class="rc-main">
      <header class="rc-who-big">${recruitAvatar(c, 48)}<div><h1>${esc(c.name)}</h1><p class="muted">Template · runs on your computer</p></div></header>
      ${c.tags?.length ? `<div class="rc-chips">${c.tags.slice(0, 6).map(t => `<span>${esc(t)}</span>`).join('')}</div>` : ''}
      ${days.length ? `<section><h2>Day one</h2><ul class="rc-days">${days.map(x => `<li>${esc(x)}</li>`).join('')}</ul></section>` : ''}
      ${needs.length ? `<section><h2>Needs</h2><div class="rc-needs">${needs.map(n => `<div class="rc-need ${esc(n.state)}"><b>${esc(n.label)}</b><span>${RECRUIT_NEED[n.state] || ''}</span></div>`).join('')}</div></section>` : ''}
      <details class="rc-more"><summary>More</summary><div>${more}</div></details>
    </article>
    ${hire ? `<aside class="rc-hire"><form data-recruit-hire-form><h2>Hire</h2>
      <label class="rp-field"><span>Name</span><input name="name" maxlength="100" value="${esc(seat.title)}"></label>
      <label class="rp-field"><span>Reports to</span><select name="reports_to">${recruitOptions(recruitPlaces().some(([id]) => id === seat.reports_to) ? recruitPlaces() : [[seat.reports_to, managerLabel || recruitPlaceName(seat.reports_to)], ...recruitPlaces()], seat.reports_to)}</select></label>
      ${manager?.until ? `<p class="muted" data-reports-now>Reports to ${esc(manager.name || 'the person or bot above')} until ${esc(manager.until)} is hired.</p>` : ''}
      <label class="rp-field"><span>Computer</span><select name="runner_id"><option value="">Default</option></select></label>
      <details class="rc-settings"><summary>More settings</summary>
        <div class="rp-field" data-hire-model><span>Model</span></div></details>
      <p class="err" role="alert" data-hire-error hidden></p>
      <button class="primary rc-hire-go" type="submit">Hire</button></form></aside>` : ''}
    </div>
    ${hire ? `<div class="rc-hirebar"><span>${esc(c.name)}</span><button class="primary" type="button" data-recruit-hirebar>Hire</button></div>` : ''}
  </div>`;
  const page = main.querySelector<HTMLElement>('.rc-applicant')!, form = page.querySelector<RecruitFormOf<RecruitHireFields>>('[data-recruit-hire-form]');
  if (!form) return;
  // The line says where the bot reports only while the seat's own line is what is picked: a new manager replaces it.
  const reportsNow = form.querySelector<HTMLElement>('[data-reports-now]');
  if (reportsNow) form.elements.reports_to.addEventListener('change', () => { reportsNow.hidden = form.elements.reports_to.value !== seat.reports_to; });
  void recruitHireModel(form, c, model);
  void recruitComputers().then(rows => {
    if (!form.isConnected) return;
    form.elements.runner_id.insertAdjacentHTML('beforeend', recruitOptions(rows, prefs.runner_id || ''));
  });
  form.onsubmit = event => {
    event.preventDefault();
    const f = form.elements;
    void recruitHire(found, c, page.querySelector('.rc-who-big'), {name: f.name.value.trim(), reports_to: f.reports_to.value,
      runner_id: f.runner_id.value, ...recruitHirePick(form)}, form.querySelector<HTMLElement>('[data-hire-error]'));
  };
  page.querySelector<HTMLElement>('[data-recruit-hirebar]')!.onclick = () => form.requestSubmit();
}

// The Hire box's Model is the shared picker (settings-bots-choice.ts); its catalog is read once when the page has none.
async function recruitHireModel(form: HTMLFormElement, c: RecruitCandidate, model: string | undefined) {
  const host = form.querySelector<HTMLElement>('[data-hire-model]');
  if (!host) return;
  try { await settingsChoiceCatalog(); } catch { return; }
  if (!form.isConnected) return;
  const start = settingsAllChoices().find(row => row.model === model)?.value || '';
  host.insertAdjacentHTML('beforeend', settingsChoiceBox({kind: 'hire', name: 'Model', current: start, mode: 'bot', none: `Suggested: ${c.model_name || 'the team default'}`}));
  settingsWireCombos(host, 'hold');
}
// What the Hire box's picker holds: the model, its harness and effort, and the window when one was chosen.
function recruitHirePick(form: HTMLFormElement): RecruitChoice {
  const box = form.querySelector<SettingsChoiceBox>('[data-bot-choice]'), value = box?.picker?.value() || '';
  if (!value) return {model: ''};
  const pick = settingsChoiceFromValue(value);
  return {model: pick.model, harness: settingsRealHarness(pick.harness), effort: pick.effort, compact_at: box?.picker?.compact() || null};
}

// Hire: the bot is created in Onboarding in the seat's place; a HIRED stamp lands on its avatar, then its page opens.
async function recruitHire(found: RecruitFound, c: RecruitCandidate, card: Element | null, choice: RecruitChoice, errorEl: HTMLElement | null = null) {
  const seat = found.seat;
  if (RECRUIT.hiring || !S.me?.id || !recruitCanHire(seat)) return;
  RECRUIT.hiring = true;
  const buttons = [...document.querySelectorAll<HTMLButtonElement>('[data-recruit-hire],.rc-hire-go,[data-recruit-hirebar]')];
  buttons.forEach(b => { b.disabled = true; });
  const fail = (message: string) => {
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
    const hired: import('../types/api').SeatHired & {bot: string} = await post(`/v2/seats/${encodeURIComponent(seat.id)}/hire`,
      {template: c.template, model: model || '', ...(model && choice.harness ? {harness: choice.harness} : {}), ...(model && choice.effort ? {effort: choice.effort} : {}),
        ...(model && choice.compact_at ? {compact_at: choice.compact_at} : {}), name: choice.name || seat.title, ...(runner ? {runner_id: runner} : {})});
    await recruitStamp(card);
    delete RECRUIT.found[seat.id]; delete RECRUIT.counts[seat.id]; delete RECRUIT.prefs[seat.id];
    RECRUIT.hiring = false;
    await refresh(true);
    const where = recruitGroupName(seat.team);
    toast(`${hired.name || choice.name || seat.title} joined ${where || 'the team'}.`);
    location.hash = '#/bot/' + encodeURIComponent(hired.bot);
  } catch (e) { fail(errorMessage(e, '')); }
}
function recruitStamp(card: Element | null): Promise<void> {
  const av = card?.querySelector<HTMLElement>('.rc-av');
  if (!av || !card || recruitCalm()) return Promise.resolve();
  av.insertAdjacentHTML('afterend', '<span class="rc-stamp" aria-hidden="true">Hired</span>');
  const stamp = av.nextElementSibling;
  if (!(stamp instanceof HTMLElement)) return Promise.resolve();
  stamp.style.left = av.offsetLeft + av.offsetWidth / 2 + 'px';
  stamp.style.top = av.offsetTop + av.offsetHeight / 2 + 'px';
  card.classList.add('rc-lift');
  const motion = stamp.animate([{opacity: 0, transform: 'translate(-50%,-50%) rotate(-8deg) scale(1.3)'},
    {opacity: 1, transform: 'translate(-50%,-50%) rotate(-8deg) scale(1)'}], {duration: 180, easing: 'cubic-bezier(.2,0,0,1)', fill: 'forwards'});
  // It holds a moment so the stamp is seen; a click cuts it short.
  return new Promise<void>(done => {
    const end = () => { document.removeEventListener('pointerdown', end, true); clearTimeout(hold); motion.finish(); done(); };
    const hold = setTimeout(end, 520);
    document.addEventListener('pointerdown', end, true);
  });
}

// Job post: the description, editable by whoever may change the seat.
function recruitJobPost(found: RecruitFound) {
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
  dialog.querySelectorAll<HTMLButtonElement>('[data-post-cancel]').forEach(b => { b.onclick = () => dialog.close(); });
  const form = dialog.querySelector<RecruitFormOf<{description: HTMLTextAreaElement}>>('form'), error = dialog.querySelector<HTMLElement>('[data-post-error]');
  if (!form || !error) return;
  form.onsubmit = async event => {
    event.preventDefault();
    const text = form.elements.description.value.trim();
    if (text === seat.description) { dialog.close(); return; }
    const submit = form.querySelector<HTMLButtonElement>('[type=submit]');
    if (!submit) return;
    submit.disabled = true;
    try {
      await patch(`/v2/seats/${encodeURIComponent(seat.id)}`, {description: text});
      dialog.close(); delete RECRUIT.found[seat.id]; route();
    } catch (e) { error.textContent = errorMessage(e, ''); error.hidden = false; submit.disabled = false; }
  };
  dialog.showModal();
}
// Close job asks twice on the same button: the first click arms it ("Close job?"), a second within a few seconds closes;
// waiting or moving away puts it back.
async function recruitClose(seat: RecruitSeatRow, button: HTMLButtonElement) {
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
  } catch (e) { button.disabled = false; toast(errorMessage(e, ''), true); }
}
