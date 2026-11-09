// A fake of the Google mailboxes routes (backend/mailboxes.py) for the inbox pages under Tools (ui/tests/inboxes.cjs,
// ui/tests/mailbox.cjs).
// answer(path, method, body) gives the JSON a route answers ({status, body} for a refusal), or undefined for any other
// path. `fake.state` holds the mailboxes, their accesses and routines; every write is kept in `fake.writes` as
// {method, path, body}. `fake.admin` is whether the caller is an admin; `fake.me` the caller's person id.
import type {Browser, Page} from 'playwright';
import type {GoogleFake} from './google-fake.cjs';
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./page.cjs') as typeof import('./page.cjs');
const {unrouted} = require('./routes.cjs') as typeof import('./routes.cjs');

type Json = Record<string, unknown>;

export interface Box {
  id: string;
  address: string;
  provider: string;
  kind: string;
  person_id: string | null;
  person_name: string;
  connection: string;
  status: string;
  state: string;
  level: string;
  routines: unknown[];
  used_by: {people: number; bots: number; names: string[]};
  synced_at: string | null;
  newest_epoch: number | null;
  sync: string;
  sync_error: string;
  [key: string]: unknown;
}
export interface Access {
  id: string | null;
  grantee_type: string;
  grantee_id: string;
  name: string;
  level: string;
  implicit: boolean;
}
export interface Routine {
  id: string;
  name: string;
  enabled: boolean;
  can_edit: boolean;
}
export interface MailboxWrite {
  method: string;
  path: string;
  body: unknown;
}
export interface MailboxesFake {
  admin: boolean;
  me: string;
  writes: MailboxWrite[];
  n: number;
  may: {read: boolean; organize: boolean; send: boolean; change_calendar: boolean};
  state: {
    mailboxes: Box[];
    accesses: Record<string, Access[]>;
    routines: Record<string, Routine[]>;
    candidates: {type: string; id: string; name: string}[];
  };
  // The JSON a route answers ({status, body} for a refusal), or undefined for any other path.
  answer(path: string, method?: string, body?: Json | null): unknown;
}

// The answer a route gets back: JSON, or a refusal as {status, body}.
interface Answer {
  status?: number;
  body?: unknown;
}

function box(id: string, address: string, over: Partial<Box> = {}): Box {
  return {id, address, provider: 'google_workspace', kind: 'personal', person_id: null,
    person_name: '', connection: 'workspace', status: 'active', state: 'connected', level: 'none', routines: [],
    used_by: {people: 0, bots: 0, names: []}, synced_at: null, newest_epoch: null, sync: '', sync_error: '', ...over};
}

