import { createRootRoute } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { startAddress } from './router';

// the router module only; the app's pages are not needed to test where it starts
vi.mock('@/routeTree.gen', () => ({ routeTree: createRootRoute() }));

const CODE = 'WXYZ-2345';
/** The code held for the sign-in hop, if any (the Skills page reads it; this tab only). */
const held = () => (JSON.parse(sessionStorage.getItem('tico.skillsHelper') ?? '{}') as { code?: string }).code ?? null;

afterEach(() => sessionStorage.clear());

describe('the address the app starts at', () => {
  it("opens the helper's old link on Skills, the code held and never in the address", () => {
    expect(startAddress('/next/', '', `#/skills/connect?code=${CODE}`)).toBe('/next/library/skills');
    expect(held()).toBe(CODE);
  });

  it('takes the code out of a new-style link too, and keeps the rest', () => {
    expect(startAddress('/next/library/skills', `?connect=${CODE}&q=calls`, '')).toBe('/next/library/skills?q=calls');
    expect(held()).toBe(CODE);
  });

  it('keeps any other address as it is', () => {
    expect(startAddress('/next/tasks', '?q=calls', '')).toBeNull();
    expect(startAddress('/next/library/skills', '?connect=setup', '')).toBeNull();
    expect(startAddress('/elsewhere', `?connect=${CODE}`, '')).toBeNull();
    expect(held()).toBeNull();
  });

  it('opens an old address with no page at home', () => {
    expect(startAddress('/next/', '', '#/no-such-page')).toBe('/next/');
  });

  it('replaces the address before the router reads it: no new history entry', async () => {
    vi.resetModules();
    window.history.replaceState(null, '', `/next/library/skills?connect=${CODE}`);
    const before = window.history.length;
    const { router } = await import('./router');
    expect(window.location.pathname + window.location.search).toBe('/next/library/skills');
    expect(window.history.length).toBe(before);
    expect(router.state.location.href).not.toContain(CODE);
    window.history.replaceState(null, '', '/');
  });
});
