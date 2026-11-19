import { describe, expect, it } from 'vitest';
import { newAddressFor, ROWS, rowFor } from './map';

/* At least one case per row of the table (the last test checks that), with made-up ids. */
const CASES: [old: string, now: string | null][] = [
  ['#/file/f_7k2?v=3&task=41', '/library/files?file=f_7k2&v=3'],
  ['#/settings?recruit=%7B%22title%22%3A%22QA%22%2C%22brief%22%3A%22x%22%7D', '/settings/agents'],
  ['#/decide', '/flow'],
  ['#/decide?task=41', '/flow/41'],
  ['#/goals', '/company/strategy/goals'],
  ['#/goals/g_12', '/company/strategy/goals?panel=goal:g_12'],
  ['#/settings/listening', '/company/listening'],
  ['#/strategy/market/listening', '/company/listening'],
  ['#/library', '/library/docs'],
  ['#/strategy/growth?segment=rental', '/company/growth'],
  ['#/strategy/gtm/sources', '/company/growth'],
  ['#/strategy/gtm', '/company/strategy'],
  ['#/recurring', '/company/routines'],
  ['#/tasks/routines', '/company/routines'],
  ['#/automations', '/company/routines'],
  ['#/automations/au_9', '/company/routines?panel=routine:au_9'],
  ['#/automations/au_9/runs', '/company/routines?panel=routine:au_9'],
  ['#/automations/webhooks', '/company/routines?tab=webhooks'],
  ['#/automations/webhooks/wh_3', '/company/routines?tab=webhooks&panel=hook:wh_3'],
  ['#/automations/system-jobs', '/company/routines?panel=builtin:system'],
  ['#/automations/heartbeat', '/company/routines?panel=builtin:heartbeat'],
  ['#/listening', '/company/listening'],
  ['#/listening/au_4', '/company/listening?watch=au_4'],
  ['#/library/tools', '/library/tools'],
  ['#/library/tools/api/t_1', '/library/tools?tool=api:t_1'],
  ['#/library/tools/credentials/k_1', '/library/tools'],
  ['#/integrations/website/s_2', '/library/tools?tool=website:s_2'],
  ['#/library/connections/slack', '/library/tools?tool=slack'],
  ['#/credentials', '/library/tools'],
  ['#/settings/inboxes', '/library/tools?kind=mailbox'],
  ['#/settings/inboxes/messages', '/threads/messaging'],
  ['#/settings/inboxes/mailbox/ops%40example.test', '/library/tools?tool=mailbox:ops%40example.test'],
  ['#/settings/mailboxes/ops%40example.test', '/library/tools?tool=mailbox:ops%40example.test'],
  ['#/settings/google', '/library/tools?kind=mailbox'],
  ['#/tools', '/library/tools'],
  ['#/tools?issues=1&q=stripe', '/library/tools?q=stripe&needs=1'],
  ['#/tools/api/t_1', '/library/tools?tool=api:t_1'],
  ['#/tools/api/t_1/activity', '/library/tools?tool=api:t_1'],
  ['#/tools/website/s_2', '/library/tools?tool=website:s_2'],
  ['#/tools/vendor/acme', '/library/tools?tool=vendor:acme'],
  ['#/tools/keys?show=credentials', '/library/tools'],
  ['#/tools/key/k_1', '/library/tools?tool=key:k_1'],
  ['#/tools/webhooks', '/library/tools?kind=webhook'],
  ['#/tools/webhooks/wh_5', '/library/tools?tool=webhook:wh_5'],
  ['#/tools/mailbox/ops%40example.test', '/library/tools?tool=mailbox:ops%40example.test'],
  ['#/tools/number/n_8', '/library/tools?tool=number:n_8'],
  ['#/tools/address/intake', '/library/tools?tool=address:intake'],
  ['#/tools/settings/email', '/library/tools?tool=mailbox'],
  ['#/tools/settings', '/library/tools?tool=mailbox'],
  ['#/tools/settings/texts', '/library/tools?tool=number'],
  ['#/tools/settings/letters-out', '/library/tools?tool=address'],
  ['#/tools/github?github=installed', '/library/tools?tool=github&github=installed'],
  ['#/tools/slack', '/library/tools?tool=slack'],
  ['#/strategy/brand', '/company/strategy/brand'],
  ['#/growth', '/company/growth'],
  ['#/risk/disputes', '/company/risk/disputes'],
  ['#/risk/r/late-fees', '/company/risk?risk=late-fees'],
  ['#/risk', '/company/risk'],
  ['#/risk/obligations', '/company/risk'],
  ['#/rules', '/company/rules'],
  ['#/rules/builtin', '/company/rules?tab=built-in'],
  ['#/rules/external', '/company/rules?tab=outside'],
  ['#/products', '/company/products'],
  ['#/products/cleaning', '/company/products?panel=cleaning'],
  ['#/library/skills', '/library/skills'],
  ['#/library/skills/deploy', '/library/skills?skill=deploy'],
  ['#/library/skills/proposals/p_3', '/library/skills?proposal=p_3'],
  ['#/library/contacts/c_5?type=person', '/library/contacts?type=person&panel=c_5'],
  ['#/library/directory', '/library/contacts'],
  ['#/library/files?q=lease', '/library/files?q=lease'],
  ['#/page/pg_1?v=2', '/library/files?file=pg_1&v=2'],
  ['#/strategy/goals/g_12', '/company/strategy/goals?panel=goal:g_12'],
  ['#/strategy/org', '/company/strategy/org'],
  ['#/strategy/budget', '/company/finance?tab=budgets'],
  ['#/finance', '/company/finance'],
  ['#/finance/pnl', '/company/finance?tab=pnl'],
  ['#/finance/mine', '/company/finance'],
  ['#/company-page', '/company'],
  ['#/notes', '/calendar?view=list'],
  ['#/recordings?recording=m_4', '/calendar?panel=meeting:m_4'],
  ['#/call/room_1', '/call/room_1'],
  ['#/inbox', '/threads/mail'],
  ['#/home', '/company'],
  ['#/overview', '/company'],
  ['#/overview/campus', '/company'],
  ['#/overview/floor/f_1', '/company'],
  ['#/overview/company-page', '/company'],
  ['#/team/engineering', '/team/engineering'],
  ['#/welcome', '/start?setup=path'],
  ['#/roadmap', '/roadmap'],
  ['#/tags', '/tags'],
  ['#/tag/bug', '/tags/bug'],
  ['#/task/412', '/task/412'],
  ['#/tasks/412', '/task/412'],
  ['#/chat', '/tasks'],
  ['#/meetings/live/lm_1', '/calendar'],
  ['#/meetings', '/calendar?view=list'],
  ['#/meetings?meeting=m_4', '/calendar?panel=meeting:m_4'],
  ['#/meetings?live=lm_1', '/calendar'],
  ['#/bot/qa-agent', '/team/qa-agent'],
  ['#/bot/qa-agent/settings', '/team/qa-agent?tab=settings'],
  ['#/bot/qa-agent/more', '/team/qa-agent'],
  ['#/threads', '/threads'],
  ['#/calendar?view=3day&date=2026-03-04', '/calendar?view=3day&date=2026-03-04'],
  ['#/threads/new', '/threads/new'],
  ['#/thread/th_9?m=msg_2', '/threads/th_9'],
  ['#/seat/s_1/ops', '/settings/agents'],
  ['#/assistant', '/'],
  ['#/assistant/learned', '/learned'],
  ['#/skills/connect?code=WXYZ-2345', '/library/skills?connect=WXYZ-2345'],
  ['#/person/h_3', '/team/h_3'],
  ['#/person/h_3/settings', '/team/h_3?tab=settings'],
  ['#/tasks', '/tasks'],
  ['#/tasks?view=foryou', '/flow'],
  ['#/tasks?owner=bot:qa&tag=bug', '/tasks'],
  ['#/updates', '/updates'],
  ['#/updates?kind=weekly&update=u_1', '/updates/weekly?open=u_1'],
  ['#/updates?view=archive', '/updates/archive'],
  ['#/settings', '/settings'],
  ['#/help', '/company?chat=support'],
  ['#/getting-started', '/tasks'],
  ['#/repositories', '/settings/general'],
  ['#/triggers', '/company/routines?tab=webhooks'],
  ['#/settings/webhooks/outgoing', '/library/tools?kind=webhook'],
  ['#/settings/webhooks/gateway', '/settings/models'],
  ['#/health', '/settings/health'],
  ['#/settings/roadmap', '/settings/tasks'],
  ['#/settings/storage', '/settings/general'],
  ['#/settings/rules', '/company/routines?panel=builtin:system'],
  ['#/settings/people', '/settings/people'],
  ['#/settings/computers', '/settings/computers'],
  ['#/subscriptions', '/settings/agents'],
  ['#/settings/ceo', '/team/strategy-owner?tab=settings'],
  ['#/settings/bots', '/settings/agents'],
  ['#/settings/devices', '/settings/computers'],
  ['#/settings/devices/settings-tokens', '/settings/account'],
  ['#/settings/devices/set-gateway', '/settings/models'],
  ['#/settings/general/set-repos', '/settings/general'],
  ['#/settings/models/wh-gateway', '/settings/models'],
  ['#/settings/history?view=runs&failed=1', '/settings/history'],
  ['#/settings/history?who=bot:qa', '/settings/history?who=bot:qa'],
  ['#/settings/system/set-general-manager-card', '/team/general-manager?tab=settings'],
  ['#/settings/system', '/company/routines?panel=builtin:system'],
  ['#/settings/security/set-screening-card', '/settings/security'],
  ['#/settings/releases', '/settings/agents'],
  ['#/settings/nonsense', '/settings'],
  ['#/sql', null],
  ['#/mail?mailbox=mb_1&thread=t_2', '/threads/mail?box=mb_1&open=t_2'],
  ['#/messaging?bot=qa', '/threads/messaging'],
  ['#/changelog', '/settings/changelog'],
  ['#/market?note=n_1', '/company/strategy/market'],
  ['#/strategy/market/briefing', '/company/strategy/market'],
  ['#/strategy/lenses', '/company/strategy/market'],
  ['#/strategy', '/company/strategy'],
  ['#/docs/d_1?collection=market', '/company/strategy/market'],
  ['#/docs', '/library/docs'],
  ['#/docs/d_1', '/library/d_1'],
  ['#/runs', '/settings/history'],
  ['#/learnings', '/learned'],
  ['#/usage', '/company/usage'],
  ['#/replay', null],
  ['#/computers/pc_1/screen', '/settings/computers?open=pc_1'],
  ['#/computers/pc_1/performance', '/settings/computers?open=pc_1'],
  ['#/style-guide', '/settings/style-guide'],
  ['#/', '/'],
];

