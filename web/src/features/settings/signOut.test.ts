import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Ctx } from '@/actions/types';
import type { TicoDesktop } from '@/desktop';
import { signOut } from './actions';

afterEach(() => {
  delete (window as { tico?: TicoDesktop }).tico;
  vi.unstubAllGlobals();
});

describe('sign out', () => {
  it('tells the desktop app before the page leaves for the server’s sign-out', async () => {
    const order: string[] = [];
    (window as { tico?: TicoDesktop }).tico = {
      signingOut: async () => {
        await Promise.resolve();
        order.push('told the app');
      },
    } as unknown as TicoDesktop;
    vi.stubGlobal('location', { assign: (to: string) => order.push(`left for ${to}`) });
    await signOut.run({} as Ctx, {});
    expect(order).toEqual(['told the app', 'left for /api/v2/logout']);
  });

  it('in a browser, goes straight to the server’s sign-out', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { assign });
    await signOut.run({} as Ctx, {});
    expect(assign).toHaveBeenCalledWith('/api/v2/logout');
  });
});
