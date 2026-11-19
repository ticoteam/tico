// tico:// links (platforms.md 4.5). Pure.
import { APP_PATH, checkAddress, isPageUrl } from './address';

export type DeepLink =
  | { kind: 'open'; companyId: string; url?: string }
  | { kind: 'add'; server: string }
  | { kind: 'focus' };

/**
 * - `tico://open?u=<encoded https URL>`: the company whose origin matches, at that URL; an unknown server opens
 *   Add company prefilled with it.
 * - `tico://<path>` (today's form): the active company at /next/<path>.
 * Anything else only brings the window forward.
 */
export function parseDeepLink(
  raw: string,
  companies: ReadonlyArray<{ id: string; origin: string }>,
  activeId: string | null,
): DeepLink | null {
  let link: URL;
  try {
    link = new URL(raw);
  } catch {
    return null;
  }
  if (link.protocol !== 'tico:' || raw.length > 4096) return null;

  if (link.hostname === 'open') {
    const u = link.searchParams.get('u');
    if (!u) return { kind: 'focus' };
    const address = checkAddress(u);
    if (!address.ok || !/^https?:\/\//i.test(u)) return { kind: 'focus' };
    const company = companies.find((c) => c.origin === address.origin);
    if (!company) return { kind: 'add', server: address.origin };
    // Any website can hand the app a tico:// link: it opens the company, at the link only when that is an app page.
    const href = new URL(u).href;
    return isPageUrl(href, company.origin) ? { kind: 'open', companyId: company.id, url: href } : { kind: 'open', companyId: company.id };
  }

  const active = companies.find((c) => c.id === activeId);
  const path = `${link.hostname}${link.pathname}`.replace(/^\/+|\/+$/g, '');
  if (!active || !path) return { kind: 'focus' };
  // An encoded slash or a dot segment could step a server that decodes paths out of /next/.
  if (/%2f|%5c/i.test(path) || path.split('/').some((s) => s === '..' || s === '.')) return { kind: 'focus' };
  let target: URL;
  try {
    target = new URL(APP_PATH + path + link.search, active.origin);
  } catch {
    return { kind: 'focus' };
  }
  // A path can never leave the company (`//evil.example`, `..` out of /next/).
  if (target.origin !== active.origin || !target.pathname.startsWith(APP_PATH)) return { kind: 'focus' };
  return { kind: 'open', companyId: active.id, url: target.href };
}

/** The tico:// link in a second instance's command line (Windows, Linux). */
export function deepLinkFromArgv(argv: readonly string[]): string | null {
  return argv.find((a) => a.toLowerCase().startsWith('tico://')) ?? null;
}
