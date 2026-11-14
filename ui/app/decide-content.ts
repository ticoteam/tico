/* ui/app/decide-content.ts — what an OK is for, in words, before Approve (ui/app/decide.ts decideOkLoad). Each kind of
   OK whose module has no preview of its own (backend/task_asks.py OWNED and GENERIC) opens on a summary: what will
   happen, to whom, how much, what changes. The exact content the OK is bound to (its hash covers all of it) stays one tap
   away under Details, unchanged. Ids, hashes and nested objects show only there.
   - finance (backend/finance/actions.py _approval_payload): Finance's own sentence (`what`), the amount in the book's
     currency (minor units in the content), the bank it is paid from and the line it posts to by name, a rule's words and
     examples. Names read from GET /v2/finance/pay-from and /v2/finance/chart; one that does not load is left out.
   - edition, board (backend/company_page/editions.py): title, who it goes to, the period, the link, the text.
   - proposal (backend/proposals.py): one line per change, from and to (GET /v2/usage/limits, /v2/task-caps).
   - webhook (backend/webhooks.py _spec): the endpoint's name, its address and what it sends (GET /v2/event-types).
   - anything else (budget raise, HQ reply, a new kind): its title and text, then its plain fields as labelled lines.
   No route here changes anything, and nothing here changes what Approve sends (the OK's shown_hash).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type DcObj = Record<string, unknown>;
const dcObj = (x: unknown): DcObj => (x && typeof x === 'object' && !Array.isArray(x) ? x as DcObj : {});
const dcStr = (x: unknown) => (typeof x === 'string' ? x : typeof x === 'number' ? String(x) : '');
// An id or a hash: never on the first screen.
const DC_HIDDEN_KEY = /(^|_)(id|ids|sha256|hash|digest|revision|source)$/i;
const DC_HIDDEN_VALUE = /^(?:[0-9a-f]{24,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?:bot|human|ep|act|fin)_[A-Za-z0-9]{6,})$/i;
const DC_LABELS: Record<string, string> = {to: 'To', from: 'From', cc: 'Cc', bcc: 'Bcc', usd: 'Amount', project: 'Project',
  audience: 'Goes to', period: 'Period', url: 'Link', memo: 'Memo', reason: 'Why'};
const dcLabel = (k: string) => DC_LABELS[k] || (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, ' ');
const dcFacts = (rows: [string, string][]) => rows.length
  ? `<dl class="dc-facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>` : '';
const dcLink = (url: string) => /^(https?:\/\/|\/)/.test(url) ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a>` : esc(url);
/** The exact content, as the OK is bound to it, behind a Details toggle. */
function decideDetailsHTML(content: unknown): string {
  return `<details class="dc-details"><summary>Details</summary><pre class="dc-raw">${esc(JSON.stringify(content, null, 2))}</pre></details>`;
}
/** Money in minor units, in its currency ($1,920.00 for 192000 USD). */
function decideMoney(minor: unknown, currency = 'USD'): string {
  const n = Number(minor);
  if (minor == null || minor === '' || !Number.isFinite(n)) return '';
  let f: Intl.NumberFormat;
  try { f = new Intl.NumberFormat('en-US', {style: 'currency', currency}); } catch { f = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}); }
  return f.format(n / 10 ** (f.resolvedOptions().maximumFractionDigits ?? 2));
}
const decideUsd = (x: unknown) => { const n = Number(x); return x == null || x === '' || !Number.isFinite(n) ? '' : new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(n); };

/** What an OK of `kind` is for: its summary, then Details. Text content is itself the summary. */
async function decideOkContentHTML(kind: string, content: unknown): Promise<string> {
  if (content == null || content === '') return '';
  if (typeof content === 'string') return `<div class="md">${md(content)}</div>`;
  let head = '';
  try {
    head = kind === 'finance' ? await decideFinanceHTML(content)
      : kind === 'edition' || kind === 'board' ? decideEditionHTML(content)
      : kind === 'proposal' ? await decideProposalHTML(content)
      : kind === 'webhook' ? await decideWebhookHTML(content)
      : '';
  } catch { head = ''; }
  return (head || decideFieldsHTML(content)) + decideDetailsHTML(content);
}

