// Ad campaigns under each ad account on a Google Ads tool's Finance card (ui/app/finance-campaigns.ts, tools-api.js) over a
// fake API in backend/finance_ads_routes.py's and backend/finance/campaigns.py's shapes. Covers: the campaigns of one
// account under it (Campaign, Spend, Clicks, Conv., Per conv., Owner) with an account that has none showing no rows; a
// campaign with no conversions reads "—" per conversion; the owner picker shows the saved owner, blank is the account's
// owner; a change posts campaign_owner_set with team or teammate (blank posts none) and the card reads its campaigns
// again; desktop and 390px with no sideways scroll (the table scrolls in its container).
// TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {mailboxesFake, open} = require('./support/mailboxes-fake.cts') as import('./support/mailboxes-fake.cts').Api;
const {googleFake} = require('./support/google-fake.cts') as import('./support/google-fake.cts').Api;
const {t} = require('./support/load.cts') as import('./support/load.cts').Api;

type ActionBody = {kind: string; payload: {campaign_id: string; teammate?: string | undefined; team?: string | undefined}};

const iso = '2026-11-05T12:00:00Z';
const key = (id: string, name: string) => ({id, name, preview: 'dt_…01ab', access_level: 'read_write', expires_on: null, env: 'GOOGLE_ADS_DEVELOPER_TOKEN',
  username: '', stored: true, created_by: 'human:ana', created_at: iso, last_used_at: null, last_used_by: ''});

function fake() {
  const state: {writes: {method: string; path: string; body: ActionBody}[]; campaigns: {id: string; name: string; platform: string; ad_account: {id: string; name: string}; owner: {inherited: boolean; teammate?: string | undefined; team?: string | undefined}; month: {spend: number; clicks: number; conversions: number; per_conversion: number | null}; as_of: string}[]} = {writes: [], campaigns: [
    {id: 'camp-1', name: 'Brand search', platform: 'google_ads', ad_account: {id: 'ad-1', name: 'Consumer search'},
      owner: {inherited: true}, month: {spend: 50000, clicks: 420, conversions: 12.5, per_conversion: 4000}, as_of: iso},
    {id: 'camp-2', name: 'Rental remarketing', platform: 'google_ads', ad_account: {id: 'ad-1', name: 'Consumer search'},
      owner: {teammate: 'bot:paid-rental-ads', inherited: false}, month: {spend: 26000, clicks: 90, conversions: 0, per_conversion: null}, as_of: iso}]};
  const tool = {tool: {id: 't-gads', name: 'Google Ads', service: 'google-ads', base_url: '', auth_scheme: 'bearer', check_path: '', delivery: 'computer'},
    keys: [key('k-gads', 'Google Ads · GOOGLE_ADS_DEVELOPER_TOKEN')], access: [], checks: [], recent: [], seen_by: [],
    status: {tone: 'idle', text: 'Key on computers'}, needs_you: false, can_manage: true, private: false, can_set_audience: false};
  const fin = {tool_id: 't-gads', tool_name: 'Google Ads', platform: 'google_ads', platform_label: 'Google Ads',
    import: {saved: true, enabled: true, history_from: '2026-01-01', account_id: 'acct-ads', account_name: 'Advertising', status: 'ok',
      last_import_at: iso, last_error: '', run_requested: false, every_hours: 4},
    slots: [{slot: 'developer_token', label: 'Developer token', required: true, credential_id: 'k-gads', options: [{id: 'k-gads', name: 'Google Ads · GOOGLE_ADS_DEVELOPER_TOKEN'}]}],
    books_lines: [{id: 'acct-ads', code: '6300', name: 'Advertising'}], elsewhere: null, month: '2026-11', month_label: 'November',
    accounts: [{id: 'ad-1', external_id: '5550001111', name: 'Consumer search', currency: 'USD', bot_slug: 'sales', active: true, month_to_date: 76000},
      {id: 'ad-2', external_id: '5550002222', name: 'Paid rental', currency: 'USD', bot_slug: null, active: true, month_to_date: 0}]};
  const access = {teammates: [{teammate: 'human:ana', kind: 'person', team: '', default: 'everything', rights: ['finance.attribution']},
      {teammate: 'bot:paid-rental-ads', kind: 'bot', team: 'rental', default: 'own_expenses', rights: []}],
    teams: [{id: 'rental', name: 'Rental', parent: '', lead: ''}, {id: 'consumer', name: 'Consumer', parent: '', lead: ''}],
    defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1', private_lines: [],
    can_view_as: false, can_edit_defaults: false};
  const totals = () => ({spend: 76000, clicks: 510, conversions: 12.5, per_conversion: 6080});
  const answer = (p: string, method: string, body: ActionBody) => {
    if (p === '/api/v2/finance/ad-accounts' && method === 'GET') return structuredClone(fin);
    if (p === '/api/v2/finance/campaigns' && method === 'GET') return {month: '2026-11', campaigns: structuredClone(state.campaigns), totals: totals(), as_of: iso};
    if (p === '/api/v2/finance/access' && method === 'GET') return structuredClone(access);
    if (p === '/api/v2/finance/actions' && method === 'POST') {
      state.writes.push({method, path: p, body});
      if (body.kind === 'campaign_owner_set') {
        const c = state.campaigns.find(x => x.id === body.payload.campaign_id);
        if (c) c.owner = body.payload.teammate ? {teammate: body.payload.teammate, inherited: false}
          : body.payload.team ? {team: body.payload.team, inherited: false} : {teammate: 'bot:sales', inherited: true};
      }
      return {action_id: 'act-' + state.writes.length, kind: body.kind, status: 'committed', code: null, reason: null, approver: null, result: {}};
    }
    if (p === '/api/employees') return [{name: 'paid-rental-ads', display_name: 'Paid rental ads', status: 'active'},
      {name: 'sales', display_name: 'Sales', status: 'active'}];
    if (p === '/api/v2/api-tools/t-gads' && method === 'GET') return structuredClone(tool);
    return undefined;
  };
  return {state, answer};
}

