/* ui/app/cloud-access.js — Tico Cloud: the Out of credit strip and Pay to continue (backend/cloud_access.py)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// HQ's word on a cloud company: `cloud_access` on GET /api/status (every page's poll), GET /api/v2/cloud-access, and
// the `cloud` of any 402 `locked`. Off cloud it is null and nothing here shows. Frozen (out of credit): a strip over
// every page, "Out of credit · Add credit"; everything else stays usable. Locked (trial over, or unpaid): the whole page
// is Pay to continue, with Download your files for the owner (Settings › General's Download all files). While either
// shows, the state is read every 30 s, so paying opens the app by itself; leaving a lock reloads the page, since what
// was open behind it was refused while it lasted.
let CLOUD_ACCESS = null, CLOUD_TIMER = 0, CLOUD_SHOWN = 'open', CLOUD_ROLE = null;
const CLOUD_POLL = 30000;
const CLOUD_WHY = {trial_over: 'Your trial has ended.', unpaid: "This month's payment didn't go through."};
const cloudPayUrl = u => typeof u === 'string' && /^https:\/\/\S+$/.test(u) ? u : '';
function cloudAccessApply(cloud) {
  CLOUD_ACCESS = cloud && typeof cloud === 'object' && ['open', 'frozen', 'locked'].includes(cloud.access) ? cloud : null;
  const access = CLOUD_ACCESS?.access || 'open', was = CLOUD_SHOWN;
  CLOUD_SHOWN = access;
  if (was === 'locked' && access !== 'locked') { location.reload(); return; }
  cloudAccessRender();
  clearTimeout(CLOUD_TIMER);
  if (access !== 'open') CLOUD_TIMER = setTimeout(cloudAccessPoll, CLOUD_POLL);
}
async function cloudAccessPoll() {
  if (!document.hidden) {
    try { cloudAccessApply((await get('/v2/cloud-access', {fresh: true}))?.cloud ?? null); return; } catch {}
  }
  CLOUD_TIMER = setTimeout(cloudAccessPoll, CLOUD_POLL);
}
// Any request refused 402 `locked` (api.js signInRedirect): Pay to continue at once, not at the next poll.
function cloudAccessLocked(cloud) {
  if (CLOUD_SHOWN !== 'locked') cloudAccessApply({...(CLOUD_ACCESS || {}), ...(cloud || {}), access: 'locked'});
}
// The status poll (refresh.js, boot.js) carries it on every page.
function cloudAccessFromStatus() {
  if (S.status && 'cloud_access' in S.status) cloudAccessApply(S.status.cloud_access);
}
function cloudAccessRender() {
  const access = CLOUD_ACCESS?.access || 'open', url = cloudPayUrl(CLOUD_ACCESS?.billing_url), root = document.documentElement;
  root.classList.toggle('cloud-frozen', access === 'frozen');
  root.classList.toggle('cloud-locked', access === 'locked');
  root.classList.toggle('strip', root.classList.contains('demo') || access === 'frozen');
  document.querySelectorAll('[data-cloud-pay]').forEach(a => { if (url) a.href = url; else a.removeAttribute('href'); });
  if (access !== 'locked') return;
  const why = $('#cloud-lock [data-cloud-why]');
  if (why) why.textContent = CLOUD_WHY[CLOUD_ACCESS?.reason] || 'Your plan is paused.';
  const download = $('#cloud-lock [data-cloud-download]');
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
