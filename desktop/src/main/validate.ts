// Checks for everything that arrives from a page over IPC (platforms.md 4.3, 4.10). Every handler runs these first
// and throws on anything else. Pure.
import { checkAddress, isLoopback, isPageUrl } from './address';
import { COMPANY_ID } from './companies';

export class BadArgument extends Error {
  constructor(what: string) {
    super(`Refused: ${what}`);
  }
}

export function companyIdArg(v: unknown): string {
  if (typeof v !== 'string' || !COMPANY_ID.test(v)) throw new BadArgument('company id');
  return v;
}

/** Call ids are the server's UUIDs. */
export function callIdArg(v: unknown): string {
  if (typeof v !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(v)) throw new BadArgument('call id');
  return v;
}

export function countArg(v: unknown): number {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < 0 || v > 99999) throw new BadArgument('count');
  return v;
}

export function boolArg(v: unknown): boolean {
  if (typeof v !== 'boolean') throw new BadArgument('flag');
  return v;
}

export function optionalBoolArg(v: unknown): boolean | undefined {
  return v === undefined ? undefined : boolArg(v);
}

/** A URL that must be on `origin`. */
export function urlOnOriginArg(v: unknown, origin: string): string {
  if (typeof v !== 'string' || v.length > 4096) throw new BadArgument('url');
  let url: URL;
  try {
    url = new URL(v);
  } catch {
    throw new BadArgument('url');
  }
  if (url.origin !== origin) throw new BadArgument('url on another origin');
  return url.href;
}

/**
 * The page `companies:switch` may load. Only a company's own page may pass one, for itself (the small call window
 * opening a page in its main window), and only one of the app's pages (never /api/ or /auth/). Switching to another
 * company ignores it: that company opens where it was, so no company can drive another's session.
 */
export function switchUrlArg(v: unknown, callerId: string, targetId: string, origin: string): string | undefined {
  if (v === undefined || callerId !== targetId) return undefined;
  const url = urlOnOriginArg(v, origin);
  if (!isPageUrl(url, origin)) throw new BadArgument('url is not a page of the app');
  return url;
}

/** A server address to prefill Add company with: only a valid one is used, anything else is ignored. */
export function serverPrefillArg(v: unknown): string | undefined {
  if (v === undefined) return undefined;
  if (typeof v !== 'string' || v.length > 2048) throw new BadArgument('server');
  const address = checkAddress(v);
  return address.ok ? address.origin : undefined;
}

export function selfInfoArg(v: unknown): { name: string; email: string; signedOut: boolean } {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new BadArgument('self info');
  const o = v as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.some((k) => k !== 'name' && k !== 'email' && k !== 'signedOut')) throw new BadArgument('self info');
  if (typeof o.name !== 'string' || typeof o.email !== 'string' || typeof o.signedOut !== 'boolean') {
    throw new BadArgument('self info');
  }
  if (o.name.length > 200 || o.email.length > 200) throw new BadArgument('self info');
  return { name: o.name, email: o.email, signedOut: o.signedOut };
}

/** The files origin for user-made pages: a bare https origin (or loopback http) that is not the company's own. */
export function filesOriginArg(v: unknown, companyOrigin: string): string {
  if (typeof v !== 'string' || v.length > 2048) throw new BadArgument('files origin');
  let url: URL;
  try {
    url = new URL(v);
  } catch {
    throw new BadArgument('files origin');
  }
  const secure = url.protocol === 'https:' || (url.protocol === 'http:' && isLoopback(url.hostname));
  if (!secure || url.origin !== v.replace(/\/$/, '') || url.origin === companyOrigin) throw new BadArgument('files origin');
  return url.origin;
}

/**
 * Only the top frame of a page this app opened, while it is on that page's company origin, may call the shell.
 * Today's rule is the same: "Only the selected server's page may call the app" (app/src/bridge.rs).
 */
/** A view's top-level page is its company's own (not a provider's sign-in page, nor anything else it reached). */
export function onOwnOrigin(pageUrl: string | null, origin: string): boolean {
  return senderAllowed(pageUrl, true, origin);
}

export function senderAllowed(frameUrl: string | null, isTopFrame: boolean, origin: string): boolean {
  if (frameUrl === null || !isTopFrame) return false;
  try {
    return new URL(frameUrl).origin === origin;
  } catch {
    return false;
  }
}
