import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setThemeChoice, useTheme } from './theme';

function fakeSystem(light: boolean) {
  const listeners = new Set<() => void>();
  const q = {
    get matches() {
      return light;
    },
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  };
  vi.stubGlobal('matchMedia', () => q);
  return {
    flip(to: boolean) {
      light = to;
      for (const fn of [...listeners]) fn();
    },
    listeners,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('useTheme', () => {
  it('follows the device while the choice is system, and stops once a theme is chosen', () => {
    const system = fakeSystem(false);
    setThemeChoice('system');
    renderHook(() => useTheme());
    expect(document.documentElement.dataset.theme).toBe('dark');
    act(() => system.flip(true));
    expect(document.documentElement.dataset.theme).toBe('light');

    act(() => setThemeChoice('dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(system.listeners.size).toBe(0);
    act(() => system.flip(false));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
