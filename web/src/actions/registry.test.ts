import type { AnyRouter } from '@tanstack/react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { actionsCatalog, buildRegistry, registry } from './registry';
import { runIn } from './run';
import { s } from './schema';
import { defineAction, type AnyAction } from './types';

// the real module, so feature actions that set up their queries on load still find what they use; a cache of its own,
// so the gates below read what each test puts there
vi.mock('@/data', async (real) => {
  const { QueryClient } = await import('@tanstack/react-query');
  return { ...(await real<object>()), queryClient: new QueryClient() };
});
vi.mock('@/search', async (real) => ({ ...(await real<object>()), openSearch: vi.fn() }));
vi.mock('@/motion', async (real) => ({ ...(await real<object>()), flight: vi.fn(), waitForTarget: vi.fn(async () => null), wash: vi.fn() }));

const make = () =>
  defineAction({ id: 'tasks.open', title: 'Open', kind: 'view', input: s.object({}), run: () => {} });

describe('registry', () => {
  it('fails loudly on a duplicate id, naming both files', () => {
    expect(() => buildRegistry({ 'a.ts': { one: make() }, 'b.ts': { two: make() } })).toThrow(
      'Action "tasks.open" is defined twice: in a.ts and in b.ts',
    );
  });

  it('collects only defined actions', () => {
    const r = buildRegistry({ 'a.ts': { open: make(), other: { id: 'x.y' }, n: 3 } });
    expect([...r.keys()]).toEqual(['tasks.open']);
  });

  it('builds from every features/*/actions.ts with unique ids', () => {
    expect(registry.has('shell.goTo')).toBe(true);
    expect(registry.has('task.open')).toBe(true);
  });

  it('prints the catalog with JSON Schemas for the server', () => {
    const open = actionsCatalog().find((a) => a.id === 'task.open');
    expect(open).toEqual({
      id: 'task.open',
      title: 'Open a task',
      kind: 'view',
      input: {
        type: 'object',
        properties: { task: { type: 'integer', minimum: 1 }, pipeline: { type: 'string', minLength: 1, maxLength: 100 } },
        required: ['task'],
        additionalProperties: false,
      },
    });
    const ids = actionsCatalog().map((a) => a.id);
    expect(ids).toEqual([...ids].sort());
  });

  it('leaves actions kept from Tico out of the catalog', () => {
    const open = make();
    const mine = defineAction({ ...make(), id: 'tasks.confirm', tico: false });
    const ids = actionsCatalog(new Map<string, AnyAction>([[open.id, open as AnyAction], [mine.id, mine as AnyAction]])).map((a) => a.id);
    expect(ids).toEqual(['tasks.open']);
  });

  it('refuses an id that is not area.verb', () => {
    expect(() => defineAction({ id: 'Tasks.open', title: '', kind: 'view', input: s.object({}), run: () => {} })).toThrow();
  });
});

/* Money, budgets, the books, who may read or use what, credentials and what cannot be undone: the person's own press
   (owner's direction). Each row is refused from Tico and still runs from a click; the second table is the narrow side
   of the same actions, which stays Tico's. `run` is swapped for a spy, so nothing is sent. */
