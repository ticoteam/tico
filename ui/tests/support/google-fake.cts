// A fake of the Google tab's routes (the contract in _brief-google-contract.md) for ui/tests/google-tab.cts.
// answer(path, url, method, body) gives the JSON a route answers, or undefined for any other path, as threadsFake does.
// `fake.state` is what GET /v2/google sends and a test sets it directly; `fake.personal` is the person's own state, and
// `fake.people` the team list GET /v2/google/people sends (an admin's only; `fake.peopleReads` counts its reads),
// `fake.peoplePage` people a page, after the `after` person id as the server pages it.
// Every write is kept in `fake.writes` as {method, path, body}. `fake.checks` is a queue of answers for the next
// POST /workspace/check: 'ok', 'unauthorized' (the default when the queue is empty) or 'other'.
// Every key view and setup code carries a `generation` (row() gives 1 unless set). A new key or code takes the next one.
const SCOPES = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/gmail.modify'];
const CLIENT_ID = '1234567890-acme.apps.googleusercontent.com';
const ago = (minutes: number): string => new Date(Date.now() - minutes * 60000).toISOString();
const soon = (minutes: number): string => new Date(Date.now() + minutes * 60000).toISOString();
const delegation = `https://admin.google.com/ac/owl/domainwidedelegation?clientIdToAdd=${CLIENT_ID}`
  + `&clientScopeToAdd=${encodeURIComponent(SCOPES.join(','))}&overwriteClientId=true`;

// A workspace row in the contract's shape: status 'pending' | 'connected' | 'failing'.
export interface GoogleRow {
  status: string;
  domain: string;
  project_id: string;
  client_id: string;
  scopes: string[];
  delegation_url: string;
  connected_at: string | null;
  checked_at: string | null;
  last_error: string | null;
  probe: unknown;
  generation: number;
  [key: string]: unknown;
}
export interface GoogleState {
  can_admin: boolean;
  workspace: GoogleRow | null;
  pending: GoogleRow | null;
  me?: {mailbox: string; covered_by_workspace: boolean} | null;
  oauth: {configured: boolean; source: string | null; redirect_uri: string; client_id: string | null};
  setup: {base: string; reachable: boolean; newest?: number};
  permissions: {organize_mail: boolean; send_mail: boolean; change_calendar: boolean};
  [key: string]: unknown;
}
export interface GooglePerson {
  person_id: string;
  name: string;
  mailbox: string;
  status: string;
}
export interface GoogleMail {
  configured: boolean;
  mailbox: string;
  status: string;
  syncing: boolean;
  can: string[];
}
export interface GoogleWrite {
  method: string;
  path: string;
  body: unknown;
}
// The answer to a queued POST /workspace/check.
export type GoogleCheck = 'ok' | 'unauthorized' | 'other';
// A route's JSON body as the test sent it (null for none).
type Body = Record<string, unknown> | null;
export interface GoogleFake {
  writes: GoogleWrite[];
  checks: GoogleCheck[];
  codes: number;
  gen: number;
  personal: GoogleMail | null;
  peopleReads: number;
  peoplePage: number;
  people: GooglePerson[];
  state: GoogleState;
  answer(path: string, url: string, method?: string, body?: unknown): unknown;
}

const row = (status: string, over: Partial<GoogleRow> = {}): GoogleRow => ({status, domain: 'acme.example', project_id: 'tico-mail-a1b2c3', client_id: CLIENT_ID,
  scopes: SCOPES, delegation_url: delegation, connected_at: status === 'pending' ? null : ago(90), checked_at: null,
  last_error: null, probe: null, generation: 1, ...over});
// A string the test sent, or the fallback when it sent none (as `value || fallback` read it).
const text = (value: unknown, fallback: string): string => typeof value === 'string' && value ? value : fallback;

