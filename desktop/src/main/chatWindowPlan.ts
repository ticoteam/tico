// What opening an agent's chat window does (platforms.md 4.6 for the call window it copies). Pure: chatWindows.ts
// applies it. Each window holds its own live stream, and a browser shares a handful of connections per site, so a
// company gets a few windows, never an unbounded number.

/** At most this many chat windows per company. */
export const CHAT_WINDOW_CAP = 6;

export type OpenChat = { companyId: string; agent: string };

/** 'focus': that agent's window is open already; 'open': a new one; 'full': the company holds the cap already. */
export function chatWindowStep(open: readonly OpenChat[], companyId: string, agent: string): 'focus' | 'open' | 'full' {
  const mine = open.filter((w) => w.companyId === companyId);
  if (mine.some((w) => w.agent === agent)) return 'focus';
  return mine.length >= CHAT_WINDOW_CAP ? 'full' : 'open';
}
