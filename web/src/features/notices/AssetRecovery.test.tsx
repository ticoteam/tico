import { act, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AssetRecovery } from './AssetRecovery';
import { Cover } from './Cover';
import { isAssetFailure } from './recovery';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/** An element's load error, as the browser sends it (it does not bubble; the target is the element). */
function failedLoad(el: Element): Event {
  const e = new Event('error');
  Object.defineProperty(e, 'target', { value: el });
  return e;
}

const script = (src: string) => Object.assign(document.createElement('script'), { src });
const link = (rel: string, href: string) => Object.assign(document.createElement('link'), { rel, href });
const rejected = (reason: unknown) => Object.assign(new Event('unhandledrejection'), { reason });

describe('Asset recovery @s1197', () => {
  it('keeps the app and the notice behind a recovery card out of the focus order', () => {
    const view = (planLocked: boolean, recover: boolean) => (
      <>
        <div data-testid="app">App</div>
        {planLocked && <Cover label="Plan"><button className="btn-fill">Billing</button></Cover>}
        {recover && <AssetRecovery />}
      </>
    );
    const { rerender } = render(view(true, true));
    const plan = screen.getByRole('alertdialog', { name: 'Plan' }) as HTMLElement;
    act(() => window.dispatchEvent(new Event('vite:preloadError')));
    expect(plan.inert).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reload' }));
    rerender(view(false, true));
    expect((screen.getByTestId('app') as HTMLElement).inert).toBe(true);
    rerender(view(false, false));
    expect((screen.getByTestId('app') as HTMLElement).inert).toBe(false);
  });

  it('counts only our own files failing', () => {
    expect(isAssetFailure(new Event('vite:preloadError'))).toBe(true);
    expect(isAssetFailure(failedLoad(script('/assets/tasks-a1b2.js')))).toBe(true);
    expect(isAssetFailure(failedLoad(link('stylesheet', '/assets/tasks-a1b2.css')))).toBe(true);
    expect(isAssetFailure(failedLoad(link('modulepreload', '/assets/kit-c3d4.js')))).toBe(true);
    expect(isAssetFailure(rejected(new TypeError('Failed to fetch dynamically imported module: /assets/x.js')))).toBe(true);
    expect(isAssetFailure(failedLoad(script('https://beacon.example.com/b.js')))).toBe(false);
    expect(isAssetFailure(failedLoad(link('icon', '/favicon.ico')))).toBe(false);
    expect(isAssetFailure(failedLoad(document.createElement('img')))).toBe(false);
    expect(isAssetFailure(rejected(new Error('The server did not answer')))).toBe(false);
  });

  it('stays out of the way until a file fails, then covers the app with Reload', () => {
    render(
      <>
        <div data-testid="app">App</div>
        <AssetRecovery />
      </>,
    );
    expect(screen.queryByRole('alertdialog')).toBeNull();
    act(() => {
      window.dispatchEvent(new Event('vite:preloadError'));
    });
    const card = screen.getByRole('alertdialog', { name: "Tico couldn't finish loading" });
    expect(within(card).getByRole('button', { name: 'Reload' })).toBeTruthy();
    expect(within(card).getByRole('link', { name: 'Contact Tico support' })).toBeTruthy();
    expect((screen.getByTestId('app') as HTMLElement).inert).toBe(true);
  });
});
