// Where a top-level navigation in a company's view goes (platforms.md 4.3, after today's `stays_in_app` in
// app/src/windows.rs). Pure: the window code applies the decision.
import { isLoopback, isPageUrl } from './address';

export type NavContext = {
  /** The company's origin. */
  origin: string;
  /** A sign-in chain started by the company's own /auth/login or its Cloudflare Access login is under way. */
  signingIn: boolean;
  /** Every other company in the app. */
  others: ReadonlyArray<{ id: string; origin: string }>;
  /** The company's files origin for user-made pages (setFilesOrigin): iframes only, never top level. */
  filesOrigin?: string | null;
};

export type NavDecision =
  | { kind: 'stay' }
  | { kind: 'sign-in' } // stays, and starts a sign-in chain
  | { kind: 'switch'; companyId: string; url?: string } // url: where to open it; without one, where it was
  | { kind: 'external'; url: string }
  | { kind: 'block' };

const ACCESS_SUFFIX = '.cloudflareaccess.com';
/** The company's own sign-in (backend/oidc.py LOGIN_PATH): exactly this path, nothing that merely ends in it. */
const LOGIN_PATH = '/auth/login';

function parse(raw: string): URL | null {
  try {
    return new URL(raw);
  } catch {
    return null;
  }
}

/** What the system browser may be handed: https links and mail links, nothing else. */
export function externalOrBlock(url: URL): NavDecision {
  return url.protocol === 'https:' || url.protocol === 'mailto:' ? { kind: 'external', url: url.href } : { kind: 'block' };
}

/** Another company: its view comes forward, at the link only when that is one of its app pages (never /api/ or /auth/). */
function switchTo(other: { id: string; origin: string }, url: URL): NavDecision {
  return isPageUrl(url.href, other.origin) ? { kind: 'switch', companyId: other.id, url: url.href } : { kind: 'switch', companyId: other.id };
}

/** Cloudflare Access's login for this company's host: /cdn-cgi/access/login/<host> on <team>.cloudflareaccess.com. */
function accessLogin(url: URL, host: string): boolean {
  if (url.protocol !== 'https:' || !url.hostname.endsWith(ACCESS_SUFFIX)) return false;
  const prefix = '/cdn-cgi/access/login/';
  return url.pathname.startsWith(prefix) && url.pathname.slice(prefix.length).split('/')[0] === host;
}

export function decideNavigation(raw: string, ctx: NavContext): NavDecision {
  const url = parse(raw);
  if (!url) return { kind: 'block' };
  if (url.href === 'about:blank') return { kind: 'stay' };
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return externalOrBlock(url);

  if (url.origin === ctx.origin) return url.pathname === LOGIN_PATH ? { kind: 'sign-in' } : { kind: 'stay' };
  // Isolation comes before any sign-in exception: another company always opens in its own view (its own session),
  // and the files origin never loads top level, in the middle of a sign-in chain too.
  const other = ctx.others.find((c) => c.origin === url.origin);
  if (other) return switchTo(other, url);
  if (ctx.filesOrigin && url.origin === ctx.filesOrigin) return { kind: 'block' };
  if (accessLogin(url, parse(ctx.origin)?.host ?? '')) return { kind: 'sign-in' };
  const secure = url.protocol === 'https:' || isLoopback(url.hostname);
  if (ctx.signingIn && secure) return { kind: 'stay' };
  if (url.hostname === 'accounts.google.com' && url.protocol === 'https:') return { kind: 'stay' };
  return externalOrBlock(url);
}

/**
 * A new-window request (`window.open`, `target=_blank`). No page gets a second window: another company switches,
 * anything else the browser may open goes to the browser, and the rest is dropped.
 */
export function decideNewWindow(raw: string, ctx: NavContext): NavDecision {
  const url = parse(raw);
  if (!url) return { kind: 'block' };
  const other = ctx.others.find((c) => c.origin === url.origin);
  if (other) return switchTo(other, url);
  if (ctx.filesOrigin && url.origin === ctx.filesOrigin) return { kind: 'block' };
  return externalOrBlock(url);
}

/**
 * The small call window stays on its call (platforms.md 4.6). On top of the company view's decision: another page of
 * the company opens in the main window (a switch to the same company), a file goes where the main window sends one
 * (the browser, https only), and the call's own page, a sign-in chain and everything off the company are unchanged.
 */
export function keepOnCall(raw: string, base: NavDecision, ctx: NavContext & { companyId: string; callPath: string }): NavDecision {
  if (ctx.signingIn || (base.kind !== 'stay' && base.kind !== 'external')) return base;
  const url = parse(raw);
  if (!url || url.origin !== ctx.origin) return base;
  const path = url.pathname.replace(/\/+$/, '');
  if (path === ctx.callPath.replace(/\/+$/, '') || path.startsWith('/auth/')) return base;
  if (path.startsWith('/api/')) return externalOrBlock(url);
  return { kind: 'switch', companyId: ctx.companyId, url: url.href };
}

/** A top-level page committed: back on the company origin, a sign-in chain is over. */
export function signInOver(committedUrl: string, origin: string): boolean {
  return parse(committedUrl)?.origin === origin;
}
