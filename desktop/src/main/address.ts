// Server addresses. A company is an origin (platforms.md 5): whatever path, query or fragment a pasted link has is
// dropped. HTTPS everywhere; plain HTTP only for this computer (loopback), as today's app (app/src/config.rs).

export const APP_PATH = '/next/';
const CLOUD_DOMAIN = 'tico.team';

export type AddressResult = { ok: true; origin: string } | { ok: false; error: string };

export function isLoopback(host: string): boolean {
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
}

/** What a person typed or pasted into Add company, or a server handed over by a link. */
export function checkAddress(raw: string): AddressResult {
  const text = raw.trim();
  if (!text || text.length > 2048) return { ok: false, error: 'Enter a server address, such as https://acme.tico.team.' };
  // A bare name is a Tico Cloud company: "acme" is https://acme.tico.team.
  const withHost = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(text) ? `${text}.${CLOUD_DOMAIN}` : text;
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(withHost) ? withHost : `https://${withHost}`;
  let url: URL;
  try {
    url = new URL(withScheme);
  } catch {
    return { ok: false, error: 'Enter a server address, such as https://acme.tico.team.' };
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    return { ok: false, error: 'Use an https:// server address.' };
  }
  if (!url.hostname) return { ok: false, error: 'Enter a server address, such as https://acme.tico.team.' };
  if (url.username || url.password) return { ok: false, error: 'Use an address without a user name or password.' };
  if (url.protocol === 'http:' && !isLoopback(url.hostname)) {
    return { ok: false, error: 'Use HTTPS. Plain HTTP works only for this computer (localhost).' };
  }
  return { ok: true, origin: url.origin };
}

/** The new app's first page on a company's server. */
export function appUrl(origin: string): string {
  return origin + APP_PATH;
}

export function originOf(raw: string): string | null {
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.origin : null;
  } catch {
    return null;
  }
}

/** A URL inside the new app on this origin: the only kind kept as a company's last place. */
export function isAppUrl(raw: string, origin: string): boolean {
  try {
    const url = new URL(raw);
    return url.origin === origin && (url.pathname === '/next' || url.pathname.startsWith(APP_PATH));
  } catch {
    return false;
  }
}

/**
 * A page on `origin` the app may load for someone other than that page itself (another company's link, a tico://
 * link, a page's own switch): never the server's API or sign-in addresses, where a plain GET can act (sign-out is
 * `GET /api/v2/logout`). Checked on the decoded path, as the server routes it, and with no encoded slash in it.
 */
export function isPageUrl(raw: string, origin: string): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.origin !== origin || /%2f|%5c/i.test(url.pathname)) return false;
  let path: string;
  try {
    path = decodeURIComponent(url.pathname).toLowerCase().replace(/\/+/g, '/');
  } catch {
    return false;
  }
  return !/^\/(api|auth)(\/|$)/.test(path);
}

/** A local copy's token sign-in (backend: `GET /api/v2/local-signin?token=…&next=…`), its only way in. */
const LOCAL_SIGNIN_PATH = '/api/v2/local-signin';

/**
 * A local copy's sign-in link: plain http on this computer (loopback) at exactly LOCAL_SIGNIN_PATH, with its query.
 * The one /api/ address the app may open for someone (a tico:// link, a link pasted into Add company); never for a
 * server that is not on this computer. Returns the link to load, or null. The token is in it: never log it.
 */
export function localSignInUrl(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' || !isLoopback(url.hostname) || url.username || url.password) return null;
  if (url.pathname !== LOCAL_SIGNIN_PATH || !url.searchParams.get('token')) return null;
  url.hash = '';
  return url.href;
}

export function hostOf(origin: string): string {
  try {
    return new URL(origin).host;
  } catch {
    return origin;
  }
}
