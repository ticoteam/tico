/* ui/app/native.js — The desktop app's bridge
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The native shell owns window placement: an ordinary window the person moves and resizes, with the page's own
// compact layout up to 760px.
// The app's bridge (app/src/main.rs BRIDGE_SCRIPT): one function, one message per name.
const nativeHandler = name => window.ticoNative ? window.ticoNative(name) : undefined;

// Several organizations in the Mac app (app/src/bridge.rs). The app keeps the list and draws it (its rail and native
// menus); this page only tells the app its own company name and email, and asks whether to offer Switch organization.
// It never receives the other organizations.
const nativeHas = feature => (window.ticoNativeFeatures || []).includes(feature);
let nativeOrgSent = '';
function nativeOrgSync() {
  const info = {name: String(S.config?.company_name || '').trim(), email: String(S.me?.email || '').trim()};
  const key = JSON.stringify(info);
  if (nativeHas('orgInfo') && info.email && key !== nativeOrgSent) {
    nativeOrgSent = key;
    try { Promise.resolve(nativeHandler('orgInfo')?.postMessage(info)).catch(() => {}); } catch {}
  }
  nativeOrgMenu();
}
async function nativeOrgMenu() {
  const add = $('#org-add'), swap = $('#org-switch');
  if (!add || !swap) return;
  add.hidden = !nativeHas('addOrg');
  let many = false;
  if (nativeHas('multiOrg')) try { many = await nativeHandler('multiOrg')?.postMessage(null) === true; } catch {}
  swap.hidden = !many;
}
$('#org-add')?.addEventListener('click', () => nativeHandler('addOrg')?.postMessage(null));
$('#org-switch')?.addEventListener('click', () => nativeHandler('showOrgMenu')?.postMessage(null));
