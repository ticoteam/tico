import type { IconName } from '@/kit/Icon';
import { linksIn } from '@/kit/Markdown';
import { newAddressFor } from '@/oldAddress/map';

/* What an address in a message points at, read from the address alone (thread-content.html, "What is in a thread"):
   one of the app's files (it opens full screen), a page of the app (it opens beside the thread), or a place on the
   web (it opens there, in a new tab), with the words and the mark its chip shows. Plain functions: no React. */

export type Pointer =
  | { kind: 'file'; file: string; version?: number }
  | { kind: 'app'; to: string; icon: IconName; what: 'task' | 'teammate' | 'thread' | 'doc' | 'page' }
  | { kind: 'web'; href: string; icon: IconName; label: string; site: string };

/** An app file id: `file-<hex>` (a page, a spec) or any id the files route takes. */
const FILE_ID = /^[\w:-]{1,200}$/;

const whole = (n: string | null) => (n && /^[1-9]\d{0,8}$/.test(n) ? Number(n) : undefined);

/** A file the app serves: `/api/v2/files/<id>`, its `/versions/<n>`, or `?v=<n>`. */
function fileAt(path: string, search: URLSearchParams): Pointer | null {
  const m = /^\/api\/v2\/files\/([^/]+)(?:\/versions\/(\d+))?\/?$/.exec(path);
  if (!m || !FILE_ID.test(decodeURIComponent(m[1]!))) return null;
  return { kind: 'file', file: decodeURIComponent(m[1]!), version: whole(m[2] ?? search.get('v')) };
}

/** A page of this app, by its new address (path and search). Files in the library open as files. */
function appAt(to: string): Pointer {
  const u = new URL(to, 'http://app');
  const p = u.pathname;
  if (p === '/library/files' && u.searchParams.get('file')) {
    return { kind: 'file', file: u.searchParams.get('file')!, version: whole(u.searchParams.get('v')) };
  }
  if (/^\/(task|flow)\/[^/]+|^\/tasks\/[^/]+\/[^/]+/.test(p)) return { kind: 'app', to, icon: 'tasks', what: 'task' };
  if (/^\/team\/[^/]+/.test(p)) return { kind: 'app', to, icon: 'people', what: 'teammate' };
  if (/^\/threads\/[^/]+/.test(p)) return { kind: 'app', to, icon: 'threads', what: 'thread' };
  if (/^\/library\/(?!docs$|files$|skills$|tools$|contacts$)[^/]+$/.test(p)) return { kind: 'app', to, icon: 'library', what: 'doc' };
  return { kind: 'app', to, icon: 'link', what: 'page' };
}

/** `www.` and a trailing dot off a host. */
const bareHost = (h: string) => h.replace(/^www\./, '').replace(/\.$/, '');