/** Any object: its title and text, then its plain fields (text, numbers, yes/no, lists of text) as labelled lines. */
function decideFieldsHTML(content: unknown): string {
  if (Array.isArray(content)) return '';
  const o = dcObj(content);
  const textKey = typeof o.body === 'string' ? 'body' : typeof o.text === 'string' ? 'text' : typeof o.message === 'string' ? 'message' : '';
  const titleKey = typeof o.subject === 'string' ? 'subject' : typeof o.title === 'string' ? 'title' : typeof o.name === 'string' ? 'name' : '';
  const rows: [string, string][] = [];
  for (const [k, v] of Object.entries(o)) {
    if (k === textKey || k === titleKey || DC_HIDDEN_KEY.test(k)) continue;
    const list = Array.isArray(v) && v.every(x => typeof x === 'string' || typeof x === 'number') ? v.map(String) : null;
    const s = list ? list.join(', ') : typeof v === 'boolean' ? (v ? 'Yes' : 'No') : dcStr(v);
    if (!s.trim() || (!list && DC_HIDDEN_VALUE.test(s))) continue;
    rows.push([dcLabel(k), k === 'usd' ? esc(decideUsd(v) || s) : k === 'url' ? dcLink(s) : esc(s)]);
  }
  return (titleKey ? `<h3 class="dc-title">${esc(String(o[titleKey]))}</h3>` : '') + dcFacts(rows)
    + (textKey ? `<div class="md">${md(String(o[textKey]))}</div>` : '');
}

// ---- Finance
interface DcFinNames { banks: Map<string, string>; lines: Map<string, string>; currency: string }
async function decideFinanceNames(): Promise<DcFinNames> {
  const [pay, chart] = await Promise.all([
    get<{accounts?: {id: string; name: string; last4?: string | null}[]; currency?: string}>('/v2/finance/pay-from', {fresh: true}).catch(() => null),
    get<{accounts?: {id: string; number?: string | null; name: string}[]}>('/v2/finance/chart', {fresh: true}).catch(() => null)]);
  return {
    banks: new Map((pay?.accounts || []).map(a => [a.id, `${a.name}${a.last4 ? ' ••' + a.last4 : ''}`])),
    lines: new Map((chart?.accounts || []).map(a => [a.id, a.number ? `${a.name} · ${a.number}` : a.name])),
    currency: pay?.currency || 'USD'};
}
const DC_EXPECT: Record<string, string> = {post: 'posts', ask: 'asks', none: 'not matched', allow: 'allowed', deny: 'refused',
  needs_approval: 'needs approval', limit: 'limited'};
async function decideFinanceHTML(content: unknown): Promise<string> {
  const o = dcObj(content), p = dcObj(o.payload);
  const what = dcStr(o.what).replace(/\s*\(Finance action [^)]*\)\s*$/, '').trim();
  const names = await decideFinanceNames();
  const money = (n: unknown) => decideMoney(n, names.currency);
  const bank = (id: unknown) => names.banks.get(dcStr(id)) || '';
  const line = (id: unknown) => names.lines.get(dcStr(id)) || '';
  const rows: [string, string][] = [];
  const amount = o.amount ?? p.amount;
  if (typeof amount === 'number') rows.push(['Amount', esc(money(amount))]);
  const from = bank(p.money_account_id) || bank(p.from_account) || bank(o.account);
  if (from) rows.push(['Paid from', esc(from)]);
  if (bank(p.to_account)) rows.push(['Into', esc(bank(p.to_account))]);
  if (line(p.account_id)) rows.push(['Posts to', esc(line(p.account_id))]);
  if (typeof p.memo === 'string' && p.memo.trim()) rows.push(['Memo', esc(p.memo)]);
  const words = dcStr(p.words).trim();
  if (words && !what.includes(words)) rows.push(['Rule', esc(words)]);
  const ex = Array.isArray(p.examples) ? p.examples.map(dcObj) : [];
  if (ex.length) rows.push(['Examples', ex.map(e => {
    const input = dcObj(e.input), to = line(e.account_id ?? e.account) || line(dcObj(p.outcome).account_id);
    const said = DC_EXPECT[dcStr(e.expect)] || dcStr(e.expect);
    return esc([dcStr(input.counterparty), typeof input.amount === 'number' ? money(input.amount) : ''].filter(Boolean).join(' ')
      + (said ? ` → ${said}${said === 'posts' && to ? ' to ' + to : ''}` : ''));
  }).join('<br>')]);
  if (!what && !rows.length) return '';
  return (what ? `<h3 class="dc-title">${esc(what)}</h3>` : '') + dcFacts(rows);
}

