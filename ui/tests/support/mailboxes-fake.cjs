// A fake of the Google mailboxes routes (backend/mailboxes.py) for the Settings > Inboxes scripts (ui/tests/inboxes.cjs,
// ui/tests/mailbox.cjs).
// answer(path, method, body) gives the JSON a route answers ({status, body} for a refusal), or undefined for any other
// path. `fake.state` holds the mailboxes, their accesses and routines; every write is kept in `fake.writes` as
// {method, path, body}. `fake.admin` is whether the caller is an admin; `fake.me` the caller's person id.
const box = (id, address, over = {}) => ({id, address, provider: 'google_workspace', kind: 'personal', person_id: null,
  person_name: '', connection: 'workspace', status: 'active', state: 'connected', level: 'none', routines: [],
  used_by: {people: 0, bots: 0, names: []}, synced_at: null, newest_epoch: null, sync: '', sync_error: '', ...over});
exports.box = box;

exports.mailboxesFake = ({admin = true, me = 'ana'} = {}) => {
  const fake = {
    admin, me, writes: [], n: 0,
    may: {read: true, organize: true, send: false, change_calendar: false},
    state: {
      mailboxes: [
        box('mb-ana', 'ana@acme.example', {person_id: 'ana', person_name: 'Ana', level: 'read_write'}),
        box('mb-ben', 'ben@acme.example', {person_id: 'ben', person_name: 'Ben'}),
        box('mb-support', 'support@acme.example', {kind: 'shared', level: 'read_write'}),
        box('mb-dan', 'dan@acme.example', {state: 'awaiting_person'}),
        box('mb-old', 'old@acme.example', {status: 'stopped', state: 'stopped'}),
      ],
      accesses: {'mb-support': [{id: 'acc-cara', grantee_type: 'person', grantee_id: 'cara', name: 'Cara', level: 'read', implicit: false},
        {id: 'acc-bot', grantee_type: 'bot', grantee_id: 'support-bot', name: 'Support bot', level: 'read_write', implicit: false}]},
      routines: {'support@acme.example': [{id: 'au-triage', name: 'Support triage', enabled: true, can_edit: true}]},
      candidates: [{type: 'person', id: 'ben', name: 'Ben'}, {type: 'bot', id: 'ops', name: 'Ops'}],
    },
  };
  const s = fake.state;
  const view = m => ({...m, routines: (s.routines[m.address] || []).map(({id, name}) => ({id, name}))});
  const find = id => s.mailboxes.find(m => m.id === id);
  const manages = m => m.status === 'active' && (m.kind === 'shared' ? fake.admin : m.person_id === fake.me && !!m.person_id);
  const detail = m => {
    const implicit = m.kind === 'shared' ? [{id: null, grantee_type: 'admins', grantee_id: '', name: 'Admins', level: 'read_write', implicit: true}]
      : m.person_id ? [{id: null, grantee_type: 'person', grantee_id: m.person_id, name: m.person_name, level: 'read_write', implicit: true}] : [];
    const rows = s.accesses[m.id] || [];
    const taken = new Set(rows.map(a => `${a.grantee_type}:${a.grantee_id}`));
    return {mailbox: view(m), can_manage: manages(m), can_stop: m.status === 'active' && (fake.admin || m.person_id === fake.me),
      accesses: [...implicit, ...rows], routines: structuredClone(s.routines[m.address] || []), may: structuredClone(fake.may),
      candidates: manages(m) ? s.candidates.filter(x => !taken.has(`${x.type}:${x.id}`)) : []};
  };
  const refuse = (status, detail) => ({status, body: {error: {code: 'refused', detail, retryable: false}}});

  fake.answer = (p, method = 'GET', body = null) => {
    if (method !== 'GET') fake.writes.push({method, path: p, body});
    if (p === '/api/v2/mailboxes' && method === 'GET') {
      const mine = s.mailboxes.find(m => m.person_id === fake.me && m.status === 'active');
      return {admin: fake.admin, domain: 'acme.example', may: structuredClone(fake.may), mine: mine ? view(mine) : null,
        shared: s.mailboxes.filter(m => m !== mine && m.status === 'active' && m.level !== 'none').map(view),
        mailboxes: fake.admin ? s.mailboxes.map(view) : []};
    }
    if (p === '/api/v2/mailboxes' && method === 'POST') {
      const address = String(body?.address || '').toLowerCase();
      if (s.mailboxes.some(m => m.address === address)) return refuse(409, `${address} is already in Tico`);
      const made = box(`mb-new${++fake.n}`, address, body?.shared ? {kind: 'shared', level: 'read_write'} : {state: 'awaiting_person'});
      s.mailboxes.push(made);
      return {mailbox: view(made)};
    }
    const one = p.match(/^\/api\/v2\/mailboxes\/([^/]+)(\/.*)?$/);
    if (!one) return undefined;
    const m = find(decodeURIComponent(one[1]));
    if (!m) return refuse(404, 'No such mailbox');
    const rest = one[2] || '';
    if (!rest && method === 'GET') return detail(m);
    if (!rest && method === 'DELETE') { Object.assign(m, {status: 'stopped', state: 'stopped'}); return {mailbox: view(m)}; }
    if (rest === '/share' && method === 'POST') { Object.assign(m, {kind: 'shared', state: 'connected'}); return detail(m); }
    if (rest === '/accesses' && method === 'POST') {
      const who = s.candidates.find(x => x.type === body.grantee_type && x.id === body.grantee_id);
      (s.accesses[m.id] ||= []).push({id: `acc-${body.grantee_id}`, grantee_type: body.grantee_type, grantee_id: body.grantee_id,
        name: who?.name || body.grantee_id, level: body.level, implicit: false});
      return detail(m);
    }
    const acc = rest.match(/^\/accesses\/([^/]+)$/);
    if (acc) {
      const list = s.accesses[m.id] || [];
      const row = list.find(a => a.id === decodeURIComponent(acc[1]));
      if (!row) return refuse(404, 'No such access');
      if (method === 'PATCH') row.level = body.level;
      if (method === 'DELETE') s.accesses[m.id] = list.filter(a => a !== row);
      return detail(m);
    }
    return undefined;
  };
  return fake;
};

