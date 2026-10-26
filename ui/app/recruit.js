/* Recruiting a seat (backend/recruiting.py): Team > Add bot (and Settings > Bots > Add bot > Recruit candidates) makes a
   Recruiting seat first, then goes through the same flow as opening a seat on the org chart: its job description (the
   CEO Bot drafts it for the owner), Recruit, and the pick, which creates the bot in Onboarding. */
'use strict';
let RECRUIT_HANDOFF = null;
let RECRUIT_POLL = null;
function settingsRecruitHandoff() {
  if (!RECRUIT_HANDOFF || !S.me?.id || !settingsCanCreateBots()) return;
  const seed = RECRUIT_HANDOFF; RECRUIT_HANDOFF = null;
  settingsShow('bots'); void settingsRecruitFlow(seed);
}
// Settings > Bots > Add bot: Recruit candidates makes a seat; a template, an existing agent or a bot from scratch as before.
async function settingsRecruitFlow(seed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  const dialog = recruitDialog();
  const draft = {title:String(seed.title||'').slice(0,100),brief:String(seed.brief||'').slice(0,2000),template:String(seed.template||'').slice(0,80)};
  let route = draft.template ? 'template' : 'recruit', cards = [], creating = false;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="recruit-title">Add a bot with Tico Recruit</h2><span class="spacer"></span><button class="ghost" type="button" data-recruit-close aria-label="Close">✕</button></div><div class="bot-editor-body"><p class="muted">Recruit candidates makes a Recruiting seat on the org chart first; you pick a candidate for it next.</p><div class="row" style="flex-wrap:wrap;margin:16px 0">${[['recruit','Recruit candidates'],['template','From a template'],['existing','Existing agent'],['scratch','From scratch']].map(([id,label])=>`<button class="ghost" type="button" data-recruit-route="${id}" aria-pressed="${route===id}">${label}</button>`).join('')}</div><label>Role title<input name="title" maxlength="100" value="${esc(draft.title)}" placeholder="Customer support"></label><label>Job description <small class="muted" data-recruit-brief-note>Optional, except for Recruit candidates</small><textarea name="brief" rows="4" maxlength="2000" placeholder="What should this bot own? What does success look like?">${esc(draft.brief)}</textarea></label><details><summary>A couple of useful details</summary><label>Tools and sources<input name="tools" maxlength="300" placeholder="e.g. Mailbox, team docs"></label><label>Who reviews the work?<input name="reviewer" maxlength="150" placeholder="e.g. Head of support"></label></details><p class="muted" data-recruit-status role="status"></p><div data-recruit-candidates></div></div><div class="bot-editor-actions"><button class="primary" type="submit">Continue</button><button class="ghost" type="button" data-recruit-close>Cancel</button></div></form>`;
  dialog.querySelectorAll('[data-recruit-close]').forEach(b=>b.onclick=()=>dialog.close());
  dialog.querySelectorAll('[data-recruit-route]').forEach(b=>b.onclick=()=>{route=b.dataset.recruitRoute;dialog.querySelectorAll('[data-recruit-route]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));dialog.querySelector('[data-recruit-candidates]').replaceChildren();});
  const form=dialog.querySelector('form'), status=dialog.querySelector('[data-recruit-status]'), results=dialog.querySelector('[data-recruit-candidates]');
  form.onsubmit=e=>e.preventDefault();
  dialog.showModal();
  try { cards=catalogCards(await get('/v2/templates')); } catch(error){status.textContent=error.message;}
  // Only Recruit candidates needs a title and a job description (its seat is made from them); the other ways go on without.
  const recruitMissing=()=>{
    const title=form.elements.title,brief=form.elements.brief;settingsFieldClear(title);settingsFieldClear(brief);
    if(!title.value.trim()){settingsFieldError(title,'Give the role a title: Recruit candidates makes a seat with it.');title.focus();return true;}
    if(brief.value.trim().length<10){settingsFieldError(brief,'Describe the job in a sentence or two (at least 10 characters). Recruit candidates needs it to find candidates.');brief.focus();return true;}
    return false;
  };
  const fail=message=>{status.classList.add('err');status.textContent=message;};
  form.onsubmit=async e=>{
    e.preventDefault();if(creating)return;const data=new FormData(form);draft.title=String(data.get('title'));draft.brief=String(data.get('brief'));
    status.classList.remove('err');settingsFieldClear(form.elements.title);settingsFieldClear(form.elements.brief);
    if(route==='existing'){results.innerHTML='<p>Keep your agent where it runs and pair it with Tico using a scoped connection code.</p><a class="linkish" href="https://github.com/ticoteam/tico/blob/main/docs/connect-an-agent.md" target="_blank" rel="noopener noreferrer">Open the connection guide ↗</a>';return;}
    if(route==='scratch'){dialog.close();settingsEditBot();const editor=$('#bot-editor');editor.querySelector('[name=display_name]').value=draft.title;editor.querySelector('[name=description]').value=draft.brief;return;}
    if(route==='recruit'){
      // Recruiting goes through a Recruiting seat (ui/app/recruit.js below): the seat first, then candidates and the pick.
      if(!settingsCanCreateBots()){fail('You no longer have permission to add bots.');return;}
      if(recruitMissing())return;
      creating=true;
      try{const seat=await post('/v2/seats',{title:draft.title.trim(),description:draft.brief.trim()});void refresh(true);await recruitSeat(seat.id);}
      catch(error){fail(error.message);}
      creating=false;return;
    }
    const terms=new Set(draft.brief.toLowerCase().match(/[a-z]{3,}/g)||[]);
    const score=c=>(draft.template&&c.template===draft.template?100:0)+(c.required?-100:0)+(c.summary||'').toLowerCase().split(/\W+/).filter(w=>terms.has(w)).length;
    const candidates=cards.filter(c=>c.template!=='inbox'&&!c.required).slice().sort((a,b)=>score(b)-score(a)).slice(0,6);
    status.textContent='Catalog pitches from this Tico. Your brief stays on this computer.';
    results.innerHTML=candidates.map(c=>`<article class="cat-card"><h3>${esc(c.name)}</h3><p class="cat-summary">${esc(c.summary||'')}</p>${c.owns?.length?`<p class="muted">Owns: ${esc(c.owns.join('; '))}</p>`:''}<button class="primary" type="button" data-recruit-create="${esc(c.slug)}">Choose & create ${esc(c.name)}</button></article>`).join('')+'<p class="muted">Want an external service? Review provider setup separately.</p><a href="https://agents.tico.dev/#bots" target="_blank" rel="noopener noreferrer">Browse third-party options ↗</a>';
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
    dialog.addEventListener('close', () => { clearInterval(RECRUIT_POLL); RECRUIT_POLL = null; });
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

async function recruitNewSeat(seed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  const dialog = recruitDialog(), owner = S.me?.role === 'owner';
  const title = String(seed.title || '').slice(0, 120), brief = String(seed.brief || '').slice(0, 8000);
  dialog.innerHTML = `<form data-recruit-new><div class="tmodal-head"><h2 id="recruit-title">Add a bot</h2><span class="spacer"></span><button class="ghost" type="button" data-recruit-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body"><p class="muted">Every bot starts as a Recruiting seat on the org chart: the role, its job description and who it reports to. You pick a candidate next.</p>
    <label>Role title<input name="title" maxlength="120" required value="${esc(title)}" placeholder="Customer support"></label>
    <label>Reports to<select name="reports_to">${recruitPlaces().map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join('')}</select></label>
    <label>Job description <span class="muted">(optional)</span><textarea name="description" rows="4" maxlength="8000" placeholder="${owner ? 'Leave it empty and your CEO Bot drafts one' : 'What should this role own? You can write it later'}">${esc(brief)}</textarea></label>
    <p class="muted">Connecting an agent you already run? <button class="linkish" type="button" data-recruit-botops>Ask BotOps</button></p>
    <p class="err" role="alert" data-recruit-error hidden></p></div>
    <div class="bot-editor-actions"><button class="primary" type="submit">Create seat</button><button class="ghost" type="button" data-recruit-close>Cancel</button></div></form>`;
  dialog.querySelectorAll('[data-recruit-close]').forEach(b => { b.onclick = () => dialog.close(); });
  dialog.querySelector('[data-recruit-botops]').onclick = () => { dialog.close(); recruitAskBotOps(); };
  const form = dialog.querySelector('form'), error = dialog.querySelector('[data-recruit-error]');
  let creating = false;
  form.onsubmit = async event => {
    event.preventDefault();
    if (creating) return;
    if (!S.me?.id || !settingsCanCreateBots()) { error.textContent = 'You no longer have permission to add bots.'; error.hidden = false; return; }
    creating = true; error.hidden = true;
    const data = new FormData(form);
    try {
      const seat = await post('/v2/seats', {title: String(data.get('title') || '').trim(), reports_to: String(data.get('reports_to') || ''),
        description: String(data.get('description') || '').trim()});
      void refresh(true);
      await recruitSeat(seat.id);
    } catch (e) { error.textContent = e.message; error.hidden = false; creating = false; }
  };
  if (!dialog.open) dialog.showModal();
  form.elements.title.focus();
}

function recruitAskBotOps() {
  const bot = (S.emps || []).find(row => row.name === 'botops' && row.can_chat && !botLimited(row));
  if (!bot) { toast('BotOps chat is unavailable. Ask your team owner for access.', true); return; }
  const pill = botPill('botops'), box = pq(pill, '.p-text');
  if (!box.value.trim()) {
    box.value = 'Help me connect an agent I already run. Here is where it lives (folder path and computer, or GitHub URL):\n\n';
    box.dispatchEvent(new Event('input', {bubbles: true}));
  }
  pill.focusOnOpen = true;
  if (location.hash === '#/bot/botops/chat') showBotTab('chat'); else location.hash = '#/bot/botops/chat';
}

function recruitDraftLine(seat) {
  const by = esc(seat.draft?.by || 'CEO Bot');
  if (seat.draft?.state === 'drafting') return `<span class="recruit-spin" aria-hidden="true"></span>${by} is drafting the job description…`;
  if (seat.description_by === 'ceo_bot') return `Drafted by ${by}.${seat.can_edit ? ' Edit it as you like.' : ''}`;
  if (seat.description_by === 'chart') return seat.description ? 'From the org chart model.' : 'No job description yet.';
  return '';
}

// Open a seat: from the org chart, or right after Add bot made it.
async function recruitSeat(id) {
  const dialog = recruitDialog();
  clearInterval(RECRUIT_POLL); RECRUIT_POLL = null;
  let seat;
  try { seat = await post(`/v2/seats/${encodeURIComponent(id)}/open`, {}); }
  catch (e) { toast(e.message, true); return; }
  // `saved` is the text the server last acknowledged; one save runs at a time and keeps going until it matches the
  // textarea, so a reply never marks newer typing as saved and the bot is never hired from older words.
  let dirty = false, picking = false, saving = null, saved = seat.description;
  dialog.innerHTML = `<div class="recruit-seat"><div class="tmodal-head"><h2 id="recruit-title">${esc(seat.title)}</h2><span class="pill recruiting">Recruiting</span><span class="spacer"></span><button class="ghost" type="button" data-recruit-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body"><p class="muted">Reports to ${esc(recruitPlaceName(seat.reports_to))}. No bot, repository or computer until you pick a candidate.</p>
    <label>Job description<textarea name="description" rows="7" maxlength="8000"${seat.can_edit ? '' : ' readonly'}>${esc(seat.description)}</textarea></label>
    <p class="muted" data-recruit-draft role="status" aria-live="polite">${recruitDraftLine(seat)}</p>
    ${seat.can_edit ? '<div class="row"><button type="button" data-recruit-save hidden>Save job description</button><button class="primary" type="button" data-recruit-go>Recruit</button></div>' : '<p class="muted">Only someone this seat reports up to recruits for it.</p>'}
    <p class="muted" data-recruit-status role="status"></p><div class="recruit-candidates" data-recruit-candidates></div></div></div>`;
  dialog.querySelectorAll('[data-recruit-close]').forEach(b => { b.onclick = () => dialog.close(); });
  const text = dialog.querySelector('textarea'), line = dialog.querySelector('[data-recruit-draft]');
  const status = dialog.querySelector('[data-recruit-status]'), results = dialog.querySelector('[data-recruit-candidates]');
  const save = dialog.querySelector('[data-recruit-save]'), go = dialog.querySelector('[data-recruit-go]');
  const showDirty = () => { dirty = text.value !== saved; if (save) save.hidden = !dirty; };
  text.oninput = showDirty;
  const saveText = () => saving || (text.value === saved ? Promise.resolve() : saving = (async () => {
    try {
      while (text.value !== saved) {
        const sent = text.value;
        seat = {...seat, ...await patch(`/v2/seats/${encodeURIComponent(id)}`, {description: sent}), description_by: 'person',
          draft: {...seat.draft, state: seat.draft?.state === 'drafting' ? 'none' : seat.draft?.state}};
        saved = sent; line.innerHTML = recruitDraftLine(seat);
      }
    } finally { saving = null; showDirty(); }
  })());
  if (save) save.onclick = async () => { try { await saveText(); toast('Job description saved.'); } catch (e) { status.textContent = e.message; } };
  // The CEO Bot's draft arrives in a minute or so; a description being typed is never replaced.
  if (seat.draft?.state === 'drafting') RECRUIT_POLL = setInterval(async () => {
    if (!dialog.open) { clearInterval(RECRUIT_POLL); return; }
    try {
      const now = await get(`/v2/seats/${encodeURIComponent(id)}`, {fresh: true});
      if (now.draft?.state === 'drafting') return;
      clearInterval(RECRUIT_POLL); RECRUIT_POLL = null;
      if (!dirty && !saving) { seat = now; text.value = saved = now.description; }
      line.innerHTML = recruitDraftLine(now);
    } catch { /* the next tick tries again */ }
  }, 4000);
  if (go) go.onclick = async () => {
    if (picking) return;
    go.disabled = true; status.textContent = 'Finding candidates…';
    try {
      await saveText();
      const found = await post(`/v2/seats/${encodeURIComponent(id)}/recruit`, {});
      status.textContent = found.candidates.length ? 'Tico templates for this role, from this Tico. Nothing leaves your team.' : 'No template fits yet.';
      const options = c => [`<option value="">Suggested: ${esc(c.model_name)}</option>`,
        ...found.models.filter(m => m.id !== c.model).map(m => `<option value="${esc(m.id)}">${esc(m.name)}</option>`)].join('');
      results.innerHTML = found.candidates.map(c => `<article class="cat-card${c.recommended ? ' recommended' : ''}" data-recruit-candidate="${esc(c.template)}">
        <h3>${esc(c.name)}${c.recommended ? ' <span class="pill run">Recommended</span>' : ''}</h3>
        <p class="recruit-reason">${esc(c.reason)}</p>${c.summary && c.summary !== c.reason ? `<p class="muted cat-summary">${esc(c.summary)}</p>` : ''}
        <label>Model<select data-recruit-model aria-label="Model for ${esc(c.name)}">${options(c)}</select></label>
        <button class="${c.recommended ? 'primary' : ''}" type="button" data-recruit-pick>Pick ${esc(c.name)}</button></article>`).join('');
      results.querySelectorAll('[data-recruit-pick]').forEach(button => { button.onclick = () => recruitPick(dialog, id, seat, button); });
    } catch (e) { status.textContent = e.message; }
    go.disabled = false;
  };
  const recruitPick = async (host, seatId, current, button) => {
    if (picking || !S.me?.id || !settingsCanCreateBots()) return;
    const card = button.closest('[data-recruit-candidate]');
    picking = true; host.querySelectorAll('[data-recruit-pick]').forEach(b => { b.disabled = true; });
    // The bot is made from the stored job description: save what is on screen first, and hold it still until the hire
    // answers. A failed save hires nobody and keeps the text.
    text.readOnly = true;
    status.textContent = 'Saving the job description…';
    try {
      await saveText();
      status.textContent = 'Creating the bot…';
      const hired = await post(`/v2/seats/${encodeURIComponent(seatId)}/hire`,
        {template: card.dataset.recruitCandidate, model: card.querySelector('[data-recruit-model]').value});
      host.close(); await refresh(true);
      toast(`${current.title} is onboarding: it learns in chat until someone above it marks onboarding complete.`);
      location.hash = '#/bot/' + encodeURIComponent(hired.slug);
    } catch (e) {
      status.textContent = e.message; picking = false; text.readOnly = !seat.can_edit;
      host.querySelectorAll('[data-recruit-pick]').forEach(b => { b.disabled = false; });
    }
  };
  if (!dialog.open) dialog.showModal();
  (go || text).focus();
}
