import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { GITHUB_WHY } from './githubReturn';
import { websiteToolQuery } from './queries';
import { GITHUB_RETURNS, toolsSearch } from './search';
import { ToolsPage } from './ToolsPage';

/* GitHub's return to Tools: said once from the server's read, then taken out of the address. And a website account's
   Open browser. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/features/team/exports', () => ({
  BrowserDialog: ({ account }: { account: string }) => <div role="dialog" aria-label={`Browser for ${account}`} />,
}));

// Made-up tools only.
const githubRow = (tone: 'ok' | 'wait' | 'idle') => ({
  id: 'github',
  kind: 'github',
  name: 'GitHub',
  detail: 'sample-org',
  way: 'GitHub App',
  status: { tone, text: tone === 'ok' ? 'On' : tone === 'wait' ? 'Waiting' : 'Off' },
  who: [],
  who_total: 0,
  needs_you: false,
  href: '',
});
function serve(rows: unknown[]) {
  const reads: string[] = [];
  vi.stubGlobal('fetch', async (url: string) => {
    reads.push(String(url));
    const github = (rows as { kind?: string; status?: { tone?: string } }[]).find((r) => r.kind === 'github');
    const body = String(url).includes('/tool-index')
      ? { rows }
      : String(url).endsWith('/github/app') && github
        ? { connected: true, org: 'sample-org', installed: github.status?.tone === 'ok', administration: true }
        : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return reads;
}

function show(at: string) {
  const Tools = () => <ToolsPage search={toolsSearch(useSearch({ strict: false }))} />;
  const router = createRouter({ routeTree: createRootRoute({ component: Tools }), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}
const query = (router: ReturnType<typeof show>) => Object.fromEntries(new URL(router.state.location.href, 'http://x').searchParams);
const toastOf = (text: string) => screen.findByText((_, el) => el?.tagName === 'SPAN' && el.textContent === text);

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("GitHub's return", () => {
  it('keeps only known words', () => {
    for (const w of GITHUB_RETURNS) expect(toolsSearch({ tool: 'github', github: w })).toEqual({ tool: 'github', github: w });
    expect(toolsSearch({ github: '<script>' })).toEqual({ github: 'other' });
  });

  it('says installed only when the tool index read again says GitHub is on, then cleans the address', async () => {
    const reads = serve([githubRow('ok')]);
    const router = show('/library/tools?tool=github&github=installed');
    expect(await toastOf('Installed: GitHub')).toBeTruthy();
    await waitFor(() => expect(query(router)).toEqual({ tool: 'github' }));
    expect(reads.filter((u) => u.includes('/tool-index')).length).toBeGreaterThan(0);
  });

  it('does not take "connected" from the address: an App still waiting says to install it', async () => {
    serve([githubRow('wait')]);
    const router = show('/library/tools?tool=github&github=connected');
    expect(await screen.findByText(GITHUB_WHY.pending as string)).toBeTruthy();
    // the owner's GitHub app section offers the install
    expect(await screen.findByRole('button', { name: 'Install' })).toBeTruthy();
    expect(screen.queryByText(/^Connected/)).toBeNull();
    await waitFor(() => expect(query(router)).toEqual({ tool: 'github' }));
  });

  it.each(['declined', 'finish', 'again', 'expired'] as const)('says what %s means in the GitHub panel', async (word) => {
    serve([githubRow('ok')]);
    const router = show(`/library/tools?tool=github&github=${word}`);
    expect(await screen.findByText(GITHUB_WHY[word] as string)).toBeTruthy();
    await waitFor(() => expect(query(router)).toEqual({ tool: 'github' }));
  });

  it('says authorized when the App is kept, and nothing when it is not', async () => {
    serve([githubRow('wait')]);
    show('/library/tools?tool=github&github=authorized');
    expect(await toastOf('Authorized: GitHub')).toBeTruthy();
    cleanup();
    queryClient.clear();
    serve([]);
    const router = show('/library/tools?tool=github&github=authorized');
    await waitFor(() => expect(query(router)).toEqual({ tool: 'github' }));
    expect(screen.queryByText(/^Authorized/)).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Not connected' })).toBeNull();
  });

  it('ignores an unknown word and only cleans the address', async () => {
    serve([githubRow('ok')]);
    const router = show('/library/tools?kind=github&github=%3Cimg%20src%3Dx%3E');
    await waitFor(() => expect(query(router)).toEqual({ kind: 'github' }));
    expect(document.body.innerHTML).not.toContain('img src');
    expect(screen.queryByText(/: GitHub$/)).toBeNull();
  });
});

describe("a website's Open browser", () => {
  const account = (id: string, can_open: boolean) => ({
    id,
    username: `user-${id}`,
    kind: 'shared',
    holder: null,
    access: [],
    all_bots: false,
    can_manage: true,
    can_open,
    can_use: true,
    has_password: false,
    has_totp: false,
  });

  it('shows only for an account that can be opened, and opens the browser for it', async () => {
    serve([{ ...githubRow('ok'), id: 'website:w1', kind: 'website', name: 'Sample site' }]);
    queryClient.setQueryData(websiteToolQuery('w1').queryKey, {
      tool: { id: 'w1', name: 'Sample site', sites: ['example.com'] },
      accounts: [account('a1', true), account('a2', false)],
      can_manage: true,
      status: { tone: 'ok', label: 'Ready' },
      host: null,
      recent: [],
    } as never);
    show('/library/tools?tool=website:w1');
    const buttons = await screen.findAllByRole('button', { name: 'Open browser' });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0] as HTMLElement);
    expect(await screen.findByRole('dialog', { name: 'Browser for a1' })).toBeTruthy();
  });
});
