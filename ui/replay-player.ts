/* ui/replay-player.ts — The history replay player: bot faces, a task board and an activity feed that play a list of
   history items (GET /api/v2/replay) back at a chosen speed, with play/pause, speeds, skip-quiet and a scrubber.
   It mounts through `window.TicoReplay` and reads no app state: the Replay page (ui/app/replay.ts) passes the app's
   faces and names in through `opts`. */
'use strict';

// A task as a history item names it.
interface ReplayTaskRef { id: string; title: string; }

// The fields every history item has. The server sends one kind per item (backend/replay.py); `kind` says which, and
// the kinds below narrow what each one carries.
interface ReplayItemBase {
  at: string;
  kind: string;
  who?: string | undefined;
  bot?: string | undefined;
  task?: ReplayTaskRef | undefined;
}
// A task made in the range.
interface ReplayTaskCreated extends ReplayItemBase {
  kind: 'task.created';
  task: ReplayTaskRef;
  owner_id?: string | null | undefined;
  status?: string | undefined;
}
// A status or owner change: 'task.' plus the field, from TASK_FIELDS in backend/replay.py.
interface ReplayTaskChange extends ReplayItemBase {
  kind: 'task.status' | 'task.owner';
  task: ReplayTaskRef;
  old?: string | null | undefined;
  new?: string | null | undefined;
  text?: string | undefined;
}
interface ReplayMessage extends ReplayItemBase {
  kind: 'chat.message' | 'task.comment';
  to?: string | null | undefined;
  text?: string | undefined;
}
interface ReplayRun extends ReplayItemBase {
  kind: 'run.started' | 'run.finished';
  exit?: string | undefined;
  text?: string | undefined;
}
interface ReplayBotStatus extends ReplayItemBase {
  kind: 'bot.status';
  state: string;
  text?: string | undefined;
}
interface ReplayGoalStatus extends ReplayItemBase {
  kind: 'goal.status';
  goal: {id: string; title: string};
  old?: string | null | undefined;
  new?: string | null | undefined;
}
type ReplayItem = ReplayTaskCreated | ReplayTaskChange | ReplayMessage | ReplayRun | ReplayBotStatus | ReplayGoalStatus;
// An item once mount has read it: its index in the range, and its time in ms.
type ReplayEvent = ReplayItem & {i: number; ms: number};

// A task the board holds. `owner` is set when a task first appears in the range (task.created, or a status change to a
// task not yet seen). The board reads `owner_id`, so a card set from those paths shows no owner.
interface ReplayTask {
  id: string;
  title: string;
  owner_id?: string | null | undefined;
  owner?: string | null | undefined;
  status: string;
  touched: number;
  fresh?: boolean | undefined;
}
// A bot as the board knows it: its state and what it is working on. A run that finishes for a bot the board has not seen
// leaves a bot with no state (as the original did), so state is optional.
interface ReplayBot { state?: string | undefined; focus?: string | undefined; }

// A line or a message in the activity feed. `shown` is how many characters are typed out so far.
interface ReplayEntry {
  kind: 'line' | 'msg';
  who?: string | undefined;
  html?: string | undefined;
  text: string;
  shown: number;
  ms: number;
  to?: string | null | undefined;
  task?: ReplayTaskRef | undefined;
  type?: boolean | undefined;
}
type ReplayEntryInit = Omit<ReplayEntry, 'text' | 'shown'> & {text?: string | undefined};

// An actor as the history names it: 'bot:slug', 'human:id', 'keeper', or none.
type ReplayActor = string | null | undefined;

interface ReplayStartTask { id: string; title: string; owner_id?: string | null | undefined; status: string; }
interface ReplayStartBot { state: string; focus?: string | undefined; }

// GET /api/v2/replay (backend/replay.py): the range, its items in time order, the board and bot states it starts
// from, and the names of the actors the items show.
interface ReplayData {
  from: string;
  to: string;
  items: ReplayItem[];
  names?: Record<string, string> | undefined;
  start?: {tasks?: ReplayStartTask[] | undefined; bots?: Record<string, ReplayStartBot> | undefined} | undefined;
}

