// The Docs page, against an in-memory stand-in for the docs API (fixtures only, no server): write a doc,
// edit it (with the version-conflict message), read its history and restore an old version, lock it
// (only owners and bot administrators), add, open, edit and remove a linked doc, search across both
// kinds, and import a file.
// TICO_SCREENSHOT_DIR=<dir> saves the review screenshots (both themes, and a phone).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ui = path.join(__dirname, '..');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const shots = process.env.TICO_SCREENSHOT_DIR;
const shot = async (page, name) => {
  if (!shots) return;
  for (const scheme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme: scheme});
    await page.screenshot({animations: 'disabled', path: path.join(shots, `docs-${name}-${scheme}.png`)});
  }
  await page.emulateMedia({colorScheme: 'light'});
};

const NAMES = {'human:ana': 'Ana', 'human:ben': 'Ben', 'bot:ops': 'Ops'};
const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1280, height: 860}, serviceWorkers: 'block', permissions: []});
    // ---- the stand-in server
    let seq = 10;
    let me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', cloud: true, bot_admin: true};
    const docs = new Map(), versions = new Map(), linked = [], requests = [];
    const stamp = (doc, actor, note, minutes) => {
      versions.get(doc.id).unshift({version: doc.version, title: doc.title, path: doc.path, body: doc.body, actor, created: ago(minutes), note});
    };
    const add = (id, docPath, title, body, {locked = false, history = []} = {}) => {
      const doc = {id, path: docPath, title, body, locked, version: 1, created_by: 'human:ben', created: ago(600), updated_by: 'human:ben', updated: ago(600), archived: false};
      docs.set(id, doc); versions.set(id, []); stamp(doc, 'human:ben', 'Created', 600);
      for (const [text, actor, note] of history) { doc.version += 1; doc.body = text; doc.updated_by = actor; doc.updated = ago(90); stamp(doc, actor, note, 90); }
      return doc;
    };
    add('doc-000000000001', 'support/refund-policy.md', 'Refund policy', 'Refund within 14 days.', {history: [['# Refund policy\n\nRefund within **30 days**.', 'bot:ops', 'Longer window']]});
    add('doc-000000000002', 'pricing.md', 'Pricing and plans', '# Pricing and plans\n\n| Plan | Price |\n|---|---|\n| Starter | $35 |\n\nSee [refunds](support/refund-policy.md).');
    const addLinked = (raw, title, kind, description, by = 'human:ana') => {
      const url = /^https?:\/\//.test(raw) ? raw : 'https://' + raw;
      const row = {id: 'link-' + (++seq), title, url, kind, host: new URL(url).hostname.replace(/^www\./, ''), description, added_by: by, created: ago(300), updated: ago(300)};
      linked.push(row); return row;
    };
    addLinked('https://help.acme.example/refunds', 'Help centre', 'website', 'What customers read');
    addLinked('https://drive.google.com/drive/folders/brand', 'Brand assets', 'google_drive', 'Logos and the tone guide', 'human:ben');
    const view = doc => ({...doc, updated_by_name: NAMES[doc.updated_by] || doc.updated_by, created_by_name: NAMES[doc.created_by] || doc.created_by});
    const row = doc => ({id: doc.id, path: doc.path, title: doc.title, updated: doc.updated, updated_by: doc.updated_by, updated_by_name: NAMES[doc.updated_by], locked: doc.locked, version: doc.version});
    const linkView = l => ({...l, added_by_name: NAMES[l.added_by]});
    const sourceView = l => ({...linkView(l), provider: l.kind, repo: '', reference: '', reference_override: '', path: '', status: 'link_only', last_checked: null, doc_count: 0, skipped: 0, error_code: ''});
    const kindOf = raw => { const host = new URL(/^https?:\/\//.test(raw) ? raw : 'https://' + raw).hostname; return /notion\.so$/.test(host) ? 'notion' : /github\.com$/.test(host) ? 'github' : 'website'; };
    const titleOf = raw => { const u = new URL(/^https?:\/\//.test(raw) ? raw : 'https://' + raw); return u.hostname.replace(/^www\./, '') + u.pathname.replace(/\/$/, ''); };
    let started = {empty: {docs: false}, cards: []};
    let librarianQueued = false;   // the Librarian's run waits on a missing AI provider
    let chatSequence = 0, reopenBlocked = false;
    const chats = [
      {id: 'docs-earlier', title: 'Who helps new hires?', created: ago(90), closed_at: ago(60)},
      {id: 'docs-archived', title: 'How do refunds work?', created: ago(300), closed_at: ago(200)},
    ];
    const externalCitation = 'https://github.com/acme/handbook/blob/' + 'a'.repeat(40) + '/docs/refunds.md?plain=1#L3-L4';
    const answer = 'See [Internal doc · Refund policy](doc:doc-000000000001). [External · Refunds — Policy](' + externalCitation + ')';
    const chatMessages = new Map([
      ['docs-earlier', [{id: 'earlier-question', from_actor: 'human:ana', body: 'Who helps new hires?'}]],
      ['docs-archived', [{id: 'old-question', from_actor: 'human:ana', body: 'How do refunds work?'},
        {id: 'old-answer', in_reply_to: 'old-question', from_actor: 'bot:librarian', body: 'Refunds take 30 days. ' + answer + '<script>window.docsUnsafe = true</script>'}]],
    ]);
    const outside = (id, body, actor = 'human:ben') => {       // someone else saves while this person is editing
      const doc = docs.get(id); doc.version += 1; doc.body = body; doc.updated_by = actor; doc.updated = ago(0); stamp(doc, actor, 'Their edit', 0);
    };

    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const problem = (code, detail, status, extra = {}) => json({error: {code, detail, retryable: false, ...extra}}, status);
      if (url.origin !== 'https://tico-ui.test') return route.fulfill({contentType: 'text/html', body: '<p>an external page</p>'});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(ui, p) : '';
      if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
      if (p === '/api/me') return json(me);
      if (p === '/api/employees') return json([]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      if (p === '/api/v2/updates/unread') return json({unread: 0});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/librarian/conversations') {
        const offset = Number(url.searchParams.get('offset') || 0);
        return json({conversations: chats.slice(offset, offset + 1), next_offset: offset + 1 < chats.length ? offset + 1 : null});
      }
      const reopen = /^\/api\/v2\/librarian\/conversations\/([^/]+)\/reopen$/.exec(p);
      if (reopen) {
        requests.push({api: 'librarian/reopen', id: reopen[1]});
        if (reopenBlocked) return problem('busy', 'Wait for this conversation’s current turn to finish', 409);
        chats.forEach(chat => { chat.closed_at = chat.id === reopen[1] ? null : chat.closed_at || ago(0); });
        return json({conversation_id: reopen[1]});
      }
      if (p === '/api/v2/librarian/research') {
        const body = req.postDataJSON(); requests.push({api: 'librarian/research', body});
        return json({task_id: 'task-research', bot: 'librarian', kind: 'doc', target: body.target});
      }
      if (p === '/api/v2/docs/ask') {
        const body = req.postDataJSON(); requests.push({api: 'docs/ask', body});
        const id = body.conversation_id || 'librarian-chat-' + (++chatSequence);
        if (body.new_conversation) {
          chats.forEach(chat => { chat.closed_at ||= ago(0); });
          chats.unshift({id, title: body.question, created: ago(0), closed_at: null});
        }
        const messageId = 'ask-docs-' + requests.length;
        chatMessages.set(id, [...(chatMessages.get(id) || []), {id: messageId, from_actor: 'human:ana', body: body.question},
          ...(!librarianQueued ? [{id: 'answer-' + messageId, in_reply_to: messageId, from_actor: 'bot:librarian', body: answer}] : [])]);
        return json({conversation_id: id, message_id: messageId, results: [{id: 'doc-000000000001', title: 'Refund policy', type: 'internal'}]});
      }
      const chatRead = /^\/api\/v2\/conversations\/([^/]+)\/(watch|snapshot)$/.exec(p);
      if (chatRead && chatMessages.has(chatRead[1])) {
        const messages = chatMessages.get(chatRead[1]);
        const snapshot = {messages, execution: librarianQueued ? {message_id: messages.at(-1).id, state: 'queued',
          readiness_reason: 'missing_provider', label: 'Saved — no AI provider is chosen'} : null};
        if (chatRead[2] === 'snapshot') return json(snapshot);
        return route.fulfill({contentType: 'text/event-stream', body: 'event: snapshot\ndata: ' + JSON.stringify(snapshot) + '\n\n'});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      // A one-row market, so the Market page draws and Ask the Librarian answers from its graph.
      if (p === '/api/v2/market/entities') return json({entities: [{id: 'company/rival', type: 'company', name: 'Rival', status: 'active'}]});
      if (p === '/api/v2/market/entities/company/rival') return json({entity: {id: 'company/rival', type: 'company', name: 'Rival', summary: 'Sells to studios.'}, edges: [], evidence: []});
      if (p === '/api/v2/market/graph') return json({entities: [{id: 'company/rival', type: 'company', name: 'Rival', degree: 0}], edges: []});
      if (p === '/api/company-docs') return json({documents: []});
      if (p === '/api/v2/market/ask') {
        requests.push({api: 'market/ask', body: req.postDataJSON()});
        return json({answer: 'Rival competes on price [entity:company/rival].', citations: [{kind: 'entity', id: 'company/rival'}], excerpts: []});
      }
      if (p === '/api/v2/setup/getting-started' && method === 'GET') return json({items: [], done: 0, total: 0, complete: true, dismissed: true, tour_seen: true,
        cards_dismissed: started.cards, can_build: true, owner: me.role === 'owner', empty: {docs: true, market: false, tasks: false, updates: false, goals: false, meetings: false, ...started.empty}});
      if (p === '/api/v2/setup/getting-started/state') return json({tour: true, checklist: true, cards: [], skipped: []});
      if (!p.startsWith('/api/v2/')) return json({});
      const api = p.slice(8);
      const body = ['POST', 'PATCH'].includes(method) && (req.headers()['content-type'] || '').includes('json') ? req.postDataJSON() : null;
      if (['POST', 'PATCH'].includes(method)) requests.push({method, api, body, key: req.headers()['idempotency-key']});
      if (api === 'docs' && method === 'GET') return json({docs: [...docs.values()].filter(d => d.archived === (url.searchParams.get('archived') === 'true')).sort((a, b) => a.path.localeCompare(b.path)).map(row), next_cursor: null});
      if (api === 'docs' && method === 'POST') {
        const id = 'doc-' + String(++seq).padStart(12, '0');
        const doc = add(id, body.path || body.title.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.md', body.title, body.body);
        Object.assign(doc, {created_by: 'human:ana', updated_by: 'human:ana', updated: ago(0)}); versions.get(id)[0].actor = 'human:ana';
        return json({doc: view(doc)});
      }
      if (api === 'docs/import') {
        assert.match(req.headers()['content-type'], /^multipart\/form-data; boundary=/);
        const raw = req.postDataBuffer().toString();
        const name = /filename="([^"]+)"/.exec(raw)[1], text = raw.split(/\r\n\r\n/)[1].split(/\r\n--/)[0];
        const id = 'doc-' + String(++seq).padStart(12, '0');
        const doc = add(id, 'imports/' + name, name.replace(/\.\w+$/, ''), text);
        requests.push({api, name, key: req.headers()['idempotency-key']});
        return json({doc: view(doc)});
      }
      if (api === 'docs/search') {
        const words = url.searchParams.get('q').toLowerCase().match(/\w+/g) || [];
        const hit = text => words.every(w => text.toLowerCase().includes(w));
        const results = [
          ...[...docs.values()].filter(d => !d.archived && hit(d.title + ' ' + d.path + ' ' + d.body)).map(d => ({type: 'internal', id: d.id, path: d.path, title: d.title, excerpt: d.body.replace(/\s+/g, ' ').slice(0, 80), score: 12})),
          ...linked.filter(l => hit(l.title + ' ' + l.description + ' ' + l.url)).map(l => ({type: 'linked', id: l.id, title: l.title, url: l.url, kind: l.kind, description: l.description, score: 2}))];
        return json({results});
      }
      const one = /^docs\/([^/]+)(?:\/(versions|restore))?(?:\/(\d+))?$/.exec(api);
      if (one) {
        const doc = docs.get(one[1]);
        if (!doc) return problem('not_found', 'Doc not found', 404);
        if (!one[2] && method === 'GET') return json({doc: view(doc)});
        if (one[2] === 'versions' && one[3]) return json({version: {...versions.get(doc.id).find(v => v.version === Number(one[3])), actor_name: 'Ben'}});
        if (one[2] === 'versions') return json({doc: doc.id, versions: versions.get(doc.id).map(v => ({...v, body: undefined, current: v.version === doc.version, actor_name: NAMES[v.actor]}))});
        if (one[2] === 'restore') {
          const old = versions.get(doc.id).find(v => v.version === body.version);
          doc.archived = false; doc.version += 1; doc.body = old.body; doc.title = old.title; doc.updated_by = 'human:ana'; doc.updated = ago(0); stamp(doc, 'human:ana', `Restored version ${body.version}`, 0);
          return json({doc: view(doc)});
        }
        if (method === 'PATCH') {
          if (doc.locked && me.role !== 'owner') return problem('locked', 'This doc is locked', 403);
          if (body.version !== doc.version) return problem('version_conflict', 'Someone changed this doc', 409, {version: doc.version, updated_by: doc.updated_by, updated_by_name: NAMES[doc.updated_by]});
          if (body.locked !== undefined) doc.locked = body.locked;
          if (body.archived) doc.archived = true;
          if (['title', 'body', 'path'].some(k => body[k] !== undefined && body[k] !== doc[k])) {
            Object.assign(doc, Object.fromEntries(['title', 'body', 'path'].filter(k => body[k] !== undefined).map(k => [k, body[k]])));
            doc.version += 1; doc.updated_by = 'human:ana'; doc.updated = ago(0); stamp(doc, 'human:ana', body.note || '', 0);
          }
          return json({doc: view(doc)});
        }
      }
      if (api === 'doc-sources' && method === 'GET') return json({sources: linked.map(sourceView), next_cursor: null});
      if (api === 'doc-sources' && method === 'POST') {
        const link = addLinked(body.url, body.title || titleOf(body.url), kindOf(body.url), body.description);
        return json({source: sourceView(link)});
      }
      const sourceFiles = /^doc-sources\/([^/]+)\/documents$/.exec(api);
      if (sourceFiles) {
        const link = linked.find(l => l.id === sourceFiles[1]);
        return link ? json({source: sourceView(link), documents: [], next_cursor: null}) : problem('not_found', 'Source not found', 404);
      }
      const sourceEdit = /^doc-sources\/([^/]+)$/.exec(api);
      if (sourceEdit && method === 'PATCH') {
        const link = linked.find(l => l.id === sourceEdit[1]);
        if (body.archived) { linked.splice(linked.indexOf(link), 1); return json({linked: linkView(link)}); }
        Object.assign(link, {title: body.title ?? link.title, description: body.description ?? link.description, url: body.url ?? link.url});
        return json({source: sourceView(link)});
      }
      if (api === 'linked-docs' && method === 'GET') return json({linked: linked.map(linkView)});
      if (api === 'linked-docs' && method === 'POST') {
        const link = addLinked(body.url, body.title || titleOf(body.url), kindOf(body.url), body.description);
        return json({linked: linkView(link)});
      }
      const lk = /^linked-docs\/([^/]+)$/.exec(api);
      if (lk && method === 'PATCH') {
        const link = linked.find(l => l.id === lk[1]);
        if (body.archived) linked.splice(linked.indexOf(link), 1); else Object.assign(link, {title: body.title ?? link.title, description: body.description ?? link.description, url: body.url ?? link.url});
        return json({linked: linkView(link || {})});
      }
      return json(unrouted(p));
    });

    const errors = [];
    const open = async (hash, viewport) => {
      const page = await context.newPage();
      page.on('pageerror', e => errors.push(hash + ': ' + e.message));
      page.on('dialog', dialog => dialog.accept());
      if (viewport) await page.setViewportSize(viewport);
      await page.goto('https://tico-ui.test/' + hash);
      await page.locator('.docs-heading').waitFor();
      await page.locator('#docs-browser .docs-section').first().waitFor();
      return page;
    };
    const items = page => page.locator('#docs-browser .docs-item').evaluateAll(els => els.map(el => el.textContent.trim()));

    // ---- the page: two sections, internal docs by folder, linked docs as a list
    let page = await open('#/docs');
    assert.deepEqual(await page.locator('.docs-section-head h2').allInnerTexts(), ['INTERNAL DOCS 2', 'EXTERNAL SOURCES 2']);
    assert.deepEqual(await items(page), ['Pricing and plans', 'Refund policy']);
    assert.equal(await page.locator('details[data-docs-folder="support"] .docs-item').count(), 1);
    const links = page.locator('.docs-link-main');
    assert.equal(await links.count(), 2);
    assert.match(await links.first().textContent(), /Help centre[\s\S]*Link only[\s\S]*What customers read/);
    assert.match(await links.first().getAttribute('href'), /#\/docs\/source%3Alink-/);
    assert.equal(await links.nth(1).locator('.docs-kind').getAttribute('data-kind'), 'google_drive');
    assert.equal(await page.locator('.docs-welcome').count(), 1);
    await shot(page, 'list');

    // Desktop rail stays open while reading, streams answers and preserves collapse per viewer.
    assert.equal(await page.locator('[data-docs-ask]').getAttribute('role'), 'complementary');
    assert.equal(await page.locator('.dask-backdrop').count(), 0);
    const mainBox = await page.locator('#main').boundingBox(), railBox = await page.locator('[data-docs-ask]').boundingBox();
    assert(mainBox.x + mainBox.width <= railBox.x + 1, 'rail has its own space');
    await page.locator('.dask-form textarea').fill('What is the refund policy?');
    await page.locator('.dask-form button').click();
    await page.locator('.dask-cite').first().waitFor();
    assert.equal(await page.locator('.dask-cite').first().getAttribute('href'), '#/docs/doc-000000000001');
    assert.equal(await page.locator('.dask-cite', {hasText: 'External · Refunds'}).getAttribute('href'), externalCitation);
    assert.equal(await page.locator('.dask-a .md-card').count(), 0);
    assert.equal(await page.locator('.dask-results a').textContent(), 'Refund policy');
    await shot(page, 'rail-desktop');
    // Archived personal conversations survive reload, restore sanitized answers and keep follow-ups in the same room.
    await page.reload();
    await page.locator('.docs-heading').waitFor();
    await page.locator('.dask-history summary').click();
    const oldChat = page.locator('[data-docs-conversation="docs-archived"]');
    const findOldChat = async () => {
      while (!await oldChat.count()) {
        const shown = await page.locator('[data-docs-conversation]').count();
        await page.locator('[data-conversations-more]').click();
        await page.waitForFunction(count => document.querySelectorAll('[data-docs-conversation]').length > count, shown);
      }
    };
    await findOldChat();
    reopenBlocked = true;
    await oldChat.click();
    await page.locator('.toast', {hasText: 'Wait for this conversation'}).waitFor();
    assert.equal(await page.locator('[data-docs-ask] [data-turn]').count(), 0, 'a busy response preserves the thread on screen');
    reopenBlocked = false;
    await oldChat.click();
    await page.locator('[data-docs-ask] [data-answer]', {hasText: 'Refunds take 30 days'}).waitFor();
    assert.equal(await page.locator('.dask-q').innerText(), 'How do refunds work?');
    assert.equal(await page.locator('.dask-cite').first().getAttribute('href'), '#/docs/doc-000000000001');
    assert.equal(await page.evaluate(() => window.docsUnsafe), undefined);
    assert.equal(await page.locator('.dask-form button').isDisabled(), false);
    await page.locator('.dask-form textarea').fill('And gift cards?');
    await page.locator('.dask-form button').click();
    await page.locator('[data-docs-ask] [data-turn]').nth(1).locator('[data-answer]').waitFor();
    assert.deepEqual(requests.findLast(r => r.api === 'docs/ask').body, {question: 'And gift cards?', conversation_id: 'docs-archived'});
    await page.locator('[data-new-chat]').click();
    await page.locator('.dask-form textarea').fill('Who owns release checks?');
    await page.locator('.dask-form button').click();
    await page.locator('.dask-results a').waitFor();
    assert.equal(requests.findLast(r => r.api === 'docs/ask').body.new_conversation, true);
    await page.locator('.dask-history summary').click();
    await findOldChat();
    await shot(page, 'previous-conversations');
    await page.locator('[data-docs-conversation="docs-earlier"]').click();
    await page.locator('[data-docs-ask] .dask-err', {hasText: 'No reply saved'}).waitFor();
    assert.equal(await page.locator('.dask-form button').isDisabled(), false, 'an unanswered archived question does not block the composer');
    assert.equal(await page.locator('[data-docs-ask] [data-thinking]').count(), 0);
    // The rail is part of the page: Escape elsewhere leaves it open.
    await page.locator('#docs-search').focus();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('[data-docs-ask]').count(), 1);
    await page.locator('.dask-close').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'docs-ask', 'hiding the rail focuses its button');
    await page.reload();
    await page.locator('.docs-heading').waitFor();
    assert.equal(await page.locator('[data-docs-ask]').count(), 0);
    await page.locator('#docs-ask').click();
    await page.locator('[data-docs-ask]').waitFor();

    // ---- read an internal doc, and its relative link to another doc stays in the app
    await page.locator('.docs-item', {hasText: 'Pricing and plans'}).click();
    await page.locator('.docs-reader-head h2', {hasText: 'Pricing and plans'}).waitFor();
    assert.match(await page.locator('.docs-reader-head').textContent(), /Version 1 · Updated .* by Ben/);
    assert.equal(await page.locator('.docs-content table').count(), 1);
    assert.equal(await page.locator('.docs-content a', {hasText: 'refunds'}).getAttribute('href'), '#/docs/doc-000000000001');
    await page.locator('.docs-item', {hasText: 'Refund policy'}).click();
    await page.locator('.docs-reader-head h2', {hasText: 'Refund policy'}).waitFor();
    await shot(page, 'doc');

    // Research: the Librarian adds what it finds on the web to the section chosen.
    await page.locator('#doc-more').click();
    await page.locator('#doc-research').click();
    const research = page.locator('form[data-research-form]');
    await research.locator('textarea[name=instructions]').fill('Add how other stores handle refunds');
    await shot(page, 'research');
    await research.locator('[type=submit]').click();
    await research.locator('a', {hasText: 'View task'}).waitFor();
    const asked = requests.findLast(r => r.api === 'librarian/research').body;
    assert.equal(asked.instructions, 'Add how other stores handle refunds');
    assert.equal(asked.section, '');
    assert.equal(await research.locator('a', {hasText: 'View task'}).getAttribute('href'), '#/task/task-research');
    await research.locator('button', {hasText: 'Close'}).click();

    // Market: the same rail, its own thread, answered from the market graph; what it drew on opens the note.
    const docsAsks = requests.filter(r => r.api === 'docs/ask').length;
    await page.evaluate(() => { location.hash = '#/market'; });
    await page.locator('#market-shell [data-librarian-open]').waitFor({state: 'attached'});
    await page.locator('[data-docs-ask]').waitFor();
    assert.equal(await page.locator('.dask-form textarea').getAttribute('placeholder'), 'Ask about the market…');
    assert.equal(await page.locator('.dask-history').isVisible(), false, 'Docs history stays out of Market');
    assert.equal(await page.locator('[data-docs-ask] [data-turn]').count(), 0, 'Market has its own thread');
    await page.locator('.dask-form textarea').fill('Who competes with us?');
    await page.locator('.dask-form button').click();
    await page.locator('.dask-cites .dask-cite', {hasText: 'Rival'}).waitFor();
    assert.equal(requests.findLast(r => r.api === 'market/ask').body.question, 'Who competes with us?');
    assert.equal(requests.filter(r => r.api === 'docs/ask').length, docsAsks);
    assert.equal(await page.locator('[data-docs-ask] [data-answer]').innerText(), 'Rival competes on price.');
    await page.locator('.dask-cites .dask-cite').click();
    await page.waitForFunction(() => location.hash === '#/market?note=company%2Frival');
    await page.locator('#market-read [data-market-research="company/rival"]').click();
    await page.locator('form[data-research-form] textarea').fill('Find their pricing');
    await page.locator('form[data-research-form] [type=submit]').click();
    await page.locator('form[data-research-form] a', {hasText: 'View task'}).waitFor();
    assert.equal(requests.findLast(r => r.api === 'librarian/research').body.target, 'company/rival');
    await page.locator('form[data-research-form] button', {hasText: 'Close'}).click();
    assert.equal(await page.locator('[data-docs-ask]').count(), 1, 'a citation opens the note beside the rail');
    await page.evaluate(() => { location.hash = '#/docs'; });
    await page.locator('.docs-heading').waitFor();
    assert.equal(await page.locator('.dask-form textarea').getAttribute('placeholder'), 'Ask about your docs…');

    // A narrow desktop still reserves space for the rail and lets a doc open.
    await page.setViewportSize({width: 1024, height: 860});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator('.docs-item', {hasText: 'Refund policy'}).click();
    await page.locator('.docs-reader-head h2', {hasText: 'Refund policy'}).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.setViewportSize({width: 1280, height: 860});

    // ---- create: title, path, a Markdown body, a safe preview
    await page.locator('#docs-new').click();
    await page.locator('form.docs-editor').waitFor();
    await page.locator('form.docs-editor input[name=title]').fill('Onboarding checklist');
    await page.locator('form.docs-editor input[name=path]').fill('people/onboarding');
    await page.locator('form.docs-editor textarea').fill('# Welcome\n\n- **Laptop** first\n\n<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>');
    await page.locator('[data-tab=preview]').click();
    assert.equal(await page.locator('.docs-preview h1').textContent(), 'Welcome');
    assert.equal(await page.locator('.docs-preview li strong').textContent(), 'Laptop');
    assert.equal(await page.locator('.docs-preview script, .docs-preview [onerror]').count(), 0);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    await shot(page, 'editor');
    await page.locator('form.docs-editor [type=submit]').click();
    await page.locator('.docs-reader-head h2', {hasText: 'Onboarding checklist'}).waitFor();
    const created = requests.find(r => r.api === 'docs' && r.method === 'POST');
    assert.deepEqual([created.body.title, created.body.path], ['Onboarding checklist', 'people/onboarding']);
    assert.ok(created.key);
    assert.equal(await page.locator('details[data-docs-folder="people"] .docs-item').count(), 1);
    assert.equal(await page.locator('.docs-reader-head').textContent().then(t => /Version 1/.test(t)), true);
    const newId = new URL(page.url()).hash.split('/')[2];

    // ---- edit, and the version-conflict message when someone saved first
    await page.locator('#doc-edit').click();
    await page.locator('form.docs-editor textarea').waitFor();
    outside(newId, '# Welcome\n\nBen rewrote this while you were editing.');
    await page.locator('form.docs-editor textarea').fill('# Welcome\n\nMy careful rewrite.');
    await page.locator('form.docs-editor input[name=note]').fill('Rewrote it');
    await page.locator('form.docs-editor [type=submit]').click();
    await page.locator('.docs-conflict:not([hidden])').waitFor();
    assert.match(await page.locator('.docs-conflict').textContent(), /Ben saved version 2 while you were editing\. Your text is still here\./);
    assert.equal(await page.locator('form.docs-editor textarea').inputValue(), '# Welcome\n\nMy careful rewrite.');
    await shot(page, 'conflict');
    await page.locator('.docs-conflict button', {hasText: 'Keep mine'}).click();
    await page.locator('form.docs-editor [type=submit]').click();
    await page.locator('.docs-content', {hasText: 'My careful rewrite.'}).waitFor();
    assert.match(await page.locator('.docs-reader-head').textContent(), /Version 3/);
    const patches = requests.filter(r => r.method === 'PATCH' && r.api === 'docs/' + newId);
    assert.deepEqual(patches.map(r => r.body.version), [1, 2]);
    assert.equal(patches[1].body.note, 'Rewrote it');

    // ---- history: who changed it, view an old version, restore it
    await page.locator('#doc-more').click();
    await page.locator('#doc-history').click();
    await page.locator('.docs-history-list button').first().waitFor();
    assert.deepEqual(await page.locator('.docs-history-list .docs-history-top strong').allInnerTexts(), ['v3', 'v2', 'v1']);
    assert.match(await page.locator('.docs-history-list li').nth(1).textContent(), /Ben/);
    assert.match(await page.locator('.docs-history-list li').first().textContent(), /Current/);
    await page.locator('.docs-history-list button', {hasText: 'v1'}).click();
    await page.locator('.docs-history-text li strong').waitFor();
    assert.equal(await page.locator('.docs-history-text li strong').textContent(), 'Laptop');
    await shot(page, 'history');
    await page.locator('.docs-history [type=button].primary, .docs-history button.primary').click();
    await page.locator('.docs-history').waitFor({state: 'detached'});
    await page.locator('#docs-reader .docs-content li strong', {hasText: 'Laptop'}).waitFor();
    assert.match(await page.locator('.docs-reader-head').textContent(), /Version 4/);
    assert.equal(versions.get(newId)[0].note, 'Restored version 1');

    // ---- lock: owners and bot administrators only
    await page.locator('#doc-more').click();
    await page.locator('#doc-lock').click();
    await page.locator('.docs-lock-badge').waitFor();
    assert.equal(docs.get(newId).locked, true);
    assert.equal(await page.locator('#doc-lock').textContent(), 'Unlock');
    await page.close();
    me = {...me, id: 'cara', name: 'Cara', role: 'human', bot_admin: false};
    page = await open('#/docs/' + newId);
    await page.locator('.docs-lock-badge').waitFor();
    assert.equal(await page.locator('#doc-lock').count(), 0);
    assert.equal(await page.locator('#doc-edit').getAttribute('aria-disabled'), 'true');
    assert.match(await page.locator('.docs-locked-note').textContent(), /only an owner or bot administrator/i);
    await page.locator('#doc-more').click();
    await page.locator('#doc-history').click();
    await page.locator('.docs-history-list button', {hasText: 'v1'}).click();
    await page.locator('.docs-history button.primary').waitFor();
    assert.equal(await page.locator('.docs-history button.primary').isDisabled(), true);   // a locked doc is not restored by everyone
    await page.close();
    me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', cloud: true, bot_admin: true};

    // ---- linked docs: add (kind detected), open at the source, edit, remove
    page = await open('#/docs');
    await page.locator('#docs-add-source').click();
    await page.locator('form[data-link-form] input[name=url]').fill('www.notion.so/Acme-Handbook');
    assert.match(await page.locator('[data-detected]').textContent(), /Link only/);
    await page.locator('form[data-link-form] input[name=description]').fill('The company handbook');
    await page.locator('form[data-link-form] [type=submit]').click();
    await page.locator('.docs-link-main', {hasText: 'Acme-Handbook'}).waitFor();
    assert.deepEqual(requests.findLast(r => r.api === 'doc-sources').body, {url: 'www.notion.so/Acme-Handbook', title: '', description: 'The company handbook', reference: ''});
    assert.equal(await page.locator('#docs-browser .docs-link', {hasText: 'Acme-Handbook'}).locator('.docs-kind').getAttribute('data-kind'), 'notion');
    await page.locator('#docs-browser .docs-link-main', {hasText: 'Help centre'}).click();
    // Open original is a plain new-tab link to the source. The pane is redrawn for the clicked link, so wait for its
    // link (the previous one's can still be on screen), then check it rather than open a real tab: that is the browser's work.
    const original = page.getByRole('link', {name: 'Open original'}).and(page.locator('[href="https://help.acme.example/refunds"]'));
    await original.waitFor();
    assert.deepEqual(await original.evaluate(a => [a.target, a.rel]), ['_blank', 'noopener noreferrer']);
    await page.locator('#docs-browser .docs-link', {hasText: 'Help centre'}).hover();
    await page.locator('#docs-browser .docs-link', {hasText: 'Help centre'}).locator('[data-link-edit]').click();
    await page.locator('form[data-link-form] input[name=title]').fill('Public help centre');
    await page.locator('form[data-link-form] [type=submit]').click();
    await page.locator('.docs-link-main', {hasText: 'Public help centre'}).waitFor();
    await page.locator('#docs-browser .docs-link', {hasText: 'Brand assets'}).locator('[data-link-edit]').click();
    await page.locator('[data-remove]').click();
    await page.waitForFunction(() => !document.body.textContent.includes('Brand assets'));
    assert.deepEqual(linked.map(l => l.title), ['Public help centre', 'notion.so/Acme-Handbook']);

    // ---- search: both kinds, with a badge; Enter opens the first internal hit
    await page.locator('#docs-search').fill('refund');
    await page.locator('.docs-result').first().waitFor();
    assert.deepEqual(await page.locator('.docs-result .docs-badge').allInnerTexts(), ['INTERNAL', 'INTERNAL', 'LINKED · WEBSITE']);
    assert.equal(await page.locator('.docs-result[target=_blank]').count(), 1);
    await shot(page, 'search');
    await page.locator('#docs-search').press('Enter');
    await page.locator('.docs-reader-head h2', {hasText: 'Refund policy'}).waitFor();
    await page.locator('#docs-search').fill('zzz');
    await page.locator('#docs-browser .empty', {hasText: 'No docs match'}).waitFor();
    await page.locator('#docs-search').press('Escape');
    await page.locator('#docs-browser .docs-section').first().waitFor();

    // ---- import a file
    await page.locator('#docs-more').click();
    await page.locator('#docs-import').click();
    await page.locator('form[data-import-form] input[type=file]').setInputFiles({name: 'handbook.md', mimeType: 'text/markdown', buffer: Buffer.from('# Handbook\n\nWelcome aboard.\n')});
    await page.locator('form[data-import-form] [type=submit]').click();
    await page.locator('.docs-reader-head h2', {hasText: 'handbook'}).waitFor();
    assert.match(await page.locator('.docs-content').textContent(), /Welcome aboard/);
    const imported = requests.findLast(r => r.api === 'docs/import');
    assert.equal(imported.name, 'handbook.md');
    assert.ok(imported.key, 'the upload carries an Idempotency-Key');
    await page.close();

    // ---- a phone
    docs.clear();
    add('doc-000000000001', 'support/refund-policy.md', 'Refund policy', '# Refund policy\n\nRefund within **30 days**.\n\n## Who decides\n\nSupport up to $200.\n\n## Escalation\n\nAsk the owner.\n\n## Records\n\nKeep the receipt.', {locked: true});
    addLinked('https://drive.google.com/drive/folders/brand', 'Brand assets', 'google_drive', 'Logos and the tone guide');
    page = await open('#/docs', {width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.locator('#docs-ask .docs-ask-word').isVisible(), false);
    await shot(page, 'phone-list');
    assert.equal(await page.locator('[data-docs-ask]').count(), 0);
    await page.locator('#docs-ask').click();
    assert.equal(await page.locator('[data-docs-ask]').getAttribute('aria-modal'), 'true');
    await shot(page, 'rail-phone');
    // No AI provider: the question is saved and waits; the sheet says so, not that the Librarian is reading.
    librarianQueued = true;
    await page.locator('.dask-form textarea').fill('How do I add an AI provider?');
    await page.locator('.dask-form button').click();
    await page.locator('[data-no-provider]').waitFor();
    assert.equal(await page.locator('[data-docs-ask] [data-thinking]').count(), 0);
    assert.equal(await page.locator('[data-no-provider] a').getAttribute('data-gs-tab'), 'providers');
    assert.equal(await page.locator('.dask-form button').isDisabled(), false);
    await shot(page, 'rail-phone-no-provider');
    librarianQueued = false;
    await page.locator('.dask-close').click();

    await page.locator('.docs-item').first().click();
    await page.locator('.docs-reader-head h2').waitFor();
    assert.equal(await page.locator('#docs-browser').isVisible(), false);
    assert.equal(await page.locator('.docs-back').first().isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await shot(page, 'phone-doc');
    await page.locator('#doc-edit').click();
    await page.locator('form.docs-editor').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await shot(page, 'phone-editor');
    await page.locator('.docs-back').first().click();
    await page.locator('#docs-browser .docs-section').first().waitFor();
    add('doc-000000000002', 'pricing.md', 'Pricing and plans', '# Pricing and plans');
    await page.setViewportSize({width: 1280, height: 860});
    await page.goto('https://tico-ui.test/#/docs/doc-000000000002');
    await page.locator('#doc-more').click();
    await page.locator('#doc-archive').click();
    await page.locator('.toast button').filter({hasText: 'Undo'}).click();
    await page.locator('#doc-archive').waitFor({state: 'attached'});
    assert.equal(docs.get('doc-000000000002').archived, false);
    await page.locator('#doc-more').click();
    await page.locator('#doc-archive').click();
    await page.locator('#docs-more').click();
    await page.locator('#docs-archived').filter({hasText: /^Archived$/}).click();
    await page.locator('#docs-h-internal').filter({hasText: 'Archived docs'}).waitFor();
    await page.locator('.docs-item').filter({hasText: 'Pricing and plans'}).click();
    await page.locator('#doc-more').click();
    await page.locator('#doc-archive').filter({hasText: 'Restore'}).click();
    await page.locator('#doc-archive').filter({hasText: 'Archive'}).waitFor({state: 'attached'});
    assert.equal(docs.get('doc-000000000002').archived, false);
    // Hundreds of internal docs cannot push the external-source heading out of reach.
    for (let i = 0; i < 300; i++) add('long-doc-' + i, `guides/guide-${i}.md`, 'Guide ' + i, 'Team procedures.');
    await page.goto('https://tico-ui.test/#/docs');
    await page.locator('#docs-h-internal', {hasText: '302'}).waitFor();
    const scroll = await page.locator('.docs-internal-section .docs-group-scroll').evaluate(el => ({height: el.clientHeight, total: el.scrollHeight}));
    assert(scroll.height > 0 && scroll.total > scroll.height);
    const sourceHeading = await page.locator('#docs-h-linked').boundingBox();
    assert(sourceHeading.y + sourceHeading.height < page.viewportSize().height);
    await page.close();

    assert.deepEqual(errors, []);
    console.log('PASS: docs are written, edited (with the conflict message), versioned and restored, locked for admins, linked, searched and imported.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