describe('newAddressFor', () => {
  it.each(CASES)('%s', (old, now) => {
    expect(newAddressFor(old)).toBe(now);
  });

  it('every row has a case', () => {
    const hit = new Set(CASES.map(([old]) => rowFor(old)?.row));
    expect(ROWS.filter((r) => !hit.has(r)).map((r) => r.page)).toEqual([]);
  });

  it('opens the known return addresses', () => {
    expect(newAddressFor('/#/skills/connect?code=WXYZ-2345')).toBe('/library/skills?connect=WXYZ-2345');
    expect(newAddressFor('#/skills/connect')).toBe('/library/skills?connect=setup');
    expect(newAddressFor('#/tools/api/t_77')).toBe('/library/tools?tool=api:t_77');
    expect(newAddressFor('#/tools/website/s_77')).toBe('/library/tools?tool=website:s_77');
  });

  it('carries only the QuickBooks and Xero return words, dropping anything else', () => {
    expect(newAddressFor('#/finance?books=connected')).toBe('/company/finance?tab=sources&books=connected');
    expect(newAddressFor('#/finance?books=error&reason=access_denied')).toBe('/company/finance?tab=sources&books=error&reason=access_denied');
    expect(newAddressFor('#/finance?books=connected&code=c0de-xyz&state=forged&reason=access_denied')).toBe('/company/finance?tab=sources&books=connected');
    expect(newAddressFor('#/finance?books=error&reason=%3Cb%3Ex%3C%2Fb%3E')).toBe('/company/finance?tab=sources&books=error');
    expect(newAddressFor('#/finance?books=https://evil.example')).toBe('/company/finance?tab=sources');
  });

  it("carries only GitHub's return word, dropping anything else", () => {
    expect(newAddressFor('#/tools/github?github=declined')).toBe('/library/tools?tool=github&github=declined');
    expect(newAddressFor('#/tools/github?github=installed&code=c0de-xyz&state=forged&installation_id=99')).toBe('/library/tools?tool=github&github=installed');
    expect(newAddressFor('#/tools/github?github=javascript:alert(1)')).toBe('/library/tools?tool=github');
  });

  it("carries a skills helper's code only in its shape", () => {
    expect(newAddressFor('#/skills/connect?code=WXYZ-2345&next=https://evil.example')).toBe('/library/skills?connect=WXYZ-2345');
    expect(newAddressFor('#/skills/connect?code=%3Cscript%3E')).toBe('/library/skills?connect=setup');
  });

  it('takes the address with or without its #', () => {
    expect(newAddressFor('task/412')).toBe('/task/412');
    expect(newAddressFor('/task/412')).toBe('/task/412');
    expect(newAddressFor('#/task/412/')).toBe('/task/412');
  });

  it('keeps encoded ids encoded once', () => {
    expect(newAddressFor('#/bot/qa%20agent/settings')).toBe('/team/qa%20agent?tab=settings');
    expect(newAddressFor('#/thread/th%2F9')).toBe('/threads/th%2F9');
    expect(newAddressFor('#/tag/needs%20design')).toBe('/tags/needs%20design');
    expect(newAddressFor('#/goals/g%3A1')).toBe('/company/strategy/goals?panel=goal:g:1');
    // a malformed escape typed by hand is used as it is
    expect(newAddressFor('#/task/%E0%A4%A')).toBe('/task/%25E0%25A4%25A');
  });

  it('quotes a search value the router would read as a number', () => {
    expect(newAddressFor('#/updates?update=12345')).toBe('/updates?open=%2212345%22');
    expect(newAddressFor('#/computers/77/screen')).toBe('/settings/computers?open=%2277%22');
  });

  it('knows nothing of addresses the old app did not have', () => {
    expect(newAddressFor('#/nowhere')).toBeNull();
    expect(newAddressFor('#/taskboard')).toBeNull();
    expect(newAddressFor('#/settings/Bad_Tab')).toBeNull();
    expect(newAddressFor('#/thread')).toBeNull();
  });
});
