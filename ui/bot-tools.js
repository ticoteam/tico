/* A bot's tools (docs/creating-bots.md, "What people see about a bot's tools"), in two places.
   Beside the bot's name, after its runtime mark: up to three small icons, the service's logo when
   ui/tool-icons.js has it and the name's first two letters otherwise, then "+N" for the rest; each
   shows its tool's details on hover or focus (which account or repository, what it may do, its state, so two
   tools of one service read apart) and opens the full list. The model the bot runs on is left out there, since
   the runtime mark beside the name already says it. A red dot marks a tool with a problem.
   The full list is the Tools card under More: every tool with what it acts as, what it may do, its
   scope, note, credential name and status.
   ui/app/bot-page.js calls window.botTools.mountStrip(host, deps) once per bot page and mountList(host,
   deps) when More opens; deps keeps this file free of the page's globals (get, esc). Nothing here reads
   a secret: the server sends names, never values. */
(function () {
  const STRIP_MAX = 3;

  const css = `
.bot-tool-strip{display:inline-flex;align-items:center;gap:6px;flex:none;margin-left:6px}
.bot-tool-strip .bts-icon{position:relative;flex:none;display:inline-grid;place-items:center;width:16px;height:16px;border-radius:4px;color:var(--muted);text-decoration:none;opacity:.8}
.bot-tool-strip .bts-icon:hover,.bot-tool-strip .bts-more:hover{opacity:1;color:var(--ink);text-decoration:none}
.bot-tool-strip a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;opacity:1}
.bot-tool-strip .bts-icon svg{width:100%;height:100%;display:block}
.bot-tool-strip .bts-icon.bt-tint{border-radius:50%;background:color-mix(in srgb,hsl(var(--h) 60% 50%) 22%,transparent);color:color-mix(in srgb,hsl(var(--h) 65% 42%) 72%,var(--ink))}
.bot-tool-strip .tool-initials{font-size:8px;font-weight:600;line-height:1;letter-spacing:0}
.bot-tool-strip .bt-dot{position:absolute;right:-3px;bottom:-3px;width:7px;height:7px;border-radius:50%;background:var(--fail);border:1.5px solid var(--bg)}
.bot-tool-strip .bts-more{flex:none;font-size:11px;font-weight:600;line-height:16px;color:var(--muted);text-decoration:none;font-variant-numeric:tabular-nums}
.tool-initials{font-size:11.5px;font-weight:600;letter-spacing:.01em}
.bt-list .bt-item{display:grid;grid-template-columns:22px minmax(0,1fr);gap:2px 10px;padding:10px 0;border-top:1px solid var(--line);font-size:13px;line-height:1.45}
.bt-list .bt-item:first-child{border-top:0;padding-top:2px}
.bt-list .bt-icon-static{grid-row:span 2;flex:none;width:22px;height:22px;border-radius:50%;background:var(--surface2);display:inline-grid;place-items:center;color:var(--ink)}
.bt-list .bt-icon-static svg{width:13px;height:13px}
.bt-list .bt-tint{background:color-mix(in srgb,hsl(var(--h) 60% 50%) 20%,var(--surface));color:color-mix(in srgb,hsl(var(--h) 65% 42%) 72%,var(--ink))}
.bt-list .bt-name{font-weight:600;overflow-wrap:anywhere;align-self:center}
.bt-list dl{margin:0;display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 12px}
.bt-list dt{color:var(--muted);font-size:12px;padding-top:1px}
.bt-list dd{margin:0;min-width:0;overflow-wrap:anywhere}
.bt-list .pill{margin:0 4px 2px 0}
.bt-list .bt-state{display:none}
@media (max-width:760px){
  .bt-list .bt-item{grid-template-columns:22px minmax(0,1fr) auto;align-items:center;padding:7px 0}
  .bt-list .bt-icon-static{grid-row:auto}
  .bt-list .bt-state{display:block;font-size:12px;text-align:right}
  .bt-list .bt-item{cursor:pointer}
  .bt-list .bt-item dl{display:none;grid-column:2/-1}
  .bt-list .bt-item.open dl{display:grid}
}
.bt-list .bt-problem{color:var(--fail)}
.bts-tip{max-width:300px}
.bts-tip .tip-head{display:block;margin-bottom:2px;font-weight:600;overflow-wrap:anywhere}
.bts-tip .tip-sub{margin-bottom:4px;overflow-wrap:anywhere}
.bts-tip .bt-problem{color:var(--fail)}`;

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

  // One tooltip for the strip, moved to whichever icon the pointer or focus is on (the .tip look of ui/app/tooltip.js).
  let TIP = null;
  function tipShow(el, tool, esc) {
    if (!TIP) {
      TIP = document.createElement('div');
      TIP.id = 'bts-tip'; TIP.className = 'tip bts-tip'; TIP.setAttribute('role', 'tooltip'); TIP.hidden = true;
      document.body.append(TIP);
    }
    TIP.innerHTML = `<div class="tip-head">${esc(tool.name)}${tool.identity ? ` · ${esc(shownIdentity(tool))}` : ''}</div>
      <dl>${facts(tool).map(([k, v]) => `<dt>${esc(k)}</dt><dd${k === 'State' && tool.status === 'problem' ? ' class="bt-problem"' : ''}>${esc(v)}</dd>`).join('')}</dl>`;
    TIP.hidden = false;
    el.setAttribute('aria-describedby', 'bts-tip');
    const r = el.getBoundingClientRect(), box = TIP.getBoundingClientRect();
    TIP.style.left = Math.max(8, Math.min(r.left, innerWidth - box.width - 8)) + 'px';
    TIP.style.top = (r.bottom + box.height + 14 > innerHeight ? Math.max(8, r.top - box.height - 6) : r.bottom + 6) + 'px';
    TIP.owner = el;
  }
  function tipHide(el) {
    if (!TIP || (el && TIP.owner !== el)) return;
    TIP.hidden = true;
    TIP.owner?.removeAttribute('aria-describedby');
    TIP.owner = null;
  }
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape') tipHide(); });
  window.addEventListener('hashchange', () => tipHide());

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

  // Beside the name: three icons at most and "+N", every one a link to the full list (deps.href).
  function mountStrip(host, deps) {
    if (!host) return;
    style();
    const {slug, get, esc, href, skipModel} = deps;
    host.classList.add('bot-tool-strip');
    host.setAttribute('role', 'group');
    host.setAttribute('aria-label', 'Tools');
    let tools = [];
    function render() {
      const shown = skipModel ? tools.filter(t => t.id !== 'model') : tools;
      host.hidden = !shown.length;
      const few = shown.slice(0, STRIP_MAX), rest = shown.length - few.length;
      tipHide();
      host.innerHTML = few.map(tool => `<a class="bts-icon${tint(tool)}" href="${esc(href)}" data-tool="${esc(tool.id)}"
          aria-label="${esc(summary(tool))}">${icons().markup(tool)}${tool.status === 'problem' ? '<span class="bt-dot" aria-hidden="true"></span>' : ''}</a>`).join('')
        + (rest > 0 ? `<a class="bts-more" href="${esc(href)}" title="All ${tools.length} tools" aria-label="All ${tools.length} tools">+${rest}</a>` : '');
      for (const el of host.querySelectorAll('.bts-icon')) {
        const tool = few.find(t => t.id === el.dataset.tool);
        const show = () => tipShow(el, tool, esc), hide = () => tipHide(el);
        el.addEventListener('mouseenter', show); el.addEventListener('focus', show);
        el.addEventListener('mouseleave', hide); el.addEventListener('blur', hide); el.addEventListener('click', hide);
      }
    }
    async function refresh() {
      try { const got = await load(slug, get); if (!host.isConnected) return; tools = got.tools; }
      catch { if (!host.isConnected) return; }
      render();
    }
    refresh();
    const timer = setInterval(() => { if (!host.isConnected) clearInterval(timer); else refresh(); }, 60000);
  }

  // The Tools card under More: every tool and its details.
  function mountList(host, deps) {
    if (!host) return;
    style();
    const {slug, get, esc} = deps;
    const statusPill = tool => { const [kind, word] = STATUS[tool.status] || STATUS.unknown; return `<span class="pill ${kind}">${esc(word)}</span>`; };
    function detail(tool) {
      const rows = [];
      const repo = isRepo(tool);
      const identityLabel = tool.id === 'model' ? 'Runs on' : repo ? 'Repository' : 'Acts as';
      if (tool.identity) rows.push([identityLabel, tool.url && repo
        ? `<a href="${esc(tool.url)}" target="_blank" rel="noopener noreferrer">${esc(tool.identity)}</a>` : esc(shownIdentity(tool))]);
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
      return `<dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`;
    }
    // A phone shows one line per tool, its name and only what is wrong; a tap opens its details.
    const state = tool => tool.status === 'problem'
      ? `<span class="bt-state">${tool.problem ? `<span class="bt-problem">${esc(tool.problem)}</span>` : statusPill(tool)}</span>` : '';
    async function refresh() {
      let got = null, failed = '';
      try { got = await load(slug, get); } catch (e) { failed = e.message || 'Could not load the tools.'; }
      if (!host.isConnected) return;
      host.classList.add('bt-list');
      if (!got) { host.innerHTML = `<div class="err">${esc(failed)}</div>`; return; }
      host.innerHTML = (got.reportError ? `<p class="err" data-tool-report-error>${esc(got.reportError)}</p>` : '')
        + (got.tools.length ? got.tools.map(tool => `<div class="bt-item" data-tool="${esc(tool.id)}">
            <span class="bt-icon-static${tint(tool)}">${icons().markup(tool)}</span>
            <span class="bt-name">${esc(tool.name)}</span>${state(tool)}${detail(tool)}</div>`).join('') : '<div class="empty">None.</div>');
    }
    host.addEventListener('click', ev => {
      const item = ev.target.closest('.bt-item');
      if (item && !ev.target.closest('a') && matchMedia('(max-width: 760px)').matches) item.classList.toggle('open');
    });
    refresh();
  }

  window.botTools = {mountStrip, mountList, summary};
})();
