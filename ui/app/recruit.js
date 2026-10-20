/* Tico Recruit: role brief → candidate pitch → the existing parked-template creation path. */
'use strict';
let RECRUIT_HANDOFF = null;
function settingsRecruitHandoff() {
  if (!RECRUIT_HANDOFF || !S.me?.id || !settingsCanCreateBots()) return;
  const seed = RECRUIT_HANDOFF; RECRUIT_HANDOFF = null;
  settingsShow('bots'); void settingsRecruitFlow(seed);
}
async function settingsRecruitFlow(seed = {}) {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  let dialog = $('#recruit-picker');
  if (!dialog) { dialog = document.createElement('dialog'); dialog.id = 'recruit-picker'; dialog.className = 'bot-editor catalog-picker'; dialog.setAttribute('aria-labelledby','recruit-title'); document.body.append(dialog); }
  const draft = {title:String(seed.title||'').slice(0,100),brief:String(seed.brief||'').slice(0,2000),template:String(seed.template||'').slice(0,80)};
  let route = draft.template ? 'template' : 'recruit', cards = [], creating = false;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="recruit-title">Add a bot with Tico Recruit</h2><span class="spacer"></span><button class="ghost" type="button" data-recruit-close aria-label="Close">✕</button></div><div class="bot-editor-body"><p class="muted">Start with a pending role. Choose a candidate before creating the bot.</p><div class="row" style="flex-wrap:wrap;margin:16px 0">${[['recruit','Recruit candidates'],['template','From a template'],['existing','Existing agent'],['scratch','From scratch']].map(([id,label])=>`<button class="ghost" type="button" data-recruit-route="${id}" aria-pressed="${route===id}">${label}</button>`).join('')}</div><label>Role title<input name="title" maxlength="100" required value="${esc(draft.title)}" placeholder="Customer support"></label><label>Job description<textarea name="brief" rows="4" maxlength="2000" required minlength="10" placeholder="What should this bot own? What does success look like?">${esc(draft.brief)}</textarea></label><details><summary>A couple of useful details</summary><label>Tools and sources<input name="tools" maxlength="300" placeholder="e.g. Mailbox, team docs"></label><label>Who reviews the work?<input name="reviewer" maxlength="150" placeholder="e.g. Head of support"></label></details><p class="muted" data-recruit-status role="status"></p><div data-recruit-candidates></div></div><div class="bot-editor-actions"><button class="primary" type="submit">Continue</button><button class="ghost" type="button" data-recruit-close>Cancel</button></div></form>`;
  dialog.querySelectorAll('[data-recruit-close]').forEach(b=>b.onclick=()=>dialog.close());
  dialog.querySelectorAll('[data-recruit-route]').forEach(b=>b.onclick=()=>{route=b.dataset.recruitRoute;dialog.querySelectorAll('[data-recruit-route]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));dialog.querySelector('[data-recruit-candidates]').replaceChildren();});
  const form=dialog.querySelector('form'), status=dialog.querySelector('[data-recruit-status]'), results=dialog.querySelector('[data-recruit-candidates]');
  form.onsubmit=e=>e.preventDefault();
  dialog.showModal();
  try { cards=catalogCards(await get('/v2/templates')); } catch(error){status.textContent=error.message;}
  form.onsubmit=async e=>{
    e.preventDefault();if(creating)return;const data=new FormData(form);draft.title=String(data.get('title'));draft.brief=String(data.get('brief'));
    if(route==='existing'){results.innerHTML='<p>Keep your agent where it runs and pair it with Tico using a scoped connection code.</p><a class="linkish" href="https://github.com/ticoteam/tico/blob/main/docs/connect-an-agent.md" target="_blank" rel="noopener noreferrer">Open the connection guide ↗</a>';return;}
    if(route==='scratch'){dialog.close();settingsEditBot();const editor=$('#bot-editor');editor.querySelector('[name=display_name]').value=draft.title;editor.querySelector('[name=description]').value=draft.brief;return;}
    const terms=new Set(draft.brief.toLowerCase().match(/[a-z]{3,}/g)||[]);
    const score=c=>(draft.template&&c.template===draft.template?100:0)+(c.required?-100:0)+(c.summary||'').toLowerCase().split(/\W+/).filter(w=>terms.has(w)).length;
    const candidates=cards.filter(c=>c.template!=='inbox'&&!c.required).slice().sort((a,b)=>score(b)-score(a)).slice(0,route==='template'?6:3);
    status.textContent='Catalog pitches from this Tico. Your brief stays on this computer.';
    results.innerHTML=candidates.map(c=>`<article class="cat-card"><h3>${esc(c.name)}</h3><p class="cat-summary">${esc(c.summary||'')}</p>${c.owns?.length?`<p class="muted">Owns: ${esc(c.owns.join('; '))}</p>`:''}<button class="primary" type="button" data-recruit-create="${esc(c.slug)}">Choose & create ${esc(c.name)}</button></article>`).join('')+'<p class="muted">Want an external service? Review provider setup separately.</p><a href="https://agents.tico.dev/#bots" target="_blank" rel="noopener noreferrer">Browse third-party options ↗</a>';
    results.querySelectorAll('[data-recruit-create]').forEach(b=>b.onclick=async()=>{
      if(creating||!S.me?.id||!settingsCanCreateBots())return;
      const card=cards.find(c=>c.slug===b.dataset.recruitCreate);if(!card)return;
      creating=true;results.querySelectorAll('[data-recruit-create]').forEach(x=>x.disabled=true);status.textContent='Creating a bot that waits for setup…';
      const slugBase=draft.title.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,55)||card.slug;
      let slug=slugBase;for(let n=2;S.emps.some(x=>x.name===slug);n++)slug=slugBase+'-'+n;
      const brief='\n\n## Role brief\n'+draft.brief+'\nTools and sources: '+String(data.get('tools')||'Not set')+'\nReviewer: '+String(data.get('reviewer')||'Ask the owner during setup');
      try{await post('/v2/bots',{slug,display_name:draft.title,description:draft.brief,template:card.template,
        instructions:(card.instructions||'')+brief,reports_to:null,status:'planned',repo:'bot-'+slug,thread_mode:'personal',
        model:card.model||'',effort:card.reasoning_effort||'',operator:S.me.id,owners:[S.me.id],runner_id:null});
        dialog.close();await refresh(true);if(S.route===SETTINGS){await loadSettings();settingsShow('bots');}toast('Bot created. Complete its setup conversation next.');
      }catch(error){status.textContent=error.message;creating=false;results.querySelectorAll('[data-recruit-create]').forEach(x=>x.disabled=false);}
    });
  };
}
