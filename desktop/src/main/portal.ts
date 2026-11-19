// "Sign in to Tico" through the Tico Cloud portal (platforms.md 6, after app/src/signin.rs). The portal lists the
// person's Cloud companies; when they pick, it goes to `{portal}/desktop/orgs/done?issuer=<origin>&org=<server>...`.
// That navigation is cancelled and read here. Pure.
import { checkAddress } from './address';

const DEFAULT_PORTAL = 'https://app.tico.dev';

/** TICO_PORTAL_URL (tests, development) when it is a valid address, else Tico Cloud's portal. */
export function portalOrigin(env: string | undefined): string {
  if (env) {
    const address = checkAddress(env);
    if (address.ok) return address.origin;
  }
  return DEFAULT_PORTAL;
}

export const portalPage = (portal: string) => `${portal}/desktop/orgs`;

export function isDone(portal: string, raw: string): boolean {
  try {
    const url = new URL(raw);
    return url.origin === portal && url.pathname === '/desktop/orgs/done';
  } catch {
    return false;
  }
}

/**
 * A done page is the portal's own answer only when the portal's page asked for it: the frame that started the
 * navigation is on the portal's origin, and so is the page it leaves (for a redirect, the address that redirected).
 * Any other page reached in the sign-in window (a provider's, a link's) cannot name companies or an issuer.
 */
export function doneFromPortal(portal: string, raw: string, initiatorOrigin: string | null, from: string | null): boolean {
  if (!isDone(portal, raw) || initiatorOrigin !== portal || !from) return false;
  try {
    return new URL(from).origin === portal;
  } catch {
    return false;
  }
}

export type Picks = { issuer: string; servers: string[] };

/**
 * The picks a done page carries: exactly one issuer (an origin, never Google's or the portal's own host), and each
 * server valid as a typed address, repeats dropped. Null without a usable issuer.
 */
export function parsePicks(portal: string, raw: string): Picks | null {
  if (!isDone(portal, raw)) return null;
  const url = new URL(raw);
  const issuers = url.searchParams.getAll('issuer');
  if (issuers.length !== 1) return null;
  const issuerRaw = issuers[0]!;
  if (!/^https?:\/\//i.test(issuerRaw)) return null;
  const issuer = checkAddress(issuerRaw);
  if (!issuer.ok) return null;
  let issuerUrl: URL;
  try {
    issuerUrl = new URL(issuerRaw);
  } catch {
    return null;
  }
  if (issuerUrl.pathname !== '/' || issuerUrl.search || issuerUrl.hash) return null;
  const host = new URL(issuer.origin).hostname;
  const portalHost = new URL(portal).hostname;
  const loopback = host === 'localhost' || host === '127.0.0.1';
  if (host === 'google.com' || host.endsWith('.google.com') || (!loopback && host === portalHost)) return null;
  const servers: string[] = [];
  for (const raw of url.searchParams.getAll('org')) {
    if (!/^https?:\/\//i.test(raw)) continue;
    const address = checkAddress(raw);
    if (address.ok && !servers.includes(address.origin)) servers.push(address.origin);
  }
  return { issuer: issuer.origin, servers };
}

/** A cookie belongs to the issuer when its domain, less one leading dot, is the issuer's host; not a parent or child. */
export function issuerCookie(domain: string | undefined, host: string): boolean {
  if (!domain || !host) return false;
  return domain.replace(/^\./, '').toLowerCase() === host.toLowerCase();
}
