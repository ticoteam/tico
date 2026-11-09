// Replay on the campus: plays history items (GET /api/v2/replay, fetched by the page around this frame, so it
// carries only what the viewer may see) against the campus. Each bot's desk shows its state as of the replay
// time, a message or a handoff between two teammates draws a link between their desks, and a short caption
// feed says what is happening. Returning to live hands the scene back to the current status.
// One history item as GET /api/v2/replay gives it: the fields this file reads.
interface ReplayItem {
  at: string;
  kind: string;
  who?: string | undefined;
  to?: string | undefined;
  new?: string | undefined;
  bot?: string | undefined;
  state?: string | undefined;
  text?: string | undefined;
  exit?: string | number | undefined;
  owner_id?: string | undefined;
  task?: { title?: string | undefined } | undefined;
  goal?: { title?: string | undefined } | undefined;
}
// An item with its time read as milliseconds.
interface ReplayTimed extends ReplayItem { ms: number }
// A bot's desk at the replay's start.
interface ReplayBot { state?: string | undefined }
// The replay as the page fetched it: its window, the desks at its start and the items in it.
interface ReplayData {
  from: string;
  to: string;
  next?: string | undefined;
  truncated?: boolean | undefined;
  items?: ReplayItem[] | undefined;
  start?: { bots?: Record<string, ReplayBot> | undefined } | undefined;
}
// What one link between two desks takes: its two ends and what joins them.
type ReplayLink = [string, string, string];
// The scene the replay draws on (the page's campus); each hook may be missing.
interface ReplayScene {
  link?: ((from: string, to: string, kind: string) => void) | undefined;
  clearLinks?: (() => void) | undefined;
}
// A caption in the feed: its text, its time, and whether it is new since the last frame.
interface ReplayCaption { text: string; ms: number; fresh: boolean }
// The player's own state: playing or not, the speed, the animation frame and its last time, the captions.
interface ReplayState { playing: boolean; speed: number; raf: number; last: number; captions: ReplayCaption[] }
// What createReplay is given: the names and words of the page, the scene, and what it calls back.
interface ReplayOptions {
  names?: Record<string, string> | undefined;
  words?: Record<string, string> | undefined;
  scene: () => ReplayScene | null | undefined;
  onState: () => void;
  onEnd?: (() => void) | undefined;
  reduced: () => boolean;
}

const SPEEDS = [60, 600, 3600];
const QUIET_MS = 1200;      // the longest a quiet stretch lasts in real time
const LINKS_PER_FRAME = 4;  // a burst still reads as a burst without flooding the sky
const CAPTIONS = 4;
const STATUS_WORDS: Record<string, string> = {open: 'To do', ready: 'To do', doing: 'Doing', waiting: 'Waiting', review: 'Review',
                      declined: 'Declined', done: 'Done', closed: 'Closed'};
const NEEDS: (string | undefined)[] = ['waiting_human', 'blocked', 'crashed', 'quarantined'];
const ICON_PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/></svg>';

// The campus's own vocabulary: running, needs (a person), paused or idle.
export const deskState = (state: string | undefined) => state === 'running' ? 'running' : NEEDS.includes(state) ? 'needs' : state === 'paused' ? 'paused' : 'idle';
const clip = (s: string | undefined, n: number) => { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s; };

