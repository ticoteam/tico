/* ui/app/visual-pictures.js — Picture, SVG and visual page cards (Visual replies 2/4) and Expand, full screen.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Every one shows from 1/4's sign-in-free address (visualSrc, ui/app/visual-cards.js), never the app's file route. */
'use strict';

// A visual page runs in a frame on the visual address with no same-origin, forms, top navigation or modals: the only
// script it can run is the server's height reporter (backend/visuals.py REPORTER, allowed by hash), and a link opens a
// new tab only when clicked. Lead, Oct 19: these four tokens and nothing else.
const VISUAL_PAGE_SANDBOX = 'allow-scripts allow-popups allow-popups-to-escape-sandbox';
const VISUAL_PAGE_MAX = 20000;               // px: a reported height is clamped to this
const VISUAL_PAGE_WAIT = 2500;               // ms after the frame loads with no height: it did not render
const VISUAL_PAGE_H = new Map();             // "<file>@<version>" -> the height its page last reported (no jump on redraw)
const VISUAL_PAGE_FILL = new Set();          // "<file>@<version>" of pages sized by the screen (visualPageFill)

const visualTip = f => esc(visualTitle(f));
function visualPictureBody(f) {
  return `<button type="button" class="vcard-hit" data-vcard-act="expand" aria-label="Expand ${visualTip(f)}"><img class="vcard-img" alt="${visualTip(f)}" src="${esc(visualSrc(f))}" decoding="async"></button>`;
}
VISUAL_BODY.image = visualPictureBody;
VISUAL_BODY.svg = visualPictureBody;         // an <img>: an SVG never runs scripts or loads anything there
VISUAL_SPACE.image = () => 'min-height:120px';
VISUAL_SPACE.svg = () => 'aspect-ratio:4/3';
// The frame starts 1px tall while the card holds the space: a page measures itself no smaller than its frame
// (scrollHeight), so the first height it reports is its own, and a short page gets no blank below it.
VISUAL_BODY.page = f => `<iframe class="vcard-frame" title="${visualTip(f)}" sandbox="${VISUAL_PAGE_SANDBOX}" credentialless referrerpolicy="no-referrer" scrolling="no" style="height:${VISUAL_PAGE_H.get(visualRef(f)) || 1}px" data-src="${esc(visualSrc(f))}"></iframe>
  <div class="vcard-fade" aria-hidden="true"></div><button type="button" class="vcard-expand" data-vcard-act="expand">Expand</button>
  <button type="button" class="vcard-tap" data-vcard-act="expand" aria-label="Expand ${visualTip(f)}"></button>`;
VISUAL_SPACE.page = f => {
  const h = VISUAL_PAGE_H.get(visualRef(f));
  return h ? `height:${h}px` : 'height:var(--vcard-cap)';
};

// Once: a broken address gets the newest one the thread read (data-fresh-src, visualKeep), then the card says so.
function visualRetry(card, el, attr = 'src') {
  const fresh = visualSrc({src: card.dataset.freshSrc});
  if (fresh && !card.dataset.retried && fresh !== el.getAttribute(attr)) { card.dataset.retried = '1'; el.setAttribute(attr, fresh); return true; }
  visualFail(card);
  return false;
}
function visualPictureWire(card) {
  const img = card.querySelector('img.vcard-img'), body = card.querySelector('.vcard-body');
  if (!img) return;
  const ready = () => {
    body.classList.add('ready');
    // Size not known before: the picture's own, from now on.
    if (!/aspect-ratio/.test(body.getAttribute('style') || '') && img.naturalWidth && img.naturalHeight)
      body.style.cssText = `aspect-ratio:${img.naturalWidth}/${img.naturalHeight};--vcard-w:${img.naturalWidth}px`;
  };
  img.addEventListener('load', ready);
  img.addEventListener('error', () => visualRetry(card, img));
  if (img.complete) img.naturalWidth ? ready() : visualRetry(card, img);
}
VISUAL_WIRE.image = visualPictureWire;
VISUAL_WIRE.svg = visualPictureWire;