interface ReplayPlayerState { playing: boolean; speed: number; skip: boolean; at: string; }
type ReplayName = (actor: ReplayActor) => string;
type ReplayFace = (actor: ReplayActor, size: number, state?: string) => string;
interface ReplayOptions {
  name?: ReplayName | undefined;
  face?: ReplayFace | undefined;
  speed?: number | undefined;
  skip?: boolean | undefined;
  at?: string | undefined;
  autoplay?: boolean | undefined;
  onChange?: ((state: ReplayPlayerState) => void) | undefined;
}
interface ReplayPlayer {
  play(): void;
  pause(): void;
  step(dir: number): void;
  seek(t: number): void;
  setSpeed(s: number | string | undefined): void;
  setSkip(on: boolean): void;
  state(): ReplayPlayerState;
  destroy(): void;
}
interface TicoReplayModule {
  mount(root: HTMLElement, data: ReplayData, opts?: ReplayOptions): ReplayPlayer;
  SPEEDS: number[];
}
interface Window { TicoReplay: TicoReplayModule; }

function ticoReplayPlayer(): TicoReplayModule {
  const SPEEDS = [1, 10, 60, 600];
  const QUIET_MS = 1500;          // the longest a quiet stretch lasts, in real time, with Skip quiet on
  const FEED_MAX = 80;
  const DONE_MAX = 12;
  type ReplayColumn = 'todo' | 'doing' | 'waiting' | 'done';
  const COLUMNS: readonly (readonly [ReplayColumn, string, readonly string[]])[] = [['todo', 'To do', ['open', 'ready']], ['doing', 'Doing', ['doing']],
                   ['waiting', 'Waiting', ['waiting', 'review', 'declined']], ['done', 'Done', ['done', 'closed']]];
  const STATUS_WORDS: Record<string, string> = {open: 'To do', ready: 'To do', doing: 'Doing', waiting: 'Waiting', review: 'Review',
                         declined: 'Declined', done: 'Done', closed: 'Closed'};
  const ICON_PLAY = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>';
  const ICON_PAUSE = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/></svg>';
  const ENTITY: Record<string, string> = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
  const escHtml = (s: unknown): string => String(s ?? '').replace(/[&<>"']/g, c => ENTITY[c] ?? c);
  const hue = (s: unknown): number => { let h = 2166136261; for (const ch of String(s)) h = Math.imul(h ^ (ch.codePointAt(0) ?? 0), 16777619); return (h >>> 0) % 360; };
  const initials = (s: unknown): string => (String(s || '').split(/[^A-Za-z0-9]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('') || '?').toUpperCase();
  const reduced = (): boolean => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const columnOf = (status: string): ReplayColumn => COLUMNS.find(([, , ss]) => ss.includes(status))?.[0] ?? 'todo';
  const live = (state: string | undefined): 'running' | 'needs' | 'paused' | 'idle' => state === 'running' ? 'running' : ['waiting_human', 'blocked', 'crashed', 'quarantined'].includes(state ?? '') ? 'needs'
    : state === 'paused' ? 'paused' : 'idle';

  function mount(root: HTMLElement, data: ReplayData, opts: ReplayOptions = {}): ReplayPlayer {
    const items: ReplayEvent[] = (data.items || []).map((it, i) => ({...it, i, ms: Date.parse(it.at)}));
    const from = Date.parse(data.from), to = Date.parse(data.to);
    const names: Record<string, string> = data.names || {};
    const name: ReplayName = opts.name || (a => names[String(a)] || String(a || '').replace(/^(bot|human):/, ''));
    const face: ReplayFace = opts.face || ((a, size) => `<span class="av av-initials" style="width:${size}px;height:${size}px;--hue:${hue(name(a))};font-size:${Math.round(size * 0.42)}px" aria-hidden="true">${escHtml(initials(name(a)))}</span>`);
    interface ReplayState {
      t: number; next: number; playing: boolean; speed: number; skip: boolean;
      tasks: Map<string, ReplayTask>; bots: Map<string, ReplayBot>; feed: ReplayEntry[]; typing: ReplayEntry | null;
      raf: number; last: number; dirty: {board: boolean; feed: boolean; bots: boolean}; flash: Set<string>;
      idleTimer: number;
    }
    const st: ReplayState = {t: from, next: 0, playing: false, speed: SPEEDS.includes(Number(opts.speed)) ? Number(opts.speed) : 60, skip: opts.skip !== false,
                tasks: new Map(), bots: new Map(), feed: [], typing: null, raf: 0, last: 0, dirty: {board: true, feed: true, bots: true}, flash: new Set(),
                idleTimer: 0};
    const fmtClock = (ms: number): string => new Date(ms).toLocaleString(undefined, {weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    const fmtTime = (ms: number): string => new Date(ms).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'});

    root.classList.add('rp');
    root.innerHTML = `
      <div class="rp-stage">
        <div class="rp-top"><div class="rp-bots" aria-label="Bots"></div><span class="rp-stamp tnum" aria-hidden="true"></span></div>
        <div class="rp-main">
          <div class="rp-board" aria-label="Tasks"></div>
          <div class="rp-feed" aria-label="Activity" aria-live="off"></div>
        </div>
      </div>
      <div class="rp-bar">
        <button type="button" class="rp-play" aria-label="Play">${ICON_PLAY}</button>
        <span class="rp-clock tnum" aria-live="off"></span>
        <div class="rp-scrub"><div class="rp-ticks" aria-hidden="true"></div>
          <input type="range" class="rp-range" min="0" max="1000" step="1" value="0" aria-label="Position"></div>
        <div class="rp-speeds" role="group" aria-label="Speed">${SPEEDS.map(s => `<button type="button" data-speed="${s}">${s}×</button>`).join('')}</div>
        <button type="button" class="rp-skip" aria-pressed="false" title="Compress quiet time">Skip quiet</button>
      </div>`;
    // The markup above holds each of these, so the lookup finds it.
    const $q = <E extends Element>(sel: string): E => root.querySelector<E>(sel)!;
    const els = {stamp: $q<HTMLElement>('.rp-stamp'), bots: $q<HTMLElement>('.rp-bots'), board: $q<HTMLElement>('.rp-board'), feed: $q<HTMLElement>('.rp-feed'),
                 play: $q<HTMLButtonElement>('.rp-play'), clock: $q<HTMLElement>('.rp-clock'),
                 range: $q<HTMLInputElement>('.rp-range'), ticks: $q<HTMLElement>('.rp-ticks'), speeds: $q<HTMLElement>('.rp-speeds'), skip: $q<HTMLButtonElement>('.rp-skip')};

    // Activity ticks: how much happened in each slice of the range.
    const BUCKETS = 120, counts = new Array<number>(BUCKETS).fill(0);
    for (const it of items) { const k = Math.min(BUCKETS - 1, Math.max(0, Math.floor((it.ms - from) / Math.max(1, to - from) * BUCKETS))); counts[k] = (counts[k] ?? 0) + 1; }
    const peak = Math.max(1, ...counts);
    els.ticks.innerHTML = counts.map(n => `<i style="height:${n ? Math.max(12, Math.round(n / peak * 100)) : 0}%"></i>`).join('');

    // ---- state: apply one item
    function reset(): void {
      st.tasks = new Map(); st.bots = new Map(); st.feed = []; st.typing = null; st.next = 0; st.flash.clear();
      for (const t of data.start?.tasks || []) st.tasks.set(t.id, {...t, touched: from});
      for (const [slug, b] of Object.entries(data.start?.bots || {})) st.bots.set(slug, {state: b.state, focus: b.focus || ''});
      for (const it of items) if (it.bot && !st.bots.has(it.bot)) st.bots.set(it.bot, {state: 'idle', focus: ''});
      for (const it of items) { const s = String(it.who || ''); if (s.startsWith('bot:') && !st.bots.has(s.slice(4))) st.bots.set(s.slice(4), {state: 'idle', focus: ''}); }
      st.dirty = {board: true, feed: true, bots: true};
    }
    function touch(id: string): void {
      const task = st.tasks.get(id);
      if (task) { task.touched = st.t; st.flash.add(id); st.dirty.board = true; }
    }
    function say(init: ReplayEntryInit, animate: boolean): void {
      if (st.typing) { st.typing.shown = st.typing.text.length; st.typing = null; }
      const text = init.text || '';
      const shown = init.type && animate && !reduced() ? 0 : text.length;
      const entry: ReplayEntry = {...init, text, shown};
      if (shown < text.length) st.typing = entry;
      st.feed.push(entry);
      if (st.feed.length > FEED_MAX) st.feed.splice(0, st.feed.length - FEED_MAX);
      st.dirty.feed = true;
    }
    function apply(it: ReplayEvent, animate: boolean): void {
      const task = it.task?.id ? st.tasks.get(it.task.id) : null;
      const actor = String(it.who || '');
      const bot = it.bot || (actor.startsWith('bot:') ? actor.slice(4) : '');
      if (it.kind === 'task.created') {
        st.tasks.set(it.task.id, {id: it.task.id, title: it.task.title, owner: it.owner_id, status: it.status || 'open', touched: it.ms, fresh: true});
        st.flash.add(it.task.id); st.dirty.board = true;
        say({kind: 'line', who: it.who, html: `added <b>${escHtml(it.task.title)}</b>${it.owner_id && it.owner_id !== it.who ? ` for ${escHtml(name(it.owner_id))}` : ''}`, ms: it.ms}, animate);
      } else if (it.kind === 'task.status') {
        if (task) { task.status = it.new || task.status; touch(task.id); }
        else if (it.task) { st.tasks.set(it.task.id, {id: it.task.id, title: it.task.title, owner: it.who, status: it.new || 'open', touched: it.ms}); touch(it.task.id); }
        say({kind: 'line', who: it.who, html: `moved <b>${escHtml(it.task.title)}</b> to ${escHtml(STATUS_WORDS[String(it.new)] || it.new || '')}`, text: it.text, ms: it.ms}, animate);
      } else if (it.kind === 'task.owner') {
        if (task) { task.owner_id = it.new; touch(task.id); }
        say({kind: 'line', who: it.who, html: `gave <b>${escHtml(it.task.title)}</b> to ${escHtml(name(it.new))}`, ms: it.ms}, animate);
      } else if (it.kind === 'chat.message' || it.kind === 'task.comment') {
        if (it.task) touch(it.task.id);
        say({kind: 'msg', who: it.who, to: it.to, task: it.task, text: it.text || '', type: actor.startsWith('bot:'), ms: it.ms}, animate);
      } else if (it.kind === 'run.started') {
        if (bot) { st.bots.set(bot, {...(st.bots.get(bot) || {}), state: 'running'}); st.dirty.bots = true; }
        if (it.task) touch(it.task.id);
      } else if (it.kind === 'run.finished') {
        if (bot) { const b: ReplayBot = st.bots.get(bot) || {}; st.bots.set(bot, {...b, state: b.state === 'running' ? 'idle' : b.state}); st.dirty.bots = true; }
        // A run is one short line: what the bot said in chat is the content, so a summary that repeats it is left out.
        const failed = it.exit && it.exit !== 'completed' && it.exit !== '0';
        const flat = (t: unknown): string => String(t || '').replace(/\s+/g, ' ').trim();
        const summary = flat(it.text), head = summary.slice(0, 80);
        const said = summary && st.feed.slice(-30).some(e => e.kind === 'msg' && e.who === it.who && flat(e.text).startsWith(head));
        const note = said || !summary ? '' : summary.length > 140 ? summary.slice(0, 139).trimEnd() + '…' : summary;
        say({kind: 'line', who: it.who, html: (failed ? 'had a run fail' : 'finished a run') + (it.task ? ` on <b>${escHtml(it.task.title)}</b>` : ''),
             text: note, ms: it.ms}, animate);
      } else if (it.kind === 'bot.status') {
        if (bot) { st.bots.set(bot, {state: it.state, focus: it.text || ''}); st.dirty.bots = true; }
      } else if (it.kind === 'goal.status') {
        say({kind: 'line', who: it.who, html: `marked goal <b>${escHtml(it.goal.title)}</b> ${escHtml(String(it.new || '').replace(/_/g, ' '))}`, ms: it.ms}, animate);
      }
    }
    function advance(t: number, animate: boolean): void {
      st.t = t;
      let item = items[st.next];
      while (item && item.ms <= t) { st.next++; apply(item, animate); item = items[st.next]; }
    }
    function seek(t: number): void {
      t = Math.max(from, Math.min(to, t));
      if (t < st.t || !st.playing) { reset(); }
      advance(t, false);
      st.t = t; paint(false);
    }

    // ---- painting
    function faceOf(actor: ReplayActor, size: number, state?: string): string { return `<span class="rp-face" data-state="${escHtml(state || '')}">${face(actor, size, state)}</span>`; }
    // A card's owner face takes the owner bot's live state, when the board knows that bot.
    const liveOf = (owner: string): string => { const bot = st.bots.get(String(owner).replace(/^bot:/, '')); return bot?.state ? live(bot.state) : ''; };
    function paintBots(): void {
      const rows = [...st.bots.entries()].sort((a, b) => name('bot:' + a[0]).localeCompare(name('bot:' + b[0])));
      els.bots.innerHTML = rows.map(([slug, b]) => {
        const s = live(b.state);
        return `<div class="rp-bot" data-state="${s}" title="${escHtml(name('bot:' + slug))}${b.focus ? ' · ' + escHtml(b.focus) : ''}">${faceOf('bot:' + slug, 28, s)}<span class="rp-bot-name">${escHtml(name('bot:' + slug))}</span></div>`;
      }).join('');
      els.bots.hidden = !rows.length;
    }
    function paintBoard(animate: boolean): void {
      const before = new Map<string | undefined, DOMRect>();
      if (animate && !reduced()) for (const el of els.board.querySelectorAll<HTMLElement>('[data-rp-task]')) before.set(el.dataset.rpTask, el.getBoundingClientRect());
      const groups: Record<ReplayColumn, ReplayTask[]> = {todo: [], doing: [], waiting: [], done: []};
      for (const t of st.tasks.values()) groups[columnOf(t.status)].push(t);
      groups.done.sort((a, b) => b.touched - a.touched); groups.done = groups.done.slice(0, DONE_MAX);
      const sortable: ReplayColumn[] = ['todo', 'doing', 'waiting'];
      for (const k of sortable) groups[k].sort((a, b) => b.touched - a.touched);
      els.board.innerHTML = COLUMNS.map(([k, label]) => `<section class="bcol" data-col="${k}"><header><h2>${label}</h2><span class="cnt tnum">${groups[k].length}</span></header>
        <div class="bcol-body">${groups[k].map(t => `<div class="bcard${st.flash.has(t.id) ? ' rp-flash' : ''}" data-rp-task="${escHtml(t.id)}">
          <div class="bcard-open"><span class="bcard-title">${escHtml(t.title)}</span></div>
          <div class="bcard-foot"><span class="rp-owner">${escHtml(name(t.owner_id))}</span><span class="spacer"></span>${t.owner_id ? faceOf(t.owner_id, 16, liveOf(t.owner_id)) : ''}</div></div>`).join('')}</div></section>`).join('');
      if (before.size) for (const el of els.board.querySelectorAll<HTMLElement>('[data-rp-task]')) {
        const was = before.get(el.dataset.rpTask), now = el.getBoundingClientRect();
        if (was && (was.left !== now.left || was.top !== now.top)) el.animate([{transform: `translate(${was.left - now.left}px,${was.top - now.top}px)`}, {transform: 'none'}], {duration: 420, easing: 'cubic-bezier(.2,0,0,1)'});
        else if (!was) el.animate([{opacity: 0, transform: 'scale(.96)'}, {opacity: 1, transform: 'none'}], {duration: 260, easing: 'cubic-bezier(0,0,.2,1)'});
      }
      st.flash.clear();
    }
    function entryHTML(e: ReplayEntry): string {
      const text = (e.text || '').slice(0, e.shown);
      const typing = e === st.typing ? '<span class="rp-caret" aria-hidden="true"></span>' : '';
      const when = `<span class="rp-when tnum">${fmtTime(e.ms)}</span>`;
      if (e.kind === 'line') return `<div class="rp-line">${faceOf(e.who, 18)}<span class="rp-line-text"><b>${escHtml(name(e.who))}</b> ${e.html}${e.text ? `<span class="rp-line-note">${escHtml(e.text)}</span>` : ''}</span>${when}</div>`;
      const head = `<b>${escHtml(name(e.who))}</b>${e.to && !e.task ? ` → ${escHtml(name(e.to))}` : ''}${e.task ? ` · <span class="rp-task">${escHtml(e.task.title)}</span>` : ''}`;
      return `<div class="rp-msg${e.type ? ' bot' : ' you'}">${faceOf(e.who, 22)}<div class="rp-msg-body"><div class="rp-msg-head">${head}${when}</div><div class="rp-msg-text">${escHtml(text)}${typing}</div></div></div>`;
    }
    // A light divider wherever the day changes, so a range of several days never reads out of order.
    const dayOf = (ms: number): string => new Date(ms).toDateString();
    const several = dayOf(from) !== dayOf(to - 1);
    function paintFeed(): void {
      let day = '';
      els.feed.innerHTML = st.feed.map((e, i) => {
        const d = dayOf(e.ms), divider = d !== day && (i > 0 || several)
          ? `<div class="rp-day">${escHtml(new Date(e.ms).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}))}</div>` : '';
        day = d;
        return divider + entryHTML(e);
      }).join('') || '<p class="rp-empty">Nothing yet</p>';
      els.feed.scrollTop = els.feed.scrollHeight;
    }
    function paintTyping(): void {
      const last = els.feed.lastElementChild?.querySelector<HTMLElement>('.rp-msg-text');
      if (!last || !st.typing) return;
      last.innerHTML = escHtml(st.typing.text.slice(0, st.typing.shown)) + (st.typing.shown < st.typing.text.length ? '<span class="rp-caret" aria-hidden="true"></span>' : '');
      els.feed.scrollTop = els.feed.scrollHeight;
    }
    function paintBar(): void {
      els.play.innerHTML = st.playing ? ICON_PAUSE : ICON_PLAY;
      els.play.setAttribute('aria-label', st.playing ? 'Pause' : 'Play');
      els.clock.textContent = els.stamp.textContent = fmtClock(st.t);
      els.range.value = String(Math.round((st.t - from) / Math.max(1, to - from) * 1000));
      for (const b of els.speeds.querySelectorAll<HTMLButtonElement>('[data-speed]')) b.setAttribute('aria-pressed', String(Number(b.dataset.speed) === st.speed));
      els.skip.setAttribute('aria-pressed', String(st.skip));
      root.classList.toggle('playing', st.playing);
    }
    function paint(animate: boolean): void {
      if (st.dirty.bots) paintBots();
      if (st.dirty.board) paintBoard(animate);
      if (st.dirty.feed) paintFeed();
      st.dirty = {board: false, feed: false, bots: false};
      paintBar();
    }

    // ---- the clock
    function frame(now: number): void {
      if (!st.playing) return;
      const dt = Math.min(250, now - (st.last || now)); st.last = now;
      const prev = items[st.next - 1]?.ms ?? from, next = items[st.next]?.ms ?? to;
      const gap = Math.max(0, next - Math.max(prev, from));
      const rate = st.skip && gap / st.speed > QUIET_MS ? gap / QUIET_MS : st.speed;
      if (st.typing) {
        const cps = 45 * Math.sqrt(st.speed);
        st.typing.shown = Math.min(st.typing.text.length, st.typing.shown + Math.max(1, Math.round(cps * dt / 1000)));
        if (st.typing.shown >= st.typing.text.length) st.typing = null;
        paintTyping();
      }
      advance(Math.min(to, st.t + dt * rate), true);
      paint(true);
      if (st.t >= to) { st.playing = false; paintBar(); opts.onChange?.(api.state()); return; }
      st.raf = requestAnimationFrame(frame);
    }
    function play(): void {
      if (st.playing) return;
      if (st.t >= to) seek(from);
      st.playing = true; st.last = 0; st.raf = requestAnimationFrame(frame); paintBar(); idle(); opts.onChange?.(api.state());
    }
    function pause(): void { st.playing = false; cancelAnimationFrame(st.raf); if (st.typing) { st.typing.shown = st.typing.text.length; st.typing = null; st.dirty.feed = true; } paint(false); opts.onChange?.(api.state()); }
    function step(dir: number): void {
      pause();
      if (dir > 0) { const it = items[st.next]; seek(it ? it.ms : to); }
      else { const it = items[st.next - 2]; seek(it ? it.ms : from); }
    }
    function setSpeed(s: number | string | undefined): void { if (SPEEDS.includes(Number(s))) { st.speed = Number(s); paintBar(); opts.onChange?.(api.state()); } }
    function setSkip(on: boolean): void { st.skip = !!on; paintBar(); opts.onChange?.(api.state()); }
    // Controls fade while playing when asked (presentation), and come back on any pointer movement.
    function idle(): void {
      root.classList.remove('rp-idle'); clearTimeout(st.idleTimer);
      if (st.playing) st.idleTimer = setTimeout(() => root.classList.add('rp-idle'), 1800);
    }

    els.play.onclick = () => st.playing ? pause() : play();
    els.speeds.onclick = (ev: MouseEvent) => { const b = ev.target instanceof Element ? ev.target.closest<HTMLElement>('[data-speed]') : null; if (b) setSpeed(b.dataset.speed); };
    els.skip.onclick = () => setSkip(!st.skip);
    els.range.oninput = () => { const wasPlaying = st.playing; if (wasPlaying) { st.playing = false; cancelAnimationFrame(st.raf); } seek(from + (+els.range.value / 1000) * (to - from)); if (wasPlaying) play(); };
    root.addEventListener('pointermove', idle);
    const onKey = (ev: KeyboardEvent): void => {
      if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || !root.isConnected) return;
      const target = ev.target instanceof HTMLElement ? ev.target : null;
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(target?.tagName || '') && ev.target !== els.range) return;
      if (target?.isContentEditable || document.querySelector('dialog[open]')) return;
      if (ev.key === ' ' || ev.code === 'Space') { ev.preventDefault(); st.playing ? pause() : play(); }
      else if (ev.key === 'ArrowRight') { ev.preventDefault(); step(1); }
      else if (ev.key === 'ArrowLeft') { ev.preventDefault(); step(-1); }
      else if (/^[1-4]$/.test(ev.key)) setSpeed(SPEEDS[+ev.key - 1]);
    };
    document.addEventListener('keydown', onKey);

    reset(); paint(false);
    if (opts.at) seek(Date.parse(opts.at));
    const api: ReplayPlayer = {
      play, pause, step, seek, setSpeed, setSkip,
      state: () => ({playing: st.playing, speed: st.speed, skip: st.skip, at: new Date(st.t).toISOString()}),
      destroy() { pause(); clearTimeout(st.idleTimer); document.removeEventListener('keydown', onKey); root.removeEventListener('pointermove', idle); },
    };
    if (opts.autoplay) play();
    return api;
  }
  return {mount, SPEEDS};
}
window.TicoReplay = ticoReplayPlayer();
