// The Dock badge: every company's "needs you" count, summed (platforms.md 4.7). A signed-out company shows "!" on
// macOS when nothing else is counted, as today. Also what the pages report that the app acts on (a live call). Pure.

export function badgeFor(companies: ReadonlyArray<{ needs: number; signedOut: boolean }>): { count: number; text: string } {
  const count = Math.min(
    99999,
    companies.reduce((sum, c) => sum + (c.signedOut ? 0 : c.needs), 0),
  );
  const text = count > 0 ? String(count) : companies.some((c) => c.signedOut) ? '!' : '';
  return { count, text };
}

/** A call is live somewhere: in the small window, or in any company's page (on screen or not). Restart waits. */
export function anyCallLive(smallWindow: boolean, pages: ReadonlyArray<{ callLive: boolean }>): boolean {
  return smallWindow || pages.some((p) => p.callLive);
}

/** Chrome's user agent without the Electron and app tokens, so sign-in providers see a plain browser. */
export function cleanUserAgent(ua: string, appName: string): string {
  const app = appName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return `${ua.replace(/ Electron\/\S+/, '').replace(new RegExp(` ${app}\\/\\S+`, 'i'), '')} TicoDesktop/3`;
}