// The state machine, without any page: what each bot was doing at a moment, and what each item says.
export function replayTimeline(data: ReplayData, names: Record<string, string> = {}) {
  const items: ReplayTimed[] = (data.items || []).map(it => ({...it, ms: Date.parse(it.at)})).filter(it => Number.isFinite(it.ms)).sort((a, b) => a.ms - b.ms);
  // A cut-short day ends at its cutoff: nothing after it was read, so the desks would only look frozen.
  const from = Date.parse(data.from), to = Date.parse(data.truncated && data.next ? data.next : data.to);
  const name = (a: string | undefined) => names[String(a)] || String(a || '').replace(/^(bot|human):/, '');
  const botOf = (it: ReplayItem) => it.bot || (String(it.who || '').startsWith('bot:') ? String(it.who).slice(4) : '');
  let bots = new Map<string, string | undefined>(), next = 0, t = from;
  function reset() {
    bots = new Map(Object.entries<ReplayBot>(data.start?.bots || {}).map(([slug, b]): [string, string | undefined] => [slug, b.state]));
    next = 0; t = from;
  }
  // A link joins two different teammates: a message's sender and its addressee, or a task's old and new owner.
  const linkOf = (it: ReplayItem): ReplayLink | null => (it.kind === 'chat.message' || it.kind === 'task.comment') && it.who && it.to && it.who !== it.to ? [it.who, it.to, 'message']
    : it.kind === 'task.owner' && it.who && it.new && it.who !== it.new ? [it.who, it.new, 'handoff'] : null;
  function caption(it: ReplayItem) {
    const who = name(it.who), task = it.task?.title ? `“${clip(it.task.title, 60)}”` : '';
    switch (it.kind) {
      case 'chat.message': case 'task.comment':
        return `${who}${it.to ? ' → ' + name(it.to) : ''}${task ? ' on ' + task : ''}: ${clip(it.text, 90)}`;
      case 'task.created': return `${who} added ${task}${it.owner_id && it.owner_id !== it.who ? ' for ' + name(it.owner_id) : ''}`;
      case 'task.status': return `${who} moved ${task} to ${STATUS_WORDS[String(it.new)] || it.new || ''}`;
      case 'task.owner': return `${who} handed ${task} to ${name(it.new)}`;
      case 'run.started': return `${name('bot:' + botOf(it))} started work${task ? ' on ' + task : ''}`;
      case 'run.finished': return `${name('bot:' + botOf(it))} ${it.exit && !['completed', '0'].includes(String(it.exit)) ? 'had a run fail' : 'finished a run'}${task ? ' on ' + task : ''}`;
      case 'bot.status': return deskState(it.state) === 'needs' ? `${name('bot:' + botOf(it))} needs a person` : '';
      case 'goal.status': return `${who} marked goal “${clip(it.goal?.title, 60)}” ${String(it.new || '').replace(/_/g, ' ')}`;
      default: return '';
    }
  }
  function apply(it: ReplayItem) {
    const bot = botOf(it);
    if (!bot) return;
    if (it.kind === 'run.started') bots.set(bot, 'running');
    else if (it.kind === 'run.finished' && bots.get(bot) === 'running') bots.set(bot, 'idle');
    else if (it.kind === 'bot.status') bots.set(bot, it.state);
  }
  // Moves the clock to `until` and returns the items it passed, in order.
  function advance(until: number) {
    const passed: ReplayTimed[] = [];
    while (next < items.length) {
      const it = items[next];
      if (!it || it.ms > until) break;
      apply(it); passed.push(it); next++;
    }
    t = until; return passed;
  }
  reset();
  return {from, to, items, reset, advance, caption, linkOf, get t() { return t; }, get next() { return next; }, bots: () => bots};
}

