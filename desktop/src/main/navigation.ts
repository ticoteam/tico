// Where a top-level navigation in a company's view goes (platforms.md 4.3, after today's `stays_in_app` in
// app/src/windows.rs). Pure: the window code applies the decision.
import { isLoopback } from './address';

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
  | { kind: 'switch'; companyId: string; url: string }
  | { kind: 'external'; url: string }
  | { kind: 'block' };

const ACCESS_SUFFIX = '.cloudflareaccess.com';

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

export function decideNavigation(raw: string, ctx: NavContext): NavDecision {
  const url = parse(raw);
  if (!url) return { kind: 'block' };
  if (url.href === 'about:blank') return { kind: 'stay' };
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return externalOrBlock(url);

  const host = parse(ctx.origin)?.host ?? '';
  if (url.origin === ctx.origin) {
    return url.pathname.endsWith('/auth/login') ? { kind: 'sign-in' } : { kind: 'stay' };
  }
  const secure = url.protocol === 'https:' || isLoopback(url.hostname);
  if (url.hostname.endsWith(ACCESS_SUFFIX) && url.protocol === 'https:') {
    // Cloudflare's login names the application it signs in to: /cdn-cgi/access/login/<host>.
    const app = url.pathname.startsWith('/cdn-cgi/access/login/')
      ? url.pathname.slice('/cdn-cgi/access/login/'.length).split('/')[0]
      : null;
    return app === host ? { kind: 'sign-in' } : { kind: 'stay' };
  }
  if (ctx.signingIn && secure) return { kind: 'stay' };
  if (url.hostname === 'accounts.google.com' && url.protocol === 'https:') return { kind: 'stay' };
  const other = ctx.others.find((c) => c.origin === url.origin);
  if (other) return { kind: 'switch', companyId: other.id, url: url.href };
  if (ctx.filesOrigin && url.origin === ctx.filesOrigin) return { kind: 'block' };
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
  if (other) return { kind: 'switch', companyId: other.id, url: url.href };
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
