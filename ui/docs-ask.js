/* Ask the Librarian on Docs and Market (docs/librarian.md): a right rail on desktop, open unless this viewer
   hid it (remembered in this browser), and a full-screen sheet on a phone.

   On Docs, type a question and matching docs, internal and linked, show at once from the search that comes
   back with POST /api/v2/docs/ask; the Librarian's answer then streams in from the conversation it
   went to (its snapshot, read again as live events say it changed), with clickable citations. Only the person who asked
   can read that conversation. On Market, the question is answered from the market graph as it is now
   (POST /api/v2/market/ask): the answer and the organizations, people and pages it drew on, which open the
   note and light up on the graph. Each page keeps its own thread.

   window.openDocsAsk(question?) opens it; with a question it asks it straight away. The page's own
   [data-librarian-open] button opens it again; window.syncLibrarianRail(settled) is called after each route.

   The Librarian cites [Internal doc · Title](doc:<id>) and [Linked · host](https://...). `doc:<id>` is
   turned into the Docs page route here and nowhere else (docHref). Its text is bot text: it only ever
   reaches the page through safeMd, the sanitizing renderer, never as raw HTML. Globals used from
   ui/app: get, post, esc, safeMd, API, toast. */
(function () {
  const css = `
.dask-backdrop{position:fixed;inset:0;z-index:1500;background:rgba(10,14,18,.4)}
.dask{position:fixed;top:0;right:0;bottom:0;z-index:1501;width:min(460px,100vw);display:flex;flex-direction:column;background:var(--surface);color:var(--ink);border-left:1px solid var(--line);animation:dask-in .18s ease-out}
html.demo .dask{top:var(--demo-h)}
@keyframes dask-in{from{transform:translateX(24px);opacity:0}to{transform:none;opacity:1}}
@media (prefers-reduced-motion:reduce){.dask{animation:none}.dask-think i{animation:none}}
.dask-head{display:flex;align-items:center;gap:6px;min-height:57px;padding:10px 8px 10px 14px;border-bottom:1px solid var(--line)}
.dask-head .nav-icon{font-size:20px;color:var(--accent)}
.dask-head h2{font-size:15px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dask-head small{display:block;color:var(--muted);font-size:12px;font-weight:400}
.dask-head .spacer{flex:1}
.dask-new{font-size:12.5px;padding:4px 10px;white-space:nowrap}
.dask-close{display:inline-flex;align-items:center;justify-content:center;min-width:32px;height:32px;background:none;border:0;border-radius:6px;padding:0 6px;font-size:16px;cursor:pointer;color:var(--muted)}
.dask-close .nav-icon{font-size:20px;color:inherit}
.dask-close:focus-visible,.dask-new:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.dask-close:hover{background:var(--surface2);color:var(--ink)}
.dask-history{flex:none;border-bottom:1px solid var(--line);padding:8px 16px;font-size:12px}
.dask-history summary{cursor:pointer;color:var(--muted)}
.dask-history-list{display:flex;flex-direction:column;gap:4px;max-height:180px;overflow-y:auto;margin-top:8px}
.dask-history-list button{display:flex;gap:8px;align-items:center;text-align:left;width:100%;font-size:12px}
.dask-history-list button span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dask-history-list time{flex:none;color:var(--muted);font-size:11px}
.dask-history-list p{margin:0;color:var(--muted)}
.dask-body{flex:1 1 auto;min-height:0;overflow-y:auto;padding:14px 16px;display:flex;flex-direction:column;gap:16px}
.dask-empty{color:var(--muted);font-size:13.5px}
.dask-empty a{white-space:nowrap}
.dask-hints{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.dask-hints button{font-size:12.5px}
.dask-body>section{display:flex;flex-direction:column;gap:8px}
.dask-q{align-self:flex-end;max-width:92%;background:var(--surface2);border:1px solid var(--line);border-radius:10px;border-bottom-right-radius:3px;padding:8px 12px;overflow-wrap:anywhere;white-space:pre-wrap}
.dask-label{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:0 0 6px}
.dask-results{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.dask-results li{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--bg)}
.dask-results a{font-weight:600;overflow-wrap:anywhere}
.dask-results .dask-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.dask-results p{margin:3px 0 0;font-size:12.5px;color:var(--muted);overflow-wrap:anywhere}
.dask-badge{flex:none;font-size:11px;padding:1px 7px;border-radius:10px;background:var(--idle-bg);color:var(--muted);white-space:nowrap}
.dask-badge.internal{background:var(--ok-bg);color:var(--ok)}
.dask-badge.linked{background:var(--run-bg);color:var(--run)}
.dask-a{line-height:1.5;overflow-wrap:anywhere}
.dask-a p{margin:0 0 8px}.dask-a p:last-child{margin-bottom:0}
.dask-a ul,.dask-a ol{margin:4px 0 8px;padding-left:20px}
.dask-a a{text-decoration:underline;text-underline-offset:2px}
.dask-a a.dask-cite{display:inline-block;font-size:12px;line-height:1.35;padding:0 7px;border-radius:10px;border:1px solid var(--line);background:var(--bg);text-decoration:none;color:var(--accent);vertical-align:baseline}
.dask-a a.dask-cite:hover{background:var(--surface2)}
.dask-a code{font-family:var(--mono);font-size:12.5px}
.dask-think{color:var(--muted);font-size:13px}
.dask-think i{display:inline-block;width:5px;height:5px;margin-left:3px;border-radius:50%;background:currentColor;animation:dask-dot 1.2s infinite ease-in-out}
.dask-think i:nth-child(3){animation-delay:.15s}.dask-think i:nth-child(4){animation-delay:.3s}
@keyframes dask-dot{0%,80%,100%{opacity:.25}40%{opacity:1}}
.dask-err{color:var(--fail);font-size:13px}
.dask-form{display:flex;gap:8px;align-items:flex-end;padding:12px 16px calc(12px + env(safe-area-inset-bottom));border-top:1px solid var(--line);background:var(--surface)}
.dask-form textarea{flex:1 1 auto;min-width:0;min-height:44px;max-height:160px;resize:vertical;font-size:16px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg)}
.dask-form textarea:focus{outline:2px solid var(--accent);outline-offset:0}
.dask-form .primary{min-height:44px}
.dask-off{display:flex;flex-direction:column;gap:10px;align-items:flex-start}
.dask-cites{display:flex;flex-wrap:wrap;gap:4px 6px;margin:2px 0 0}
.dask-cites a.dask-cite{display:inline-block;font-size:12px;line-height:1.35;padding:0 7px;border-radius:10px;border:1px solid var(--line);background:var(--bg);text-decoration:none;color:var(--accent)}
.dask-cites a.dask-cite:hover{background:var(--surface2)}
@media (max-width:760px){
  .dask{inset:0;width:auto;border-left:0;box-shadow:none;height:100dvh}
  html.demo .dask{height:calc(100dvh - var(--demo-h))}
  .dask-backdrop{display:none}
}
/* Desktop: a rail beside the page, which gives up its room on the right. */
@media(min-width:761px){
  #main.librarian-open{--librarian-width:clamp(300px,26vw,360px);margin-right:var(--librarian-width);max-width:min(1180px,calc(100% - var(--librarian-width)))}
  #main.librarian-open.market-layout{max-width:calc(100% - var(--librarian-width))}
  #main.librarian-open [data-librarian-open]{display:none}
  #main.librarian-open .docs-heading{grid-template-columns:auto minmax(180px,1fr) auto}
  #main.librarian-open .docs-workspace{grid-template-columns:minmax(240px,300px) minmax(0,1fr);gap:20px}
  .dask{width:clamp(300px,26vw,360px);box-shadow:none;animation:none}
  body:has(#main.librarian-open) .toast{right:calc(clamp(300px,26vw,360px) + 20px)}
}
/* The market's three columns need room the rail takes: under 1600px the graph moves below the note. */
@media(min-width:761px) and (max-width:1599px){
  #main.librarian-open .market-shell{grid-template-columns:184px minmax(0,1fr);grid-template-rows:minmax(0,1fr) minmax(200px,34vh)}
  #main.librarian-open .market-graph{grid-column:1/-1;border-top:1px solid #2a2a2a}
  #main.librarian-open .market-read{padding:22px 24px 16px}
}
@media(min-width:761px) and (max-width:1100px){
  #main.librarian-open .docs-heading{display:flex;flex-wrap:wrap}
  #main.librarian-open .docs-search-field{flex-basis:100%}
  #main.librarian-open .docs-actions{flex-wrap:wrap;max-width:100%}
  #main.librarian-open .docs-workspace{display:block;overflow:auto}
  #main.librarian-open .docs-browser{border-right:0;padding-right:0}
  #main.librarian-open .docs-reader{overflow:visible}
  #main.librarian-open .docs-workspace.has-selection:not(.searching) .docs-browser,
  #main.librarian-open .docs-workspace.has-selection.searching .docs-reader,
  #main.librarian-open .docs-workspace:not(.has-selection) .docs-reader{display:none}
  #main.librarian-open .docs-back{display:inline-block}
}`;

  // What each page keeps between opening and closing the panel: the conversation, so a follow-up has context,
  // and what was asked and answered, so closing it does not lose the thread. Docs and Market each have their own.
  const sessions = {docs: {conversationId: '', turns: []}, market: {turns: []}};
  const where = () => /^#\/market(?:[/?]|$)/.test(location.hash) ? 'market' : 'docs';
  const HINTS = {docs: ['How do we handle a refund?', 'Who helps new hires?', 'Where is the pricing?'],
    market: ['Who competes with us?', 'What changed this week?', 'Which channels matter?']};
  const PLACEHOLDER = {docs: 'Ask about your docs…', market: 'Ask about the market…'};
  const TURN_LIMIT = 50;
  let ctx = where(), panel = null, opener = null, es = null, poll = null, busy = false;
  let conversations = [], nextOffset = null, historyRequest = 0, reopenRequest = 0, openingConversation = false;
  const session = () => sessions[ctx];

  function drawHistory() {
    const history = panel?.querySelector('.dask-history');
    if (!history) return;
    history.hidden = ctx !== 'docs';
    if (history.hidden) return;
    const previous = conversations.filter(room => room.id !== sessions.docs.conversationId);
    history.querySelector('.dask-history-list').innerHTML = previous.map(room => {
      const date = new Date(room.last_message_at || room.created);
      const stamp = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
      return `<button type="button" class="ghost" data-docs-conversation="${esc(room.id)}"${busy || openingConversation ? ' disabled' : ''}><span>${esc(room.title || 'Docs')}</span><time>${esc(stamp)}</time></button>`;
    }).join('') + (nextOffset != null ? '<button type="button" class="ghost" data-conversations-more>More</button>' : '')
      || '<p>No previous conversations</p>';
    history.querySelectorAll('[data-docs-conversation]').forEach(button => {
      button.onclick = () => reopenConversation(button.dataset.docsConversation);
    });
    const more = history.querySelector('[data-conversations-more]');
    if (more) more.onclick = () => { more.disabled = true; void loadHistory(nextOffset); };
  }

  async function loadHistory(offset = 0) {
    if (!panel || ctx !== 'docs') return;
    const at = panel, request = ++historyRequest;
    try {
      const result = await get('/v2/librarian/conversations?offset=' + offset);
      if (panel !== at || ctx !== 'docs' || request !== historyRequest) return;
      conversations = offset ? [...conversations, ...(result.conversations || [])] : result.conversations || [];
      nextOffset = result.next_offset ?? null;
      drawHistory();
    } catch (error) {
      if (panel !== at || ctx !== 'docs' || request !== historyRequest) return;
      const list = panel.querySelector('.dask-history-list');
      list.innerHTML = `<p role="alert">${esc(error.message)}</p><button type="button" class="ghost">Retry</button>`;
      list.querySelector('button').onclick = () => loadHistory(offset);
    }
  }

  async function reopenConversation(id) {
    if (busy || openingConversation || !panel || ctx !== 'docs') return;
    const at = panel, s = sessions.docs, request = ++reopenRequest;
    const current = () => panel === at && ctx === 'docs' && sessions.docs === s && request === reopenRequest;
    openingConversation = true; draw();
    try {
      const snapshot = await get('/v2/conversations/' + encodeURIComponent(id) + '/snapshot');
      if (!current()) return;
      await post('/v2/librarian/conversations/' + encodeURIComponent(id) + '/reopen', {});
      if (!current()) return;
      stop();
      s.conversationId = id;
      const messages = snapshot.messages || [];
      s.turns = messages.filter(m => m.from_actor === 'human:' + S.me.id).slice(-TURN_LIMIT).map(message => {
        const reply = messages.find(m => m.from_actor === 'bot:librarian' && m.in_reply_to === message.id);
        const execution = snapshot.execution;
        const pending = execution?.message_id === message.id && ['queued', 'leased', 'running'].includes(execution.state);
        const error = !reply && !pending
          ? (execution?.message_id === message.id ? execution.label : '') || 'No reply saved' : '';
        return {question: message.body, ctx: 'docs', answer: reply ? reply.body || '' : null, error,
          conversationId: id, messageId: message.id};
      });
      openingConversation = false;
      resume();
      panel.querySelector('.dask-history').open = false;
      panel.querySelector('textarea').focus();
      void loadHistory();
    } catch (error) { if (current()) toast?.(error.message); }
    finally { if (current()) { openingConversation = false; draw(); } }
  }

  const docHref = id => '#/docs/' + encodeURIComponent(id);
  const KINDS = {website: 'Website', google_drive: 'Google Drive', google_doc: 'Google Doc', notion: 'Notion', github: 'GitHub', other: 'Link'};
  const clean = text => String(text || '').replace(/<\/?(?:b|mark|em|strong)>/gi, '');
  const host = url => { try { return new URL(url).host; } catch { return url; } };

  function style() {
    if (document.getElementById('dask-style')) return;
    const el = document.createElement('style');
    el.id = 'dask-style';
    el.textContent = css;
    document.head.appendChild(el);
  }

  // Desktop on Docs or Market: a rail beside the page. Anywhere else, and on a phone: a sheet over it.
  const desktop = () => matchMedia('(min-width: 761px)').matches;
  const railPage = () => /^#\/(docs|market)(?:[/?]|$)/.test(location.hash);
  const asRail = () => desktop() && railPage();
  // Hidden or shown, per viewer, in this browser only; a browser that will not store it shows the rail.
  const hiddenKey = () => 'tico.librarian.collapsed.' + (S.me?.id || '');
  function railHidden() { try { return localStorage.getItem(hiddenKey()) === '1'; } catch { return false; } }
  function rememberHidden(value) { try { localStorage.setItem(hiddenKey(), value ? '1' : '0'); } catch { /* not kept */ } }
  const openButton = () => $('#main')?.querySelector('[data-librarian-open]');

  // The Librarian's markdown, sanitized. `doc:<id>` becomes the docs route first; afterwards each citation
  // is a chip, an in-app link opens in place (and closes the sheet on a phone), everything else opens in a new tab.
  function answerHtml(text) {
    return safeMd(String(text || '').replace(/\]\(doc:([^)\s]+)\)/g, (_, id) => '](' + docHref(id) + ')'));
  }
  function decorate(root) {
    root.querySelectorAll('a').forEach(a => {
      const href = a.getAttribute('href') || '';
      if (/^(Internal doc|Linked|Tico manual) · /.test(a.textContent)) a.classList.add('dask-cite');
      if (href.startsWith('#/')) { a.removeAttribute('target'); a.addEventListener('click', () => { if (!asRail()) close(false); }); }
    });
  }

  function resultsHtml(results) {
    if (!results?.length) return '<p class="dask-empty" data-no-results>No matching docs, so the Librarian will look further.</p>';
    return `<ul class="dask-results" data-results>${results.map(r => r.type === 'linked'
      ? `<li data-type="linked"><div class="dask-line"><span class="dask-badge linked">Linked · ${esc(KINDS[r.kind] || 'Link')}</span><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title || host(r.url))}</a></div>${
          r.description ? `<p>${esc(r.description)}</p>` : `<p>${esc(host(r.url))}</p>`}</li>`
      : r.type === 'manual'
      ? `<li data-type="manual"><div class="dask-line"><span class="dask-badge">Tico manual</span><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a></div>${r.excerpt ? `<p>${esc(clean(r.excerpt))}</p>` : ''}</li>`
      : `<li data-type="internal"><div class="dask-line"><span class="dask-badge internal">Internal doc</span><a href="${esc(docHref(r.id))}">${esc(r.title || r.path)}</a></div>${
          r.excerpt ? `<p>${esc(clean(r.excerpt))}</p>` : ''}</li>`).join('')}</ul>`;
  }

  function turnHtml(t, i) {
    if (t.market) {
      const state = t.error ? `<p class="dask-err" role="alert">${esc(t.error)}</p>`
        : t.answer != null ? '' : '<p class="dask-think" data-thinking role="status">Reading the market<i></i><i></i><i></i></p>';
      return `<section data-turn="${i}"><div class="dask-q">${esc(t.question)}</div>
        ${t.answer != null ? `<h3 class="dask-label">Librarian</h3><div class="dask-a md" data-answer>${safeMd(t.answer)}</div>` : ''}
        ${t.cites?.length ? `<p class="dask-cites">${t.cites.map(c => `<a class="dask-cite" href="${esc(c.href)}">${esc(c.label)}</a>`).join('')}</p>` : ''}
        ${state}</section>`;
    }
    const state = t.error ? `<p class="dask-err" role="alert">${esc(t.error)}</p>`
      : t.answer != null ? ''
      : t.noProvider ? '<p class="dask-empty" data-no-provider role="status">The Librarian needs an AI provider. <a href="#/settings" data-gs-tab="providers">Settings &gt; AI providers</a></p>'
      : `<p class="dask-think" data-thinking role="status">The Librarian is reading the docs<i></i><i></i><i></i></p>`;
    return `<section data-turn="${i}"><div class="dask-q">${esc(t.question)}</div>
      ${t.results ? `<label class="dask-label">Matching docs <select data-collection="${i}" aria-label="Search collection">${[["all", "All docs"], ["team", "Team docs"], ["manual", "Tico manual"]].map(([value, label]) => `<option value="${value}"${(t.collection || "all") === value ? " selected" : ""}>${label}</option>`).join("")}</select></label>${resultsHtml(t.results)}` : ''}
      ${(t.answer ?? t.live) ? `<h3 class="dask-label">Librarian</h3><div class="dask-a md" data-answer aria-live="polite">${answerHtml(t.answer ?? t.live)}</div>` : ''}
      ${state}</section>`;
  }

  function draw() {
    if (!panel) return;
    const body = panel.querySelector('.dask-body');
    const atEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    const turns = session().turns;
    body.innerHTML = turns.length ? turns.map(turnHtml).join('')
      : `<div class="dask-empty">
          <div class="dask-hints">${HINTS[ctx].map(h => `<button class="ghost" type="button" data-hint="${esc(h)}">${esc(h)}</button>`).join('')}</div></div>`;
    decorate(body);
    body.querySelectorAll('[data-hint]').forEach(b => { b.onclick = () => ask(b.dataset.hint); });
    body.querySelectorAll('[data-collection]').forEach(select => {
      select.onchange = async () => {
        const turn = turns[Number(select.dataset.collection)], collection = select.value;
        turn.collection = collection;
        try {
          const found = await get('/v2/docs/search?limit=8&collection=' + collection + '&q=' + encodeURIComponent(turn.question));
          if (turn.collection === collection) { turn.results = found.results || []; draw(); }
        } catch (e) { toast?.(e.message || 'Search failed'); }
      };
    });
    if (atEnd || busy) body.scrollTop = body.scrollHeight;
    const button = panel.querySelector('.dask-form button');
    if (button) button.disabled = busy || openingConversation;
    panel.querySelector('[data-new-chat]').disabled = busy || openingConversation;
    panel.querySelector('textarea').placeholder = PLACEHOLDER[ctx];
    drawHistory();
  }

  function stop() {
    try { es?.(); } catch { /* already released */ }
    es = null;
    clearInterval(poll); poll = null;
  }

  // The answer is the Librarian's message in reply to this question; while it works, what it has written
  // so far is the run's text. Anything else in the conversation (an earlier turn) is not this answer.
  // With no AI provider the question stays saved and queued: the rail says so, keeps watching, and takes
  // another question.
  function follow(turn) {
    const apply = snapshot => {
      const reply = (snapshot.messages || []).find(m => m.in_reply_to === turn.messageId && m.from_actor === 'bot:librarian');
      const x = snapshot.execution;
      if (reply) { turn.answer = reply.body || ''; turn.noProvider = false; busy = false; stop(); }
      else {
        turn.noProvider = x?.state === 'queued' && x.readiness_reason === 'missing_provider';
        busy = !turn.noProvider;
        if (x && x.state !== 'completed' && x.text) turn.live = x.text;
      }
      draw();
    };
    const cid = turn.conversationId;
    let reading = false, again = false;
    const read = async () => {
      if (reading) { again = true; return; }
      reading = true;
      try { apply(await get('/v2/conversations/' + encodeURIComponent(cid) + '/snapshot')); } catch { /* the next change reads again */ }
      reading = false;
      if (again) { again = false; void read(); }
    };
    // The page's live events (ui/app/live.js): a change to this conversation reads its snapshot again. Without
    // them, a poll.
    const live = window.liveEvents;
    if (!live || !live.available()) { if (!poll) poll = setInterval(read, 2500); return; }
    const mine = d => d.conversation_id === cid;
    let timer = 0;
    const soon = () => { clearTimeout(timer); timer = setTimeout(read, 150); };
    const offs = [live.follow(cid), live.on('messages', d => { if (mine(d)) soon(); }),
                  live.on('runs', d => { if (mine(d)) soon(); }), live.on('reset', soon)];
    es = () => { clearTimeout(timer); offs.forEach(off => off()); };
    void read();                       // what happened before the stream followed it
  }

  // Market: the graph as it is now answers, and what it drew on lights up on the graph (ui/market-page.js).
  async function askMarket(turn) {
    try {
      const out = await post('/v2/market/ask', {question: turn.question});
      const ids = [...new Set((out.citations || []).filter(c => c.kind === 'entity').map(c => c.id))];
      turn.answer = String(out.answer || '').replace(/\s*\[[a-z]+:[^\]]+\]/gi, '');
      turn.cites = ids.map(id => ({href: '#/market?note=' + encodeURIComponent(id), label: window.marketName?.(id) || id})).filter(c => c.label);
      window.marketCite?.(ids);
    } catch (e) {
      turn.error = (e && e.message) || 'The Librarian could not answer.';
    }
    if (turn.ctx === ctx) busy = false;
    draw();
  }

  async function ask(question) {
    question = String(question || '').trim();
    if (!question || busy || openingConversation || !panel) return;
    busy = true;
    const s = session();
    const turn = {question, ctx, results: null, answer: null, live: '', error: '', conversationId: s.conversationId, messageId: ''};
    if (ctx === 'market') turn.market = true;
    s.turns.push(turn);
    if (s.turns.length > TURN_LIMIT) s.turns.shift();
    draw();
    if (turn.market) return askMarket(turn);
    try {
      // The first question of a page session, and the first after New chat, opens a new conversation, so what
      // the Librarian remembers is what the thread on screen shows; follow-ups continue it.
      const sent = await post('/v2/docs/ask', {question, ...(s.conversationId ? {conversation_id: s.conversationId} : {new_conversation: true})});
      s.conversationId = turn.conversationId = sent.conversation_id;
      turn.messageId = sent.message_id;
      turn.results = sent.results || [];
      draw();
      void loadHistory();
      stop();
      if (panel && ctx === 'docs') follow(turn);
    } catch (e) {
      busy = false;
      const code = e?.body?.error?.code;
      if (code === 'conversation') s.conversationId = '';
      turn.error = code === 'librarian_off' ? 'The Librarian is off.' : (e && e.message) || 'That did not go through.';
      draw();
      if (code === 'librarian_off') offNotice();
    }
  }

  async function offNotice() {
    let info;
    try { info = await get('/v2/librarian'); } catch { return; }
    if (!panel || info.available) return;
    const note = document.createElement('div');
    note.className = 'dask-off';
    note.dataset.off = '';
    note.innerHTML = `<p><strong>The Librarian is ${info.state === 'missing' ? 'not set up yet' : esc(info.state)}.</strong> ${info.can_turn_on
      ? 'Turning it on adds it to a computer and it starts answering.' : 'Ask the owner of this team to turn it on.'}</p>
      ${info.can_turn_on ? '<button class="primary" type="button" data-turn-on>Turn on the Librarian</button><span class="dask-empty" data-turn-status role="status"></span>' : ''}`;
    panel.querySelector('.dask-body').append(note);
    const on = note.querySelector('[data-turn-on]');
    if (on) on.onclick = async () => {
      on.disabled = true;
      const status = note.querySelector('[data-turn-status]');
      try {
        const r = await post('/v2/librarian/turn-on', {});
        if (r.state === 'active') { note.remove(); session().turns.pop(); draw(); toast?.('The Librarian is on'); }
        else status.textContent = 'It is set up, but needs a computer with a model before it can answer (Settings).';
      } catch (e) { on.disabled = false; status.textContent = e.message; }
    };
  }

  // Rail or sheet: the role, the backdrop and the close button follow. A rail is part of the page (Escape leaves
  // it alone, and its button hides it); a sheet is a dialog that Escape and its ✕ close.
  function setMode() {
    if (!panel) return;
    const rail = asRail();
    panel.setAttribute('role', rail ? 'complementary' : 'dialog');
    if (rail) panel.removeAttribute('aria-modal'); else panel.setAttribute('aria-modal', 'true');
    const x = panel.querySelector('.dask-close');
    x.setAttribute('aria-label', rail ? 'Hide Ask the Librarian' : 'Close Ask the Librarian');
    x.title = rail ? 'Hide' : 'Close';
    x.innerHTML = rail ? '<span class="nav-icon" aria-hidden="true">chevron_right</span>' : '✕';
    const backdrop = document.querySelector('.dask-backdrop');
    if (rail) backdrop?.remove();
    else if (!backdrop) {
      const el = document.createElement('div');
      el.className = 'dask-backdrop';
      el.onclick = () => close();
      panel.before(el);
    }
    $('#main')?.classList.toggle('librarian-open', rail);
  }

  // hide: the viewer hid the rail, so it stays hidden on Docs and Market until they open it again.
  function close(returnFocus = true, hide = false) {
    if (!panel) return;
    stop();
    busy = false;
    openingConversation = false;
    historyRequest++;
    reopenRequest++;
    document.removeEventListener('keydown', onKey, true);
    document.querySelector('.dask-backdrop')?.remove();
    $('#main')?.classList.remove('librarian-open');
    if (hide) rememberHidden(true);
    panel.remove();
    panel = null;
    if (returnFocus) (openButton() || opener)?.focus?.();
  }
  function onKey(ev) {
    if (ev.key === 'Escape' && panel && panel.getAttribute('role') === 'dialog') { ev.stopPropagation(); close(); }
  }

  function open(question, focus = true) {
    style();
    if (!panel) {
      ctx = where();
      opener = document.activeElement;
      panel = document.createElement('aside');
      panel.className = 'dask';
      panel.setAttribute('aria-labelledby', 'dask-title');
      panel.dataset.docsAsk = '';
      panel.innerHTML = `<header class="dask-head"><span class="nav-icon" aria-hidden="true">auto_awesome</span>
          <h2 id="dask-title">Ask the Librarian</h2><span class="spacer"></span>
          <button class="ghost dask-new" type="button" data-new-chat>New chat</button>
          <button class="dask-close" type="button"></button></header>
        <details class="dask-history"><summary>Previous conversations</summary><div class="dask-history-list"></div></details>
        <div class="dask-body"></div>
        <form class="dask-form"><textarea rows="2" maxlength="4000" aria-label="Your question" required></textarea>
        <button class="primary" type="submit">Ask</button></form>`;
      document.body.append(panel);
      setMode();
      if (asRail()) rememberHidden(false);
      panel.querySelector('.dask-close').onclick = () => close(true, asRail());
      panel.querySelector('[data-new-chat]').onclick = () => {
        if (busy || openingConversation) return;
        stop(); sessions[ctx] = ctx === 'docs' ? {conversationId: '', turns: []} : {turns: []}; draw(); void loadHistory(); panel.querySelector('textarea').focus();
      };
      const box = panel.querySelector('textarea');
      box.onkeydown = ev => { if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); panel.querySelector('form').requestSubmit(); } };
      panel.querySelector('form').onsubmit = ev => {
        ev.preventDefault();
        const text = box.value;
        if (text.trim() && !busy && !openingConversation) { box.value = ''; ask(text); }
      };
      document.addEventListener('keydown', onKey, true);
      resume();
      void loadHistory();
    }
    if (focus) panel.querySelector('textarea').focus();
    if (question && question.trim()) ask(question);
  }
  // Draw this page's thread; a docs turn that was still running when the panel closed (or the page changed)
  // is picked up again.
  function resume() {
    const last = session().turns.at(-1);
    busy = false;
    draw();
    if (ctx === 'docs' && last && last.answer == null && !last.error && last.messageId) { busy = true; draw(); follow(last); }
    else if (ctx === 'market' && last && last.answer == null && !last.error) { busy = true; draw(); }
  }

  // After every route (ui/app/router.js) and when the window is resized. On Docs and Market on a desktop the
  // rail opens unless this viewer hid it; a phone gets the page's button only. `settled`: the page has drawn
  // (the market draws later than the route), so a page with no button (an empty market) has no rail.
  window.syncLibrarianRail = settled => {
    if (!railPage()) { close(false); return; }
    style();
    const button = openButton();
    if (!button) { if (settled === true) close(false); return; }
    button.onclick = () => open();
    if (panel && ctx !== where()) { stop(); openingConversation = false; historyRequest++; reopenRequest++; ctx = where(); resume(); void loadHistory(); }
    if (desktop()) {
      if (panel) setMode();
      else if (!railHidden()) open(undefined, false);
    } else if (panel?.getAttribute('role') === 'complementary') close(false);
  };
  window.addEventListener('resize', () => window.syncLibrarianRail());
  window.openDocsAsk = open;
  // The old entry point, until every page that called it says openDocsAsk.
  window.openDocsChat = () => open();
})();