function googleFake(): GoogleFake {
  // The next key or setup code takes the generation after the newest one the fake holds, as the server does.
  const newestGeneration = (): number => Math.max(fake.gen, fake.state.workspace?.generation ?? 0, fake.state.pending?.generation ?? 0);
  const answer = (p: string, url: string, method = 'GET', body: Body = null): unknown => {
    const s = fake.state;
    if (method !== 'GET') fake.writes.push({method, path: p, body});
    if (p === '/api/v2/google' && method === 'GET') {
      const out = structuredClone(s);
      if (out.setup) out.setup.newest = newestGeneration();
      return out;
    }
    if (p === '/api/v2/google/permissions' && method === 'PUT') {
      s.permissions = {organize_mail: !!body?.organize_mail, send_mail: !!body?.send_mail, change_calendar: !!body?.change_calendar};
      return {permissions: structuredClone(s.permissions)};
    }
    if (p === '/api/v2/google/people' && method === 'GET') {
      fake.peopleReads += 1;
      if (!s.can_admin) return undefined;
      const after = new URL(url).searchParams.get('after');
      const start = after ? fake.people.findIndex(person => person.person_id === after) + 1 : 0;
      const page = fake.people.slice(start, start + fake.peoplePage);
      const more = start + fake.peoplePage < fake.people.length;
      return {people: structuredClone(page), more, next: more ? page[page.length - 1]!.person_id : null};
    }
    if (p === '/api/v2/google/workspace/setup-code' && method === 'POST') {
      fake.codes += 1;
      fake.gen = newestGeneration() + 1;
      return {code: `code${fake.codes}`, command: `curl -fsSL https://tico-ui.test/api/v2/google/setup.sh | bash -s -- code${fake.codes}`,
        expires_at: soon(10), generation: fake.gen};
    }
    if (p === '/api/v2/google/workspace/key' && method === 'POST') {
      const made = row('pending', {domain: text(body?.domain, 'acme.example'), generation: newestGeneration() + 1});
      fake.gen = made.generation;
      if (s.workspace && s.workspace.status !== 'pending') s.pending = made;
      else s.workspace = made;
      return {client_id: CLIENT_ID, delegation_url: delegation, keep_key_ids: []};
    }
    if (p === '/api/v2/google/workspace/check' && method === 'POST') {
      const verdict: GoogleCheck = fake.checks.shift() || 'unauthorized';
      const mailbox = text(body?.mailbox, s.me?.mailbox || 'ana@acme.example');
      if (verdict === 'ok') {
        // A passing check records the scopes Google took, so the key narrows (backend _settle).
        const promoted = {...(s.pending || s.workspace)!, status: 'connected', connected_at: ago(0), narrowable: true};
        s.pending = null;
        s.workspace = promoted;
        return {ok: true, mailbox, error_kind: null, message: '', workspace: promoted};
      }
      if (verdict === 'other') return {ok: false, mailbox, error_kind: 'other', message: 'Gmail did not answer. Try again in a minute.', workspace: s.workspace};
      return {ok: false, mailbox, error_kind: 'unauthorized_client', message: 'Google refused the key for this mailbox.',
        workspace: s.pending || s.workspace};
    }
    if (p === '/api/v2/google/workspace' && method === 'DELETE') { s.workspace = null; s.pending = null; return {ok: true}; }
    if (p === '/api/v2/google/oauth-client' && method === 'PUT') {
      s.oauth = {configured: true, source: 'settings', redirect_uri: s.oauth.redirect_uri, client_id: body!.client_id as string};
      return {oauth: s.oauth};
    }
    if (p === '/api/v2/google/oauth-client' && method === 'DELETE') {
      s.oauth = {configured: false, source: null, redirect_uri: s.oauth.redirect_uri, client_id: null};
      return {oauth: s.oauth};
    }
    if (p === '/api/v2/mail/google' && method === 'GET') return fake.personal;
    if (p === '/api/v2/mail/google/connect' && method === 'POST') return {auth_url: 'https://accounts.google.com/o/oauth2/v2/auth?client_id=x', mailbox: 'ana@acme.example'};
    if (p === '/api/v2/mail/google/disconnect' && method === 'POST') return {ok: true};
    return undefined;
  };
  const fake: GoogleFake = {
    writes: [], checks: [], codes: 0, gen: 0, peopleReads: 0, peoplePage: 500,
    people: [
      {person_id: 'ana', name: 'Ana', mailbox: 'ana@acme.example', status: 'connected'},
      {person_id: 'ben', name: 'Ben', mailbox: 'ben@acme.example', status: 'none'},
      {person_id: 'cara', name: 'Cara', mailbox: 'cara@acme.example', status: 'workspace'},
      {person_id: 'dan', name: 'Dan', mailbox: 'dan@acme.example', status: 'pending'},
    ],
    state: {
      can_admin: true, workspace: null, pending: null,
      me: {mailbox: 'ana@acme.example', covered_by_workspace: false},
      oauth: {configured: false, source: null, redirect_uri: 'https://tico-ui.test/api/v2/mail/google/callback', client_id: null},
      setup: {base: 'https://tico-ui.test', reachable: true},
      permissions: {organize_mail: true, send_mail: true, change_calendar: true},
    },
    personal: {configured: true, mailbox: 'ana@acme.example', status: 'connected', syncing: true, can: ['read']},
    answer,
  };
  return fake;
}

const api = {row, googleFake};
export type Api = typeof api;
module.exports = api;
