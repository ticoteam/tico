/* ui/app/viewport.js — Phone keyboard: chat viewport and composer focus
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const isComposerInput = element => element?.matches?.('.ask textarea');
function chatViewport() {
  const viewport = window.visualViewport;
  // Safari pans as well as resizes the visual viewport when the keyboard opens.
  // Keep the whole chat in that visible rectangle, with history scrolling inside it.
  // Preserve browser pinch zoom instead of resizing the layout around magnification.
  if (viewport && viewport.scale > 1) return;
  // Only when it changed: the keyboard's suggestion bar fires these events while typing, and each
  // write re-lays the page out under the composer.
  const root = document.documentElement.style;
  const height = `${viewport?.height || window.innerHeight}px`, top = `${viewport?.offsetTop || 0}px`;
  if (root.getPropertyValue('--chat-viewport-height') !== height) root.setProperty('--chat-viewport-height', height);
  if (root.getPropertyValue('--chat-viewport-top') !== top) root.setProperty('--chat-viewport-top', top);
}
// The desktop-app banner (#topbar) takes its own room above a phone's full-screen chat, which starts under it
// (--top-strip, ui/styles/chat.css) instead of drawing its header over the banner.
function topStrip() {
  const bar = document.getElementById('topbar'), root = document.documentElement.style;
  const strip = `${bar ? Math.max(0, Math.round(bar.getBoundingClientRect().bottom)) : 0}px`;
  if (root.getPropertyValue('--top-strip') !== strip) root.setProperty('--top-strip', strip);
}
if (window.ResizeObserver && document.getElementById('topbar')) new ResizeObserver(topStrip).observe(document.getElementById('topbar'));
window.visualViewport?.addEventListener('resize', chatViewport);
window.visualViewport?.addEventListener('scroll', chatViewport);
window.addEventListener('resize', chatViewport);
chatViewport();
document.addEventListener('focusin', event => {
  if (drawerMedia.matches && isComposerInput(event.target)) document.body.classList.add('mobile-composer-focus');
});
document.addEventListener('focusout', event => {
  if (!isComposerInput(event.target)) return;
  setTimeout(() => {
    if (!isComposerInput(document.activeElement)) document.body.classList.remove('mobile-composer-focus');
  }, 0);
});
drawerMedia.addEventListener('change', () => {
  setDrawer(false);
  if (!drawerMedia.matches) document.body.classList.remove('mobile-composer-focus');
});