function mailboxesFake({admin = true, me = 'ana'}: {admin?: boolean; me?: string} = {}): MailboxesFake {
  const may = {read: true, organize: true, send: false, change_calendar: false};
  const s: MailboxesFake['state'] = {
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
  };
  const fake: MailboxesFake = {
    admin, me, writes: [], n: 0, may, state: s,
    answer(p: string, method = 'GET', body: Json | null = null): unknown {
      if (method !== 'GET') fake.writes.push({method, path: p, body});
      const view = (m: Box) => ({...m, routines: (s.routines[m.address] || []).map(({id, name}) => ({id, name}))});
      const find = (id: string) => s.mailboxes.find(m => m.id === id);
      const manages = (m: Box) => m.status === 'active' && (m.kind === 'shared' ? fake.admin : m.person_id === fake.me && !!m.person_id);
      const detail = (m: Box) => {
        const implicit: Access[] = m.kind === 'shared' ? [{id: null, grantee_type: 'admins', grantee_id: '', name: 'Admins', level: 'read_write', implicit: true}]
          : m.person_id ? [{id: null, grantee_type: 'person', grantee_id: m.person_id, name: m.person_name, level: 'read_write', implicit: true}] : [];
        const rows = s.accesses[m.id] || [];
        const taken = new Set(rows.map(a => `${a.grantee_type}:${a.grantee_id}`));
        return {mailbox: view(m), can_manage: manages(m), can_stop: m.status === 'active' && (fake.admin || m.person_id === fake.me),
          accesses: [...implicit, ...rows], routines: structuredClone(s.routines[m.address] || []), may: structuredClone(fake.may),
          candidates: manages(m) ? s.candidates.filter(x => !taken.has(`${x.type}:${x.id}`)) : []};
      };
      const refuse = (status: number, detail: string): Answer => ({status, body: {error: {code: 'refused', detail, retryable: false}}});

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
      const m = find(decodeURIComponent(one[1] ?? ''));
      if (!m) return refuse(404, 'No such mailbox');
      const rest = one[2] || '';
      if (!rest && method === 'GET') return detail(m);
      if (!rest && method === 'DELETE') { Object.assign(m, {status: 'stopped', state: 'stopped'}); return {mailbox: view(m)}; }
      if (rest === '/share' && method === 'POST') { Object.assign(m, {kind: 'shared', state: 'connected'}); return detail(m); }
      if (rest === '/accesses' && method === 'POST') {
        const grant = body as {grantee_type: string; grantee_id: string; level: string};
        const who = s.candidates.find(x => x.type === grant.grantee_type && x.id === grant.grantee_id);
        (s.accesses[m.id] ||= []).push({id: `acc-${grant.grantee_id}`, grantee_type: grant.grantee_type, grantee_id: grant.grantee_id,
          name: who?.name || grant.grantee_id, level: grant.level, implicit: false});
        return detail(m);
      }
      const acc = rest.match(/^\/accesses\/([^/]+)$/);
      if (acc) {
        const list = s.accesses[m.id] || [];
        const row = list.find(a => a.id === decodeURIComponent(acc[1] ?? ''));
        if (!row) return refuse(404, 'No such access');
        if (method === 'PATCH') row.level = (body as {level: string}).level;
        if (method === 'DELETE') s.accesses[m.id] = list.filter(a => a !== row);
        return detail(m);
      }
      return undefined;
    },
  };
  return fake;
}

