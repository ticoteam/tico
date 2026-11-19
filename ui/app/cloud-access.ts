/* ui/app/cloud-access.ts — Tico Cloud: the Out of credit strip and Pay to continue (backend/cloud_access.py)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// HQ's word on a cloud company: `cloud_access` on GET /api/status (every page's poll), GET /api/v2/cloud-access, and
// the `cloud` of any 402 `locked`. Off cloud it is null and nothing here shows. Frozen (out of credit): a strip over
// every page, "Out of credit · Add credit"; everything else stays usable. Locked (trial over, or unpaid): the whole page
// is Pay to continue, with Download your files for the owner (Settings › General's Download all files). While either
// shows, the state is read every 30 s, so paying opens the app by itself; leaving a lock reloads the page, since what
// was open behind it was refused while it lasted.
// Low credit (open, under two days of spend left: `days_left`, backend/cloud_access.py) shows the same strip as a warning,
// "Low credit · about N days left · Add credit", before anything stops.
// The cloud company's access state as HQ sends it: access is open, frozen or locked, with the reason and the billing page.
interface CloudAccessState {
  access?: string;
  reason?: string | null;
  billing_url?: unknown;
  days_left?: number | null;
  [key: string]: unknown;
}
const CLOUD_ACCESS_WORDS = ['open', 'frozen', 'locked'];
let CLOUD_ACCESS: CloudAccessState | null = null, CLOUD_TIMER = 0, CLOUD_SHOWN = 'open', CLOUD_ROLE: string | null = null;
const CLOUD_POLL = 30000;
const CLOUD_WHY: Record<string, string> = {trial_over: 'Your trial has ended.', unpaid: "This month's payment didn't go through."};
const cloudPayUrl = (u: unknown): string => typeof u === 'string' && /^https:\/\/\S+$/.test(u) ? u : '';
// A cloud value that carries one of the access words above, and is not anything else; any other value is not a cloud company.
function isCloudAccessState(value: unknown): value is CloudAccessState {
  return typeof value === 'object' && value !== null && 'access' in value && typeof value.access === 'string' && CLOUD_ACCESS_WORDS.includes(value.access);
}
function cloudAccessApply(cloud: unknown) {
  CLOUD_ACCESS = isCloudAccessState(cloud) ? cloud : null;
  const access = CLOUD_ACCESS?.access || 'open', was = CLOUD_SHOWN;
  CLOUD_SHOWN = access;
  if (was === 'locked' && access !== 'locked') { location.reload(); return; }
  cloudAccessRender();
  clearTimeout(CLOUD_TIMER);
  if (access !== 'open' || cloudLowDays() !== null) CLOUD_TIMER = setTimeout(cloudAccessPoll, CLOUD_POLL);
}
async function cloudAccessPoll() {
  if (!document.hidden) {
    try { cloudAccessApply((await get('/v2/cloud-access', {fresh: true}))?.cloud ?? null); return; } catch {}
  }
  CLOUD_TIMER = setTimeout(cloudAccessPoll, CLOUD_POLL);
}
async function cloudAccessRefresh(button: HTMLButtonElement): Promise<void> {
  const note = document.querySelector<HTMLElement>('[data-cloud-check]');
  button.disabled = true;
  if (note) note.textContent = 'Checking your plan…';
  try {
    const cloud = (await get('/v2/cloud-access', {fresh: true}))?.cloud ?? null;
    cloudAccessApply(cloud);
    if (note) note.textContent = CLOUD_SHOWN === 'locked' ? 'Your plan still needs attention. Review billing or contact support.'
      : CLOUD_SHOWN === 'frozen' ? 'Credit is still needed for Tico-funded work.' : 'Your team is ready.';
  } catch {
    if (note) note.textContent = 'Couldn’t check your plan. Check your connection and try again.';
  } finally { button.disabled = false; }
}
document.querySelectorAll<HTMLButtonElement>('[data-cloud-refresh]').forEach(button => {
  button.addEventListener('click', () => { void cloudAccessRefresh(button); });
});
// Any request refused 402 `locked` (api.js signInRedirect): Pay to continue at once, not at the next poll.
function cloudAccessLocked(cloud: CloudAccessState | null | undefined) {
  if (CLOUD_SHOWN !== 'locked') cloudAccessApply({...(CLOUD_ACCESS || {}), ...(cloud || {}), access: 'locked'});
}
// Whole days of spend left while the company is open and low on credit, else null.
function cloudLowDays(): number | null {
  const days = CLOUD_ACCESS?.days_left;
  return CLOUD_ACCESS?.access === 'open' && typeof days === 'number' && days >= 0 ? days : null;
}
function cloudLowWords(days: number): string {
  return days < 1 ? 'Low credit · less than a day left' : days === 1 ? 'Low credit · about 1 day left' : `Low credit · about ${days} days left`;
}
// The strip's Add credit link, for a line elsewhere that says the company is out of credit; '' unless it is.
function cloudCreditLinkHTML(): string {
  if (CLOUD_ACCESS?.access !== 'frozen') return '';
  return ` <a data-cloud-pay href="${esc(cloudPayUrl(CLOUD_ACCESS.billing_url) || 'https://app.tico.dev/billing')}" target="_blank" rel="noopener noreferrer">Add credit</a>`;
}
// The status poll (refresh.js, boot.js) carries it on every page.
function cloudAccessFromStatus() {
  if (S.status && 'cloud_access' in S.status) cloudAccessApply(S.status.cloud_access);
}
function cloudAccessRender() {
  const access = CLOUD_ACCESS?.access || 'open', url = cloudPayUrl(CLOUD_ACCESS?.billing_url), root = document.documentElement;
  const low = cloudLowDays();
  root.classList.toggle('cloud-frozen', access === 'frozen');
  root.classList.toggle('cloud-low', low !== null);
  root.classList.toggle('cloud-locked', access === 'locked');
  root.classList.toggle('strip', root.classList.contains('demo') || access === 'frozen' || low !== null);
  const banner = document.getElementById('cloud-banner'), lock = document.getElementById('cloud-lock');
  if (banner) banner.hidden = access !== 'frozen' && low === null;
  const title = banner?.querySelector<HTMLElement>('[data-cloud-title]'), detail = banner?.querySelector<HTMLElement>('[data-cloud-detail]');
  if (title) title.textContent = low !== null ? cloudLowWords(low) : 'Out of credit';
  if (detail) detail.textContent = low !== null ? 'Bots and cloud machines stop when credit runs out. Add credit or a card to keep them running.'
    : 'Tico-funded work and cloud machines are paused. Your history and settings remain available.';
  if (lock) lock.hidden = access !== 'locked';
  document.querySelectorAll<HTMLAnchorElement>('[data-cloud-pay]').forEach(a => {
    a.href = url || 'https://app.tico.dev/billing';
  });
  if (access !== 'locked') return;
  const why: HTMLElement | null = $('#cloud-lock [data-cloud-why]');
  // CLOUD_ACCESS.reason is absent when HQ sends none; '' has no entry in CLOUD_WHY, so it takes the same fallback as undefined did.
  if (why) why.textContent = CLOUD_WHY[CLOUD_ACCESS?.reason ?? ''] || 'Your plan is paused.';
  const download: HTMLAnchorElement | null = $('#cloud-lock [data-cloud-download]');
  if (!download) return;
  download.href = `${API}/v2/system/storage/export`;
  // Only the owner may download every file (backend/tico_storage.py storage_export); who that is comes with /me.
  const role = S.me?.role || CLOUD_ROLE;
  download.hidden = role !== 'owner';
  if (!role && CLOUD_ROLE === null) {
    CLOUD_ROLE = '';
    get('/me').then(me => { CLOUD_ROLE = me?.role || ''; cloudAccessRender(); }).catch(() => {});
  }
}
