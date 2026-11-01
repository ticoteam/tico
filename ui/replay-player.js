/* ui/replay-player.js — The history replay player: bot faces, a task board and an activity feed that play a list of
   history items (GET /api/v2/replay) back at a chosen speed, with play/pause, speeds, skip-quiet and a scrubber.
   It mounts through `window.TicoReplay` and reads no app state: the Replay page (ui/app/replay.js) passes the app's
   faces and names in through `opts`. */
'use strict';
function ticoReplayPlayer() {
  const SPEEDS = [1, 10, 60, 600];
  const QUIET_MS = 1500;          // the longest a quiet stretch lasts, in real time, with Skip quiet on
  const FEED_MAX = 80;
  const DONE_MAX = 12;
  const COLUMNS = [['todo', 'To do', ['open', 'ready']], ['doing', 'Doing', ['doing']],
                   ['waiting', 'Waiting', ['waiting', 'review', 'declined']], ['done', 'Done', ['done', 'closed']]];
  const STATUS_WORDS = {open: 'To do', ready: 'To do', doing: 'Doing', waiting: 'Waiting', review: 'Review',
                        declined: 'Declined', done: 'Done', closed: 'Closed'};
  const ICON_PLAY = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>';
  const ICON_PAUSE = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/></svg>';
  const escape = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const hue = s => { let h = 2166136261; for (const ch of String(s)) h = Math.imul(h ^ ch.codePointAt(0), 16777619); return (h >>> 0) % 360; };
  const initials = s => (String(s || '').split(/[^A-Za-z0-9]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('') || '?').toUpperCase();
  const reduced = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const columnOf = status => (COLUMNS.find(([, , ss]) => ss.includes(status)) || COLUMNS[0])[0];
  const live = state => state === 'running' ? 'running' : ['waiting_human', 'blocked', 'crashed', 'quarantined'].includes(state) ? 'needs'
    : state === 'paused' ? 'paused' : 'idle';

  function mount(root, data, opts = {}) {
    const items = (data.items || []).map((it, i) => ({...it, i, ms: Date.parse(it.at)}));
    const from = Date.parse(data.from), to = Date.parse(data.to);
    const names = data.names || {};
    const name = opts.name || (a => names[a] || String(a || '').replace(/^(bot|human):/, ''));
    const face = opts.face || ((a, size) => `<span class="av av-initials" style="width:${size}px;height:${size}px;--hue:${hue(name(a))};font-size:${Math.round(size * 0.42)}px" aria-hidden="true">${escape(initials(name(a)))}</span>`);
    const st = {t: from, next: 0, playing: false, speed: SPEEDS.includes(+opts.speed) ? +opts.speed : 60, skip: opts.skip !== false,
                tasks: new Map(), bots: new Map(), feed: [], typing: null, raf: 0, last: 0, dirty: {board: true, feed: true, bots: true}, flash: new Set(),
                idleTimer: 0};
    const fmtClock = ms => new Date(ms).toLocaleString(undefined, {weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    const fmtTime = ms => new Date(ms).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'});

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
    const $q = sel => root.querySelector(sel);
    const els = {stamp: $q('.rp-stamp'), bots: $q('.rp-bots'), board: $q('.rp-board'), feed: $q('.rp-feed'), play: $q('.rp-play'), clock: $q('.rp-clock'),
                 range: $q('.rp-range'), ticks: $q('.rp-ticks'), speeds: $q('.rp-speeds'), skip: $q('.rp-skip')};

    // Activity ticks: how much happened in each slice of the range.
    const BUCKETS = 120, counts = new Array(BUCKETS).fill(0);
    for (const it of items) counts[Math.min(BUCKETS - 1, Math.max(0, Math.floor((it.ms - from) / Math.max(1, to - from) * BUCKETS)))]++;
    const peak = Math.max(1, ...counts);
    els.ticks.innerHTML = counts.map(n => `<i style="height:${n ? Math.max(12, Math.round(n / peak * 100)) : 0}%"></i>`).join('');

    // ---- state: apply one item
    function reset() {
      st.tasks = new Map(); st.bots = new Map(); st.feed = []; st.typing = null; st.next = 0; st.flash.clear();
      for (const t of data.start?.tasks || []) st.tasks.set(t.id, {...t, touched: from});
      for (const [slug, b] of Object.entries(data.start?.bots || {})) st.bots.set(slug, {state: b.state, focus: b.focus || ''});
      for (const it of items) if (it.bot && !st.bots.has(it.bot)) st.bots.set(it.bot, {state: 'idle', focus: ''});
      for (const it of items) { const s = String(it.who || ''); if (s.startsWith('bot:') && !st.bots.has(s.slice(4))) st.bots.set(s.slice(4), {state: 'idle', focus: ''}); }
      st.dirty = {board: true, feed: true, bots: true};
    }
    function touch(id) { if (st.tasks.has(id)) { st.tasks.get(id).touched = st.t; st.flash.add(id); st.dirty.board = true; } }
    function say(entry, animate) {
      if (st.typing) { st.typing.shown = st.typing.text.length; st.typing = null; }
      entry.shown = entry.type && animate && !reduced() ? 0 : (entry.text || '').length;
      if (entry.shown < (entry.text || '').length) st.typing = entry;
      st.feed.push(entry);
      if (st.feed.length > FEED_MAX) st.feed.splice(0, st.feed.length - FEED_MAX);
      st.dirty.feed = true;
    }
    function apply(it, animate) {
      const task = it.task?.id ? st.tasks.get(it.task.id) : null;
      const bot = it.bot || (String(it.who || '').startsWith('bot:') ? it.who.slice(4) : '');
      if (it.kind === 'task.created') {
        st.tasks.set(it.task.id, {id: it.task.id, title: it.task.title, owner: it.owner_id, status: it.status || 'open', touched: it.ms, fresh: true});
        st.flash.add(it.task.id); st.dirty.board = true;
        say({kind: 'line', who: it.who, html: `added <b>${escape(it.task.title)}</b>${it.owner_id && it.owner_id !== it.who ? ` for ${escape(name(it.owner_id))}` : ''}`, ms: it.ms}, animate);
      } else if (it.kind === 'task.status') {
        if (task) { task.status = it.new || task.status; touch(task.id); }
        else if (it.task) { st.tasks.set(it.task.id, {id: it.task.id, title: it.task.title, owner: it.who, status: it.new || 'open', touched: it.ms}); touch(it.task.id); }
        say({kind: 'line', who: it.who, html: `moved <b>${escape(it.task.title)}</b> to ${escape(STATUS_WORDS[it.new] || it.new || '')}`, text: it.text, ms: it.ms}, animate);
      } else if (it.kind === 'task.owner') {
        if (task) { task.owner_id = it.new; touch(task.id); }
        say({kind: 'line', who: it.who, html: `gave <b>${escape(it.task.title)}</b> to ${escape(name(it.new))}`, ms: it.ms}, animate);
      } else if (it.kind === 'chat.message' || it.kind === 'task.comment') {
        if (it.task) touch(it.task.id);
        say({kind: 'msg', who: it.who, to: it.to, task: it.task, text: it.text || '', type: String(it.who || '').startsWith('bot:'), ms: it.ms}, animate);
      } else if (it.kind === 'run.started') {
        if (bot) { st.bots.set(bot, {...(st.bots.get(bot) || {}), state: 'running'}); st.dirty.bots = true; }
        if (it.task) touch(it.task.id);
      } else if (it.kind === 'run.finished') {
        if (bot) { const b = st.bots.get(bot) || {}; st.bots.set(bot, {...b, state: b.state === 'running' ? 'idle' : b.state}); st.dirty.bots = true; }
        // A run is one short line: what the bot said in chat is the content, so a summary that repeats it is left out.
        const failed = it.exit && it.exit !== 'completed' && it.exit !== '0';
        const flat = t => String(t || '').replace(/\s+/g, ' ').trim();
        const summary = flat(it.text), head = summary.slice(0, 80);
        const said = summary && st.feed.slice(-30).some(e => e.kind === 'msg' && e.who === it.who && flat(e.text).startsWith(head));
        const note = said || !summary ? '' : summary.length > 140 ? summary.slice(0, 139).trimEnd() + '…' : summary;
        say({kind: 'line', who: it.who, html: (failed ? 'had a run fail' : 'finished a run') + (it.task ? ` on <b>${escape(it.task.title)}</b>` : ''),
             text: note, ms: it.ms}, animate);
      } else if (it.kind === 'bot.status') {
        if (bot) { st.bots.set(bot, {state: it.state, focus: it.text || ''}); st.dirty.bots = true; }
      } else if (it.kind === 'goal.status') {
        say({kind: 'line', who: it.who, html: `marked goal <b>${escape(it.goal.title)}</b> ${escape(String(it.new || '').replace(/_/g, ' '))}`, ms: it.ms}, animate);
      }
    }
    function advance(t, animate) {
      st.t = t;
      while (st.next < items.length && items[st.next].ms <= t) apply(items[st.next++], animate);
    }
    function seek(t) {
      t = Math.max(from, Math.min(to, t));
      if (t < st.t || !st.playing) { reset(); }
      advance(t, false);
      st.t = t; paint(false);
    }

    // ---- painting
    function faceOf(actor, size, state) { return `<span class="rp-face" data-state="${escape(state || '')}">${face(actor, size, state)}</span>`; }
    function paintBots() {
      const rows = [...st.bots.entries()].sort((a, b) => name('bot:' + a[0]).localeCompare(name('bot:' + b[0])));
      els.bots.innerHTML = rows.map(([slug, b]) => {
        const s = live(b.state);
        return `<div class="rp-bot" data-state="${s}" title="${escape(name('bot:' + slug))}${b.focus ? ' · ' + escape(b.focus) : ''}">${faceOf('bot:' + slug, 28, s)}<span class="rp-bot-name">${escape(name('bot:' + slug))}</span></div>`;
      }).join('');
      els.bots.hidden = !rows.length;
    }
    function paintBoard(animate) {
      const before = new Map();
      if (animate && !reduced()) for (const el of els.board.querySelectorAll('[data-rp-task]')) before.set(el.dataset.rpTask, el.getBoundingClientRect());
      const groups = Object.fromEntries(COLUMNS.map(([k]) => [k, []]));
      for (const t of st.tasks.values()) groups[columnOf(t.status)].push(t);
      groups.done.sort((a, b) => b.touched - a.touched); groups.done = groups.done.slice(0, DONE_MAX);
      for (const k of ['todo', 'doing', 'waiting']) groups[k].sort((a, b) => b.touched - a.touched);
      els.board.innerHTML = COLUMNS.map(([k, label]) => `<section class="bcol" data-col="${k}"><header><h2>${label}</h2><span class="cnt tnum">${groups[k].length}</span></header>
        <div class="bcol-body">${groups[k].map(t => `<div class="bcard${st.flash.has(t.id) ? ' rp-flash' : ''}" data-rp-task="${escape(t.id)}">
          <div class="bcard-open"><span class="bcard-title">${escape(t.title)}</span></div>
          <div class="bcard-foot"><span class="rp-owner">${escape(name(t.owner_id))}</span><span class="spacer"></span>${t.owner_id ? faceOf(t.owner_id, 16, st.bots.get(String(t.owner_id).replace(/^bot:/, ''))?.state ? live(st.bots.get(String(t.owner_id).replace(/^bot:/, '')).state) : '') : ''}</div></div>`).join('')}</div></section>`).join('');
      if (before.size) for (const el of els.board.querySelectorAll('[data-rp-task]')) {
        const was = before.get(el.dataset.rpTask), now = el.getBoundingClientRect();
        if (was && (was.left !== now.left || was.top !== now.top)) el.animate([{transform: `translate(${was.left - now.left}px,${was.top - now.top}px)`}, {transform: 'none'}], {duration: 420, easing: 'cubic-bezier(.2,0,0,1)'});
        else if (!was) el.animate([{opacity: 0, transform: 'scale(.96)'}, {opacity: 1, transform: 'none'}], {duration: 260, easing: 'cubic-bezier(0,0,.2,1)'});
      }
      st.flash.clear();
    }
    function entryHTML(e) {
      const text = (e.text || '').slice(0, e.shown);
      const typing = e === st.typing ? '<span class="rp-caret" aria-hidden="true"></span>' : '';
      const when = `<span class="rp-when tnum">${fmtTime(e.ms)}</span>`;
      if (e.kind === 'line') return `<div class="rp-line">${faceOf(e.who, 18)}<span class="rp-line-text"><b>${escape(name(e.who))}</b> ${e.html}${e.text ? `<span class="rp-line-note">${escape(e.text)}</span>` : ''}</span>${when}</div>`;
      const head = `<b>${escape(name(e.who))}</b>${e.to && !e.task ? ` → ${escape(name(e.to))}` : ''}${e.task ? ` · <span class="rp-task">${escape(e.task.title)}</span>` : ''}`;
      return `<div class="rp-msg${e.type ? ' bot' : ' you'}">${faceOf(e.who, 22)}<div class="rp-msg-body"><div class="rp-msg-head">${head}${when}</div><div class="rp-msg-text">${escape(text)}${typing}</div></div></div>`;
    }
    // A light divider wherever the day changes, so a range of several days never reads out of order.
    const dayOf = ms => new Date(ms).toDateString();
    const several = dayOf(from) !== dayOf(to - 1);
    function paintFeed() {
      let day = '';
      els.feed.innerHTML = st.feed.map((e, i) => {
        const d = dayOf(e.ms), divider = d !== day && (i > 0 || several)
          ? `<div class="rp-day">${escape(new Date(e.ms).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}))}</div>` : '';
        day = d;
        return divider + entryHTML(e);
      }).join('') || '<p class="rp-empty">Nothing yet</p>';
      els.feed.scrollTop = els.feed.scrollHeight;
    }
    function paintTyping() {
      const last = els.feed.lastElementChild?.querySelector('.rp-msg-text');
      if (!last || !st.typing) return;
      last.innerHTML = escape(st.typing.text.slice(0, st.typing.shown)) + (st.typing.shown < st.typing.text.length ? '<span class="rp-caret" aria-hidden="true"></span>' : '');
      els.feed.scrollTop = els.feed.scrollHeight;
    }
    function paintBar() {
      els.play.innerHTML = st.playing ? ICON_PAUSE : ICON_PLAY;
      els.play.setAttribute('aria-label', st.playing ? 'Pause' : 'Play');
      els.clock.textContent = els.stamp.textContent = fmtClock(st.t);
      els.range.value = String(Math.round((st.t - from) / Math.max(1, to - from) * 1000));
      for (const b of els.speeds.querySelectorAll('[data-speed]')) b.setAttribute('aria-pressed', String(+b.dataset.speed === st.speed));
      els.skip.setAttribute('aria-pressed', String(st.skip));
      root.classList.toggle('playing', st.playing);
    }
    function paint(animate) {
      if (st.dirty.bots) paintBots();
      if (st.dirty.board) paintBoard(animate);
      if (st.dirty.feed) paintFeed();
      st.dirty = {board: false, feed: false, bots: false};
      paintBar();
    }

    // ---- the clock
    function frame(now) {
      if (!st.playing) return;
      const dt = Math.min(250, now - (st.last || now)); st.last = now;
      const prev = st.next > 0 ? items[st.next - 1].ms : from, next = st.next < items.length ? items[st.next].ms : to;
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
    function play() {
      if (st.playing) return;
      if (st.t >= to) seek(from);
      st.playing = true; st.last = 0; st.raf = requestAnimationFrame(frame); paintBar(); idle(); opts.onChange?.(api.state());
    }
    function pause() { st.playing = false; cancelAnimationFrame(st.raf); if (st.typing) { st.typing.shown = st.typing.text.length; st.typing = null; st.dirty.feed = true; } paint(false); opts.onChange?.(api.state()); }
    function step(dir) {
      pause();
      if (dir > 0) { const it = items[st.next]; seek(it ? it.ms : to); }
      else { const it = items[st.next - 2]; seek(it ? it.ms : from); }
    }
    function setSpeed(s) { if (SPEEDS.includes(+s)) { st.speed = +s; paintBar(); opts.onChange?.(api.state()); } }
    function setSkip(on) { st.skip = !!on; paintBar(); opts.onChange?.(api.state()); }
    // Controls fade while playing when asked (presentation), and come back on any pointer movement.
    function idle() {
      root.classList.remove('rp-idle'); clearTimeout(st.idleTimer);
      if (st.playing) st.idleTimer = setTimeout(() => root.classList.add('rp-idle'), 1800);
    }

    els.play.onclick = () => st.playing ? pause() : play();
    els.speeds.onclick = ev => { const b = ev.target.closest('[data-speed]'); if (b) setSpeed(b.dataset.speed); };
    els.skip.onclick = () => setSkip(!st.skip);
    els.range.oninput = () => { const wasPlaying = st.playing; if (wasPlaying) { st.playing = false; cancelAnimationFrame(st.raf); } seek(from + (+els.range.value / 1000) * (to - from)); if (wasPlaying) play(); };
    root.addEventListener('pointermove', idle);
    const onKey = ev => {
      if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || !root.isConnected) return;
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(ev.target?.tagName || '') && ev.target !== els.range) return;
      if (ev.target?.isContentEditable || document.querySelector('dialog[open]')) return;
      if (ev.key === ' ' || ev.code === 'Space') { ev.preventDefault(); st.playing ? pause() : play(); }
      else if (ev.key === 'ArrowRight') { ev.preventDefault(); step(1); }
      else if (ev.key === 'ArrowLeft') { ev.preventDefault(); step(-1); }
      else if (/^[1-4]$/.test(ev.key)) setSpeed(SPEEDS[+ev.key - 1]);
    };
    document.addEventListener('keydown', onKey);

    reset(); paint(false);
    if (opts.at) seek(Date.parse(opts.at));
    const api = {
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
