/* "Connect an agent": a person's own agent (Grok, Muse, Claude, Cursor, ...) reaches Tico's MCP
   server (backend/mcp.py) with a personal token (backend/personal_tokens.py) and then acts as that
   person, with their rights and no more. Pick the agent, make the token, copy the server URL and
   follow that agent's steps; the dialog says Connected once the token's first call lands
   (`last_used` in GET /api/v2/me/tokens). Docs: docs/connect-an-agent.md.

   The token is held only in this dialog's closure: never stored, logged or put in a URL, and gone
   when it closes. The MCP URL is the server's own (GET /api/v2/agent-skill): the runner hostname,
   where a bearer gets through an identity proxy such as Cloudflare Access.

   Logos come from ui/tool-icons.js (Simple Icons, CC0, and Lobe Icons, MIT; see NOTICE), drawn in
   the theme's ink next to the name only to say which product it is. An agent without one is its
   first letters. Uses the page's helpers: get, post, esc, ago, toast, copyText, setDrawer. */
'use strict';
(function () {
  const POLL_MS = 3000, POLL_FOR_MS = 5 * 60 * 1000;

  // Each agent: its tile, what an existing token label of it looks like, its steps (HTML, 2-4 short
  // lines) and the blocks to paste, with the URL and token filled in. docs/connect-an-agent.md says
  // where each path comes from. Muse, Claude and Dots show letters: Meta and Anthropic allow their
  // marks only with approval, and Dots has none of its own.
  const header = token => `Authorization: Bearer ${token}`;
  const AGENTS = [
    {id: 'grok', name: 'Grok', logo: 'grok', alias: /^grok\b|^grok-bot/i,
      steps: ['In one of your <b>Grok Bots</b>, ask it to add a custom MCP server with the block below.',
        'Or on grok.com, <b>Connectors › New Connector › Custom</b>: paste the URL, and the token if it asks.',
        '<b>Grok Build</b>: run the command below.',
        'Ask it “who needs me”.'],
      blocks: (url, token) => [['For a Grok Bot', `Add a custom MCP server:\nName: tico\nURL: ${url}\nHeader: ${header(token)}`],
        ['Grok Build', `grok mcp add --transport http tico ${url} --header "${header(token)}"`]]},
    {id: 'dots', name: 'Dots', logo: '', alias: /^dots\b/i, oauth: true,
      steps: ['In Dots, add a <b>connector</b> (remote MCP server) with the URL below. No token needed.',
        'When it asks, sign in to Tico and press <b>Allow</b>.',
        'Ask it “who needs me”.'],
      blocks: () => []},
    {id: 'muse', name: 'Muse', logo: '', alias: /^muse\b|^meta-muse|^meta muse/i,
      steps: ['In <b>Muse</b>, ask it to make a custom connector for the MCP server at the URL, with the header below.',
        '<b>Muse Code</b>: add the JSON below to <code>~/.config/muse/settings.json</code>.',
        'Ask it “who needs me”.'],
      blocks: (url, token) => [['Header', header(token)],
        ['Muse Code', JSON.stringify({schema_version: 1, mcp_servers: {tico: {transport: 'streamable_http', url,
          headers: {Authorization: 'Bearer ' + token}}}}, null, 2)]]},
    {id: 'claude', name: 'Claude', logo: '', alias: /^claude/i,
      steps: ['<b>Claude Code</b>: run the command below.',
        '<b>Claude app</b>: <b>Customize › Connectors › Add custom connector</b>, paste the URL, and sign in to Tico when it asks.',
        'Ask it “who needs me”.'],
      blocks: (url, token) => [['Claude Code', `claude mcp add --transport http tico ${url} --header "${header(token)}"`],
        ['Header', header(token)]]},
    {id: 'cursor', name: 'Cursor', logo: 'cursor', alias: /^cursor/i,
      steps: ['Open <code>~/.cursor/mcp.json</code> (<b>Cursor Settings › Tools &amp; MCP › New MCP Server</b>).',
        'Paste the JSON below, merged with any servers already there, and save.',
        'In a chat, ask it “who needs me”.'],
      blocks: (url, token) => [['mcp.json', JSON.stringify({mcpServers: {tico: {url, headers: {Authorization: 'Bearer ' + token}}}}, null, 2)]]},
    {id: 'codex', name: 'Codex', logo: 'openai', alias: /^codex/i,
      steps: ['Run the commands below in a terminal.',
        'Keep <code>TICO_TOKEN</code> set in your shell profile.',
        'Start <code>codex</code> and ask it “who needs me”.'],
      blocks: (url, token) => [['Terminal', `export TICO_TOKEN=${token}\ncodex mcp add tico --url ${url} --bearer-token-env-var TICO_TOKEN`]]},
    {id: 'other', name: 'Other', logo: '', alias: null, other: true,
      steps: ['Add a <b>remote MCP server</b> (Streamable HTTP) with the URL and the header below.',
        'An agent that only takes a local JSON config: paste the one below. <code>mcp-remote</code> adds the header.',
        'Ask it “who needs me”.'],
      blocks: (url, token) => [['Header', header(token)],
        ['JSON config', JSON.stringify({mcpServers: {tico: {command: 'npx', args: ['-y', 'mcp-remote', url, '--header', 'Authorization:${TICO_AUTH}'],
          env: {TICO_AUTH: 'Bearer ' + token}}}}, null, 2)]]},
  ];

  const CSS = `
.connect-agent{width:min(600px,calc(100vw - 24px))}
.connect-agent[open]{display:flex;flex-direction:column}
.ca-head{display:flex;align-items:flex-start;gap:var(--s3);padding:var(--s5) var(--s5) var(--s3)}
.ca-head>div{flex:1;min-width:0}
.ca-head h2{font-size:18px;line-height:1.3}
.ca-intro{margin:6px 0 0;color:var(--muted);font-size:13.5px;line-height:1.5}
.ca-head .tmodal-x{flex:none;margin:-4px -6px 0 0;border:0;font-size:15px;color:var(--muted)}
.ca-head .tmodal-x:hover{color:var(--ink);background:var(--surface2)}
.ca-body{display:flex;flex-direction:column;gap:var(--s4);padding:var(--s2) var(--s5) var(--s5);min-height:0;overflow-y:auto}
.ca-body>*{flex:none}
.ca-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(72px,1fr));gap:var(--s2);margin:0;padding:0;list-style:none}
.ca-tile{display:flex;flex-direction:column;align-items:center;gap:8px;width:100%;padding:14px 6px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);font:500 13px/1.2 var(--sans);cursor:pointer}
.ca-tile:hover{background:var(--surface2)}
.ca-tile:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ca-logo{display:inline-flex;align-items:center;justify-content:center;flex:none;width:32px;height:32px;border-radius:50%;background:var(--surface2);color:var(--ink)}
.ca-logo svg{width:18px;height:18px}
.ca-logo .ca-initials{font:600 12px/1 var(--sans)}
.ca-logo.sm{width:26px;height:26px}.ca-logo.sm svg{width:14px;height:14px}.ca-logo.sm .ca-initials{font-size:10.5px}
.ca-section{display:flex;flex-direction:column;gap:6px}
.ca-label{margin:0;font-size:12px;font-weight:600;color:var(--muted)}
.ca-conns{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;border:1px solid var(--line);border-radius:var(--radius)}
.ca-conn{display:flex;align-items:center;gap:10px;padding:8px 8px 8px 10px;font-size:13px}
.ca-conn+.ca-conn{border-top:1px solid var(--line)}
.ca-conn-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ca-conn-used{flex:none;color:var(--muted);font-size:12px}
.ca-conn button{flex:none;padding:4px 10px;font-size:12.5px}
.ca-agent{display:flex;align-items:center;gap:10px}
.ca-agent h3{margin:0;font-size:15px;flex:1}
.ca-back{padding:4px 10px;font-size:12.5px}
.ca-row{display:flex;align-items:center;gap:var(--s2);min-width:0}
.ca-row code{flex:1;min-width:0;padding:7px 10px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg);font:12.5px/1.4 var(--mono);overflow-wrap:anywhere}
.ca-row button,.ca-copy{flex:none;padding:5px 12px;font-size:12.5px}
.ca-row input{flex:1;min-width:0}
[data-copied]{color:var(--ok);border-color:var(--ok)}
.ca-steps{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px;counter-reset:ca;font-size:14px;line-height:1.45}
.ca-steps li{display:flex;align-items:baseline;gap:10px;counter-increment:ca}
.ca-steps li::before{content:counter(ca);flex:none;width:20px;height:20px;display:inline-flex;align-items:center;justify-content:center;border-radius:50%;background:var(--ok-bg);color:var(--accent);font-size:11.5px;font-weight:600;transform:translateY(-1px)}
.ca-steps code{font:12.5px var(--mono)}
.ca-block{border:1px solid var(--line);border-radius:var(--radius);background:var(--bg);min-width:0;overflow:hidden}
.ca-block-head{display:flex;align-items:center;gap:var(--s2);padding:6px 6px 6px 12px;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}
.ca-block-head span{flex:1}
.ca-block pre{margin:0;padding:12px;max-height:min(36vh,320px);overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:12.5px/1.55 var(--mono);color:var(--ink)}
.ca-note{margin:0;color:var(--muted);font-size:12px;line-height:1.5}
.ca-status{display:flex;align-items:center;gap:8px;margin:2px 0 0;font-size:13.5px;color:var(--muted)}
.ca-status button{padding:4px 10px;font-size:12.5px}
.ca-dot{flex:none;width:9px;height:9px;border-radius:50%;background:var(--idle)}
.ca-status[data-state=waiting] .ca-dot{animation:ca-pulse 1.4s ease-in-out infinite}
.ca-status[data-state=connected]{color:var(--ink);font-weight:600}
.ca-status[data-state=connected] .ca-dot{background:var(--ok);animation:none}
@keyframes ca-pulse{50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){.ca-status[data-state=waiting] .ca-dot{animation:none}}
.ca-actions{display:flex}
.ca-actions .primary{min-width:140px}
@media (max-width:760px){.ca-head{padding:var(--s4) var(--s4) var(--s2)}.ca-body{padding:var(--s2) var(--s4) var(--s4)}
  .ca-tile{padding:12px 4px 10px;font-size:12.5px}.ca-actions .primary{width:100%;padding:11px 14px}}`;

  const today = () => new Date().toLocaleDateString('en-CA');     // 2026-09-30, the person's own day
  const byId = id => AGENTS.find(agent => agent.id === id);
  // Which agent an existing token belongs to, from its label ("Grok · 2026-09-30", "grok-bot").
  const agentOf = label => AGENTS.find(agent => agent.alias && agent.alias.test(String(label || '').trim()));

  const PLUS = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path d="M11 4h2v7h7v2h-7v7h-2v-7H4v-2h7z"/></svg>';
  function logo(agent, label, small) {
    const icons = window.toolIcons;
    const key = agent && agent.logo;
    const inner = key && icons && icons.has(key) ? icons.markup({logo_key: key, name: agent.name})
      : agent && agent.other ? PLUS
      : `<span class="ca-initials">${esc(icons ? icons.initials(agent ? agent.name : label) : '?')}</span>`;
    return `<span class="ca-logo${small ? ' sm' : ''}" aria-hidden="true">${inner}</span>`;
  }

  function flash(button, text) {
    const was = button.textContent;
    button.textContent = text; button.dataset.copied = '';
    setTimeout(() => { button.textContent = was; delete button.dataset.copied; }, 2000);
  }

  function copyButton(button, value) {
    button.onclick = () => void copyText(value()).then(() => flash(button, 'Copied'))
      .catch(() => toast('Could not copy; select the text and copy it', true));
  }

  window.connectAgent = function connectAgent() {
    if (!document.getElementById('connect-agent-style')) {
      const style = document.createElement('style');
      style.id = 'connect-agent-style'; style.textContent = CSS;
      document.head.append(style);
    }
    if (typeof setDrawer === 'function') setDrawer(false);
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal connect-agent';
    dialog.setAttribute('aria-labelledby', 'ca-title');
    dialog.innerHTML = `<header class="ca-head"><div><h2 id="ca-title">Connect an external agent</h2>
        <p class="ca-intro">Connect Grok, Dots, Muse or any external agent to Tico. It can then read and act on your tasks, goals and docs as you.</p></div>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <div class="ca-body" data-ca-body></div>`;
    const body = dialog.querySelector('[data-ca-body]');
    let skill = null, issued = null, timer = 0, pollUntil = 0, closed = false, waitingText = '';

    const stopPolling = () => { clearTimeout(timer); timer = 0; };

    async function loadSkill() {
      if (!skill) skill = await get('/v2/agent-skill');
      return skill;
    }

    async function showPicker() {
      stopPolling(); issued = null;
      body.innerHTML = `<ul class="ca-tiles" aria-label="External agents">${AGENTS.map(agent =>
        `<li><button class="ca-tile" type="button" data-agent="${agent.id}">${logo(agent, agent.name)}<span>${esc(agent.name)}</span></button></li>`).join('')}</ul>
        <section class="ca-section" data-conns hidden></section>`;
      body.querySelectorAll('[data-agent]').forEach(button => { button.onclick = () => void showAgent(byId(button.dataset.agent)); });
      await renderConnections();
    }

    async function renderConnections() {
      const host = body.querySelector('[data-conns]');
      if (!host) return;
      let rows;
      try { rows = (await get('/v2/me/tokens')).tokens || []; }
      catch { return; }                                  // not a person who may hold tokens: no list
      if (closed || !host.isConnected) return;
      const now = Date.now();
      const live = rows.filter(row => !row.revoked_at && !(row.expires_at && new Date(row.expires_at) < now));
      host.hidden = !live.length;
      host.innerHTML = `<h3 class="ca-label">External agents</h3><ul class="ca-conns">${live.map(row =>
        `<li class="ca-conn" data-conn="${esc(row.id)}">${logo(agentOf(row.label), row.label, true)}
          <span class="ca-conn-name">${esc(row.label)}</span>
          <span class="ca-conn-used">${row.last_used ? 'Used ' + esc(ago(row.last_used)) : 'Never used'}</span>
          <button class="ghost" type="button" data-revoke="${esc(row.id)}">Revoke</button></li>`).join('')}</ul>`;
      host.querySelectorAll('[data-revoke]').forEach(button => {
        const row = live.find(r => r.id === button.dataset.revoke);
        button.onclick = async () => {
          if (!confirm(`Revoke ${row.label}? The external agent loses access at once.`)) return;
          button.disabled = true;
          try { await post(`/v2/me/tokens/${encodeURIComponent(row.id)}/revoke`, {}); toast(`${row.label} revoked`); }
          catch (error) { toast(error.message, true); }
          await renderConnections();
        };
      });
    }

    async function showAgent(agent) {
      stopPolling(); issued = null;
      let url, bypass;
      try { ({mcp_url: url, access_bypass: bypass} = await loadSkill()); }
      catch (error) { toast(error.message, true); return; }
      if (closed) return;
      body.innerHTML = `<div class="ca-agent">${logo(agent, agent.name)}<h3>${esc(agent.name)}</h3>
          <button class="ghost ca-back" type="button" data-back>All external agents</button></div>
        <div class="ca-section"><h4 class="ca-label">MCP server URL</h4>
          <div class="ca-row"><code data-url>${esc(url)}</code><button class="ghost" type="button" data-copy-url>Copy</button></div>
          ${bypass ? '<p class="ca-note" data-access-note>Cloudflare Access must let <code>/api/v2/mcp</code> through: give that path a Bypass policy. Tico checks the token itself.</p>' : ''}</div>
        ${agent.oauth ? '' : `<div class="ca-section" data-token-section><h4 class="ca-label">Token</h4>
          ${agent.other ? `<div class="ca-row"><input type="text" data-other maxlength="40" placeholder="External agent name" aria-label="External agent name" autocomplete="off"></div>` : ''}
          <div class="ca-actions"><button class="primary" type="button" data-create>Create token</button></div></div>`}
        <ol class="ca-steps">${agent.steps.map(step => `<li><span>${step}</span></li>`).join('')}</ol>
        <div data-after></div>`;
      body.querySelector('[data-back]').onclick = () => void showPicker();
      copyButton(body.querySelector('[data-copy-url]'), () => url);
      // An agent that signs in with OAuth (backend/mcp_oauth.py) gets its token from the person's Allow; it is
      // listed under Connected once it has called.
      if (!agent.oauth) body.querySelector('[data-create]').onclick = event => void createToken(agent, url, event.currentTarget);
    }

    async function createToken(agent, url, button) {
      const other = body.querySelector('[data-other]');
      const name = agent.other ? (other.value.trim().replace(/[·]/g, '') || 'Agent') : agent.name;
      button.disabled = true;
      try { issued = await post('/v2/me/tokens', {label: `${name} · ${today()}`, expires_in_days: 90}); }
      catch (error) { toast(error.message, true); button.disabled = false; return; }
      if (closed) return;
      const section = body.querySelector('[data-token-section]');
      section.innerHTML = `<h4 class="ca-label">Token</h4>
        <div class="ca-row"><code data-token>${esc(issued.token)}</code><button class="ghost" type="button" data-copy-token>Copy</button></div>
        <p class="ca-note">Shown once. Expires ${esc(new Date(issued.expires_at).toLocaleDateString())}.</p>
        <p class="ca-status" data-status data-state="waiting" role="status"></p>`;
      const token = issued.token;
      copyButton(section.querySelector('[data-copy-token]'), () => token);
      const blocks = agent.blocks(url, token);
      body.querySelector('[data-after]').outerHTML = `${blocks.map(([label, text], i) =>
        `<div class="ca-block"><div class="ca-block-head"><span>${esc(label)}</span>
          <button class="ghost ca-copy" type="button" data-copy-block="${i}">Copy</button></div><pre data-snippet>${esc(text)}</pre></div>`).join('')}`;
      body.querySelectorAll('[data-copy-block]').forEach(button => copyButton(button, () => blocks[Number(button.dataset.copyBlock)][1]));
      waitingText = `Waiting for ${name}’s first call`;
      startPolling();
    }

    // Connected once the token's first call lands. Every 3s for 5 minutes, then Check again; the
    // browser asks with its own session, so waiting never marks the token used.
    function setStatus(state, text, retry) {
      const el = body.querySelector('[data-status]');
      if (!el) return;
      el.dataset.state = state;
      el.innerHTML = `<span class="ca-dot"></span><span data-status-text>${esc(text)}</span>${retry ? '<button class="ghost" type="button" data-retry>Check again</button>' : ''}`;
      const again = el.querySelector('[data-retry]');
      if (again) again.onclick = () => startPolling();
    }

    function startPolling() {
      stopPolling();
      pollUntil = Date.now() + POLL_FOR_MS;
      setStatus('waiting', waitingText);
      timer = setTimeout(poll, POLL_MS);
    }

    async function poll() {
      timer = 0;
      if (closed || !issued) return;
      const id = issued.id;
      let row;
      try { row = ((await get('/v2/me/tokens')).tokens || []).find(r => r.id === id); }
      catch { row = null; }
      if (closed || !issued || issued.id !== id) return;
      if (row && row.last_used) { setStatus('connected', 'Connected'); return; }
      if (row && row.revoked_at) { setStatus('idle', 'Revoked'); return; }
      if (Date.now() >= pollUntil) { setStatus('idle', 'Not seen yet', true); return; }
      timer = setTimeout(poll, POLL_MS);
    }

    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.onclose = () => {
      closed = true; stopPolling(); issued = null;
      dialog.remove();
      if (typeof renderSettingsTokens === 'function' && document.getElementById('set-tokens')) void renderSettingsTokens();
      window.dispatchEvent(new Event('tico:agents-changed'));     // the team chart's Connect button (ui/app/sidebar.js)
    };
    document.body.appendChild(dialog); dialog.showModal();
    void showPicker();
  };

})();