function visualCap(body) { return parseFloat(getComputedStyle(body).maxHeight) || 480; }
function visualPageWire(card) {
  const frame = card.querySelector('iframe.vcard-frame');
  if (!frame) return;
  frame.addEventListener('load', () => {
    clearTimeout(frame.visualWait);
    frame.visualWait = setTimeout(() => { if (!frame.visualHeight && card.isConnected) visualRetry(card, frame); }, VISUAL_PAGE_WAIT);
  });
  const ref = `${card.dataset.file}@${card.dataset.version}`, known = VISUAL_PAGE_H.get(ref);
  if (VISUAL_PAGE_FILL.has(ref)) visualPageFill(card, frame);
  else if (known) visualPageSize(card, frame, known);
  visualPageLoad(frame, frame.dataset.src);
}
// A page's frame gets its address only once the page around it is laid out. Chromium can otherwise load the page
// before the frame has its size, at 0x0 (a frame off screen, or put back by a redraw), where the page measures
// taller (its lines wrap), and the frame taking that height then pins it there: a page measures at least its frame.
function visualPageLoad(frame, src) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const approved = visualSrc({src});
    if (!approved) { const card = frame.closest('figure.vcard'); if (card) visualFail(card); return; }
    if (!frame.hasAttribute('src')) frame.setAttribute('src', approved);
  }));
}
VISUAL_WIRE.page = visualPageWire;
function visualPageSize(card, frame, h, fill = false) {
  const body = card.querySelector('.vcard-body');
  if (frame.visualHeight !== h) frame.visualAsked = true;     // the page's next report answers this change
  frame.visualHeight = h;
  frame.style.height = h + 'px';             // the whole page: no scrollbar inside the frame
  body.style.height = h + 'px';              // the card: up to the cap (max-height), then fade + Expand
  body.classList.add('ready');
  card.classList.toggle('capped', fill || h > visualCap(body) + 1);
}
// A page sized by the screen (height or min-height 100vh / 100dvh) has no height of its own in a frame: it is as tall
// as whatever the frame is, or taller, so following its reports would leave it a sliver or grow the card without end.
// It shows at the cap with the fade and Expand, which opens it at a real screen's height, and its reports are no longer
// followed. Bot builders are told to size pages by their content (Visual replies 2/4, lead Oct 19: a known limit).
function visualPageFill(card, frame) {
  VISUAL_PAGE_FILL.add(`${card.dataset.file}@${card.dataset.version}`);
  frame.visualFill = true;
  visualPageSize(card, frame, Math.round(visualCap(card.querySelector('.vcard-body'))), true);
}
// Put back without moveBefore, a page's frame loads again: it waits without its address until it is laid out
// (visualPageLoad), then loads 1px tall as at first while the card keeps its height. What the page said before is
// forgotten, so its next report sets the height again, down as well as up.
VISUAL_MOVED.page = card => {
  const frame = card.querySelector('iframe.vcard-frame'), src = frame?.getAttribute('src');
  if (!src || frame.visualFill) return;
  frame.removeAttribute('src');
  frame.visualLast = undefined; frame.visualAsked = false; frame.visualEchoes = 0;
  frame.style.height = '1px';
  visualPageLoad(frame, src);
};
// The height a page reports, taken only from a card's own frame, as a number, clamped.
window.addEventListener('message', ev => {
  const n = ev.data && typeof ev.data === 'object' ? ev.data.ticoVisualHeight : undefined;
  if (typeof n !== 'number' || !Number.isFinite(n) || !ev.source) return;
  const frame = [...document.querySelectorAll('iframe.vcard-frame')].find(x => x.contentWindow === ev.source);
  const card = frame?.closest('figure.vcard');
  if (!card || frame.visualFill || !frame.offsetWidth) return;   // a frame shown at no width measures nothing real
  // A page measures at least its frame (scrollHeight), so a page smaller than its frame says the frame's height, as a
  // 100vh page does. What tells a page sized by the screen apart: in its first 1px frame it measured exactly 1px
  // (content, however small, measures its own height), or twice running it answered the frame taking the height it
  // said by saying more (content says the same height again, or nothing). The answer may come late: a frame off
  // screen is not laid out again until it is scrolled to.
  const raw = Math.ceil(n), was = frame.visualHeight;
  if (raw === frame.visualLast && was) return;
  const echo = frame.visualAsked && was && raw > was;
  frame.visualAsked = false;
  frame.visualLast = raw;
  frame.visualEchoes = echo ? (frame.visualEchoes || 0) + 1 : 0;
  if ((!was && raw <= 1) || frame.visualEchoes >= 2) { visualPageFill(card, frame); return; }
  const h = Math.max(24, Math.min(raw, VISUAL_PAGE_MAX));
  VISUAL_PAGE_H.set(`${card.dataset.file}@${card.dataset.version}`, h);
  visualPageSize(card, frame, h);
});
// The cap follows the screen (480px, or 60% of a phone's height): after a resize each page card is cut, or not, again.
let visualRecapSoon = 0;
window.addEventListener('resize', () => {
  cancelAnimationFrame(visualRecapSoon);
  visualRecapSoon = requestAnimationFrame(() => {
    for (const frame of document.querySelectorAll('figure.vcard iframe.vcard-frame')) {
      const card = frame.closest('figure.vcard');
      if (frame.visualFill) visualPageFill(card, frame);
      else if (frame.visualHeight) visualPageSize(card, frame, frame.visualHeight);
    }
  });
});

