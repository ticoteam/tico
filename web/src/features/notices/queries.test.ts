import { describe, expect, it } from 'vitest';
import { RELEASES_URL, downloadHref, systemOf } from './queries';

const DMG = 'https://github.com/ticoteam/tico/releases/download/v0.6.150/Tico-0.6.150-arm64.dmg';

describe('The desktop app’s download, for this computer', () => {
  it('goes to the server’s installer, or to the release page without one', () => {
    expect(downloadHref({ available: true, app_kind: 'generic', url: DMG })).toBe(DMG);
    expect(downloadHref(undefined)).toBe(RELEASES_URL);
    expect(downloadHref({ available: false })).toBe(RELEASES_URL);
    expect(downloadHref({ available: true, url: DMG })).toBe(RELEASES_URL);
    expect(downloadHref({ available: true, app_kind: 'generic', url: 'javascript:alert(1)' })).toBe(RELEASES_URL);
  });

  it('picks the system from the platform, and from the user agent only without one', () => {
    const ua = 'Mozilla/5.0';
    expect(systemOf({ platform: 'MacIntel', userAgent: ua })).toBe('mac');
    expect(systemOf({ platform: 'Win32', userAgent: ua })).toBe('windows');
    expect(systemOf({ platform: 'Linux x86_64', userAgent: ua })).toBe('linux');
    expect(systemOf({ platform: '', userAgent: ua, userAgentData: { platform: 'Windows' } })).toBe('windows');
    expect(systemOf({ platform: '', userAgent: 'Mozilla/5.0 (X11; Linux x86_64)' })).toBe('linux');
    expect(systemOf({ platform: '', userAgent: ua })).toBeNull();
  });
});
