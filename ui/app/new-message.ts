/* ui/app/new-message.ts — New message (#/threads/new): one To box over people, bots, groups and threads, in the thread
   pane beside the rail (the whole screen on a phone). With teammates chosen it offers the threads with exactly them
   (GET /api/v2/threads/match), their DM when there is one teammate, and a new named thread; the composer under it sends
   to the one marked, with any files pasted or attached (chips, as the thread composer's). `?to=<actor>` (repeatable)
   starts with those teammates chosen.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What a create answers: the thread itself, or the thread under `thread`; a held first message makes none.
type NmReply = ThreadRow & {thread?: ThreadRow; held?: boolean};
interface NmMatch {threads?: ThreadRow[]; dm?: string | null}
// One row of the list: a teammate to add, a thread to open, the DM with the one teammate chosen, or a new thread.
type NmRow = {kind: 'actor'; actor: string; label: string} | {kind: 'thread'; t: ThreadRow}
  | {kind: 'dm'; actor: string; id: string | null} | {kind: 'new'};
const NM_SVG = {
  plus: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
};
const NM_SUGGEST = 8, NM_THREADS = 6;
// The pane on screen and its address: the app routing again to the same address (the roster landing, a reconnect)
// keeps what was typed and only draws the list again.
let NM_OPEN: {pane: HTMLElement; route: string; draw: () => void} | null = null;

// #/threads/new: the pane, drawn fresh for each new visit (a teammate's DM button opens it with `?to=`).
function pageNewMessage(): void {
  const to = new URLSearchParams(S.route.split('?')[1] || '').getAll('to');
  const shell = threadsShell(), pane = shell.querySelector('.tr-pane') as HTMLElement | null;
  if (!pane) return;
  shell.dataset.mode = 'thread';
  railRender();
  if (NM_OPEN?.pane === pane && NM_OPEN.route === S.route && pane.querySelector('.nm')) { NM_OPEN.draw(); return; }
  threadUnmount(THREAD_PAGE?.view); THREAD_PAGE = null;
  pane.innerHTML = `<section class="nm" aria-label="New message">
    <header class="nm-head"><a class="thv-back thv-back-rail" href="${THREADS_ROUTE}" aria-label="Threads" title="Threads">‹</a><h1>New message</h1></header>
    <div class="nm-to"><label class="nm-k" for="nm-q">To:</label><span class="nm-chips"></span>
      <input id="nm-q" type="text" autocomplete="off" spellcheck="false" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="nm-list"></div>
    <ul class="nm-list" id="nm-list" role="listbox" aria-label="To" hidden></ul>
    <span class="nm-fill"></span>
    <div class="thv-composer nm-composer"><div class="ask pill-thread thv-ask"><div class="p-chips"></div><div class="p-pill">
      <textarea class="p-text" rows="1" autocorrect="on" autocapitalize="sentences" spellcheck="true" aria-label="Message" placeholder="Message"></textarea>
      <button class="p-attach p-icon" type="button" title="Attach" aria-label="Attach">${ICON_CLIP}</button>
      <input type="file" multiple hidden class="p-file">
      <button class="p-send p-send-icon" type="button" aria-label="Send" title="Send" disabled>${ICON_SEND}</button></div></div></div></section>`;
  NM_OPEN = {pane, route: S.route, draw: nmWire(pane, [...new Set(to.filter(a => /^(human|bot|group):./.test(a) && a !== myActor()))])};
}

// Wires the pane's controls and answers its draw, for a redraw from outside.
function nmWire(pane: HTMLElement, start: string[]): () => void {
  const q = pane.querySelector('#nm-q') as HTMLInputElement, list = pane.querySelector('.nm-list') as HTMLUListElement;
  const chips = pane.querySelector('.nm-chips') as HTMLElement, box = pane.querySelector('.p-text') as HTMLTextAreaElement;
  const send = pane.querySelector('.p-send') as HTMLButtonElement, file = pane.querySelector('.p-file') as HTMLInputElement;
  const fileChips = pane.querySelector('.p-chips') as HTMLElement;
  const chosen: string[] = [...start];
  // Files waiting to go with the first message: pasted (pill.ts pillPasteWire) or attached, each a chip (pillChipHTML).
  let files: File[] = [];
  // `found`: threads by name from Browse for the words typed. `match`: the answer for `chosen` (null while asked).
  // `hit` marks a row while typing; `pick` marks the one Send goes to among the matches.
  let found: ThreadRow[] = [], match: NmMatch | null = null, hit = 0, pick = 0, name = '', busy = false, seq = 0, timer = 0;
  const label = (a: string) => threadAddable().find(t => t.actor === a)?.label || actorName(a);
  const words = () => q.value.trim().toLowerCase();
  const typing = () => !!words() || !chosen.length;
  const single = () => chosen.length === 1 && !chosen[0]!.startsWith('group:') ? chosen[0]! : '';
  const rows = (): NmRow[] => {
    const w = words();
    if (typing()) {
      // A name that starts with the words comes before one that only has them.
      const starts = (label: string) => Number(!label.toLowerCase().startsWith(w));
      const people = threadAddable().filter(t => !chosen.includes(t.actor) && (!w || t.label.toLowerCase().includes(w)))
        .sort((a, b) => starts(a.label) - starts(b.label)).slice(0, NM_SUGGEST).map(t => ({kind: 'actor' as const, ...t}));
      // Nobody chosen yet: a thread may still start with only its name (who reads it is set in its settings).
      if (!w) return [...people, {kind: 'new'}];
      const seen = new Set<string>(), named = [...THREADS.pinned, ...THREADS.threads, ...found]
        .filter(t => !seen.has(t.id) && (seen.add(t.id), threadTitle(t).toLowerCase().includes(w))).slice(0, NM_THREADS);
      return [...people, ...named.map(t => ({kind: 'thread' as const, t}))];
    }
    const one = single();
    return [...(one && match ? [{kind: 'dm' as const, actor: one, id: match.dm || null}] : []),
      ...(match?.threads || []).map(t => ({kind: 'thread' as const, t})), {kind: 'new'}];
  };
  const faces = () => `<span class="nm-ppl">${chosen.slice(0, 3).map(a => actorAvatar(a, 18)).join('')}</span>`;
  const rowHTML = (r: NmRow, i: number, on: boolean): string => {
    const attrs = `role="option" id="nm-opt-${i}" data-i="${i}" aria-selected="${on}"`;
    if (r.kind === 'actor') return `<li ${attrs}>${actorAvatar(r.actor, 22)}<span class="nm-name">${esc(r.label)}</span></li>`;
    if (r.kind === 'thread') return `<li ${attrs}>${threadHashHTML(r.t)}<span class="nm-name">${esc(threadTitle(r.t))}</span>${typing() ? '' : faces()}</li>`;
    if (r.kind === 'dm') return `<li ${attrs}>${actorAvatar(r.actor, 22)}<span class="nm-name">${esc(label(r.actor))}</span></li>`;
    return `<li ${attrs} class="nm-new"><span class="tr-mark" aria-hidden="true">${NM_SVG.plus}</span><span class="nm-name">New thread</span>
      <span class="nm-title"><span aria-hidden="true">#</span><input id="nm-title" maxlength="200" placeholder="name" autocomplete="off" aria-label="Name" value="${esc(name)}"></span></li>`;
  };
  // The row Send goes to: the marked match once the teammates are in (none while typing or while the match is asked);
  // with nobody chosen, a new thread once it has a name.
  const target = (): NmRow | null => {
    if (!chosen.length) return !words() && name.trim() ? {kind: 'new'} : null;
    if (typing() || !match) return null;
    const r = rows(); return r[Math.min(pick, r.length - 1)] || null;
  };
  const draw = (): void => {
    chips.innerHTML = chosen.map(a => `<span class="nm-chip" data-actor="${esc(a)}">${actorAvatar(a, 18)}<span>${esc(label(a))}</span><button type="button" data-remove aria-label="Remove ${esc(label(a))}">×</button></span>`).join('');
    q.placeholder = chosen.length ? '' : 'Name or #thread';
    const all = rows(), on = typing() ? Math.min(hit, Math.max(0, all.length - 1)) : Math.min(pick, Math.max(0, all.length - 1));
    const keep = document.activeElement?.id === 'nm-title' ? (document.activeElement as HTMLInputElement).selectionStart : null;
    list.innerHTML = all.map((r, i) => rowHTML(r, i, i === on)).join('');
    list.hidden = !all.length || (!typing() && !match);
    q.setAttribute('aria-expanded', String(!list.hidden));
    if (!list.hidden) q.setAttribute('aria-activedescendant', 'nm-opt-' + on); else q.removeAttribute('aria-activedescendant');
    const title = list.querySelector<HTMLInputElement>('#nm-title');
    if (title && keep !== null) { title.focus(); title.setSelectionRange(keep, keep); }
    sendable();
  };
  const sendable = (): void => { send.disabled = busy || !target(); };
  // The threads with exactly the chosen teammates, asked again each time they change; an older answer is dropped.
  const ask = async (): Promise<void> => {
    const mine = ++seq; match = null; pick = 0; draw();
    if (!chosen.length) return;
    try {
      const r = await get<NmMatch>('/v2/threads/match?' + chosen.map(a => 'actor=' + encodeURIComponent(a)).join('&'), {fresh: true});
      if (mine !== seq) return;
      match = r || {threads: [], dm: null};
    } catch (e) { if (mine !== seq) return; match = {threads: [], dm: null}; toast(errorMessage(e, ''), true); }
    draw();
  };
  // Threads by name beyond the rail's: Browse's, for the words typed.
  const browse = (): void => {
    clearTimeout(timer);
    const w = q.value.trim();
    if (!w) { found = []; return; }
    timer = window.setTimeout(async () => {
      const r = await get<{threads?: ThreadRow[]}>('/v2/threads/browse?limit=5&q=' + encodeURIComponent(w), {fresh: true}).catch(() => null);
      if (q.value.trim() !== w) return;
      found = r?.threads || []; draw();
    }, 150);
  };
  const choose = (actor: string): void => {
    if (!chosen.includes(actor)) chosen.push(actor);
    q.value = ''; hit = 0; found = [];
    void ask(); q.focus();
  };
  const unchoose = (actor: string): void => { chosen.splice(chosen.indexOf(actor), 1); void ask(); };
  const open = (id: string): void => { location.hash = threadHref(id); };
  const made = (r: NmReply | null): string => {
    const t = r?.thread || r;
    if (!t?.id) { toast('Your first message was held by a rule', true); return ''; }
    threadsPut(t);
    return t.id;
  };
  const drawFiles = (): void => { fileChips.innerHTML = files.map((f, i) => pillChipHTML(f, String(i))).join(''); };
  // The first message into a thread: with files it is one upload, as the thread composer sends (thread-view.ts thvUpload).
  const say = async (id: string, body: string, sent: File[]): Promise<void> => {
    const r: {held?: unknown} | null = sent.length ? await thvUpload(thvPath(id).replace('/v2/', '/v2/uploads/') + '/messages', {text: body}, sent)
      : await post(thvPath(id) + '/messages', {text: body});
    if (r?.held) toast('Your first message was held by a rule', true);
  };
  // A DM or thread made for files whose upload then failed: the next Send to the same row posts there, not into a
  // second thread of the same name.
  let early: {key: string; id: string} | null = null;
  // Go to a row: a teammate is added, a thread opens, a DM or a new thread is made first when it has to be. `body`: the
  // composer's words when sending, posted there on the way with the files waiting; a DM or thread made for files is made
  // empty first, then the message with them goes in it. A row picked from the list (no `body`) only goes there, sending
  // neither the words nor the files.
  const go = async (r: NmRow | null | undefined, body?: string): Promise<void> => {
    if (!r || busy) return;
    if (r.kind === 'actor') { choose(r.actor); return; }
    const title = name.trim().replace(/^#+/, '').trim();
    if (r.kind === 'new' && !title) {
      const input = list.querySelector<HTMLInputElement>('#nm-title');
      input?.setAttribute('aria-invalid', 'true'); input?.focus();
      return;
    }
    busy = true; sendable();
    try {
      const sent = body === undefined ? [] : files.slice(), words = body || '', first = sent.length ? '' : words;
      const key = r.kind === 'dm' ? 'dm\n' + r.actor : r.kind === 'new' ? ['new', title, ...chosen].join('\n') : '';
      let id = r.kind === 'thread' ? r.t.id : r.kind === 'dm' ? r.id || '' : '';
      if (!id && key && early?.key === key) id = early.id;
      const posted = !!id;
      if (r.kind === 'dm' && !id) id = made(await post('/v2/threads/dm', {members: [r.actor], ...(first ? {body: first} : {})}));
      else if (r.kind === 'new' && !id) {
        id = made(await post('/v2/threads', {title, audience: 'members', members: chosen, pin_for_members: true, ...(first ? {body: first} : {})}));
      }
      if (!posted && id && sent.length) early = {key, id};
      if (id && (sent.length || posted && words)) await say(id, words, sent);
      if (id) { box.value = ''; files.forEach(pillPreviewForget); files = []; drawFiles(); open(id); }
    } catch (e) {
      if (errorCode(e) === 'name_required') list.querySelector<HTMLInputElement>('#nm-title')?.focus();
      toast(errorMessage(e, ''), true);
    } finally { busy = false; if (pane.isConnected) sendable(); }
  };
  const close = (): void => { location.hash = THREADS_ROUTE; };

  q.oninput = () => { hit = 0; browse(); draw(); };
  q.onkeydown = (ev: KeyboardEvent) => {
    const all = rows(), n = all.length, step = ev.key === 'ArrowDown' ? 1 : ev.key === 'ArrowUp' ? -1 : 0;
    if (step && n) {
      ev.preventDefault();
      if (typing()) hit = (Math.min(hit, n - 1) + step + n) % n; else pick = (Math.min(pick, n - 1) + step + n) % n;
      draw();
    } else if (ev.key === 'Enter' && !ev.isComposing) {
      ev.preventDefault();
      const r = typing() ? all[Math.min(hit, n - 1)] : target();
      if (r?.kind === 'new') list.querySelector<HTMLInputElement>('#nm-title')?.focus();
      else void go(r);
    } else if (ev.key === 'Backspace' && !q.value && chosen.length) { chosen.pop(); void ask(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); if (q.value) { q.value = ''; found = []; draw(); } else close(); }
  };
  chips.onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest('[data-remove]');
    const actor = b?.closest<HTMLElement>('[data-actor]')?.dataset.actor;
    if (actor) unchoose(actor);
    q.focus();
  };
  (pane.querySelector('.nm-to') as HTMLElement).onclick = (ev: MouseEvent) => { if (eventElement(ev) === ev.currentTarget) q.focus(); };
  // A press on a row (not on the new thread's name) picks it; mousedown keeps the focus where it was.
  list.onmousedown = (ev: MouseEvent) => {
    const el = eventElement(ev);
    if (el?.closest('#nm-title')) return;
    const li = el?.closest<HTMLElement>('[data-i]'); if (!li) return;
    ev.preventDefault();
    const r = rows()[Number(li.dataset.i)];
    if (r?.kind === 'new') { list.querySelector<HTMLInputElement>('#nm-title')?.focus(); return; }
    void go(r);
  };
  // The new thread's name: the one input inside the list.
  const nameOf = (ev: Event): HTMLInputElement | null => { const el = eventElement(ev); return el instanceof HTMLInputElement && el.id === 'nm-title' ? el : null; };
  // Into the name: the new thread's row is the one marked.
  list.addEventListener('focusin', ev => {
    if (!nameOf(ev)) return;
    const at = rows().findIndex(r => r.kind === 'new');
    if (at === (typing() ? hit : pick)) return;
    if (typing()) hit = at; else pick = at;
    draw();
  });
  list.addEventListener('input', ev => {
    const input = nameOf(ev);
    if (!input) return;
    name = input.value; input.removeAttribute('aria-invalid');
    sendable();
  });
  list.addEventListener('keydown', ev => {
    if (!nameOf(ev)) return;
    if (ev.key === 'Enter' && !ev.isComposing) { ev.preventDefault(); void go({kind: 'new'}, box.value.trim()); }
    else if (ev.key === 'Escape') { ev.preventDefault(); q.focus(); }
  });
  const grow = () => pillGrow(box);
  box.addEventListener('input', grow);
  box.addEventListener('keydown', (ev: KeyboardEvent) => {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && !touchKeyboard()) { ev.preventDefault(); void go(target(), box.value.trim()); }
    else if (ev.key === 'Escape') { ev.preventDefault(); close(); }
  });
  send.onclick = () => void go(target(), box.value.trim());
  pillPasteWire(box, pasted => { files.push(...pasted); drawFiles(); });
  (pane.querySelector('.p-attach') as HTMLElement).onclick = () => file.click();
  file.onchange = () => { files.push(...file.files!); file.value = ''; drawFiles(); };
  fileChips.onclick = (ev: MouseEvent) => {
    // A thumbnail opens the image full size (chat.ts chatImageOpen); × takes the file off.
    const thumb = eventElement(ev)?.closest<HTMLElement>('.p-thumb'); if (thumb) { ev.preventDefault(); chatImageOpen(thumb); return; }
    const rm = eventElement(ev)?.closest<HTMLElement>('[data-rm]'); if (!rm) return;
    ev.preventDefault();
    pillPreviewForget(files.splice(Number(rm.dataset.rm), 1)[0]);
    drawFiles();
  };
  void ask();
  (chosen.length ? box : q).focus();
  return draw;
}
