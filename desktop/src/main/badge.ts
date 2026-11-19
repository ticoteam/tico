// The Dock badge: every company's "needs you" count, summed (platforms.md 4.7). A signed-out company shows "!" on
// macOS when nothing else is counted, as today. Pure.

export function badgeFor(companies: ReadonlyArray<{ needs: number; signedOut: boolean }>): { count: number; text: string } {
  const count = Math.min(
    99999,
    companies.reduce((sum, c) => sum + (c.signedOut ? 0 : c.needs), 0),
  );
  const text = count > 0 ? String(count) : companies.some((c) => c.signedOut) ? '!' : '';
  return { count, text };
}

/** Chrome's user agent without the Electron and app tokens, so sign-in providers see a plain browser. */
export function cleanUserAgent(ua: string, appName: string): string {
  const app = appName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return `${ua.replace(/ Electron\/\S+/, '').replace(new RegExp(` ${app}\\/\\S+`, 'i'), '')} TicoDesktop/3`;
}
