import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { DesktopMove, systemOf } from './DesktopMove';
import { RELEASES_URL, downloadHref } from './queries';

const DMG = 'https://github.com/ticoteam/tico/releases/download/v0.6.150/Tico-0.6.150-arm64.dmg';
const asked: string[] = [];

function serve(body: unknown) {
  vi.stubGlobal('fetch', async (url: string) => {
    asked.push(String(url));
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

/** The page inside a desktop shell: the older one's bridge, the new one's, or both. */
function shell({ old = false, now = false }: { old?: boolean; now?: boolean }) {
  if (old) vi.stubGlobal('ticoNative', () => ({ postMessage: () => undefined }));
  if (now) vi.stubGlobal('tico', { platform: 'mac', version: '0.6.150' });
}

const show = () =>
  render(
    <QueryClientProvider client={queryClient}>
      <DesktopMove />
    </QueryClientProvider>,
  );

beforeEach(() => {
  queryClient.clear();
  sessionStorage.clear();
  asked.length = 0;
  vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('The new desktop app, inside the older one', () => {
  it('offers the download for this computer, from the server', async () => {
    shell({ old: true });
    serve({ available: true, app_kind: 'generic', url: DMG, file: 'Tico-0.6.150-arm64.dmg' });
    show();
    expect(screen.getByRole('status').textContent).toContain('A new Tico desktop app is ready.');
    const link = screen.getByRole('link', { name: 'Download' });
    await waitFor(() => expect(link.getAttribute('href')).toBe(DMG));
    expect(link.getAttribute('target')).toBe('_blank');
    expect(asked).toEqual(['/api/download/mac']);
  });

  it('stays away for the session once put away', () => {
    shell({ old: true });
    serve({ available: false });
    const first = show();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('status')).toBeNull();
    first.unmount();
    show();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('draws nothing in a browser or in the new app', () => {
    serve({ available: true, app_kind: 'generic', url: DMG });
    const browser = show();
    expect(browser.container.innerHTML).toBe('');
    browser.unmount();
    shell({ old: true, now: true });
    expect(show().container.innerHTML).toBe('');
    expect(asked).toEqual([]);
  });

  it('sends to the release page without a generic installer', () => {
    expect(downloadHref(undefined)).toBe(RELEASES_URL);
    expect(downloadHref({ available: false })).toBe(RELEASES_URL);
    // A company's own build is the older app.
    expect(downloadHref({ available: true, app_kind: 'company', url: 'https://runner.example.com/download/file/1.0.0/Acme.dmg' })).toBe(RELEASES_URL);
    expect(downloadHref({ available: true, app_kind: 'generic', url: 'javascript:alert(1)' })).toBe(RELEASES_URL);
    expect(downloadHref({ available: true, app_kind: 'generic', url: DMG })).toBe(DMG);
  });

  it('picks the system from the platform: the older app’s user agent names none', () => {
    const ua = 'TicoHub/2.0 Tauri';
    expect(systemOf({ platform: 'MacIntel', userAgent: ua })).toBe('mac');
    expect(systemOf({ platform: 'Win32', userAgent: ua })).toBe('windows');
    expect(systemOf({ platform: 'Linux x86_64', userAgent: ua })).toBe('linux');
    expect(systemOf({ platform: '', userAgent: ua, userAgentData: { platform: 'Windows' } })).toBe('windows');
    expect(systemOf({ platform: '', userAgent: 'Mozilla/5.0 (X11; Linux x86_64)' })).toBe('linux');
    expect(systemOf({ platform: '', userAgent: ua })).toBeNull();
  });
});
