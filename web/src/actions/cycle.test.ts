import { describe, expect, it, vi } from 'vitest';

/* Each actions file imports `@/actions`, and the registry collects every actions file, so the two load each other.
   Whichever loads first, nothing may read an action before its file has run (a phone once hit "Cannot access
   'cancelProposal' before initialization"). Each case starts a fresh module graph. */

const files = import.meta.glob<Record<string, unknown>>(['/src/*/actions.ts', '/src/features/*/actions.ts']);

async function idsAfter(first: () => Promise<unknown>): Promise<string[]> {
  vi.resetModules();
  await first();
  const { registry } = await import('@/actions');
  const ids = [...registry.keys()];
  for (const id of ids) expect(registry.get(id)?.id).toBe(id);
  return ids.sort();
}

describe('the action registry loads in any order', () => {
  it('from @/actions first, then from each actions file first, with the same actions', async () => {
    const all = await idsAfter(() => import('@/actions'));
    expect(all).toContain('shell.goTo');
    expect(all).toContain('conversation.cancel');
    for (const [file, load] of Object.entries(files)) {
      expect(await idsAfter(load), file).toEqual(all);
    }
  }, 60_000);
});
