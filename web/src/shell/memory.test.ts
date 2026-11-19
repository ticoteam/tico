import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { layout } from './layout';
import { resetSheetSizes, useSheetMemory, withFold, withWidth, withoutWidth } from './memory';

const KEY = 'tico-next:sheets.v1';
const width = (L: ReturnType<typeof layout>, id: string) => L.sheets.find(s => s.id === id)?.width;

describe('sheet memory', () => {
  it('keeps sizes per arrangement and Tico as one number, through storage and back', () => {
    const { result } = renderHook(() => useSheetMemory());
    act(() => result.current[1](s => withWidth(s, 'page', 'tico', 340)));
    act(() => result.current[1](s => withWidth(s, 'chat', 'chat', 400)));
    act(() => result.current[1](s => withFold(s, 'agent', 'menu', true)));

    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}')).toEqual({ v: 1, tico: 340, chat: { w: { chat: 400 } }, agent: { fold: { menu: true } } });
    const saved = renderHook(() => useSheetMemory()).result.current[0];
    expect(width(layout(1920, 'page', saved), 'tico')).toBe(340);
    expect(width(layout(1920, 'agent', saved), 'tico')).toBe(340);
    expect(width(layout(1920, 'chat', saved), 'chat')).toBe(400);
    expect(layout(1920, 'agent', saved).sheets.find(s => s.id === 'menu')?.state).toBe('fold');
    expect(layout(1920, 'page', saved).sheets.find(s => s.id === 'menu')?.state).toBe('open');

    act(() => result.current[1](s => withoutWidth(s, 'chat', 'chat')));
    expect(width(layout(1920, 'chat', result.current[0]), 'chat')).toBe(320);
    act(() => resetSheetSizes());
    expect(result.current[0]).toEqual({ v: 1 });
  });

  it('loads sane sizes from bad storage and leaves a newer version alone', () => {
    localStorage.setItem(KEY, '{not json');
    expect(renderHook(() => useSheetMemory()).result.current[0]).toEqual({ v: 1 });
    localStorage.setItem(KEY, JSON.stringify({ v: 1, tico: 9999, page: { w: { menu: 'wide' } } }));
    const saved = renderHook(() => useSheetMemory()).result.current[0];
    expect(width(layout(1920, 'page', saved), 'tico')).toBe(480);
    expect(width(layout(1920, 'page', saved), 'menu')).toBe(248);

    const newer = JSON.stringify({ v: 2, tico: 400 });
    localStorage.setItem(KEY, newer);
    const { result } = renderHook(() => useSheetMemory());
    expect(result.current[0]).toEqual({ v: 1 });
    act(() => result.current[1](s => withWidth(s, 'page', 'tico', 300)));
    expect(localStorage.getItem(KEY)).toBe(newer);
  });
});
