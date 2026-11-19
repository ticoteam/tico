import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot, run } from '@/actions';
import { queryClient } from '@/data';
import { DropDialog } from './DropDialog';
import type { DropChecks } from './queries';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

let posts: { url: string; body: unknown }[] = [];
function serve(checks: DropChecks) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'POST') posts.push({ url: String(url), body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const body = method === 'GET' ? checks : { task: { id: 't2', number: 8 }, closed: { id: 't1', number: 7 }, note: '' };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/tasks/eng'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

/* Made-up tasks only. */
const checks = (more: Partial<DropChecks> = {}): DropChecks => ({
  task: { id: 't1', number: 7, title: 'Fix the date picker', owner_id: 'bot:a1', owner: 'Builder' },
  into: { id: 't2', number: 8, title: 'Calendar fixes', owner_id: 'human:me', owner: 'Sam Lee' },
  same_owner: false,
  subtask: null,
  merge: null,
  moves: { comments: 2, asks: [], subtasks: 0, files: 1, followers: 0, requesters: 1, labels: 0, links: 0, description: true, due: null },
  ...more,
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  posts = [];
  queryClient.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('a card let go over another card @s25', () => {
  it('makes it a subtask by default', async () => {
    serve(checks());
    const close = vi.fn();
    show(<DropDialog task="t1" onto="t2" onClose={close} />);
    const dialog = await screen.findByRole('dialog', { name: 'Drop #7 on #8' });
    expect(within(dialog).getByText('#7 goes under #8. Both keep their owners and stages.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Make subtask' }));
    await waitFor(() => expect(posts).toEqual([{ url: '/api/v2/tasks/t1/parent', body: { parent: 't2' } }]));
    await waitFor(() => expect(close).toHaveBeenCalled());
  });

  it('merges, saying what moves, with the owner the person picks', async () => {
    serve(checks());
    show(<DropDialog task="t1" onto="t2" onClose={() => undefined} />);
    const dialog = await screen.findByRole('dialog', { name: 'Drop #7 on #8' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Merge' }));
    expect(within(dialog).getByText('Description, 1 file or page: added to #8')).toBeTruthy();
    expect(within(dialog).getByText('#7: closed as a duplicate')).toBeTruthy();
    expect(within(dialog).getByRole('combobox', { name: 'Who keeps #8' })).toBeTruthy();
    const go = within(dialog).getAllByRole('button', { name: 'Merge' }).at(-1) as HTMLElement;
    fireEvent.click(go);
    await waitFor(() => expect(posts).toEqual([{ url: '/api/v2/tasks/t1/merge', body: { into: 't2' } }]));
  });

  it('a merge cannot be undone, so Tico may not do it for the person', async () => {
    serve(checks());
    show(<DropDialog task="t1" onto="t2" onClose={() => undefined} />);
    await screen.findByRole('dialog', { name: 'Drop #7 on #8' });
    expect(await run('tasks.merge', { task: 't1', into: 't2', owner: null }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(posts).toEqual([]);
  });

  it('says why a choice cannot be made, and does not offer it', async () => {
    serve(checks({ subtask: '#8 is already under #7.' }));
    show(<DropDialog task="t1" onto="t2" onClose={() => undefined} />);
    const dialog = await screen.findByRole('dialog', { name: 'Drop #7 on #8' });
    // Merge is the one that can be done, so it is picked.
    expect(within(dialog).getByText('#8 stays and gets #7\'s details.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Subtask' }));
    expect(within(dialog).getByText('#8 is already under #7.')).toBeTruthy();
    expect((within(dialog).getByRole('button', { name: 'Make subtask' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the first refusal immediately when both choices are blocked', async () => {
    const subtask = 'These tasks are already linked.';
    const merge = 'Only open tasks can be merged.';
    serve(checks({ subtask, merge }));
    show(<DropDialog task="t1" onto="t2" onClose={() => undefined} />);
    const dialog = within(await screen.findByRole('dialog', { name: 'Drop #7 on #8' }));
    expect(dialog.getByText(subtask)).toBeTruthy();
    expect((dialog.getByRole('button', { name: 'Make subtask' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole('button', { name: 'Merge' }));
    expect(dialog.getByText(merge)).toBeTruthy();
    expect((dialog.getAllByRole('button', { name: 'Merge' }).at(-1) as HTMLButtonElement).disabled).toBe(true);
    expect(posts).toEqual([]);
  });
});