describe("kept from Tico, and the person's own press still works", () => {
  const router = {} as AnyRouter;
  const tool = (keys: number) => ({ access: [{ subject: 'bot:qa', level: 'read_write' }, { subject: 'bot:ro', level: 'read' }], keys: Array.from({ length: keys }, (_, i) => ({ id: `k${i}` })), tool: { base_url: 'https://api.example.test', auth_scheme: 'bearer' } });
  beforeEach(() => {
    queryClient.clear();
    queryClient.setQueryData(['tools', 'api', 'keyed'], tool(1));
    queryClient.setQueryData(['tools', 'api', 'keyless'], tool(0));
    queryClient.setQueryData(['tools', 'website', 'w1'], { accounts: [{ id: 'a1' }], tool: { sites: ['app.example.test'], central_only: true } });
  });
  const spied = (id: string) => {
    const a = registry.get(id);
    if (!a) throw new Error(`no action ${id}`);
    const run = vi.fn();
    return { run, only: new Map<string, AnyAction>([[id, { ...a, run }]]) };
  };

  const kept: [string, object][] = [
    // money, budgets and the books
    ['finance.transfer', { from: 'a1', to: 'a2', amount: 1 }],
    ['finance.startMirror', { connection: 'c1' }],
    ['finance.stopMirror', {}],
    ['finance.disconnect', { connection: 'c1' }],
    ['finance.setDefaults', { member: 'own_expenses' }],
    ['finance.postStatement', { account: 'm1', file: 'eA==', name: 'march.csv', token: 'p1' }],
    ['budget.addRow', { name: 'Tools', line: 'acc', amount: 500 }],
    ['budget.updateRow', { budget: 'b1', amount: 900 }],
    ['budget.removeRow', { budget: 'b1' }],
    ['usage.setLimit', { agent: 'qa', monthly: 100 }],
    ['usage.setDefaultLimit', { monthly: 100 }],
    ['routines.setLimit', { routine: 'r1', per30m: 5 }],
    // rights and who may read or use what
    ['people.invite', { email: 'new@example.test', role: 'admin' }],
    ['people.setRole', { person: 'p1', role: 'admin' }],
    ['settings.setRole', { person: 'p1', role: 'admin' }],
    ['threads.whoCanRead', { thread: 't1', audience: 'company' }],
    ['threads.openEarlier', { thread: 't1' }],
    ['tools.setAccess', { tool: 'keyed', subject: 'bot:new', level: 'read' }],
    ['tools.setAccess', { tool: 'keyed', subject: 'bot:ro', level: 'read_write' }],
    ['tools.setAccess', { tool: 'unread', subject: 'bot:qa', level: 'read' }],
    ['settings.reportsDirect', { on: true }],
    // where a stored credential goes, and credentials ended
    ['tools.editApi', { tool: 'keyed', base_url: 'https://elsewhere.example.test' }],
    ['tools.editApi', { tool: 'keyed', auth_scheme: 'header:X-Key' }],
    ['tools.editApi', { tool: 'unread', base_url: 'https://elsewhere.example.test' }],
    ['tools.editWebsite', { tool: 'w1', sites: ['app.example.test', 'elsewhere.example.test'] }],
    ['tools.editWebsite', { tool: 'w1', central_only: false }],
    ['tools.removeKey', { tool: 'keyed', key: 'k0' }],
    ['settings.revokeToken', { token: 'tok1' }],
    ['settings.removeSubscription', { subscription: 's1' }],
    ['settings.editSubscription', { subscription: 's1', name: 'Plan', owner: 'p2' }],
    ['settings.removeComputer', { computer: 'c1' }],
    ['computers.signOut', { computer: 'c1', runtime: 'claude' }],
    ['webhooks.remove', { hook: 'h1' }],
    ['calls.disconnectGranola', {}],
    // gone for good
    ['threads.keepMessages', { thread: 't1', days: 30 }],
  ];
  it.each(kept)('%s %j: refused from Tico, runs from a click', async (id, input) => {
    const { run, only } = spied(id);
    expect(await runIn(only, id, input, 'tico', router)).toEqual({ ok: false, error: 'not_allowed' });
    expect(run).not.toHaveBeenCalled();
    expect((await runIn(only, id, input, 'click', router)).ok).toBe(true);
    expect(run).toHaveBeenCalledTimes(1);
  });

  const stillTicos: [string, object][] = [
    ['people.invite', { email: 'new@example.test', role: 'member' }],
    ['people.invite', { email: 'new@example.test' }],
    ['people.setRole', { person: 'p1', role: 'member' }],
    ['settings.setRole', { person: 'p1', role: 'member' }],
    ['threads.whoCanRead', { thread: 't1', audience: 'members' }],
    ['threads.keepMessages', { thread: 't1', days: null }],
    ['tools.setAccess', { tool: 'keyed', subject: 'bot:qa', level: 'read' }],
    ['tools.setAccess', { tool: 'unread', subject: 'bot:qa', level: 'none' }],
    ['tools.editApi', { tool: 'keyed', name: 'Billing API' }],
    ['tools.editApi', { tool: 'keyed', base_url: 'https://api.example.test' }],
    ['tools.editApi', { tool: 'keyless', base_url: 'https://elsewhere.example.test' }],
    ['tools.editWebsite', { tool: 'w1', name: 'App' }],
    ['tools.editWebsite', { tool: 'w1', sites: [], central_only: true }],
    ['settings.editSubscription', { subscription: 's1', name: 'Plan' }],
    ['settings.reportsDirect', { on: false }],
    ['budget.openRow', { budget: 'b1' }],
    ['finance.attribute', { line: 'l1', splits: [] }],
  ];
  it.each(stillTicos)('%s %j: still runs from Tico', async (id, input) => {
    const { run, only } = spied(id);
    expect(await runIn(only, id, input, 'tico', router)).toMatchObject({ ok: true });
    expect(run).toHaveBeenCalledTimes(1);
  });
});