// Opens `hash` (Settings > Inboxes, one inbox's page or Inboxes > Settings) on a fresh page over `fake` and a google-fake
// hub `google`, signed in as fake.me: an owner when fake.admin, a member otherwise. Waits for `ready`. The other kinds'
// routes answer empty unless `extra(path, method, body, url)` answers them (undefined passes); `mail` maps a mailbox
// address to its GET /v2/mail/messages list; `skip` starts with first run skipped; `theme` sets data-theme.
const fs = require('node:fs');
const {html, uiFile} = require('./page.cjs');
const {unrouted} = require('./routes.cjs');
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const SHELL = {
  '/api/v2/config': config,
  '/api/employees': [],
  '/api/humans': {people: [{id: 'ana', name: 'Ana'}], teams: {}},
  '/api/people': {people: [], teams: {}},
  '/api/status': {cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []},
  '/api/v2/status': {bots: []},
  '/api/v2/needs-you': {items: []},
  '/api/v2/operations': {machines: [], services: []},
  '/api/v2/models': {models: [], harnesses: []},
  '/api/v2/me/tokens': {tokens: []},
};
const EMPTY = {
  '/api/v2/inbound/inboxes': () => ({inboxes: [], next: null, configured: true, last_synced_at: null, error: '', gaps: []}),
  '/api/v2/inbound/phone-requests': () => ({phone_requests: [], next: null}),
  '/api/v2/inbound/messages': () => ({messages: [], next: null}),
  '/api/v2/postal/addresses': () => ({addresses: [], orders: [], prices: {}}),
  '/api/v2/postal/items': () => ({items: [], next: null}),
  '/api/v2/postal/intake': () => ({intake: null}),
  '/api/v2/automations': () => ({automations: []}),
};
exports.open = async (browser, fake, google, {hash = '#/settings/inboxes', ready, width = 1440, height = 900, automations = {},
  extra = () => undefined, mail = {}, skip = false, theme = ''} = {}) => {
  const page = await browser.newPage({viewport: {width, height}, serviceWorkers: 'block'});
  const seen = {errors: []};
  page.on('pageerror', error => seen.errors.push(error.message));
  await page.addInitScript(({skip, theme}) => {
    try { if (skip) localStorage.setItem('tico.inboxes.skip', '1'); } catch { /* storage off */ }
    if (theme) document.documentElement.setAttribute('data-theme', theme);
  }, {skip, theme});
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const raw = request.postData(), body = raw ? JSON.parse(raw) : null;
    const more = extra(p, method, body, url);
    if (more?.status && more.body) return json(more.body, more.status);
    if (more !== undefined) return json(more);
    if (p === '/api/me') return json({id: fake.me, role: fake.admin ? 'owner' : 'human', name: fake.me, email: `${fake.me}@acme.example`,
      cloud: true, registered: true, config});
    if (p.startsWith('/api/v2/mailboxes')) {
      const answer = fake.answer(p, method, body);
      if (answer?.status && answer.body) return json(answer.body, answer.status);
      if (answer !== undefined) return json(answer);
    }
    if (p.startsWith('/api/v2/automations/') && method === 'PATCH') {
      fake.writes.push({method, path: p, body});
      const id = decodeURIComponent(p.split('/').pop());
      if (automations.refuse) return json({error: {code: 'forbidden', detail: automations.refuse, retryable: false}}, 403);
      for (const list of Object.values(fake.state.routines)) for (const r of list) if (r.id === id) r.enabled = !!body.enabled;
      return json({automation: {id, enabled: body.enabled}});
    }
    if (p.startsWith('/api/v2/google') || p.startsWith('/api/v2/mail/google')) {
      const answer = google.answer(p, request.url(), method, body);
      if (answer !== undefined) return json(answer);
    }
    if (p === '/api/v2/mail/messages') return json({messages: structuredClone(mail[url.searchParams.get('mailbox')] || [])});
    if (method === 'GET' && p in EMPTY) return json(EMPTY[p]());
    if (p in SHELL) return json(SHELL[p]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto(`https://tico-ui.test/${hash}`);
  if (ready) await page.locator(ready).first().waitFor();
  return {page, seen};
};
