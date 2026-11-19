import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { readPref, usePref, writePref } from './prefs';

describe('prefs', () => {
  it('returns the fallback, then what was written, under the tico-next: prefix', () => {
    const { result } = renderHook(() => usePref('theme.v1', 'system'));
    expect(result.current[0]).toBe('system');

    act(() => result.current[1]('light'));
    expect(result.current[0]).toBe('light');
    expect(localStorage.getItem('tico-next:theme.v1')).toBe('"light"');
  });

  it('falls back on a value that is not JSON', () => {
    localStorage.setItem('tico-next:width.v1', 'not json');
    expect(readPref('width.v1', 320)).toBe(320);
    writePref('width.v1', 400);
    expect(readPref('width.v1', 320)).toBe(400);
  });
});