// The controls and clock. `scene()` is the current renderer (it is rebuilt when the team changes shape),
// `onState()` is called when any desk changes, and `onEnd()` when playback reaches the end on its own.
export function createReplay(root: ParentNode, data: ReplayData, {names = {}, words = {}, scene, onState, onEnd, reduced}: ReplayOptions) {
  const line = replayTimeline(data, names);
  const {from, to, items} = line;
  const st: ReplayState = {playing: false, speed: 600, raf: 0, last: 0, captions: []};
  // A control the page must have: a missing one fails at once, as the first use of it always did.
  const need = <T extends Element>(s: string) => {
    const el = root.querySelector<T>(s);
    if (!el) throw new TypeError(`${s} is missing from the replay`);
    return el;
  };
  const els = {play: need<HTMLButtonElement>('#replay-play'), clock: need<HTMLElement>('#replay-clock'), range: need<HTMLInputElement>('#replay-range'), speeds: need<HTMLElement>('#replay-speeds'),
               captions: need<HTMLElement>('#replay-captions'), note: need<HTMLElement>('#replay-note'), live: root.querySelector<HTMLElement>('#replay-live')};
  const clock = (ms: number) => new Date(ms).toLocaleString(undefined, {weekday: 'short', hour: '2-digit', minute: '2-digit'});
  els.speeds.innerHTML = SPEEDS.map(s => `<button type="button" data-speed="${s}">${s}×</button>`).join('');
  els.note.hidden = !data.truncated && items.length > 0;
  els.note.textContent = !items.length ? 'Nothing happened in the last 24 hours that you can see.'
    : data.truncated ? `This replay stops at ${clock(to)}: the day had more than it can show.` : '';

  function captionsFrom(passed: ReplayTimed[], animate: boolean) {
    for (const it of passed) {
      const text = line.caption(it);
      if (text) st.captions.push({text, ms: it.ms, fresh: animate});
    }
    if (st.captions.length > CAPTIONS) st.captions.splice(0, st.captions.length - CAPTIONS);
    els.captions.replaceChildren(...st.captions.map(c => {
      const li = document.createElement('li'), when = document.createElement('time');
      when.textContent = new Date(c.ms).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'});
      li.append(when, document.createTextNode(c.text));
      if (c.fresh && !reduced()) li.className = 'fresh';
      c.fresh = false; return li;
    }));
  }
  function bar() {
    els.play.innerHTML = st.playing ? ICON_PAUSE : ICON_PLAY;
    els.play.setAttribute('aria-label', st.playing ? 'Pause' : 'Play');
    els.clock.textContent = clock(line.t);
    els.range.value = String(Math.round((line.t - from) / Math.max(1, to - from) * 1000));
    els.range.setAttribute('aria-valuetext', clock(line.t));
    for (const b of els.speeds.querySelectorAll<HTMLElement>('[data-speed]')) b.setAttribute('aria-pressed', String(Number(b.dataset.speed) === st.speed));
  }
  function step(until: number, animate: boolean) {
    const passed = line.advance(until);
    if (!passed.length) return;
    if (animate) for (const l of passed.map(line.linkOf).filter((l): l is ReplayLink => !!l).slice(-LINKS_PER_FRAME)) scene()?.link?.(...l);
    captionsFrom(passed, animate);
    if (passed.some(it => it.kind.startsWith('run.') || it.kind === 'bot.status')) onState();
  }
  function seek(t: number) {
    t = Math.max(from, Math.min(to, t));
    scene()?.clearLinks?.();
    line.reset(); st.captions = [];
    captionsFrom(line.advance(t), false); onState(); bar();
  }
  function frame(now: number) {
    if (!st.playing) return;
    const dt = Math.min(250, now - (st.last || now)); st.last = now;
    // Quiet stretches are compressed so the recap keeps moving.
    const prevItem = line.next > 0 ? items[line.next - 1] : undefined, comingItem = line.next < items.length ? items[line.next] : undefined;
    const prev = prevItem ? prevItem.ms : from, coming = comingItem ? comingItem.ms : to;
    const gap = Math.max(0, coming - Math.max(prev, from));
    const rate = gap / st.speed > QUIET_MS ? gap / QUIET_MS : st.speed;
    step(Math.min(to, line.t + dt * rate), true);
    if (line.t >= to) { st.playing = false; bar(); onEnd?.(); return; }
    bar(); st.raf = requestAnimationFrame(frame);
  }
  function play() { if (st.playing) return; if (line.t >= to) seek(from); st.playing = true; st.last = 0; st.raf = requestAnimationFrame(frame); bar(); }
  function pause() { st.playing = false; cancelAnimationFrame(st.raf); bar(); }
  els.play.onclick = () => st.playing ? pause() : play();
  els.speeds.onclick = ev => { const b = eventElement(ev)?.closest<HTMLElement>('[data-speed]'); if (b) { st.speed = Number(b.dataset.speed); bar(); } };
  els.range.oninput = () => { const at = from + (+els.range.value / 1000) * (to - from), was = st.playing; pause(); seek(at); if (was && line.t < to) play(); };

  seek(from);
  return {
    play, pause, seek,
    // The desk state for a bot as of the replay time; null for one the replay knows nothing about.
    desk(slug: string) {
      const state = line.bots().get(slug);
      return state ? {state: deskState(state), detail: words[state] || (state === 'running' ? 'Running' : 'Idle')} : null;
    },
    state: () => ({playing: st.playing, speed: st.speed, at: new Date(line.t).toISOString()}),
    destroy() { pause(); scene()?.clearLinks?.(); },
  };
}
