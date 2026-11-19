import { describe, expect, it, vi } from 'vitest';
import { actionsCatalog, buildRegistry, registry } from './registry';
import { s } from './schema';
import { defineAction, type AnyAction } from './types';

vi.mock('@/data', () => ({ queryClient: {} }));
vi.mock('@/search', () => ({ openSearch: vi.fn() }));

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
