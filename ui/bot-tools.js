/* A bot's tools (docs/creating-bots.md, "What people see about a bot's tools"), in two places.
   Beside the bot's name, after its runtime mark: one button, up to three small icons overlapped (the
   service's logo when ui/tool-icons.js has it, the name's first two letters otherwise) and "+N" for the rest.
   It opens a short list, a line a tool (name, what it acts as, a problem), and "Manage" opens the full list.
   The model the bot runs on is left out there, since the runtime mark beside the name already says it. A red
   dot marks a tool with a problem.
   The full list is the Tools card under More: one line a tool (icon, name, key values, status) that opens to
   what it acts as, what it may do, its scope, note, credential name and status; two or more GitHub
   repositories share one line ("N repositories", their access added up) that opens to a short line each.
   ui/app/bot-page.js calls window.botTools.mountStrip(host, deps) once per bot page and mountList(host,
   deps) when More opens; deps keeps this file free of the page's globals (get, esc). Nothing here reads
   a secret: the server sends names, never values. */
'use strict';
(function () {
  const STRIP_MAX = 3;

  const css = `
.bot-tool-strip{position:relative;display:inline-flex;align-items:center;flex:none;margin-left:6px}
.bts-stack{display:inline-flex;align-items:center;gap:0;height:24px;padding:0 6px 0 3px;border:0;border-radius:11px;background:none;color:var(--muted);cursor:pointer;font:inherit}
.bts-stack:hover,.bts-stack[aria-expanded=true]{background:var(--surface2);color:var(--ink)}
.bts-stack:focus-visible{outline:var(--focus-ring);outline-offset:1px}
.bts-icon{position:relative;flex:none;display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;background:var(--surface2);color:var(--ink);box-shadow:0 0 0 2px var(--bg)}
.bts-stack .bts-icon+.bts-icon{margin-left:-3px}   /* about a third under the one before, which stays on top */
.bts-stack .bts-icon:nth-child(1){z-index:3}.bts-stack .bts-icon:nth-child(2){z-index:2}.bts-stack .bts-icon:nth-child(3){z-index:1}
.bts-icon svg{width:11px;height:11px;display:block}
.bts-icon.bt-tint{background:color-mix(in srgb,hsl(var(--h) 60% 50%) 22%,var(--surface));color:color-mix(in srgb,hsl(var(--h) 65% 42%) 72%,var(--ink))}
.bts-icon .tool-initials{font-size:7.5px;font-weight:600;line-height:1;letter-spacing:0}
.bts-icon .bt-dot{position:absolute;right:-2px;top:-2px;width:6px;height:6px;border-radius:50%;background:var(--fail);border:1px solid var(--bg)}
.bts-more{flex:none;margin-left:4px;font-size:11px;font-weight:600;line-height:16px;font-variant-numeric:tabular-nums}
.bts-pop{position:absolute;left:0;top:calc(100% + 6px);z-index:40;min-width:220px;max-width:300px;padding:4px 0;border:1px solid color-mix(in srgb,var(--ink) 16%,transparent);border-radius:8px;background:var(--raised);box-shadow:0 8px 28px rgba(0,0,0,.4),0 1px 3px rgba(0,0,0,.2);font-size:12.5px;line-height:18px}
.bts-row{display:flex;align-items:center;gap:8px;padding:4px 10px;min-width:0}
.bts-row .bts-icon{box-shadow:none}
.bts-row .bts-name{flex:none;font-weight:600;color:var(--ink)}
.bts-row .bts-id{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted)}
.bts-row .bt-problem{flex:none;font-size:11.5px}
.bts-manage{display:block;margin-top:4px;padding:5px 10px 3px;border-top:1px solid var(--line);font-size:12px;color:var(--link-quiet)}
.bts-manage:hover{color:var(--link)}
.tool-initials{font-size:11.5px;font-weight:600;letter-spacing:.01em}
.bt-list .bt-item{border-top:1px solid var(--line);font-size:13px;line-height:1.45}
.bt-list .bt-item:first-child{border-top:0}
.bt-list summary{list-style:none;display:flex;align-items:center;gap:8px;min-width:0;padding:7px 2px;border-radius:6px;cursor:pointer}
.bt-list summary::-webkit-details-marker{display:none}
.bt-list summary:hover{background:var(--surface2)}
.bt-list summary:focus-visible{outline:var(--focus-ring);outline-offset:-2px}
.bt-list summary::after{content:'›';flex:none;width:12px;text-align:center;color:var(--muted);font-size:15px;line-height:1;transition:transform var(--dur-fast) var(--ease)}
.bt-list details[open]>summary::after{transform:rotate(90deg)}
.bt-list .bt-icon-static{flex:none;width:22px;height:22px;border-radius:50%;background:var(--surface2);display:inline-grid;place-items:center;color:var(--ink)}
.bt-list .bt-icon-static svg{width:13px;height:13px}
.bt-list .bt-tint{background:color-mix(in srgb,hsl(var(--h) 60% 50%) 20%,var(--surface));color:color-mix(in srgb,hsl(var(--h) 65% 42%) 72%,var(--ink))}
.bt-list .bt-name{flex:0 1 auto;max-width:45%;min-width:0;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bt-list .bt-meta{flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted)}
.bt-list .bt-meta.bt-problem{color:var(--fail)}
.bt-list .bt-line-status{flex:none;margin-left:auto}
.bt-list .bt-line-status .pill{margin:0}
.bt-list .bt-body{padding:2px 0 10px 32px}
.bt-list .bt-repos{padding:0 0 6px 32px}
.bt-list .bt-repo+.bt-repo{border-top:1px solid color-mix(in srgb,var(--line) 60%,transparent)}
.bt-list .bt-repo summary{padding:5px 2px}
.bt-list .bt-repo .bt-name{font-weight:500;max-width:55%}
.bt-list .bt-repo .bt-body{padding-left:2px}
.bt-list .bt-open{display:inline-block;margin-top:6px;font-size:var(--t-sm)}
.bt-list dl{margin:0;display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 12px}
.bt-list dt{color:var(--muted);font-size:12px;padding-top:1px}
.bt-list dd{margin:0;min-width:0;overflow-wrap:anywhere}
.bt-list dd .pill{margin:0 4px 2px 0}
.bt-list .bt-problem{color:var(--fail)}
`;

  const STATUS = {ready: ['ok', 'Ready'], problem: ['fail', 'Needs attention'], pending: ['', 'Pending'], unknown: ['', 'Not checked']};
  const label = key => String(key).replace(/[_-]+/g, ' ').replace(/^./, c => c.toUpperCase());
  const asList = value => (Array.isArray(value) ? value : [value]).map(String).filter(Boolean);

  // What a person reads: "pull_requests" is "pull requests"; a model is written one way everywhere (ui/app/format.js).
  const canWord = v => String(v).replace(/[_-]+/g, ' ');
  const shownIdentity = tool => tool.id === 'model' && typeof modelWords === 'function' ? modelWords(tool.identity) : tool.identity;
  // A repository's Access row already says own or granted; the status line keeps only the rest ("Checked out on its computer").
  const shownDetail = tool => {
    if (!isRepo(tool)) return tool.detail || '';
    const rest = String(tool.detail || '').split(/;\s*/).filter(part => !/^(own|granted) repository\b/i.test(part)).join('; ');
    return rest.charAt(0).toUpperCase() + rest.slice(1);
  };
  // A GitHub repository: the bot's own (instructions and memory) or one granted to it, read only or read and write.
  const isRepo = tool => /^github(-app)?$/i.test(String(tool.service || '')) && !!(tool.scope?.repo || tool.url);
  const ownRepo = tool => tool.id === 'repo' || /own repository/i.test(tool.detail || '');
  const can = tool => (tool.can || []).map(String);
  function access(tool) {
    const write = can(tool).includes('write'), read = write || can(tool).includes('read');
    const level = write ? 'Read and write' : read ? 'Read only' : '';
    return [ownRepo(tool) ? 'Own repository' : '', level].filter(Boolean).join(' · ');
  }
  function stateWord(tool) {
    if (tool.status === 'problem') return tool.problem || STATUS.problem[1];
    if (tool.pending === 'remove') return 'Removal pending';
    if (tool.pending === 'update') return 'Change pending';
    return (STATUS[tool.status] || STATUS.unknown)[1];
  }
  // The lines a tooltip shows, as [label, value]; the identity is the head line.
  function facts(tool) {
    const rows = [];
    if (isRepo(tool)) { if (access(tool)) rows.push(['Access', access(tool)]); }
    else if (tool.id !== 'model' && can(tool).length) rows.push(['Can', can(tool).map(canWord).join(', ')]);
    for (const [key, value] of Object.entries(tool.scope || {})) {
      if (isRepo(tool) && key === 'repo') continue;
      rows.push([label(key), asList(value).join(', ')]);
    }
    if (tool.note) rows.push(['Note', tool.note]);
    rows.push(['State', stateWord(tool)]);
    return rows.filter(([, v]) => v);
  }
  function summary(tool) {
    return [tool.name, shownIdentity(tool), ...facts(tool).map(([k, v]) => k === 'State' ? v : `${k.toLowerCase()} ${v}`)].filter(Boolean).join(', ');
  }

  // One open list at a time; leaving the page closes it.
  let closeOpen = null;
  window.addEventListener('hashchange', () => closeOpen?.());

  function style() {
    if (document.getElementById('bot-tools-style')) return;
    const el = document.createElement('style');
    el.id = 'bot-tools-style';
    el.textContent = css;
    document.head.append(el);
  }

  const icons = () => window.toolIcons || {markup: () => '', hue: () => 0};
  const tint = tool => (icons().has && icons().has(tool.logo_key) ? '' : ` bt-tint" style="--h:${icons().hue(tool.name)}`);

  // The bot's tools, with the Slack channels it reads (Tools > Slack channels) folded into its Slack tool.
  async function load(slug, get) {
    const page = await get(`/v2/bots/${encodeURIComponent(slug)}/tools`);
    const tools = Array.isArray(page?.tools) ? page.tools : [];
    const inbox = await get('/v2/messaging/bots').catch(() => null);
    const channels = (inbox?.bots || []).find(b => b.bot === slug)?.sources?.filter(s => s.kind === 'slack').map(s => s.name) || [];
    if (channels.length) {
      const slackTool = tools.find(t => t.id === 'slack' || t.service === 'slack');
      if (slackTool) slackTool.scope = {...(slackTool.scope || {}), channels};
      else tools.push({id: 'slack', service: 'slack', logo_key: 'slack', name: 'Slack', can: ['read'], status: 'ready', scope: {channels}});
    }
    return {tools, reportError: page?.report_error || ''};
  }

  // Beside the name: one button, up to three small icons overlapped and "+N"; it opens a short list of the
  // tools (name, what it acts as, a problem in red) with "Manage" to the full list (deps.href).
  function mountStrip(host, deps) {
    if (!host) return;
    style();
    const {slug, get, esc, href, skipModel} = deps;
    host.classList.add('bot-tool-strip');
    let tools = [], open = false;
    const shownTools = () => skipModel ? tools.filter(t => t.id !== 'model') : tools;
    const icon = tool => `<span class="bts-icon${tint(tool)}" data-tool="${esc(tool.id)}">${icons().markup(tool)}${tool.status === 'problem' ? '<span class="bt-dot" aria-hidden="true"></span>' : ''}</span>`;
    function close(focus) {
      if (!open) return;
      open = false; closeOpen = null;
      host.querySelector('.bts-pop')?.remove();
      host.querySelector('.bts-stack')?.setAttribute('aria-expanded', 'false');
      document.removeEventListener('pointerdown', outside, true);
      if (focus) host.querySelector('.bts-stack')?.focus();
    }
    function outside(ev) { if (!host.contains(ev.target)) close(false); }
    function openPop() {
      const shown = shownTools();
      open = true; closeOpen = () => close(false);
      const pop = document.createElement('div');
      pop.className = 'bts-pop'; pop.id = 'bts-pop'; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', 'Tools');
      pop.innerHTML = shown.map(tool => `<div class="bts-row" data-tool="${esc(tool.id)}" title="${esc(summary(tool))}">${icon(tool)}
          <span class="bts-name">${esc(tool.name)}</span><span class="bts-id">${esc(shownIdentity(tool) || '')}</span>${
          tool.status === 'problem' ? `<span class="bt-problem">${esc(stateWord(tool))}</span>` : ''}</div>`).join('')
        + `<a class="bts-manage" href="${esc(href)}">Manage</a>`;
      host.append(pop);
      host.querySelector('.bts-stack').setAttribute('aria-expanded', 'true');
      pop.querySelector('.bts-manage').addEventListener('click', () => close(false));
      document.addEventListener('pointerdown', outside, true);
      pop.querySelector('.bts-manage').focus();
    }
    host.addEventListener('keydown', ev => { if (ev.key === 'Escape' && open) { ev.stopPropagation(); close(true); } });
    function render() {
      const shown = shownTools();
      host.hidden = !shown.length;
      const few = shown.slice(0, STRIP_MAX), rest = shown.length - few.length;
      const wasOpen = open;
      close(false);
      host.innerHTML = `<button type="button" class="bts-stack" aria-haspopup="dialog" aria-expanded="false" aria-controls="bts-pop"
          aria-label="Tools: ${shown.length}" title="Tools">${few.map(icon).join('')}${rest > 0 ? `<span class="bts-more">+${rest}</span>` : ''}</button>`;
      host.querySelector('.bts-stack').addEventListener('click', () => open ? close(false) : openPop());
      if (wasOpen) openPop();
    }
    async function refresh() {
      try { const got = await load(slug, get); if (!host.isConnected) return; tools = got.tools; }
      catch { if (!host.isConnected) return; }
      render();
    }
    refresh();
    // Same as Files: do not refresh tools for a hidden tab; wake when it is shown again.
    // A tab shown again reloads only when its last load is about an interval old (a little under, so timer jitter
    // never skips a minute): switching tabs often must not
    // ask more than the minute timer did.
    let loadedAt = Date.now();
    const due = () => { if (!document.hidden && Date.now() - loadedAt >= 55000) { loadedAt = Date.now(); refresh(); } };
    const onVis = () => {
      if (!host.isConnected) { document.removeEventListener('visibilitychange', onVis); return; }
      due();
    };
    const timer = setInterval(() => {
      if (!host.isConnected) {
        clearInterval(timer); document.removeEventListener('visibilitychange', onVis); return;
      }
      due();
    }, 60000);
    document.addEventListener('visibilitychange', onVis);
  }

  // The Tools card under More: one line a tool (icon, name, its key values, status) that opens to every detail,
  // and the GitHub repositories folded into one line that opens to a short line each.
  function mountList(host, deps) {
    if (!host) return;
    style();
    const {slug, get, esc} = deps;
    const statusPill = tool => { const [kind, word] = STATUS[tool.status] || STATUS.unknown; return `<span class="pill ${kind}">${esc(word)}</span>`; };
    const repoLink = tool => tool.url ? `<a href="${esc(tool.url)}" target="_blank" rel="noopener noreferrer">${esc(tool.identity)}</a>` : esc(tool.identity);
    // Every fact about a tool; a repository's line already carries its linked name, so its details leave it out.
    function detail(tool, named) {
      const rows = [];
      const repo = isRepo(tool);
      const identityLabel = tool.id === 'model' ? 'Runs on' : repo ? 'Repository' : 'Acts as';
      if (tool.identity && !(repo && named)) rows.push([identityLabel, repo ? repoLink(tool) : esc(shownIdentity(tool))]);
      if (repo && access(tool)) rows.push(['Access', esc(access(tool))]);
      else if (tool.can?.length && tool.id !== 'model') rows.push(['Can', tool.can.map(v => `<span class="pill">${esc(canWord(v))}</span>`).join('')]);
      for (const [key, value] of Object.entries(tool.scope || {})) {
        if (repo && key === 'repo') continue;
        rows.push([label(key), esc(asList(value).join(', '))]);
      }
      if (tool.mcp) rows.push(['mcp', `<code>${esc(tool.mcp.host)}</code> <span class="muted">${esc(tool.mcp.transport)}</span>`]);
      if (tool.env) rows.push(['Credential', `<code>${esc(tool.env)}</code>`]);
      if (tool.note) rows.push(['Note', esc(tool.note)]);
      const status = tool.status === 'problem' && tool.problem
        ? `<span class="bt-problem">${esc(tool.problem)}</span>`
        : `${statusPill(tool)}${shownDetail(tool) ? ` <span class="muted">${esc(shownDetail(tool))}</span>` : ''}`;
      rows.push(['Status', status]);
      // Who else has it and its credentials live in Library › Tools (ui/app/redesign-library.js).
      const open = tool.service ? `<a class="bt-open" href="#/library/tools/${encodeURIComponent(tool.service)}?who=bot:${encodeURIComponent(slug)}">Open in Tools</a>` : '';
      return `<div class="bt-body"><dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>${open}</div>`;
    }
    // The line's key values: the model and effort for the model, access for a repository, otherwise who it acts
    // as and what it may do. A problem takes their place, so it is read without opening the tool.
    function keyValues(tool) {
      if (tool.id === 'model') return [shownIdentity(tool), ...asList(tool.scope?.effort || [])];
      if (isRepo(tool)) return [access(tool)];
      return [shownIdentity(tool), can(tool).map(canWord).join(', ')];
    }
    const meta = tool => tool.status === 'problem' && tool.problem
      ? `<span class="bt-meta bt-problem">${esc(tool.problem)}</span>`
      : `<span class="bt-meta">${esc(keyValues(tool).filter(Boolean).join(' · '))}</span>`;
    const tip = tool => [summary(tool), shownDetail(tool)].filter(Boolean).join('. ');
    const icon = tool => `<span class="bt-icon-static${tint(tool)}">${icons().markup(tool)}</span>`;
    const line = tool => `<details class="bt-item" data-tool="${esc(tool.id)}"><summary title="${esc(tip(tool))}">${icon(tool)}
        <span class="bt-name">${esc(tool.name)}</span>${meta(tool)}<span class="bt-line-status">${statusPill(tool)}</span></summary>${detail(tool, false)}</details>`;
    const repoLine = tool => `<details class="bt-repo" data-tool="${esc(tool.id)}"><summary title="${esc(tip(tool))}">
        <span class="bt-name">${repoLink(tool)}</span>${meta(tool)}<span class="bt-line-status">${statusPill(tool)}</span></summary>${detail(tool, true)}</details>`;
    // The repositories' line: how many, their access added up, and the most urgent status among them.
    function repoGroup(repos) {
      const count = (test, word) => { const n = repos.filter(test).length; return n ? `${n} ${word}` : ''; };
      const writes = tool => can(tool).includes('write');
      const levels = [count(writes, 'read and write'), count(tool => !writes(tool) && can(tool).includes('read'), 'read only')].filter(Boolean).join(', ');
      const worst = ['problem', 'pending', 'unknown', 'ready'].find(s => repos.some(tool => (STATUS[tool.status] ? tool.status : 'unknown') === s));
      const states = Object.entries(repos.reduce((n, tool) => { const w = stateWord(tool); n[w] = (n[w] || 0) + 1; return n; }, {}))
        .map(([w, n]) => `${n} ${w.toLowerCase()}`).join(', ');
      const title = `GitHub, ${repos.length} repositories: ${repos.map(tool => `${tool.identity} (${[access(tool), stateWord(tool)].filter(Boolean).join(', ')})`).join('; ')}`;
      return `<details class="bt-item bt-group" data-tool="github-repos"><summary title="${esc(title)}">${icon(repos[0])}
          <span class="bt-name">GitHub</span><span class="bt-meta">${esc([`${repos.length} repositories`, levels].filter(Boolean).join(' · '))}</span>
          <span class="bt-line-status" title="${esc(states)}">${statusPill({status: worst})}</span></summary>
        <div class="bt-repos">${repos.map(repoLine).join('')}</div></details>`;
    }
    // Two or more repositories share one line, where the first of them would be; one stays a tool line.
    function lines(tools) {
      const repos = tools.filter(isRepo);
      if (repos.length < 2) return tools.map(line).join('');
      return tools.map(tool => !isRepo(tool) ? line(tool) : tool === repos[0] ? repoGroup(repos) : '').join('');
    }
    async function refresh() {
      let got = null, failed = '';
      try { got = await load(slug, get); } catch (e) { failed = e.message || 'Could not load the tools.'; }
      if (!host.isConnected) return;
      host.classList.add('bt-list');
      if (!got) { host.innerHTML = `<div class="err">${esc(failed)}</div>`; return; }
      host.innerHTML = (got.reportError ? `<p class="err" data-tool-report-error>${esc(got.reportError)}</p>` : '')
        + (got.tools.length ? lines(got.tools) : '<div class="empty">None.</div>');
    }
    // A repository's name is a link inside its line: following it opens GitHub and leaves the line as it was.
    host.addEventListener('click', ev => {
      const link = ev.target.closest('summary a[href]');
      if (!link) return;
      ev.preventDefault();
      window.open(link.href, '_blank', 'noopener,noreferrer');
    });
    refresh();
  }

  window.botTools = {mountStrip, mountList, summary};
})();
