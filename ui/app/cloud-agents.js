/* ui/app/cloud-agents.js — Cloud agents on Recruit: the grid of agents (a seat's Cloud agents tab, and Team's plus menu),
   the Request dialog, and a seat's Connecting page. Claude Code and Codex are added as Tico bots (addDialog); the other
   agents are requested: Tico's team builds them, and the seat keeps its place on the chart until they connect
   (backend/cloud_agents.py). Uses the page's helpers: get, post, esc, toast, refresh, setDrawer, S. */
'use strict';
(function () {
  const STEPS = ['Requested', 'Building', 'Ready'];

  // Two letters for an agent's tile: the first letters of its first two words ("Claude Code" is CC, "Devin" is DE).
  const initials = name => {
    const words = String(name || '').trim().split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words.slice(0, 2).map(w => w[0]) : [...(words[0] || '').slice(0, 2)]).join('').toUpperCase();
  };

  // What a reports-to value reads as on the chart: a seat's title, a person's name or a bot's display name.
  const placeName = value => {
    const v = String(value || '');
    if (v.startsWith('seat:')) return (S.seats || []).find(s => s.id === v.slice(5))?.title || '';
    if (v.startsWith('human:')) return (S.people || []).find(p => 'human:' + p.id === v)?.name || v.slice(6);
    return (S.emps || []).find(e => e.name === v)?.display_name || v;
  };

  // The head of a team, the way the team's plus menu finds it (team-add.js teamAddBot): the bot that no other bot in
  // the team reports to. A seat added from the team reports to it.
  const teamHead = group => {
    const bots = (S.emps || []).filter(e => e.team === group && e.status !== 'archived' && !isHelperBot(e));
    const names = new Set(bots.map(e => 'b:' + e.name));
    return bots.find(e => !names.has(orgBossKey({kind: 'bot', ...e})))?.name || '';
  };

  const card = agent => `<article class="cloud-card">
      <div class="cloud-card-head"><span class="cloud-tile" aria-hidden="true">${esc(initials(agent.name))}</span>
        <span class="cloud-who"><strong>${esc(agent.name)}</strong><span class="muted">${esc(agent.maker)}</span></span></div>
      <p class="cloud-line">${esc(agent.line)}</p>
      <div class="cloud-foot">
        <a href="https://${esc(agent.site)}" target="_blank" rel="noopener noreferrer">${esc(agent.site)} ↗</a>
        ${agent.supported
          ? `<button class="primary cloud-act" type="button" data-cloud-add="${esc(agent.id)}">Add</button>`
          : `<button class="ghost cloud-act" type="button" data-cloud-request="${esc(agent.id)}">Request</button>`}
      </div></article>`;

  const gridHTML = agents => `<div class="cloud-grid">${agents.map(card).join('')}</div>
    <form class="cloud-other" data-cloud-other>
      <label><span class="muted">Another agent</span><input name="name" maxlength="120" required placeholder="Name or website" autocomplete="off" spellcheck="false"></label>
      <button class="ghost" type="submit">Request</button>
    </form>`;

  // `ctx` says where a request lands: a seat that exists (`seat`), or a new seat in a team (`team`, `reports_to`).
  const wire = (root, agents, ctx) => {
    root.querySelectorAll('[data-cloud-add]').forEach(button => {
      button.onclick = () => addDialog(agents.find(a => a.id === button.dataset.cloudAdd), ctx);
    });
    root.querySelectorAll('[data-cloud-request]').forEach(button => {
      button.onclick = () => requestDialog(agents.find(a => a.id === button.dataset.cloudRequest), ctx);
    });
    const other = root.querySelector('[data-cloud-other]');
    other.onsubmit = event => {
      event.preventDefault();
      const name = other.elements.name.value.trim();
      if (name) requestDialog({id: 'other', name, maker: '', site: '', line: ''}, ctx);
    };
  };

  async function fill(root, ctx) {
    try {
      const {agents} = await get('/v2/cloud-agents');
      root.innerHTML = gridHTML(agents);
      wire(root, agents, ctx);
    } catch (failure) {
      root.innerHTML = `<p class="err" role="alert">${esc(failure.message)}</p>`;
    }
  }

  // A dialog that removes itself when it closes, so the page keeps no stale ones.
  const dialogFor = (className, label, html) => {
    const dialog = document.createElement('dialog');
    dialog.className = `tmodal ${className}`;
    dialog.setAttribute('aria-label', label);
    dialog.innerHTML = html;
    dialog.querySelectorAll('[data-close]').forEach(button => { button.onclick = () => dialog.close(); });
    dialog.addEventListener('close', () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
    return dialog;
  };

  function requestDialog(agent, ctx) {
    if (!agent) return;
    const name = esc(agent.name);
    const dialog = dialogFor('cloud-request', `Add ${agent.name}`, `<form class="cloud-request-form" data-cloud-form>
      <header class="tmodal-head cloud-request-head"><span class="cloud-tile cloud-tile-lg" aria-hidden="true">${esc(initials(agent.name))}</span>
        <div class="cloud-request-title"><h2>Add ${name}</h2>${agent.site ? `<a href="https://${esc(agent.site)}" target="_blank" rel="noopener noreferrer">${esc(agent.site)} ↗</a>` : ''}</div>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <div class="bot-editor-body">
        <p class="cloud-note">${ldotHTML({state: 'idle', size: 12})}<span>We don't connect to ${name} yet. We'll build it. That usually takes 12 to 24 hours on a normal day.</span></p>
        <ol class="cloud-how"><li>A placeholder seat goes on your chart now</li><li>Tasks you give it wait there</li><li>When it connects, you sign in to ${name} once</li></ol>
        <label>First task (optional)<textarea name="first_task" rows="3" maxlength="8000"></textarea></label>
        <p class="err" role="alert" data-cloud-error hidden></p>
      </div>
      <div class="bot-editor-foot"><span class="spacer"></span><button class="ghost" type="button" data-close>Cancel</button><button class="primary" type="submit">Request</button></div>
    </form>`);
    const form = dialog.querySelector('[data-cloud-form]'), error = dialog.querySelector('[data-cloud-error]');
    const submit = form.querySelector('button[type="submit"]');
    form.onsubmit = async event => {
      event.preventDefault();
      const body = {first_task: form.elements.first_task.value.trim()};
      if (agent.id === 'other') body.name = agent.name;
      submit.disabled = true; error.hidden = true;
      try {
        const seat = ctx.seat
          ? await post(`/v2/seats/${encodeURIComponent(ctx.seat)}/request-agent`, {agent: agent.id, ...body})
          : await post(`/v2/cloud-agents/${encodeURIComponent(agent.id)}/request`,
              {...body, team: ctx.team || '', reports_to: ctx.reports_to || ''});
        dialog.close();
        void refresh(true);
        void seatPage(seat.id);
      } catch (failure) {
        error.textContent = failure.message || 'Could not send the request.';
        error.hidden = false; submit.disabled = false;
      }
    };
  }

  // Adding a supported agent hires a Tico bot on its harness: it runs on a Tico computer under its own bot identity, in the
  // seat when `ctx` names one (which it fills), else under the line `ctx` names. No token is issued to the person.
  function addDialog(agent, ctx) {
    if (!agent) return;
    const seat = ctx.seat ? (S.seats || []).find(s => s.id === ctx.seat) : null;
    const reportsTo = seat ? seat.reports_to : ctx.reports_to || '';
    const teamId = seat ? seat.team : ctx.team;
    const teamName = teamId ? (S.orgGroups || []).find(g => g.id === teamId)?.name || '' : '';
    const under = placeName(reportsTo) || 'the top of the chart';
    const dialog = dialogFor('cloud-request cloud-add', `Add ${agent.name}`, `<form class="cloud-request-form" data-cloud-form>
      <header class="tmodal-head cloud-request-head"><span class="cloud-tile cloud-tile-lg" aria-hidden="true">${esc(initials(agent.name))}</span>
        <div class="cloud-request-title"><h2>Add ${esc(agent.name)}</h2><span class="muted">${esc(agent.maker)}</span></div>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <div class="bot-editor-body">
        <p class="cloud-note"><span>${ctx.seat ? `Fills the seat “${esc(seat?.title || '')}”. ` : ''}It runs as a bot on a Tico computer and reports to ${esc(under)}${teamName ? ` in ${esc(teamName)}` : ''}.</span></p>
        <p class="err" role="alert" data-cloud-error hidden></p>
      </div>
      <div class="bot-editor-foot"><span class="spacer"></span><button class="ghost" type="button" data-close>Cancel</button><button class="primary" type="submit">Add</button></div>
    </form>`);
    const form = dialog.querySelector('[data-cloud-form]'), error = dialog.querySelector('[data-cloud-error]');
    const submit = form.querySelector('button[type="submit"]');
    form.onsubmit = async event => {
      event.preventDefault();
      submit.disabled = true; error.hidden = true;
      try {
        await post(`/v2/cloud-agents/${encodeURIComponent(agent.id)}/add`,
          ctx.seat ? {seat_id: ctx.seat} : {reports_to: reportsTo, team: ctx.team || ''});
        dialog.close();
        void refresh(true);
        toast(`${agent.name} added`);
      } catch (failure) {
        error.textContent = failure.message || 'Could not add the agent.';
        error.hidden = false; submit.disabled = false;
      }
    };
  }

  // The Connecting page: the three steps the request is in, the time it usually takes and the first task waiting.
  function seatHTML(detail) {
    const {seat, agent} = detail;
    const state = seat.request_state || 'requested';
    const at = Math.max(0, STEPS.findIndex(step => step.toLowerCase() === state));
    const ready = state === 'ready';
    const steps = STEPS.map((label, i) => {
      const now = !ready && i === at;
      const cls = ready || i < at ? 'done' : now ? 'now' : 'pending';
      return `<div class="cloud-step ${cls}"><i class="${now ? 'cloud-pulse' : ''}"${now ? ' aria-current="step"' : ''} aria-hidden="true"></i><span>${label}</span></div>`;
    }).join('');
    const team = (S.orgGroups || []).find(g => g.id === seat.team)?.name || '';
    const reports = placeName(seat.reports_to);
    const meta = [team, reports ? `reports to ${reports}` : ''].filter(Boolean).join(' · ');
    const tasks = detail.tasks || [];
    return `<div class="cloud-seat-page">
      <div class="cloud-seat-head"><span class="cloud-tile cloud-tile-lg cloud-tile-dashed" aria-hidden="true">${esc(initials(agent.name))}</span>
        <div class="cloud-seat-title"><h2>${esc(seat.title)}</h2><span class="muted">${esc(meta)}</span></div>
        <button class="ghost" type="button" data-cloud-cancel>Cancel request</button></div>
      <section class="cloud-connecting" aria-label="Connecting">
        <div class="cloud-connecting-head"><strong>Connecting</strong><span class="muted">Usually ready in 12 to 24 hours</span></div>
        <div class="cloud-steps">${steps}</div>
      </section>
      ${tasks.length ? `<section class="cloud-waiting"><div class="cloud-label">Waiting for ${esc(agent.name)}</div>
        <ul>${tasks.map(task => `<li><span class="cloud-task-dot" aria-hidden="true"></span><span>${esc(task.title)}</span></li>`).join('')}</ul></section>` : ''}
    </div>`;
  }

  // The seat's Connecting page: in `container` when it has one (an applicants page), else in a dialog.
  async function seatPage(seatId, container = null) {
    const host = container || dialogFor('cloud-seat', 'Cloud agent', `<div class="tmodal-head"><h2>Cloud agent</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></div><div class="bot-editor-body" data-cloud-body><p class="muted">Loading…</p></div>`);
    const body = container || host.querySelector('[data-cloud-body]');
    try {
      const detail = await get(`/v2/seats/${encodeURIComponent(seatId)}/request`, {fresh: true});
      body.innerHTML = seatHTML(detail);
      body.querySelector('[data-cloud-cancel]').onclick = async () => {
        if (!confirm('Cancel this request?')) return;
        try {
          await post(`/v2/seats/${encodeURIComponent(seatId)}/cancel-request`, {});
          void refresh(true);
          if (container) body.innerHTML = '<p class="muted">Request cancelled.</p>';
          else host.close();
        } catch (failure) { toast(failure.message, true); }
      };
    } catch (failure) {
      body.innerHTML = `<p class="err" role="alert">${esc(failure.message)}</p>`;
    }
    return host;
  }

  // The grid as a seat's Cloud agents tab: a request there attaches the agent to that seat.
  window.cloudAgentsTab = async function cloudAgentsTab(container, seat) {
    container.innerHTML = '<p class="muted">Loading…</p>';
    await fill(container, {seat: typeof seat === 'string' ? seat : seat?.id || ''});
  };

  // The grid from Team's plus menu, for a team (a group id, or none for the top of the chart): a request makes a seat in
  // that team, reporting to its head.
  window.cloudAgentsOpen = async function cloudAgentsOpen(group = '') {
    setDrawer(false);
    const team = group || '';
    const teamName = team ? (S.orgGroups || []).find(g => g.id === team)?.name || '' : '';
    const dialog = dialogFor('cloud-open', 'Cloud agents', `<div class="tmodal-head"><h2>Cloud agents</h2>${teamName ? `<span class="muted">${esc(teamName)}</span>` : ''}<span class="spacer"></span><button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></div><div class="bot-editor-body" data-cloud-body><p class="muted">Loading…</p></div>`);
    await fill(dialog.querySelector('[data-cloud-body]'), {team, reports_to: team ? teamHead(team) : ''});
  };

  // The team builder's Request link (ui/app/team-build.js) opens this dialog for the agent it picked, filed in the team
  // and under the head it names. A supported agent is added as a bot under that head, not requested: the request route
  // refuses one.
  window.cloudAgentRequest = function cloudAgentRequest(agent, {team = '', reports_to = ''} = {}) {
    if (!agent) return;
    if (agent.supported) return addDialog(agent, {team, reports_to});
    requestDialog(agent, {team, reports_to});
  };
  window.cloudAgentsSeat = seatPage;
})();
