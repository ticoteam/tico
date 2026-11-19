import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient, type Actor } from '@/data';
import { Toasts } from '@/kit/Toast';
import { FinancePage } from './Finance';
import { GrowthPage } from './Growth';
import type { GrowthRead } from './queries';
import { validateFinanceSearch, validateGrowthSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Tier 2 checks for Finance's dialogs and Growth's read-only pages. Made-up figures, keys and names only. */

const posted: { path: string; body: Record<string, unknown> }[] = [];

/** Reads answered by the first matching key (a path fragment); every POST is kept and answered by `decide`. */
function serve(reads: [string, unknown][], decide: (body: Record<string, unknown>) => unknown = () => ({ status: 'committed' })) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if ((init?.method ?? 'GET') === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
      posted.push({ path: new URL(u, 'http://x').pathname.replace('/api/v2', ''), body });
      return json({ kind: body.kind, ...(decide(body) as object) });
    }
    const hit = reads.find(([k]) => u.includes(k));
    return hit ? json(hit[1]) : json({}, 404);
  });
}

function show(page: 'finance' | 'growth', at: string) {
  const Page = () => {
    const search = useSearch({ strict: false });
    return page === 'finance' ? <FinancePage search={validateFinanceSearch(search)} /> : <GrowthPage search={validateGrowthSearch(search)} />;
  };
  const router = createRouter({ routeTree: createRootRoute({ component: Page }), history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const person = (id: string, name: string): Actor => ({ id, kind: 'person', name });
function directory(...actors: Actor[]) {
  const byId: Record<string, Actor> = {};
  for (const a of actors) Object.assign(byId, { [a.id]: a, ...(a.slug ? { [a.slug]: a, [`bot:${a.slug}`]: a, [a.id.slice(4)]: a } : {}) });
  queryClient.setQueryData(['directory'], { actors, byId });
}

/** The owner, with Sam as a teammate the owner may read Finance as. */
const ACCESS = {
  can_view_as: true,
  can_edit_defaults: false,
  teams: [],
  teammates: [
    { teammate: 'human:me', kind: 'person', default: 'everything', rights: [] },
    { teammate: 'human:sam', kind: 'person', default: 'own_expenses', rights: [] },
  ],
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  posted.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  directory(person('human:me', 'Me'), person('human:sam', 'Sam'));
});
afterEach(() => {
  vi.unstubAllGlobals();
  bind({ router: null });
});

// ---------------------------------------------------------------- Finance

describe('View as @s456', () => {
  it('the owner picks a teammate from Access and the address reads as them', async () => {
    serve([['/finance/access', ACCESS]]);
    const router = show('finance', '/company/finance?tab=access');
    fireEvent.click(await screen.findByRole('button', { name: 'View as' }));
    const dialog = await screen.findByRole('dialog', { name: 'View as' });
    expect(within(dialog).getByRole('combobox', { name: 'Teammate' }).textContent).toContain('Sam');
    fireEvent.click(within(dialog).getByRole('button', { name: 'View' }));
    await waitFor(() => expect((router.state.location.search as { as?: string }).as).toBe('human:sam'));
  });
});

describe('Attribution rule @s459', () => {
  it('a new rule names a vendor and its split, and is saved', async () => {
    serve([
      ['/finance/access', ACCESS],
      ['/finance/attribution-rules', { can_change: true, rules: [] }],
      ['/finance/contacts', { contacts: [{ id: 'v1', name: 'Paper Co' }] }],
    ]);
    show('finance', '/company/finance?tab=attribution');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add rule' }))[0]!);
    const dialog = await screen.findByRole('dialog', { name: 'New rule' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Paper to Sam' } });
    await pick(within(dialog).getByRole('combobox', { name: 'Match' }), /^Paper Co$/);
    await pick(within(dialog).getByRole('combobox', { name: 'Teammate or team' }), /^Sam$/);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Share, %' }), { target: { value: '100' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(posted.map((p) => p.body)).toEqual([
        {
          kind: 'attribution_rule_set',
          payload: { name: 'Paper to Sam', match: { kind: 'vendor', id: 'v1' }, splits: [{ teammate: 'human:sam', share_bps: 10000 }], enabled: true },
        },
      ]),
    );
  });
});

describe('Import history @s465', () => {
  it('a sheet is mapped to the chart, previewed as a dry run, then imported', async () => {
    serve(
      [
        ['/finance/access', ACCESS],
        ['/finance/balance-sheet', { as_of: '2026-10-31', assets: [], liabilities: [], equity: [] }],
        ['/finance/chart', { accounts: [{ id: 'acc_sw', number: '6100', name: 'Software', type: 'expense', subtype: '' }] }],
      ],
      (b) => (b.dry_run ? { status: 'committed', impact: { months: 1, rows: 1, accounts: [{ code: '6100', name: 'Software', total: 1250 }] } } : { status: 'committed' }),
    );
    show('finance', '/company/finance?tab=balance');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Import history' }));
    const dialog = await screen.findByRole('dialog', { name: 'Import history' });
    const file = new File(['month,line,amount\n2026-01,Software,12.50\n'], 'history.csv', { type: 'text/csv' });
    fireEvent.change(within(dialog).getByLabelText('Sheet file'), { target: { files: [file] } });
    expect((await within(dialog).findByRole('combobox', { name: 'Software' })).textContent).toContain('6100 Software');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preview' }));
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Import' }));
    await waitFor(() => expect(posted).toHaveLength(2));
    const payload = { csv: 'month,line,amount\n2026-01,Software,1250\n', basis: 'accrual', mapping: { Software: 'acc_sw' } };
    expect(posted[0]!.body).toEqual({ kind: 'import_raw_totals', payload, dry_run: true });
    expect(posted[1]!.body).toEqual({ kind: 'import_raw_totals', payload });
  });
});

// ---------------------------------------------------------------- Growth

const fig = (value: number | null) => ({ value });
const READ: GrowthRead = {
  scope: 'all',
  period: '2026-10',
  as_of: '2026-11-02',
  headline: {
    signups: fig(60),
    activated_rate: fig(0.5),
    paid_rate: fig(0.3),
    new_customers: fig(20),
    cost: fig(1000),
    cac: fig(50),
    kept: fig(0.7),
  },
  channels: [{ slug: 'search', name: 'Search ads', signups: 60, new_customers: 20, new_share: 1, cost: 1000, cac: 50, cost_share: 1, budget: 1200 }],
  health: { basis: 'customers', unknown_share: 0, stale_sources: [], reconciled: 'not_reconciled' },
  metrics: [],
  retention: [{ cohort: '2026-06', entered: 20, kept_d30: { value: 0.7, n: 20, status: 'ok' } }],
  more: [],
};
const GM: Actor = { id: 'bot:gm1', kind: 'agent', name: 'Growth Manager', slug: 'growth-manager' };

describe('Growth changes go to the Growth Manager @s479 @s481 @s482 @s483', () => {
  it('no channel, month, customer or source editor; a change asked of it lands in its chat with the scope on screen', async () => {
    directory(GM);
    serve([
      ['/growth/read', READ],
      ['/growth/segments', { segments: [] }],
      ['/page-owners', { pages: { growth: { bot: 'growth-manager', choice: 'default' } } }],
    ]);
    const router = show('growth', '/company/growth?month=2026-10&panel=channel:search');
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    expect(within(panel).getByRole('button', { name: 'Close' })).toBeTruthy();
    expect(within(panel).queryByRole('button', { name: /^(Edit|\+ Month|Save|Add)/ })).toBeNull();
    const kept = await screen.findByRole('table', { name: 'Retention' });
    expect(within(kept).queryAllByRole('link')).toHaveLength(0);
    expect(within(kept).queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /^(New channel|Edit|\+ Month|Upload|Connect)/ })).toBeNull();
    fireEvent.change(screen.getByRole('textbox', { name: 'Ask the Growth Manager' }), { target: { value: 'Add a podcast channel' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(posted).toEqual([{ path: '/chat/gm1', body: expect.objectContaining({ text: 'All, October 2026: Add a podcast channel' }) as unknown }]));
    await waitFor(() => expect((router.state.location.search as { chat?: string }).chat).toBe('agent:gm1'));
  });
});
