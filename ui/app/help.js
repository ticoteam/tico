/* ui/app/help.js — Help page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The map: how the pieces fit (docs/architecture.md). Two drawings of the same picture, wide and tall; the page's width
// picks one (help.css), and screen readers get HELP_MAP_ALT instead of either.
const HELP_MAP_ALT = 'Human teammates set direction and review results. AI teammates follow instructions and use granted tools. Humans use the web app, Slack or MCP and the CLI; external agents connect over MCP with a token. '
  + 'Both reach the Tico server, which holds the team\'s shared work: tasks, goals and KPIs, docs, updates, messages and '
  + 'Credentials. Four built-in bots come with Tico: Assistant, BotOps, Librarian and Goal Manager. Your computers ask the '
  + 'server for work, run the bots and send back results. Each bot is a Git repository plus a model, gets only the '
  + 'Credentials granted to it, and uses Tools such as GitHub, Google, Slack and AWS.';
const HELP_WORK = ['Tasks', 'Goals & KPIs', 'Docs', 'Updates', 'Messages', 'Credentials'];
const HELP_BUILT_IN = [['Assistant', 'Your own helper'], ['BotOps', 'Sets up bots'], ['Librarian', 'Answers from docs'], ['Goal Manager', 'Keeps goals current']];
function helpMapSVG(tall) {
  const out = [], id = tall ? 'help-arrow-tall' : 'help-arrow-wide', idG = id + '-granted';
  const box = (x, y, w, h, cls = 'hm-box') => out.push(`<rect class="${cls}" x="${x}" y="${y}" width="${w}" height="${h}" rx="${cls === 'hm-chip' ? 7 : 11}"/>`);
  const text = (x, y, words, cls, anchor = 'start') => out.push(`<text class="${cls}" x="${x}" y="${y}"${anchor === 'start' ? '' : ` text-anchor="${anchor}"`}>${esc(words)}</text>`);
  const chip = (x, y, w, words, cls = 'hm-chip') => { box(x, y, w, 34, cls); text(x + 12, y + 22, words, 'hm-chip-t'); };
  const line = (d, granted = false, both = false) => out.push(`<path class="hm-line${granted ? ' granted' : ''}" d="${d}" marker-end="url(#${granted ? idG : id})"${both ? ` marker-start="url(#${id})"` : ''}/>`);
  const card = (x, y, w, title, sub, cls = 'hm-card') => { box(x, y, w, 52, cls); text(x + 13, y + 22, title, 'hm-card-t'); text(x + 13, y + 40, sub, 'hm-s'); };
  const server = (x, y, w, h, col) => {
    box(x, y, w, h, 'hm-box hm-server'); text(x + 20, y + 30, 'Tico server', 'hm-t'); text(x + 20, y + 50, 'Shared work · runs no bots', 'hm-s');
    const cw = (w - 40 - 2 * col) / 3;
    HELP_WORK.forEach((words, i) => chip(x + 20 + (i % 3) * (cw + col), y + 66 + Math.floor(i / 3) * 44, cw, words, words === 'Credentials' ? 'hm-chip hm-cred' : 'hm-chip'));
  };
  const builtIn = (x, y, w) => {
    box(x, y, w, 158, 'hm-band'); text(x + 20, y + 24, 'Built-in bots', 'hm-k'); text(x + w - 20, y + 24, 'come with Tico', 'hm-k', 'end');
    const cw = (w - 48) / 2;
    HELP_BUILT_IN.forEach(([title, sub], i) => card(x + 20 + (i % 2) * (cw + 8), y + 38 + Math.floor(i / 2) * 60, cw, title, sub, 'hm-card hm-builtin'));
  };
  const computers = (x, y, w, h, cw) => {
    box(x, y, w, h); text(x + 20, y + 30, 'Computers', 'hm-t'); text(x + 20, y + 50, 'Run your AI teammates', 'hm-s');
    return (cards, foot) => { cards.forEach(([cx, cy, title, sub]) => card(cx, cy, cw, title, sub)); text(x + 20, y + h - 18, foot, 'hm-s'); };
  };
  let w, h;
  if (!tall) {
    [w, h] = [1000, 372];
    box(0, 30, 190, 170); text(18, 60, 'Humans', 'hm-t'); text(18, 79, 'Direct and review', 'hm-s');
    ['Web app', 'Slack · DM Tico', 'MCP · CLI'].forEach((words, i) => chip(18, 90 + i * 36, 154, words));
    box(0, 240, 190, 150); text(18, 270, 'External agents', 'hm-t'); text(18, 292, 'Hermes · OpenClaw', 'hm-s'); text(18, 310, 'Codex · Claude', 'hm-s'); text(18, 332, 'Work from your tools', 'hm-s'); text(18, 360, 'MCP + token', 'hm-k accent');
    line('M192,115 H256', false, true); line('M192,315 H226 V170 H256');
    server(260, 30, 440, 170, 10);
    out.push('<path class="hm-tie" d="M480,202 V230"/>');
    builtIn(260, 232, 440);
    computers(780, 30, 220, 240, 180)([[800, 98, 'Support', 'Git repo + model'], [800, 160, 'Inbox Manager', 'Message bot']], 'Run bots · return results');
    line('M702,82 H776'); text(740, 74, 'work', 'hm-l', 'middle');
    line('M778,118 H704'); text(740, 110, 'results', 'hm-l', 'middle');
    line('M682,157 H776', true); text(740, 149, 'granted', 'hm-l accent', 'middle');
    line('M890,272 V296'); text(900, 288, 'bots use', 'hm-l');
    box(780, 300, 220, 90); text(800, 332, 'Tools', 'hm-t'); text(800, 356, 'GitHub · Google · Slack · AWS', 'hm-s');
  } else {
    [w, h] = [360, 864];
    box(0, 0, 174, 168); text(14, 28, 'Humans', 'hm-t'); text(14, 46, 'Direct and review', 'hm-s');
    ['Web app', 'Slack · DM Tico', 'MCP · CLI'].forEach((words, i) => chip(14, 58 + i * 36, 146, words));
    box(186, 0, 174, 168); text(200, 28, 'External agents', 'hm-t'); text(200, 52, 'Hermes · OpenClaw', 'hm-s'); text(200, 72, 'Codex · Claude', 'hm-s'); text(200, 94, 'Work from your tools', 'hm-s'); text(200, 126, 'MCP + token', 'hm-k accent');
    line('M87,170 V196', false, true); line('M273,170 V196');
    server(0, 200, 360, 162, 8);
    out.push('<path class="hm-tie" d="M180,364 V378"/>');
    builtIn(0, 380, 360);
    line('M60,542 V580'); text(68, 566, 'work', 'hm-l');
    line('M150,580 V542'); text(158, 566, 'results', 'hm-l');
    line('M240,542 V580', true); text(248, 566, 'granted', 'hm-l accent');
    computers(0, 584, 360, 172, 160)([[16, 650, 'Support', 'Git repo + model'], [184, 650, 'Inbox Manager', 'Message bot']], 'Run bots · return results');
    line('M180,758 V784'); text(188, 775, 'bots use', 'hm-l');
    box(0, 788, 360, 74); text(16, 816, 'Tools', 'hm-t'); text(16, 840, 'GitHub · Google · Slack · AWS', 'hm-s');
  }
  const marker = (mid, cls) => `<marker id="${mid}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="${cls}" d="M0,0 L10,5 L0,10 z"/></marker>`;
  return `<svg class="help-map-svg ${tall ? 'tall' : 'wide'}" viewBox="0 ${tall ? 0 : 24} ${w} ${h}" aria-hidden="true" focusable="false">
    <defs>${marker(id, 'hm-head')}${marker(idG, 'hm-head granted')}</defs>${out.join('')}</svg>`;
}

// Who does what: the four built-ins, then the bots a team adds (docs/glossary.md).
const HELP_WHO = [
  ['Assistant', true, 'Your own helper: finds things, makes tasks, drafts the rest for your click', 'Sidebar, or ⌘K', 'What needs me today?'],
  ['BotOps', true, 'Sets up and fixes bots, computers, Tools and Credentials', 'Sidebar', 'Add a bot that answers #support'],
  ['Librarian', true, 'Answers from your docs and the Tico manual, with sources', 'Ask the Librarian on Docs and Market', 'What is our refund limit?'],
  ['Goal Manager', true, 'Keeps KPIs and goal colours current', 'Top of Goals', 'Why is this goal yellow?'],
  ['Your bots', false, 'Do the team\'s work from their Instructions', 'Team in the sidebar: chat or a task', 'Draft replies to the open tickets'],
  ['Message bots', false, 'Work a human\'s email or a Slack channel', 'Message bots in the sidebar', 'Sort today\'s email', 'message_bots'],
];
const HELP_GLOSSARY = [
  ['Human teammate', 'A person who directs work, answers questions, and reviews results.'],
  ['AI teammate', 'An agent that works from instructions and uses the tools granted to it.'],
  ['Computer', 'A Mac, Linux machine, or Docker host that runs your bots.'],
  ['Connected agent', 'An agent in another app that joins shared work through MCP and an access token.'],
  ['MCP and CLI', 'Two ways for agents to reach the same shared Tico tools.'],
  ['Credentials', 'Access to an outside service, granted to the teammates that need it.'],
];
const HELP_PLATFORMS = [
  ['Claude', 'Claude Code works with your team’s shared context and tools.'],
  ['OpenAI', 'Codex works with your team’s shared context and tools.'],
  ['Hermes', 'Connect a Hermes agent to your team’s shared work.', 'external_agents'],
  ['OpenClaw', 'Pair an OpenClaw profile with a code; it checks in on its own schedule.', 'external_agents'],
];
// The Beta features, from the server's one list (backend/beta.py), each with its pages in the manual.
function helpBetaHTML() {
  const rows = S.config.beta || [];
  if (!rows.length) return '';
  const page = doc => `<a href="${GH}/blob/main/docs/${esc(doc)}" target="_blank" rel="noopener noreferrer">${esc(doc.replace(/\.md(#.*)?$/, ''))}</a>`;
  return `<details class="help-tools help-beta" id="help-beta">
      <summary><strong>Beta features</strong></summary>
      <p>These work today and may still change. A support request's diagnostics say which of them your team has on.</p>
      <ul>${rows.map(f => `<li>${esc(f.name)}${betaPill(f.id)} <span class="muted">${(f.docs || []).map(page).join(' · ')}</span></li>`).join('')}</ul>
    </details>`;
}

// The server's version, and inside the desktop app its own version (app/src/main.rs bridge_script) and a way to its
// log file (app/src/applog.rs), which a shell that keeps one says in ticoNativeFeatures.
function helpVersionHTML() {
  const parts = [S.config.version && `<span id="help-version">Version ${esc(nvVersion(S.config.version))}</span>`,
    window.ticoNativeVersion && `<span id="help-app-version">App ${esc(nvVersion(window.ticoNativeVersion))}</span>`,
    (window.ticoNativeFeatures || []).includes('showLog') && '<button class="help-log" type="button" id="help-app-log">Show the app\'s log</button>'];
  return parts.filter(Boolean).join(' · ');
}
function pageHelp() {
  $('#main').innerHTML = `<article class="help-page" id="help-page">
    <header class="help-hero">
      <h1>Your human and AI team</h1>
      <p class="help-lede">Tico brings your human and AI teammates together around shared goals, tasks, conversations, and knowledge. People set direction and review results. AI teammates carry out work with their instructions and the tools you give them. Your server keeps the shared record; your computers run the bots.</p>
      <div class="help-actions"><a class="gs-link" href="${GH}/blob/main/docs/README.md" target="_blank" rel="noopener noreferrer">Docs</a><a class="gs-link" id="help-first-result" href="${GH}/blob/main/docs/install.md#your-first-result" target="_blank" rel="noopener noreferrer">Your first result</a><button class="ghost" type="button" data-gs-tour>Take the tour</button><a class="gs-link help-support-link" href="#support-rail">Support</a></div>
    </header>

    <figure class="help-map" id="help-map" role="img" aria-label="${esc(HELP_MAP_ALT)}">${helpMapSVG(false)}${helpMapSVG(true)}</figure>
    <div class="help-team-description"><p><strong>Human teammates</strong> set priorities, delegate work, answer questions, and review results.</p><p><strong>AI teammates</strong> follow instructions, use granted tools, and report progress. Built-in bots run on your computers; connected agents join from their own apps.</p></div>
    <section class="help-platforms" aria-label="Agent platforms">${HELP_PLATFORMS.map(([name, body, beta]) => `<p><strong>${esc(name)}${beta ? betaPill(beta) : ''}</strong><span>${esc(body)}</span></p>`).join('')}</section>

    <section class="help-who" aria-labelledby="help-who-h">
      <h2 id="help-who-h">Who does what</h2>
      <table>
        <thead><tr><th scope="col">Teammate</th><th scope="col">For</th><th scope="col">Find it</th><th scope="col">Ask</th></tr></thead>
        <tbody>${HELP_WHO.map(([name, builtIn, what, where, ask, beta]) => `<tr${builtIn ? ' class="built-in"' : ''}>
          <th scope="row">${esc(name)}${builtIn ? '<span class="help-tag">Built-in</span>' : ''}${beta ? betaPill(beta) : ''}</th>
          <td data-label="For">${esc(what)}</td><td data-label="Find it">${esc(where)}</td><td data-label="Ask" class="help-ask">“${esc(ask)}”</td></tr>`).join('')}</tbody>
      </table>
    </section>

    <details class="help-tools help-glossary">
      <summary><strong>Glossary</strong></summary>
      <dl>${HELP_GLOSSARY.map(([term, meaning]) => `<dt>${esc(term)}</dt><dd>${esc(meaning)}</dd>`).join('')}</dl>
      <p><a href="${GH}/blob/main/docs/glossary.md" target="_blank" rel="noopener noreferrer">Full glossary</a> · <a href="${GH}/blob/main/docs/README.md" target="_blank" rel="noopener noreferrer">MCP and CLI reference</a></p>
    </details>
    ${helpBetaHTML()}
    <p class="help-foot">${helpVersionHTML()}</p>
  </article>`;
  $('#help-app-log')?.addEventListener('click', () => nativeHandler('showLog')?.postMessage(null));
  $('.help-support-link').onclick = e => { e.preventDefault(); $('#support-rail')?.scrollIntoView({block: 'start'}); $('#support-rail textarea[name=message]')?.focus(); };
  window.supportHelp?.($('#help-page'));    // Contact support and Your requests (ui/support.js)
}
