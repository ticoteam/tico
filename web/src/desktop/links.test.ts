import { afterEach, describe, expect, it, vi } from 'vitest';
import { followFromSmallWindow, reportCallToDesktop, resetDesktopReports, smallWindowLink, tellDesktopSigningOut, type CompanyRef, type TicoDesktop } from '@/desktop';

const ORIGIN = 'https://acme.example';

function fakeBridge(extra: Partial<TicoDesktop> = {}) {
  const me: CompanyRef = { id: 'c1', origin: ORIGIN, name: 'Acme', email: '', active: true, self: true, signedOut: false, needs: 0 };
  const t = {
    companies: { list: vi.fn(async () => [me]), switchTo: vi.fn(async () => {}) },
    ...extra,
  } as unknown as TicoDesktop;
  (window as { tico?: TicoDesktop }).tico = t;
  return t;
}

afterEach(() => {
  delete (window as { tico?: TicoDesktop }).tico;
  vi.restoreAllMocks();
});

describe('links in the small call window', () => {
  it('sends the app’s own pages to the main window, outside https pages and files to the browser', () => {
    expect(smallWindowLink('/next/task/412', ORIGIN)).toEqual({ kind: 'app', url: `${ORIGIN}/next/task/412` });
    expect(smallWindowLink(`${ORIGIN}/next/docs/plan`, ORIGIN)).toEqual({ kind: 'app', url: `${ORIGIN}/next/docs/plan` });
    expect(smallWindowLink('https://example.com/a', ORIGIN)).toEqual({ kind: 'outside', url: 'https://example.com/a' });
    expect(smallWindowLink('//example.com/a', ORIGIN)).toEqual({ kind: 'outside', url: 'https://example.com/a' });
    expect(smallWindowLink('/api/files/9', ORIGIN)).toEqual({ kind: 'file', url: `${ORIGIN}/api/files/9` });
  });

  it('follows nothing that is not https or the app', () => {
    for (const href of ['http://example.com/a', 'mailto:a@example.com', 'javascript:alert(1)', 'data:text/html,x', 'tel:1', '#notes', ''])
      expect(smallWindowLink(href, ORIGIN)).toEqual({ kind: 'none' });
  });

  it('opens an app page in the main window of this company, and an outside page in a new window', async () => {
    const t = fakeBridge();
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    await followFromSmallWindow({ kind: 'app', url: `${ORIGIN}/next/task/412` });
    expect(t.companies.switchTo).toHaveBeenCalledWith('c1', `${ORIGIN}/next/task/412`);
    expect(open).not.toHaveBeenCalled();
    await followFromSmallWindow({ kind: 'outside', url: 'https://example.com/a' });
    expect(open).toHaveBeenCalledWith('https://example.com/a', '_blank', 'noopener,noreferrer');
    await followFromSmallWindow({ kind: 'none' });
    expect(open).toHaveBeenCalledTimes(1);
    expect(t.companies.switchTo).toHaveBeenCalledTimes(1);
  });
});

describe('sign-out', () => {
  it('does nothing in a browser or an app without the call', async () => {
    await expect(tellDesktopSigningOut()).resolves.toBeUndefined();
    fakeBridge();
    await expect(tellDesktopSigningOut()).resolves.toBeUndefined();
  });

  it('waits for the app, but never long', async () => {
    const signingOut = vi.fn(async () => {});
    fakeBridge({ signingOut });
    await tellDesktopSigningOut();
    expect(signingOut).toHaveBeenCalledWith();
    vi.useFakeTimers();
    try {
      fakeBridge({ signingOut: () => new Promise<void>(() => {}) });
      const done = tellDesktopSigningOut();
      await vi.advanceTimersByTimeAsync(1000);
      await expect(done).resolves.toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('a call in this page', () => {
  it('tells the desktop app when it starts and ends, once each, and nothing to an older app', () => {
    resetDesktopReports();
    reportCallToDesktop(true); // no bridge: nothing
    fakeBridge();
    reportCallToDesktop(true); // an older app without reportCall: nothing
    const reportCall = vi.fn();
    fakeBridge({ reportCall });
    reportCallToDesktop(true);
    reportCallToDesktop(true);
    reportCallToDesktop(false);
    expect(reportCall.mock.calls).toEqual([[true], [false]]);
  });
});
