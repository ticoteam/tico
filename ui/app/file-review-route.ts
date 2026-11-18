/* ui/app/file-review-route.ts — the file review view's address and its opening (file-review.ts has the view itself):
   the parts the router, a file link and the viewer call while drawing, kept out of the file-review page part so a cold
   open of Home does not download the view (P05b). frvShow, frvAboveTask and frvTeardown are the part's entries: the
   first opening loads it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const FILE_ROUTE = '#/file/';
interface FrvCtx { task?: string | undefined; bot?: string | undefined }
interface FrvOpen extends FrvCtx { name?: string | undefined; version?: number | null | undefined }
interface FrvVersion { version: number; current?: boolean; name?: string | null; size?: number | null; mime?: string | null; actor?: string | null; created_at?: string | null }
interface FrvInfo { id: string; bot?: string | null; title?: string | null; name?: string | null; mime?: string | null; locator?: string | null;
  version?: number | null; open?: {type: string; url: string} | null; task_id?: string | null }
interface FrvState {
  id: string; name: string; v: number | null; cmp: boolean; ctx: FrvCtx; back: string;
  d: HTMLDialogElement; info: FrvInfo | null; versions: FrvVersion[]; chat: ThvView | null;
  offs: Array<() => void>; seq: number; poll: number; shown: string; gone: boolean;
}
let FRV: FrvState | null = null;
/** The review view's address for a file: a version pins it, a task or a bot names the chat beside it. */
function fileReviewHref(id: string, o: FrvOpen = {}): string {
  const q = new URLSearchParams();
  if (o.version) q.set('v', String(o.version));
  if (o.task) q.set('task', o.task); else if (o.bot) q.set('bot', o.bot);
  const s = q.toString();
  return FILE_ROUTE + encodeURIComponent(id) + (s ? '?' + s : '');
}
function frvParse(hash: string): (FrvOpen & {id: string}) | null {
  if (!hash.startsWith(FILE_ROUTE)) return null;
  const [path = '', query = ''] = hash.slice(FILE_ROUTE.length).split('?');
  let id = '';
  try { id = decodeURIComponent(path); } catch { return null; }
  if (!/^[A-Za-z0-9._:-]{1,200}$/.test(id)) return null;
  const q = new URLSearchParams(query), v = q.get('v') || '';
  return {id, version: /^[1-9][0-9]{0,8}$/.test(v) ? Number(v) : null, task: q.get('task') || undefined, bot: q.get('bot') || undefined};
}
// The page a cold link returns to: the task, the bot, else Library › Files.
const frvBase = (o: FrvCtx): string => o.task ? '#/task/' + encodeURIComponent(o.task) : o.bot ? '#/bot/' + encodeURIComponent(o.bot) : RD_FILES;
/** Whether an address belongs to the review view: its own, or the page under it that closing it returns to. */
const fileReviewOwns = (hash: string): boolean => hash.startsWith(FILE_ROUTE) || !!FRV && hash === FRV.back;

/** Opens a file in the review view, over the page that is showing (a history entry of its own). */
function fileReviewOpen(id: string, o: FrvOpen = {}): void {
  const href = fileReviewHref(id, o);
  if (FRV) { history.replaceState(history.state, '', href); frvShow({id, ...o}, FRV.back); return; }
  const back = location.hash || UPDATES;
  history.pushState({...(history.state || {}), fileReview: 1}, '', href);
  frvShow({id, ...o}, back);
}
/** Called first by route(): true when the address is the review view's (opened or updated) or the page under it
    (closed, the page left as it was), so the page is not drawn again. */
function fileReviewRoute(hash: string): boolean {
  const want = frvParse(hash);
  if (want) {
    if (FRV) { frvShow(want, FRV.back); return true; }
    const cold = !LAST_ROUTE;
    if (cold) {
      // A cold link: the page it belongs to is drawn under it, one history entry back, so closing returns there.
      const base = frvBase(want);
      history.replaceState(null, '', base);
      route(base);
      history.pushState({fileReview: 1}, '', hash);
    }
    frvShow(want, LAST_ROUTE || frvBase(want));
    if (cold && want.task) frvAboveTask();
    return true;
  }
  if (!FRV) return false;
  const back = FRV.back;
  frvTeardown();
  return hash === back;
}
function frvKindOf(name: string | null | undefined, mime: string | null | undefined): string | null {
  const m = String(mime || '').toLowerCase();
  if (/\.docx$/i.test(String(name || '')) || m === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return 'docx';
  const kind = viewKindOf(name);
  if (kind) return kind;
  if (m.startsWith('text/csv')) return 'csv';
  if (m === 'text/markdown') return 'markdown';
  if (m.startsWith('text/plain') || m === 'application/json') return 'text';
  if (m === 'application/pdf') return 'pdf';
  if (/^image\/(png|jpeg|gif|webp|avif)/.test(m)) return 'image';
  if (m.startsWith('video/')) return 'video';
  if (m.startsWith('audio/')) return 'audio';
  return null;
}
/** Google's own preview of a Google Doc, Sheet, Slides or Drive file, or '' for any other link. */
function googlePreviewUrl(href: string): string {
  let u: URL; try { u = new URL(href); } catch { return ''; }
  if (u.protocol !== 'https:') return '';
  if (u.hostname === 'docs.google.com') {
    const m = u.pathname.match(/^\/(document|spreadsheets|presentation|drawings)\/(?:u\/\d+\/)?d\/([\w-]{10,200})(?:\/|$)/);
    if (m) return `https://docs.google.com/${m[1]}/d/${m[2]}/preview`;
  }
  if (u.hostname === 'drive.google.com') {
    const m = u.pathname.match(/^\/file\/(?:u\/\d+\/)?d\/([\w-]{10,200})(?:\/|$)/);
    if (m) return `https://drive.google.com/file/d/${m[1]}/preview`;
  }
  return '';
}