// Opens `hash` (Tools, one inbox's page or Tools › Settings) on a fresh page over `fake` and a google-fake
// hub `google`, signed in as fake.me: an owner when fake.admin, a member otherwise. Waits for `ready`. The other kinds'
// routes answer empty unless `extra(path, method, body, url)` answers them (undefined passes); `mail` maps a mailbox
// address to its GET /v2/mail/messages list; `skip` starts with first run skipped; `theme` sets data-theme.
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const SHELL: Record<string, unknown> = {
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
const EMPTY: Record<string, () => unknown> = {
  '/api/v2/inbound/inboxes': () => ({inboxes: [], next: null, configured: true, last_synced_at: null, error: '', gaps: []}),
  '/api/v2/inbound/phone-requests': () => ({phone_requests: [], next: null}),
  '/api/v2/inbound/messages': () => ({messages: [], next: null}),
  '/api/v2/postal/addresses': () => ({addresses: [], orders: [], prices: {}}),
  '/api/v2/postal/items': () => ({items: [], next: null}),
  '/api/v2/postal/intake': () => ({intake: null}),
  '/api/v2/automations': () => ({automations: []}),
  '/api/v2/tool-index': () => ({rows: [], counts: {}}),
};
// A TICO_SHOTS screenshot once the route's fade has ended, in the theme its name says ('' keeps the page's own).
async function shot(page: Page, file: string, theme = ''): Promise<void> {
  if (!process.env.TICO_SHOTS) return;
  if (theme) await page.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme);
  await page.evaluate(() => Promise.all(document.getAnimations()
    .filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: `${process.env.TICO_SHOTS}/${file}`, fullPage: true, animations: 'disabled'});
}
export interface OpenOptions {
  hash?: string;
  ready?: string | undefined;
  width?: number;
  height?: number;
  automations?: {refuse?: string};
  extra?: (path: string, method: string, body: unknown, url: URL) => unknown;
  mail?: Record<string, unknown[]>;
  skip?: boolean;
  theme?: string;
}
async function open(browser: Browser, fake: MailboxesFake, google: GoogleFake, {hash = '#/tools', ready, width = 1440, height = 900, automations = {},
  extra = () => undefined, mail = {}, skip = false, theme = ''}: OpenOptions = {}): Promise<{page: Page; seen: {errors: string[]}}> {
  const page = await browser.newPage({viewport: {width, height}, serviceWorkers: 'block'});
  const seen = {errors: [] as string[]};
  page.on('pageerror', error => seen.errors.push(error.message));
  await page.addInitScript(skip => {
    try { if (skip) localStorage.setItem('tico.inboxes.skip', '1'); } catch { /* storage off */ }
  }, skip);
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1] ?? '')))
      return route.fulfill({contentType: (ui[1] ?? '').endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1] ?? ''), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const raw = request.postData(), body: Json | null = raw ? JSON.parse(raw) : null;
    const more = extra(p, method, body, url) as Answer | undefined;
    if (more?.status && more.body) return json(more.body, more.status);
    if (more !== undefined) return json(more);
    if (p === '/api/me') return json({id: fake.me, role: fake.admin ? 'owner' : 'human', name: fake.me, email: `${fake.me}@acme.example`,
      cloud: true, registered: true, config});
    if (p.startsWith('/api/v2/mailboxes')) {
      const answer = fake.answer(p, method, body) as Answer | undefined;
      if (answer?.status && answer.body) return json(answer.body, answer.status);
      if (answer !== undefined) return json(answer);
    }
    if (p.startsWith('/api/v2/automations/') && method === 'PATCH') {
      fake.writes.push({method, path: p, body});
      const id = decodeURIComponent(p.split('/').pop() ?? '');
      if (automations.refuse) return json({error: {code: 'forbidden', detail: automations.refuse, retryable: false}}, 403);
      for (const list of Object.values(fake.state.routines)) for (const r of list) if (r.id === id) r.enabled = !!body?.enabled;
      return json({automation: {id, enabled: body?.enabled}});
    }
    if (p.startsWith('/api/v2/google') || p.startsWith('/api/v2/mail/google')) {
      const answer = google.answer(p, request.url(), method, body);
      if (answer !== undefined) return json(answer);
    }
    if (p === '/api/v2/org') return json({bots: [{id: 'support-bot', slug: 'support-bot', display_name: 'Support bot'}], humans: []});
    if (p.endsWith('/sending-policy') || p.endsWith('/email-sending-policy')) {
      if (method !== 'GET') fake.writes.push({method, path: p, body});
      return json({company: {}, inbox: {}, bot: {}, legacy_automation_count: 0,
        effective: {mode: 'ask_first', what: 'replies_only', to: 'anyone', daily_cap: 50, mailbox_daily_cap: 200,
          company_daily_cap: 1000, pacing_seconds: 10, undo_seconds: 120, ask_new_address: true, ask_attachments: true,
          ask_many_recipients: true, ask_legal_money: true, who_can_ok: 'bot_managers', approvers: [], sources: {mode: 'company'}}});
    }
    if (p === '/api/v2/email/sends') return json({sends: [], next_cursor: null});
    if (p === '/api/v2/mail/messages') return json({messages: structuredClone(mail[url.searchParams.get('mailbox') ?? ''] || [])});
    if (method === 'GET' && p in EMPTY) return json(EMPTY[p]!());
    if (p in SHELL) return json(SHELL[p]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto(`https://tico-ui.test/${hash}`);
  // Set once the document exists: an init script runs before <html> does.
  if (theme) await page.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme);
  if (ready) await page.locator(ready).first().waitFor();
  return {page, seen};
}

const api = {box, mailboxesFake, shot, open};
export type Api = typeof api;
module.exports = api;
