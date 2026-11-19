/* The skills helper's code (Connect Claude Code, backend/skill_devices.py) is a one-time approval: it never stays in the
   address, the cache, a toast or a log. The app takes it out of the address at startup, before the first render, and
   holds it here for the Skills page. A person signed out signs in first and the browser leaves, so it also waits in
   this tab's sessionStorage for that one hop (as GitHub's setup state does, start/handoff.ts); the first read takes it
   out, and it is good only for the code's own ten minutes. */

const KEY = 'tico.skillsHelper';
const CODE = /^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$/;
const TTL_MS = 10 * 60_000;

let held: string | null = null;

/** The address without a helper code in `?connect=` on the Skills page; the code is held for that page. */
export function takeHelperCode(address: string): string {
  const at = address.indexOf('?');
  if (at < 0 || address.slice(0, at) !== '/library/skills') return address;
  const q = new URLSearchParams(address.slice(at + 1));
  const code = q.get('connect');
  if (!code || !CODE.test(code)) return address;
  q.delete('connect');
  held = code;
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ code, at: Date.now() }));
  } catch {
    // storage blocked: a signed-in person still gets it; only the sign-in hop loses it
  }
  const rest = q.toString();
  return `/library/skills${rest ? `?${rest}` : ''}`;
}

/** The held code, if any; the tab's copy is taken out on this first read. */
export function heldHelperCode(): string | null {
  if (held) return held;
  try {
    const raw = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    const v = raw ? (JSON.parse(raw) as { code?: unknown; at?: unknown }) : null;
    if (v && typeof v.code === 'string' && CODE.test(v.code) && typeof v.at === 'number' && Date.now() - v.at < TTL_MS) held = v.code;
  } catch {
    // storage blocked or not ours: nothing held
  }
  return held;
}

/** The page has it in its own state: let it go. */
export function forgetHelperCode(): void {
  held = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked: nothing was kept
  }
}