/** A path's last part in words, when it is words ("cedar-raised-bed-4ft" → "Cedar raised bed 4ft"); else ''. */
function slugWords(path: string): string {
  const last = decodeURIComponent(path.split('/').filter(Boolean).pop() ?? '').replace(/\.(html?|php|aspx?)$/i, '');
  if (!/[a-z]{3,}[-_][a-z]{2,}/i.test(last) || /^[\w]{20,}$/.test(last)) return '';
  const words = last.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A place on the web: the chip's mark and words, by what the address is. Known sites name the thing; any other
    site is its name and, when the path is words, those words. */
function webAt(u: URL): Pointer {
  const href = u.href;
  const host = bareHost(u.hostname.toLowerCase());
  const parts = u.pathname.split('/').filter(Boolean).map((s) => decodeURIComponent(s));
  const web = (icon: IconName, label: string): Pointer => ({ kind: 'web', href, icon, label, site: host });
  if (host === 'github.com' && parts.length >= 2) {
    const [, repo, what, n] = parts;
    if (what === 'pull' && n) return web('pr', `${repo} #${n}`);
    if (what === 'issues' && n) return web('issue', `${repo} #${n}`);
    if (what === 'commit' && n) return web('pr', `${repo} ${n.slice(0, 7)}`);
    if (what === 'releases' && parts[3] === 'tag' && parts[4]) return web('globe', `${repo} ${parts[4]}`);
    if (what === 'actions') return web('play', `${repo} checks`);
    if (what === 'blob' || what === 'tree') return web('file', parts[parts.length - 1] ?? `${repo}`);
    return web('globe', `${parts[0]}/${repo}`);
  }
  if (host === 'x.com' || host === 'twitter.com' || host === 'mobile.twitter.com') {
    return parts[0] && !['i', 'home', 'search'].includes(parts[0]) ? web('message', `@${parts[0]}`) : web('message', 'X');
  }
  if (host === 'youtube.com' || host === 'youtu.be' || host === 'm.youtube.com') return web('video', 'YouTube');
  if (host === 'loom.com') return web('video', 'Loom');
  if (host === 'vimeo.com') return web('video', 'Vimeo');
  if (host === 'docs.google.com') {
    const k = parts[0];
    return web('file', k === 'spreadsheets' ? 'Google Sheet' : k === 'presentation' ? 'Google Slides' : k === 'forms' ? 'Google Form' : 'Google Doc');
  }
  if (host === 'drive.google.com') return web('file', 'Drive file');
  if (host === 'figma.com') return web('edit', 'Figma');
  if (host === 'notion.so' || host.endsWith('.notion.site')) return web('file', slugWords(u.pathname).replace(/ [0-9a-f]{32}$/i, '') || 'Notion');
  if (host === 'linear.app' && parts[1] === 'issue' && parts[2]) return web('issue', parts[2].toUpperCase());
  if (host.endsWith('.slack.com') || host === 'slack.com') return web('threads', 'Slack message');
  const words = slugWords(u.pathname);
  return { kind: 'web', href, icon: 'globe', label: words ? `${host} · ${words}` : host + (parts.length ? '/…' : ''), site: host };
}

/**
 * What `href` points at, as a message reads it. `origin` is this app's own (`location.origin`): an address on it, a
 * relative one or an old app address (`#/task/12`) is the app's; anything else on http(s) is the web's. Null for an
 * address that is neither (mailto:, a bare #section): it stays a plain link.
 */
export function pointerOf(href: string, origin: string): Pointer | null {
  if (/^\/?#\//.test(href)) {
    const to = newAddressFor(href);
    return to ? appAt(to) : null;
  }
  let u: URL;
  try {
    u = new URL(href, origin);
  } catch {
    return null;
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  const own = u.origin === origin;
  if (own || (href.startsWith('/') && !href.startsWith('//'))) {
    if (u.hash.startsWith('#/') && (u.pathname === '/' || u.pathname === '/next/')) {
      const to = newAddressFor(u.hash);
      return to ? appAt(to) : null;
    }
    const f = fileAt(u.pathname, u.searchParams);
    if (f) return f;
    if (u.pathname.startsWith('/api/')) return null;
    return appAt(u.pathname.replace(/^\/next(?=\/|$)/, '') + u.search);
  }
  return webAt(u);
}

/** `page:file-<hex>@2` and `file-<hex>@2`, as agents name a page in words: a link the chip can draw. Never inside
    code. */
const REF = /(^|[\s(,;:])(?:page:)?(file-[0-9a-f]{12,40})(?:@(\d{1,6}))?(?=$|[\s),.;:!?])/g;

export function withFileRefs(text: string): string {
  if (!text.includes('file-')) return text;
  return text
    .split(/(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`|\[[^\]\n]*\]\([^)\n]*\))/)
    .map((part, i) => (i % 2 ? part : part.replace(REF, (_m, pre: string, id: string, v?: string) => `${pre}[${id}](/api/v2/files/${id}${v ? `/versions/${v}` : ''})`)))
    .join('');
}

/** The words a chip shows for words that are only an address or a ref: nothing to keep. */
export const isRawWords = (words: string, href: string) => words === href || /^(page:)?file-[0-9a-f]{12,40}(@\d+)?$/.test(words) || /^https?:\/\//.test(words);

/** A path part decoded, or '' when its escape is malformed (it names nothing). */
function decoded(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return '';
  }
}

/** The app's tasks a message's text links to (`/tasks/<pipeline>/<n>`, `/task/<id>`, `/flow/<n>`, an old `#/task/…`),
    read from the links its Markdown draws, each once, by its number or id (the route's task reference). */
export function tasksIn(text: string, origin: string): string[] {
  const out = new Set<string>();
  for (const href of linksIn(withFileRefs(text), true)) {
    const p = pointerOf(href, origin);
    if (p?.kind !== 'app' || p.what !== 'task') continue;
    const [, kind, a = '', b = ''] = /^\/(tasks|task|flow)\/([^/]+)(?:\/([^/]+))?/.exec(new URL(p.to, 'http://app').pathname) ?? [];
    // the pipeline's editor (`/tasks/<pipeline>/edit`) is no task
    if (kind === 'tasks' && b === 'edit') continue;
    const key = decoded(kind === 'tasks' ? b : a);
    if (key) out.add(key);
  }
  return [...out.values()];
}