const sideways = (page: import('playwright').Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page: import('playwright').Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, `finance-campaigns-${name}.png`), fullPage: true});
}
async function start(browser: import('playwright').Browser, width: number) {
  const f = fake();
  const got = await open(browser, mailboxesFake(), googleFake(), {hash: '#/tools/api/t-gads', width, height: width < 500 ? 844 : 900,
    ready: '#tool-finance tr.fc-campaigns', extra: (p, method, body, url) => (f.answer as (p: string, method: string, body: ActionBody, url: URL) => unknown)(p, method, body as ActionBody, url)});
  // Each owner write reaches the fake a little late, as on a loaded runner: an assert that reads the writes before the
  // card's POST lands then fails every run, not only on a busy CI box.
  await got.page.route('**/api/v2/finance/actions', async route => { await new Promise(r => setTimeout(r, t(300))); await route.fallback(); });
  return {...got, f};
}

// Picks an owner and waits for the save to finish: the write has reached the fake and the card has read its campaigns
// again (the picker is disabled while it saves and drawn enabled after the reload). Returns the write it posted.
async function pick(page: import('playwright').Page, f: ReturnType<typeof fake>, id: string, value: string) {
  const before = f.state.writes.length, sel = `#tool-finance select[data-fc-owner="${id}"]`;
  await page.locator(sel).selectOption(value);
  for (const end = Date.now() + t(5000); f.state.writes.length === before && Date.now() < end;) await new Promise(r => setTimeout(r, 25));
  assert.equal(f.state.writes.length, before + 1, `${id}: one owner write for ${value || 'blank'}`);
  // Without this wait the next pick can land on the picker the reload is about to replace, and its change is lost.
  await page.waitForFunction(([sel, value]) => { const el = document.querySelector(sel ?? '') as HTMLSelectElement | null; return !!el && !el.disabled && el.value === value; }, [sel, value]);
  return f.state.writes.at(-1)!.body;
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const width of [1440, 390]) {
      const tag = width < 500 ? 'phone' : 'desktop';
      const {page, seen, f} = await start(browser, width);
      const card = page.locator('#tool-finance');
      // Only the account with campaigns gets rows; the table reads as its columns say.
      assert.equal(await card.locator('tr.fc-campaigns').count(), 1, `${tag}: one account has campaigns`);
      assert.deepEqual(await card.locator('.fc-table thead th').allTextContents(), ['Campaign', 'Spend', 'Clicks', 'Conv.', 'Per conv.', 'Owner'], `${tag}: columns`);
      const one = card.locator('tr[data-fc-campaign="camp-1"] td');
      assert.deepEqual([await one.nth(0).innerText(), await one.nth(1).innerText(), await one.nth(2).innerText(), await one.nth(3).innerText(), await one.nth(4).innerText()],
        ['Brand search', '$500.00', '420', '12.5', '$40.00'], `${tag}: campaign figures`);
      assert.equal(await card.locator('tr[data-fc-campaign="camp-2"] td').nth(4).innerText(), '—', `${tag}: no conversions, no cost per conversion`);
      // The picker: blank is the account's owner; a teammate that owns the campaign is picked.
      assert.equal(await card.locator('select[data-fc-owner="camp-1"]').inputValue(), '', `${tag}: inherited owner is blank`);
      assert.equal(await card.locator('select[data-fc-owner="camp-1"] option').first().innerText(), 'Account owner', `${tag}: blank label`);
      assert.equal(await card.locator('select[data-fc-owner="camp-2"]').inputValue(), 'teammate:bot:paid-rental-ads', `${tag}: saved owner picked`);
      await shot(page, `${tag}-before`);
      // Blank posts campaign_owner_set with no owner, and the campaigns read again.
      assert.deepEqual(await pick(page, f, 'camp-2', ''), {kind: 'campaign_owner_set', payload: {campaign_id: 'camp-2'}}, `${tag}: blank write`);
      // A team picked posts team; the card shows it after the reload.
      assert.deepEqual(await pick(page, f, 'camp-1', 'team:rental'), {kind: 'campaign_owner_set', payload: {campaign_id: 'camp-1', team: 'rental'}}, `${tag}: team write`);
      // A teammate picked posts teammate.
      assert.deepEqual(await pick(page, f, 'camp-2', 'teammate:human:ana'), {kind: 'campaign_owner_set', payload: {campaign_id: 'camp-2', teammate: 'human:ana'}}, `${tag}: teammate write`);
      await shot(page, `${tag}-after`);
      assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
      assert.deepEqual(seen.errors, [], `${tag}: page errors`);
      await page.close();
    }
    console.log('Finance campaigns: rows under the account, owner picker writes and reloads, phone width');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
