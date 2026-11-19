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

/**
 * The Windows taskbar has no badge count, only an overlay icon (platforms.md 4.7): a small round jade dot, the "waits
 * on you" mark (tokens.css --dot, #4F9C71), drawn here as premultiplied BGRA pixels for nativeImage.createFromBitmap.
 */
export function dotBitmap(size = 16): Buffer {
  const px = Buffer.alloc(size * size * 4);
  const centre = (size - 1) / 2;
  const radius = size / 2 - 1;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const alpha = Math.min(1, Math.max(0, radius + 0.5 - Math.hypot(x - centre, y - centre)));
      const i = (y * size + x) * 4;
      px[i] = Math.round(0x71 * alpha);
      px[i + 1] = Math.round(0x9c * alpha);
      px[i + 2] = Math.round(0x4f * alpha);
      px[i + 3] = Math.round(255 * alpha);
    }
  }
  return px;
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
