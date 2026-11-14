/* ui/app/chat-index.ts — Your messages: one quiet tick per message the reader sent, on the right edge of a chat
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The style guide's "Your messages, on the edge" (ui/style-guide/guide.html #sg-chat, part 19). Every chat mounts the
// same index: the Assistant (ui/app/chat-outline.ts, from its outline so a tick can name a message not loaded yet), a
// thread, a bot's Home or DM and a page's chat (ui/app/thread-view.ts), and a task's chat (ui/app/task-modal.ts).
//  - one short faint tick per message the reader sent, only theirs; it shows once there are three of them;
//  - the ticks sit in a narrow strip on the feed's right edge, inset from its top and bottom, left of its scrollbar;
//    the feed keeps that strip clear of its text while the index shows;
//  - ticks for messages on screen are brighter; pointing grows the tick and its two neighbours (a quiet dock) and
//    shows the message beside it, two lines and then the time; clicking brings the message back and marks it;
//  - arrow keys, Home and End move between ticks; the strip is a navigation landmark, "Your messages";
//  - hundreds of messages stay calm: past what fits the height, neighbours share one tick (the first one's words);
//  - a phone (under 600px) or a screen with no pointer that hovers has no index: the Assistant's Outline serves it.
// One message the reader sent: its id, its words (or what it carried), and when (`at`, or `when` already in words).
interface PidxItem { id: string; text: string; at?: string | undefined; when?: string | undefined }
interface PidxOpts {
  scroller: HTMLElement;                       // the feed that scrolls; its parent holds the strip
  items: () => PidxItem[];                     // the reader's messages, oldest first
  row: (id: string) => HTMLElement | null;     // a message's row, when it is on the page
  go?: ((id: string) => unknown) | undefined;  // brings a message back (default: scroll to its row and mark it)
}
interface PromptIndex { el: HTMLElement; sync: () => void; destroy: () => void }
const PIDX_MIN = 3;            // own messages before the index shows
const PIDX_STEP = 8;           // px per tick: a 2px line with 3px above and below
const PIDX_INSET = 28;         // px kept clear at the feed's top and bottom
const PIDX_ROOM = 22;          // px the feed keeps clear on its right for the strip
const PIDX_WIDE = matchMedia('(min-width: 600px) and (hover: hover)');

// What a message says in the index: its first words, plain; a message of files only says what it carried.
function pidxText(body: string | null | undefined, files: {content_type?: string | undefined; name?: string | undefined}[] = []): string {
  const words = String(body || '').replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`#>~|]/g, '').replace(/\s+/g, ' ').trim();
  if (words) return words.length > 240 ? words.slice(0, 239) + '…' : words;
  if (!files.length) return 'Message';
  const kind = (f: {content_type?: string | undefined; name?: string | undefined}) =>
    /^image\//.test(String(f.content_type || '')) || /\.(png|jpe?g|gif|webp|heic|avif)$/i.test(String(f.name || '')) ? 'photo'
    : /^video\//.test(String(f.content_type || '')) || /\.(mp4|mov|webm|m4v)$/i.test(String(f.name || '')) ? 'video' : 'file';
  const kinds = new Set(files.map(kind)), one = kinds.size === 1 ? [...kinds][0]! : 'file';
  const noun = one.charAt(0).toUpperCase() + one.slice(1);
  return files.length === 1 ? noun : `${files.length} ${noun.toLowerCase()}s`;
}

function promptIndexMount(opts: PidxOpts): PromptIndex {
  const {scroller} = opts;
  const nav = document.createElement('nav');
  nav.className = 'pidx'; nav.setAttribute('aria-label', 'Your messages'); nav.hidden = true;
  const tip = document.createElement('div');
  tip.className = 'pidx-tip'; tip.setAttribute('aria-hidden', 'true'); tip.hidden = true;
  let ticks: {ids: string[]; item: PidxItem}[] = [], drawn = '', pending = false, frame = 0, gone = false, padWas: string | null = null;
  let focusId = '';      // the tick the keyboard last stood on, kept through a redraw
  const host = (): HTMLElement | null => scroller.parentElement;
  // The strip lives beside the feed, so it never scrolls with it; the feed's parent holds it.
  const place = () => {
    const h = host();
    if (!h) return;
    if (nav.parentElement !== h) h.append(nav);
    if (getComputedStyle(h).position === 'static') h.style.position = 'relative';
    const bar = scroller.offsetWidth - scroller.clientWidth - (parseFloat(getComputedStyle(scroller).borderRightWidth) || 0);
    const right = h.clientWidth - (scroller.offsetLeft + scroller.offsetWidth) + Math.max(0, bar) + 2;
    nav.style.top = `${scroller.offsetTop + PIDX_INSET}px`;
    nav.style.height = `${Math.max(0, scroller.clientHeight - PIDX_INSET * 2)}px`;
    nav.style.right = `${Math.max(0, right)}px`;
  };
  // The feed keeps the strip clear of its words while the index shows, and gets its own padding back after. A feed
  // that already pads that much (the Assistant's column, centred in a wide pane) is left as it is; it is measured
  // with its own padding each time, so a wider window gives the column its centring back.
  // A feed read at its newest line stays there when the words rewrap.
  const room = (on: boolean) => {
    const was = scroller.style.paddingRight, atEnd = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 2;
    if (padWas !== null) { scroller.style.paddingRight = padWas; padWas = null; }
    if (on && (parseFloat(getComputedStyle(scroller).paddingRight) || 0) < PIDX_ROOM) { padWas = scroller.style.paddingRight; scroller.style.paddingRight = `${PIDX_ROOM}px`; }
    if (atEnd && scroller.style.paddingRight !== was) scroller.scrollTop = scroller.scrollHeight;
  };
  const hide = () => { nav.hidden = true; tip.hidden = true; room(false); };
  // Past what fits the height, neighbours share a tick: each tick stands for an even share of the messages.
  const group = (items: PidxItem[]) => {
    const fit = Math.max(PIDX_MIN, Math.floor((scroller.clientHeight - PIDX_INSET * 2) / PIDX_STEP));
    if (items.length <= fit) return items.map(item => ({ids: [item.id], item}));
    return Array.from({length: fit}, (_, k) => {
      const from = Math.floor(k * items.length / fit), to = Math.floor((k + 1) * items.length / fit);
      const share = items.slice(from, to);
      return {ids: share.map(i => i.id), item: share[0]!};
    });
  };
  const whenOf = (it: PidxItem) => it.when || (it.at ? chatWhen(it.at) : '');
  const label = (it: PidxItem) => whenOf(it) ? `${it.text}, ${whenOf(it)}` : it.text;
  const draw = () => {
    const items = opts.items();
    if (items.length < PIDX_MIN || !PIDX_WIDE.matches || !scroller.isConnected || scroller.clientHeight < 120) { hide(); drawn = ''; return; }
    room(true);
    place();
    ticks = group(items);
    const key = ticks.map(t => `${t.item.id}:${t.ids.length}:${t.item.text}`).join('|') + '#' + scroller.clientHeight;
    nav.hidden = false;
    if (key !== drawn) {
      drawn = key;
      const had = nav.contains(document.activeElement) ? focusId : '';
      const at = Math.max(0, ticks.findIndex(t => t.ids.includes(focusId)));
      nav.innerHTML = ticks.map((t, k) => `<button type="button" class="pidx-t" data-i="${k}" tabindex="${k === (focusId ? at : ticks.length - 1) ? 0 : -1}" aria-label="${esc(label(t.item))}"></button>`).join('');
      nav.append(tip);
      tip.hidden = true;
      if (had) nav.querySelector<HTMLElement>(`.pidx-t[data-i="${at}"]`)?.focus({preventScroll: true});
    }
    lit();
  };
  // Ticks for what is on screen are brighter.
  const lit = () => {
    if (nav.hidden) return;
    const box = scroller.getBoundingClientRect();
    nav.querySelectorAll<HTMLElement>('.pidx-t').forEach((b, k) => {
      const on = (ticks[k]?.ids || []).some(id => { const r = opts.row(id)?.getBoundingClientRect(); return !!r && r.height > 0 && r.bottom > box.top && r.top < box.bottom; });
      b.classList.toggle('on', on);
    });
  };
  // A change while the pointer is on the strip waits until it leaves, so the tick under it does not jump away.
  const sync = () => {
    if (gone) return;
    if (!scroller.isConnected) { destroy(); return; }
    if (nav.matches(':hover')) { pending = true; return; }
    pending = false;
    draw();
  };
  const soon = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; sync(); }); };
  const show = (b: HTMLElement) => {
    const t = ticks[Number(b.dataset.i)];
    if (!t) { tip.hidden = true; return; }
    tip.innerHTML = `<span class="pidx-tip-t">${esc(t.item.text)}</span>${whenOf(t.item) ? `<time>${esc(whenOf(t.item))}</time>` : ''}`;
    tip.hidden = false;
    const top = b.offsetTop + b.offsetHeight / 2, room = nav.clientHeight;
    tip.style.top = `${Math.round(top)}px`;
    // Near the strip's ends the box stays inside the feed.
    tip.style.setProperty('--pidx-shift', top < 40 ? '0%' : top > room - 40 ? '-100%' : '-50%');
  };
  const markRow = (id: string) => {
    const el = opts.row(id);
    if (!el) return;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({block: 'center', behavior: still ? 'auto' : 'smooth'});
    el.classList.remove('pidx-mark'); void el.offsetWidth; el.classList.add('pidx-mark');
    setTimeout(() => el.classList.remove('pidx-mark'), 1800);
  };
  const go = (b: HTMLElement) => {
    const t = ticks[Number(b.dataset.i)];
    if (!t) return;
    focusId = t.item.id;
    if (opts.go) void opts.go(t.item.id); else markRow(t.item.id);
  };
  nav.addEventListener('click', ev => { const b = eventElement(ev)?.closest<HTMLElement>('.pidx-t'); if (b) go(b); });
  nav.addEventListener('pointerover', ev => { const b = eventElement(ev)?.closest<HTMLElement>('.pidx-t'); if (b) show(b); });
  nav.addEventListener('pointerleave', () => { tip.hidden = true; if (pending) sync(); });
  nav.addEventListener('focusin', ev => { const b = eventElement(ev)?.closest<HTMLElement>('.pidx-t'); if (b) { focusId = ticks[Number(b.dataset.i)]?.item.id || ''; show(b); } });
  nav.addEventListener('focusout', ev => { if (!nav.contains(ev.relatedTarget as Node | null)) tip.hidden = true; });
  nav.addEventListener('keydown', ev => {
    const all = [...nav.querySelectorAll<HTMLElement>('.pidx-t')], at = all.indexOf(document.activeElement as HTMLElement);
    const to = ev.key === 'ArrowUp' ? at - 1 : ev.key === 'ArrowDown' ? at + 1 : ev.key === 'Home' ? 0 : ev.key === 'End' ? all.length - 1 : null;
    if (to === null || at < 0) return;
    ev.preventDefault();
    const next = all[Math.max(0, Math.min(all.length - 1, to))]!;
    for (const b of all) b.tabIndex = b === next ? 0 : -1;
    next.focus({preventScroll: true});
  });
  const onScroll = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; lit(); }); };
  scroller.addEventListener('scroll', onScroll, {passive: true});
  // Messages drawn, paged in or removed: the index follows on the next frame, however the feed was redrawn.
  const watch = new MutationObserver(soon);
  watch.observe(scroller, {childList: true, subtree: true});
  const sized = window.ResizeObserver ? new ResizeObserver(soon) : null;
  sized?.observe(scroller);
  PIDX_WIDE.addEventListener('change', soon);
  function destroy() {
    if (gone) return;
    gone = true;
    cancelAnimationFrame(frame);
    watch.disconnect(); sized?.disconnect();
    scroller.removeEventListener('scroll', onScroll);
    PIDX_WIDE.removeEventListener('change', soon);
    room(false);
    nav.remove();
  }
  soon();
  return {el: nav, sync: soon, destroy};
}

// A chat whose messages this file only sees as rows reads them from the page: each of the reader's rows, its words and
// when. A task's chat (ui/app/task-modal.ts): `.tcomment.from-me`; the Ask panel (ui/app/ask-panel.ts, chat.ts rows):
// `.cw-msg.from-me`. One call where the feed is made; the index follows the rows as they are drawn.
function chatRowsIndexMount(feed: HTMLElement | null, mine: string): PromptIndex | null {
  if (!feed || (feed as HTMLElement & {pidx?: PromptIndex}).pidx) return null;
  const idx = promptIndexMount({scroller: feed,
    row: id => feed.querySelector<HTMLElement>(`[data-message="${CSS.escape(id)}"]`),
    items: () => [...feed.querySelectorAll<HTMLElement>(`${mine}[data-message]`)].filter(r => r.dataset.message).map(r => {
      const files = [...r.querySelectorAll<HTMLElement>('.chat-thumb, .pill[data-name]')].map(f => ({name: f.dataset.name}));
      const time = r.querySelector('time[datetime]')?.getAttribute('datetime');
      return {id: r.dataset.message!, text: pidxText(r.querySelector('.th-body, .bubble .md, .bubble')?.textContent, files),
        at: time || undefined, when: time ? undefined : r.querySelector('time[title]')?.getAttribute('title') || undefined};
    })});
  (feed as HTMLElement & {pidx?: PromptIndex}).pidx = idx;
  return idx;
}
const taskChatIndexMount = (feed: HTMLElement | null) => chatRowsIndexMount(feed, '.tcomment.tmsg.from-me');
const askChatIndexMount = (feed: HTMLElement | null) => chatRowsIndexMount(feed, '.cw-msg.from-me');