// ---- Company page edition, stats board
function decideEditionHTML(content: unknown): string {
  const o = dcObj(content), rows: [string, string][] = [];
  if (dcStr(o.audience)) rows.push(['Goes to', esc(dcStr(o.audience))]);
  if (dcStr(o.period)) rows.push(['Period', esc(dcStr(o.period))]);
  if (dcStr(o.url)) rows.push(['Link', dcLink(dcStr(o.url))]);
  return (dcStr(o.title) ? `<h3 class="dc-title">${esc(dcStr(o.title))}</h3>` : '') + dcFacts(rows)
    + (dcStr(o.text) ? `<div class="md">${md(dcStr(o.text))}</div>` : '');
}

// ---- A proposal: a task cap or a spend limit
const DC_PERIODS: [string, string][] = [['per_30m_usd', 'every 30 minutes'], ['hourly_usd', 'an hour'], ['daily_usd', 'a day'],
  ['weekly_usd', 'a week'], ['monthly_usd', 'a month']];
async function decideProposalHTML(content: unknown): Promise<string> {
  const o = dcObj(content), change = dcObj(o.change), lines: string[] = [];
  const who = (id: unknown) => { const s = dcStr(id); return s.includes(':') ? actorLabel(s) : s ? actorLabel('bot:' + s) : ''; };
  if (o.kind === 'task_cap') {
    const caps = await get<{caps?: {sender: string; receiver: string; per_day?: number | null}[]}>('/v2/task-caps', {fresh: true}).catch(() => null);
    const was = caps?.caps?.find(c => c.sender === change.sender && c.receiver === change.receiver);
    const say = (n: unknown) => n == null ? 'no cap' : Number(n) === 0 ? 'none (blocked)' : `${n} a day`;
    lines.push(`Tasks ${who(change.sender)} may send ${who(change.receiver)}: ${caps ? say(was?.per_day) + ' → ' : ''}${say(change.per_day)}`);
  } else if (o.kind === 'spend_limit') {
    const limits = await get<{bots?: Record<string, DcObj>; pools?: {company?: DcObj | null; groups?: Record<string, DcObj | null>} | null}>('/v2/usage/limits', {fresh: true}).catch(() => null);
    const groups = String(change.pool || '').startsWith('group:')
      ? await get<{id: string; name?: string}[] | {groups?: {id: string; name?: string}[]}>('/v2/groups', {fresh: true}).catch(() => null) : null;
    const gid = String(change.pool || '').slice(6);
    const gname = (Array.isArray(groups) ? groups : groups?.groups || []).find(g => g.id === gid)?.name;
    const name = change.bot ? who(change.bot) : change.pool === 'company' ? 'the company' : gname ? `the ${gname} group` : 'a group';
    const bot = dcStr(change.bot).replace(/^bot:/, '');
    const now = limits ? (change.bot ? dcObj(limits.bots?.[bot] ?? limits.bots?.['bot:' + bot])
      : change.pool === 'company' ? dcObj(limits.pools?.company) : dcObj(limits.pools?.groups?.[gid])) : null;
    for (const [key, per] of DC_PERIODS) {
      if (!(key in change)) continue;
      const say = (x: unknown) => decideUsd(x) || 'no limit';
      lines.push(`Spend limit for ${name}: ${now ? say(now[key]) + ' → ' : ''}${say(change[key])} ${per}`);
    }
  }
  return lines.length ? `<h3 class="dc-title">${esc(lines[0]!)}</h3>${lines.slice(1).map(l => `<p class="dc-sum">${esc(l)}</p>`).join('')}` : '';
}

// ---- A webhook endpoint
async function decideWebhookHTML(content: unknown): Promise<string> {
  const o = dcObj(content);
  const events = Array.isArray(o.events) ? o.events.map(String) : [];
  let sends = 'All events';
  if (events.length && !events.includes('*')) {
    const types = await get<{items?: {name: string; label?: string; description?: string; aliases?: string[]}[]}>('/v2/event-types').catch(() => null);
    const word = (n: string) => { const t = types?.items?.find(x => x.name === n || (x.aliases || []).includes(n)); return t?.label || (t?.description || '').replace(/\.$/, '') || n; };
    sends = events.map(word).join(', ');
  }
  const rows: [string, string][] = [['Address', dcLink(dcStr(o.url))], ['Sends', esc(sends)]];
  if (typeof o.rate_per_hour === 'number') rows.push(['At most', esc(`${o.rate_per_hour} an hour`)]);
  if (o.allow_http === true) rows.push(['Plain http', 'Allowed (not encrypted)']);
  return `<h3 class="dc-title">${esc(`Make the webhook endpoint "${dcStr(o.name)}"`)}</h3>` + dcFacts(rows);
}
