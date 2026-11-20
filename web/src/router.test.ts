import { createRootRoute } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { appHref, startAddress } from './router';

// the router module only; the app's pages are not needed to test where it starts
vi.mock('@/routeTree.gen', () => ({ routeTree: createRootRoute() }));

const CODE = 'WXYZ-2345';
/** The code held for the sign-in hop, if any (the Skills page reads it; this tab only). */
const held = () => (JSON.parse(sessionStorage.getItem('tico.skillsHelper') ?? '{}') as { code?: string }).code ?? null;

afterEach(() => sessionStorage.clear());

describe('the address the app starts at', () => {
  it("opens the helper's old link on Skills, the code held and never in the address", () => {
    expect(startAddress('/', '', `#/skills/connect?code=${CODE}`)).toBe('/library/skills');
    expect(held()).toBe(CODE);
  });

  it('takes the code out of a new-style link too, and keeps the rest', () => {
    expect(startAddress('/library/skills', `?connect=${CODE}&q=calls`, '')).toBe('/library/skills?q=calls');
    expect(held()).toBe(CODE);
  });

  it('keeps any other address as it is', () => {
    expect(startAddress('/tasks', '?q=calls', '')).toBeNull();
    expect(startAddress('/tasks', '?q=calls', '#top')).toBeNull();
    expect(startAddress('/library/skills', '?connect=setup', '')).toBeNull();
    expect(startAddress('/nextdoor', '', '')).toBeNull();
    expect(held()).toBeNull();
  });

  it('opens an old address with no page at home', () => {
    expect(startAddress('/', '', '#/no-such-page')).toBe('/');
  });

  it('opens an old app address from mail or a push at its page', () => {
    expect(startAddress('/', '', '#/task/k_1')).toBe('/task/k_1');
    expect(startAddress('/', '', '#/tools/github?github=connected')).toBe('/library/tools?tool=github&github=connected');
  });

  it('opens an address from when the app was at /next without it', () => {
    expect(startAddress('/next', '', '')).toBe('/');
    expect(startAddress('/next/', '', '')).toBe('/');
    expect(startAddress('/next/tasks/engineering', '?view=list', '#top')).toBe('/tasks/engineering?view=list#top');
    expect(startAddress('/next/', '', '#/task/k_1')).toBe('/task/k_1');
  });

  it('takes an old app address in a link to its page and leaves any other link alone', () => {
    expect(appHref('/#/task/k_1')).toBe('/task/k_1');
    expect(appHref('#/finance')).toBe('/company/finance');
    expect(appHref('#/no-such-page')).toBe('/');
    expect(appHref('/tasks/engineering?view=list')).toBe('/tasks/engineering?view=list');
    expect(appHref('/library/f1#part-2')).toBe('/library/f1#part-2');
  });

  it('replaces the address before the router reads it: no new history entry', async () => {
    vi.resetModules();
    window.history.replaceState(null, '', `/next/library/skills?connect=${CODE}`);
    const before = window.history.length;
    const { router } = await import('./router');
    expect(window.location.pathname + window.location.search).toBe('/library/skills');
    expect(window.history.length).toBe(before);
    expect(router.state.location.href).not.toContain(CODE);
    window.history.replaceState(null, '', '/');
  });
});