// ---- Expand: full screen over the conversation. Pictures zoom (click/tap, pinch, ctrl+wheel), pages scroll.
// Close, Esc, swipe down or Back return to the same place in the conversation.
function visualFullDialog() {
  let d = $('#vcard-full');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'vcard-full'; d.className = 'vcard-full';
  d.innerHTML = `<div class="vcard-full-bar"><span class="vcard-full-title"></span><button type="button" class="ghost vcard-full-close" data-vfull-close aria-label="Close">Close</button></div><div class="vcard-full-body"></div>`;
  d.addEventListener('click', ev => { if (ev.target.closest('[data-vfull-close]')) d.close(); });
  d.addEventListener('close', () => visualFullClosed(d));
  visualFullSwipe(d);
  document.body.append(d);
  return d;
}
function visualScrollers(el) {
  const out = [];
  for (let x = el.parentElement; x; x = x.parentElement) if (x.scrollHeight > x.clientHeight) out.push([x, x.scrollTop]);
  const doc = document.scrollingElement;
  if (doc) out.push([doc, doc.scrollTop]);
  return out;
}
function visualExpand(card) {
  const src = visualSrc({src: card.dataset.freshSrc}), kind = card.dataset.kind;
  if (!src) { visualFail(card); return; }
  const d = visualFullDialog();
  const title = card.querySelector('.vcard-title')?.textContent || 'Visual';
  d.querySelector('.vcard-full-title').textContent = title;
  d.setAttribute('aria-label', title);
  d.dataset.kind = kind;
  const body = d.querySelector('.vcard-full-body');
  body.innerHTML = kind === 'page'
    ? `<iframe class="vcard-full-frame" title="${esc(title)}" sandbox="${VISUAL_PAGE_SANDBOX}" credentialless referrerpolicy="no-referrer" src="${esc(src)}"></iframe>`
    : `<img class="vcard-full-img" alt="${esc(title)}" src="${esc(src)}" draggable="false">`;
  d.visualFrom = {card, opener: document.activeElement, scroll: visualScrollers(card)};
  visualZoomReset(d);
  if (kind !== 'page') visualZoom(d, body.querySelector('img'));
  history.pushState({...(history.state || {}), vcardFull: 1}, '');
  d.showModal();
  d.querySelector('.vcard-full-close').focus({preventScroll: true});
}
VISUAL_EXPAND.image = visualExpand;
VISUAL_EXPAND.svg = visualExpand;
VISUAL_EXPAND.page = visualExpand;
// The dialog is reused: the next visual opens fitted, with swipe down working, and the last one's zoom gestures gone.
function visualZoomReset(d) {
  d.zoomAbort?.abort(); d.zoomAbort = null;
  d.zoomed = false;
  d.classList.remove('zoomed');
}
function visualFullClosed(d) {
  const from = d.visualFrom; d.visualFrom = null;
  visualZoomReset(d);
  d.querySelector('.vcard-full-body').innerHTML = '';
  if (history.state?.vcardFull) history.back();
  if (!from) return;
  for (const [el, top] of from.scroll) el.scrollTop = top;
  const back = from.opener?.isConnected ? from.opener : from.card.querySelector('summary, .vcard-hit, .vcard-expand');
  back?.focus?.({preventScroll: true});
  for (const [el, top] of from.scroll) el.scrollTop = top;
}
window.addEventListener('popstate', () => {
  const d = $('#vcard-full');
  if (d?.open && !history.state?.vcardFull) d.close();
});
function visualFullSwipe(d) {
  let start = null;
  d.addEventListener('pointerdown', ev => {
    if (ev.pointerType === 'mouse' || d.zoomed) { start = null; return; }
    const body = d.querySelector('.vcard-full-body');
    start = body.scrollTop <= 0 ? {x: ev.clientX, y: ev.clientY} : null;
  });
  d.addEventListener('pointerup', ev => {
    if (!start) return;
    const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
    start = null;
    if (dy > 80 && Math.abs(dx) < dy * 0.6) d.close();
  });
  d.addEventListener('pointercancel', () => { start = null; });
}
// Fitted at 1; a click or tap toggles 2x at that point; pinch and ctrl+wheel (a trackpad pinch) scale between 1 and 6.
function visualZoom(d, img) {
  const body = d.querySelector('.vcard-full-body'), signal = (d.zoomAbort = new AbortController()).signal;
  let scale = 1;
  const set = (next, x = body.clientWidth / 2, y = body.clientHeight / 2) => {
    const was = scale;
    scale = Math.max(1, Math.min(6, next));
    d.zoomed = scale > 1;
    d.classList.toggle('zoomed', d.zoomed);
    const fx = (body.scrollLeft + x) / was, fy = (body.scrollTop + y) / was;
    img.style.width = scale > 1 ? `${scale * 100}%` : '';
    img.style.height = scale > 1 ? `${scale * 100}%` : '';
    body.scrollLeft = fx * scale - x; body.scrollTop = fy * scale - y;
  };
  const at = ev => { const r = body.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
  img.addEventListener('click', ev => set(scale > 1 ? 1 : 2, ...at(ev)), {signal});
  body.addEventListener('wheel', ev => { if (!ev.ctrlKey) return; ev.preventDefault(); set(scale * Math.exp(-ev.deltaY / 200), ...at(ev)); }, {passive: false, signal});
  const touches = new Map(); let pinch = null;
  body.addEventListener('pointerdown', ev => { if (ev.pointerType === 'touch') touches.set(ev.pointerId, ev); }, {signal});
  body.addEventListener('pointermove', ev => {
    if (!touches.has(ev.pointerId)) return;
    touches.set(ev.pointerId, ev);
    if (touches.size !== 2) return;
    const [a, b] = [...touches.values()], dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    if (!pinch) { pinch = {dist, scale}; return; }
    const r = body.getBoundingClientRect();
    set(pinch.scale * dist / pinch.dist, (a.clientX + b.clientX) / 2 - r.left, (a.clientY + b.clientY) / 2 - r.top);
  }, {signal});
  const up = ev => { touches.delete(ev.pointerId); if (touches.size < 2) pinch = null; };
  body.addEventListener('pointerup', up, {signal}); body.addEventListener('pointercancel', up, {signal});
}
