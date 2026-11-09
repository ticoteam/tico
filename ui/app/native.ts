/* ui/app/native.ts — The desktop app's bridge
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The native shell owns window placement: an ordinary window the person moves and resizes, with the page's own
// compact layout up to 760px.
// The app's bridge (app/src/main.rs BRIDGE_SCRIPT): one function, one message per name.
// What one bridge name gives back: a handler that takes a message, or undefined outside the desktop app. window.ticoNative
// is typed any in ui/types/globals.d.ts (this file cannot change it), so this shape is declared, not checked.
interface NativeBridgeHandler { postMessage(message?: unknown): unknown; }
const nativeHandler = (name: string): NativeBridgeHandler | undefined => window.ticoNative ? window.ticoNative(name) : undefined;

// Several organizations in the Mac app (app/src/bridge.rs). The app keeps the list and draws it (its rail and native
// menus); this page only tells the app its own company name and email, and asks whether to offer Switch organization.
// It never receives the other organizations.
const nativeHas = (feature: string): boolean => (window.ticoNativeFeatures || []).includes(feature);
let nativeOrgSent = '';
function nativeOrgSync(): void {
  const info = {name: String(S.config?.company_name || '').trim(), email: String(S.me?.email || '').trim()};
  const key = JSON.stringify(info);
  if (nativeHas('orgInfo') && info.email && key !== nativeOrgSent) {
    nativeOrgSent = key;
    try { Promise.resolve(nativeHandler('orgInfo')?.postMessage(info)).catch(() => {}); } catch {}
  }
  nativeOrgMenu();
}
async function nativeOrgMenu(): Promise<void> {
  const add = document.querySelector<HTMLElement>('#org-add'), swap = document.querySelector<HTMLElement>('#org-switch');
  if (!add || !swap) return;
  add.hidden = !nativeHas('addOrg');
  let many = false;
  if (nativeHas('multiOrg')) try { many = await nativeHandler('multiOrg')?.postMessage(null) === true; } catch {}
  swap.hidden = !many;
}
document.querySelector<HTMLElement>('#org-add')?.addEventListener('click', () => nativeHandler('addOrg')?.postMessage(null));
document.querySelector<HTMLElement>('#org-switch')?.addEventListener('click', () => nativeHandler('showOrgMenu')?.postMessage(null));
