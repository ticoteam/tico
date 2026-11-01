// Tico Recorder's window. It asks `view` for what to draw when the app says something changed
// (no polling), and calls one command per action;
// all state that matters lives in the Rust app. Regions re-render only when their HTML changes,
// and inputs keep their own values, so typing is never interrupted by a refresh.
(() => {
  'use strict';
  const invoke = (cmd, args = {}) => window.__TAURI__.core.invoke(cmd, args);
  const esc = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const icon = window.icon;

  const S = {
    page: 'library',
    status: {},
    library: null,
    meeting: null,
    transcript: null,
    settings: null,
    hash: { library: '', meeting: '', transcript: '', settings: '' },
    statusKey: '',
    ui: {
      search: '', ask: '', addItem: '', peopleQuery: '', ownerQuery: '', tq: '', signin: null, legacy: '', key: '', xai: '',
      editLine: null, editDraft: '', editInitial: '', removeLine: null, // { id, seq }: a draft or question belongs to one meeting
      hot: 0, mention: null,
      panel: null, opened: null, pop: null, tsearch: false, showAudio: false, confirm: null,
      form: null, saving: false, titleDraft: null, notesDraft: null, prevPage: 'library', stick: true,
    },
  };
  const regions = {};

  // ---- Formatting ----
  const pad = (n) => String(n).padStart(2, '0');
  function clock(ms) {
    const s = Math.floor(ms / 1000);
    return s >= 3600 ? `${Math.floor(s / 3600)}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}` : `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
  }
  function length(ms) {
    const s = Math.floor(ms / 1000);
    if (s >= 3600) return `${Math.floor(s / 3600)}h ${pad(Math.floor(s / 60) % 60)}m`;
    if (s >= 60) return `${Math.floor(s / 60)} min`;
    return s > 0 ? `${s} sec` : '';
  }
  const timeOf = (date) => date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  function dayOf(date) {
    const today = new Date();
    const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((start(today) - start(date)) / 86400000);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days > 1 && days < 7) return date.toLocaleDateString([], { weekday: 'long' });
    const sameYear = date.getFullYear() === today.getFullYear();
    return date.toLocaleDateString([], sameYear ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function fullDate(iso) {
    const date = new Date(iso);
    if (isNaN(date)) return '';
    const day = dayOf(date);
    return `${day === 'Today' || day === 'Yesterday' ? day : date.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}, ${timeOf(date)}`;
  }
  const initials = (name) =>
    String(name || '?').replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?';
  function avatar(row, extra = '') {
    if (row.kind === 'bot' || row.bot) return `<span class="avatar bot ${extra}">${icon('bot', 14)}</span>`;
    if (row.me) return `<span class="avatar me ${extra}">${esc(initials('Me'))}</span>`;
    let hue = 0;
    for (const c of String(row.name)) hue = (hue * 31 + c.charCodeAt(0)) % 360;
    const letters = extra.includes('small') ? initials(row.name).slice(0, 1) : initials(row.name);
    return `<span class="avatar ${extra}" style="background:hsl(${hue} 32% 52%)">${esc(letters)}</span>`;
  }
  const badge = (label, tone, extra = '') => `<span class="badge ${tone}" ${extra}>${label}</span>`;

  // ---- Models: inputs read and write these; values are set after rendering, never in HTML ----
  const models = {
    title: [() => S.ui.titleDraft ?? S.meeting?.title ?? '', (v) => (S.ui.titleDraft = v)],
    notes: [() => S.ui.notesDraft ?? S.meeting?.notes.text ?? '', (v) => (S.ui.notesDraft = v)],
    signin: [() => S.ui.signin ?? S.status.suggested ?? '', (v) => (S.ui.signin = v)],
  };
  const model = (name) => {
    if (models[name]) return models[name][0]();
    if (name.startsWith('form.')) return S.ui.form?.[name.slice(5)] ?? '';
    return S.ui[name] ?? '';
  };
  function setModel(name, value) {
    if (models[name]) models[name][1](value);
    else if (name.startsWith('form.')) S.ui.form[name.slice(5)] = value;
    else S.ui[name] = value;
  }

  // ---- Updates: the app emits `changed` when the view may differ and `level` while recording ----
  let inflight = null;
  let queued = null;
  /** Fetches what changed; calls made while one is in flight share a single follow-up. */
  function poll() {
    if (inflight) {
      queued = queued || inflight.then(() => { queued = null; return poll(); });
      return queued;
    }
    inflight = refresh().finally(() => { inflight = null; });
    return inflight;
  }
  /** The clock and level bars, ten times a second while recording, without any redraw. */
  function onLevel(payload) {
    const rec = S.status.recording;
    if (!rec || rec.id !== payload.id || document.hidden) return;
    rec.elapsed_ms = payload.elapsed_ms;
    for (const src of rec.sources) {
      const now = payload.sources.find((row) => row.channel === src.channel);
      if (now) src.level = now.level;
    }
    live();
  }
  function statusKey(status) {
    const rec = status.recording;
    return JSON.stringify({ ...status, recording: rec && { id: rec.id, phase: rec.phase, error: rec.error, n: rec.sources.map((s) => s.label + s.no_signal) } });
  }
  async function refresh() {
    try {
      const v = await invoke('view', { have: S.hash });
      let changed = false;
      for (const part of ['library', 'meeting', 'transcript', 'settings']) {
        if (v[part]) {
          S[part] = v[part].data;
          S.hash[part] = v[part].hash;
          changed = true;
        }
      }
      S.status = v.status;
      S.live = v.live || null;
      window.ticoUpdate?.(v.status.update);
      // Profiling builds open the transcript of the test recording once.
      if (S.status.profile && S.status.recording && !S.ui.profiled) { S.ui.profiled = true; S.page = 'meeting'; S.ui.panel = 'transcript'; }
      const key = statusKey(v.status) + JSON.stringify(S.live?.notices || []);
      if (key !== S.statusKey) {
        S.statusKey = key;
        changed = true;
      }
      if (S.page === 'meeting' && !S.status.meeting) {
        S.page = 'library';
        S.ui.panel = null;
        S.ui.pop = null;
        changed = true;
      }
      if (S.ui.saving && !S.status.busy) {
        S.ui.saving = false;
        if (!S.status.error) {
          S.ui.form = null;
          S.ui.key = '';
          S.ui.xai = '';
        }
        changed = true;
      }
      if (changed) render();
      else live();
    } catch (error) {
      console.error(error);
      bootError(error);
    }
  }
  // Before the first view arrives a failure would leave an empty window; say what went wrong.
  function bootError(error) {
    const root = document.getElementById('app');
    if (!root || root.dataset.ready) return;
    root.innerHTML = `<div class="boot-error">${esc(error?.message || error)}</div>`;
  }
  async function act(cmd, args) {
    try {
      const result = await invoke(cmd, args);
      await poll();
      return result;
    } catch (error) {
      console.error(error);
    }
  }

  // ---- Top bar ----
  function topbar() {
    const st = S.status;
    let left = '';
    let right = '';
    if (S.page !== 'library') {
      const label = S.page === 'settings' && S.ui.prevPage === 'meeting' ? 'Back' : 'Recordings';
      left = `<button class="pill-back" data-act="back" title="Back (Esc)">${icon('back', 14)}${label}</button>`;
    }
    if (st.demo) right += badge('Demo library', 'amber');
    if (S.page === 'meeting' && S.meeting) {
      const m = S.meeting;
      // Send does every step itself (stop, last words, finish the live meeting); the button shows the step.
      const sending = st.sending?.id === m.id ? st.sending : null;
      if (m.can_finish && !sending) right += `<button class="btn small" data-act="finish" ${st.busy ? 'disabled' : ''}>Finish meeting</button>`;
      right += peopleButton(m.people);
      if (m.sent) right += badge(`${icon('check', 12)}In Tico`, 'accent');
      else if (sending) right += `<button class="btn small sending" disabled><span class="spin"></span>${esc(sending.label)}</button>`;
      else right += `<button class="btn small" data-act="pop" data-pop="send" data-id="${esc(m.id)}" ${st.busy ? 'disabled' : ''}>${icon('send', 13)}Send</button>`;
      right += `<button class="icon-btn ${S.ui.pop?.kind === 'more' ? 'on' : ''}" data-act="pop" data-pop="more" title="More" aria-label="More">${icon('more')}</button>`;
    }
    if (S.page === 'library') right += `<button class="icon-btn" data-act="settings" title="Settings (⌘,)" aria-label="Settings">${icon('gear')}</button>`;
    return `<header class="topbar" data-tauri-drag-region>${left}<div class="spacer" data-tauri-drag-region></div>${right}</header>`;
  }
  function peopleButton(p) {
    const shown = p.rows.slice(0, 3).map((row) => avatar(row, 'small')).join('');
    const more = p.rows.length > 3 ? `<span class="more">+${p.rows.length - 3}</span>` : '';
    const on = S.ui.pop?.kind === 'people' ? 'on' : '';
    return `<button class="people-btn ${on}" data-act="pop" data-pop="people" title="People" aria-label="People"><span class="avatars">${shown}</span>${more}</button>`;
  }

  // ---- Notices ----
  function errorNotice() {
    const error = S.status.error;
    if (!error) return '';
    return `<div class="notice">${icon('alert')}<span class="grow selectable">${esc(error)}</span><button class="icon-btn dismiss" data-act="dismiss-error" title="Dismiss" aria-label="Dismiss">${icon('close', 12)}</button></div>`;
  }

  // ---- Library ----
  function library() {
    const lib = S.library;
    if (!lib) return '';
    const st = S.status;
    const q = S.ui.search.trim().toLowerCase();
    const rows = lib.rows.filter((row) => !q || row.title.toLowerCase().includes(q));
    let html = `<div class="column"><div class="lib-head"><h1 class="page-title">Recordings</h1>`;
    if (lib.rows.length) html += `<label class="search">${icon('search', 14)}<input data-model="search" placeholder="Search" spellcheck="false"></label>`;
    html += `</div>${errorNotice()}`;
    if (st.first_run) {
      // First run: sign in to a Tico, then macOS asks for the mic and meeting audio on the first recording.
      const signin = st.signing_in
        ? `<div class="waiting"><span class="spin"></span>Choose Allow in your browser…</div>`
        : `<div class="signin-row"><input class="field" data-model="signin" data-enter="signin" placeholder="your-tico.example" spellcheck="false" aria-label="Your Tico's address"><button class="btn primary" data-act="signin" ${st.busy ? 'disabled' : ''}>Sign in</button></div>`;
      html += `<div class="card setup"><div class="step"><span class="step-mark">${ticoDot()}</span><div class="grow"><div class="step-title">Sign in with Tico</div>${signin}</div></div>`;
      html += `<div class="step"><span class="step-mark">${icon('mic', 16)}</span><div class="grow step-line"><div class="step-title">Microphone and meeting audio</div><span class="faint">Asked on first recording</span></div></div></div>`;
    }
    if (!st.recording && lib.events.length && !q) {
      html += `<div class="group-label">Coming up${lib.calendar_stale ? ` · <span style="color:var(--amber)">Calendar out of date</span>` : ''}</div>`;
      html += lib.events.map((event, index) => {
        const start = new Date(event.start);
        return `<div class="event"><span class="time">${isNaN(start) ? '' : esc(timeOf(start))}</span><span class="name">${esc(event.title || 'Meeting')}</span><button class="btn small" data-act="record-event" data-index="${index}">${icon('mic', 13)}Record</button></div>`;
      }).join('');
    }
    if (!lib.rows.length) {
      html += `<div class="empty">${icon('wave', 34)}<div class="big">No recordings yet</div>Audio stays on this Mac until you send a meeting.</div>`;
    } else if (!rows.length) {
      html += `<div class="empty"><div class="big">No matches</div></div>`;
    }
    let day = '';
    let open = false;
    for (const row of rows) {
      const date = new Date(row.started_at);
      const label = isNaN(date) ? '' : dayOf(date);
      if (label !== day) {
        if (open) html += '</div>';
        html += `<div class="group-label">${esc(label)}</div><div class="rows">`;
        day = label;
        open = true;
      }
      html += libraryRow(row, date);
    }
    if (open) html += '</div>';
    return html + '</div>';
  }
  function libraryRow(row, date) {
    const id = esc(row.id);
    if (S.ui.confirm === row.id) {
      return `<div class="row confirm"><div class="tile">${icon('trash', 17)}</div><div class="body"><div class="title">Delete “${esc(row.title)}”?</div><div class="sub">Removes its audio and transcript from this Mac.</div></div><div class="tail"><button class="btn small quiet" data-act="cancel-delete">Cancel</button><button class="btn small danger" data-act="delete" data-id="${id}">Delete</button></div></div>`;
    }
    const status = row.status;
    let statusHtml = '';
    if (status) {
      const lead = status.label === 'Recording' ? '<i class="pulse"></i>' : '';
      statusHtml = badge(lead + esc(status.label), status.tone, row.sync_error ? `title="${esc(row.sync_error)}"` : '');
    }
    const menu = S.ui.pop?.anchorId === row.id ? 'menu-open' : '';
    const sub = [isNaN(date) ? '' : timeOf(date), length(row.duration_ms)].filter(Boolean).map(esc).join(' · ');
    return `<div class="row ${menu}" data-act="open" data-id="${id}" role="button" tabindex="0" aria-label="${esc(row.title)}">
      <div class="tile ${row.recording ? 'live' : ''}">${icon('wave', 18)}</div>
      <div class="body"><div class="title">${esc(row.title)}</div><div class="sub">${sub}</div></div>
      <div class="tail">${row.can_send ? `<button class="btn small hover-only" data-act="pop" data-pop="send" data-id="${id}">Send</button>` : ''}${row.recording ? '' : `<button class="icon-btn trash hover-only" data-act="ask-delete" data-id="${id}" title="Delete" aria-label="Delete ${esc(row.title)}">${icon('trash', 15)}</button>`}${statusHtml}</div>
    </div>`;
  }

  // ---- Meeting ----
  function meeting() {
    const m = S.meeting;
    if (!m) return '';
    let meta = [esc(fullDate(m.started_at))];
    if (m.duration_ms) meta.push(esc(length(m.duration_ms)));
    let html = `<div class="column"><input class="title-input" data-model="title" data-enter="rename" data-blur="rename" spellcheck="false" ${m.title_editable ? '' : 'disabled'} aria-label="Title">`;
    html += `<div class="meta">${meta.join('<span class="sep"></span>')}<span class="sep"></span>${visibility(m)}`;
    if (m.live_url) html += `<span class="sep"></span><button class="link" data-act="open-url" data-url="${esc(m.live_url)}">Open in Tico</button>`;
    html += `</div>`;
    if (m.sync_error) html += `<div class="notice">${icon('alert')}<span class="grow selectable">${esc(m.sync_error)}</span></div>`;
    for (const note of m.setup) html += `<div class="notice">${icon('alert')}<span class="grow">${esc(note)}</span>${S.page === 'meeting' ? `<button class="link" data-act="settings">Settings</button>` : ''}</div>`;
    html += errorNotice();
    html += actions(m.actions);
    const notes = m.notes;
    html += `<section class="section"><div class="section-head"><h2>Notes</h2></div><div class="notes-wrap"><div class="notes-backdrop" aria-hidden="true"></div><textarea class="notes" data-model="notes" data-defer placeholder="Write notes… @ to mention" ${notes.editable ? '' : 'disabled'} aria-label="Notes"></textarea></div></section>`;
    html += chatSection(m.chat);
    return html + '</div>';
  }
  /** The meeting's chat: people, bots and private questions to Tico, in time order, with bot-turn notices between. */
  function chatSection(c) {
    const notices = liveNotices(S.meeting);
    let html = `<section class="section chat-section" id="chat"><div class="section-head"><h2>Chat</h2></div>`;
    if (!c.messages.length && !c.replying.length && !notices.length) html += `<div class="chat-empty">Bots you add can reply here.</div>`;
    let last = null;
    let n = 0;
    // Notices are newest last, like the messages; each goes in before the first message after it.
    const flush = (until) => {
      while (n < notices.length && !(notices[n].at > until)) {
        html += chatNotice(notices[n++]);
        last = null;
      }
    };
    for (const msg of c.messages) {
      const date = new Date(msg.time);
      if (!isNaN(date)) flush(date.getTime());
      const cont = last && last.name === msg.name && last.private === msg.private && !msg.pending;
      const head = cont ? '' : `<div class="msg-head"><b>${esc(msg.name)}</b>${msg.bot && !msg.private ? '<span class="tag">Bot</span>' : ''}${msg.private ? `<span class="tag private">${icon('lock', 11)}Only you</span>` : ''}${msg.time && !isNaN(date) ? `<span class="time">${esc(timeOf(date))}</span>` : ''}</div>`;
      const who = msg.private && !msg.me ? ticoDot() : avatar({ name: msg.name, bot: msg.bot, me: msg.me });
      const text = esc(msg.text).split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
      html += `<div class="msg ${msg.private ? 'private' : ''} ${msg.pending ? 'pending' : ''} ${cont ? 'cont' : ''}">${cont ? '<span class="avatar-gap"></span>' : who}<div class="msg-body">${head}<div class="msg-text selectable">${text}</div></div></div>`;
      last = msg;
    }
    flush(Infinity);
    const typing = '<span class="dots"><i></i><i></i><i></i></span>';
    for (const name of c.replying) html += `<div class="msg replying">${avatar({ name, bot: true })}<div class="msg-body"><div class="msg-line"><b>${esc(name)}</b> is replying${typing}</div></div></div>`;
    if (c.thinking) html += `<div class="msg replying private">${ticoDot(true)}<div class="msg-body"><div class="msg-line"><b>Tico</b> is answering privately${typing}</div></div></div>`;
    if (c.error) html += `<div class="notice inline">${icon('alert')}<span class="grow selectable">${esc(c.error)}</span>${c.retry && c.ready ? `<button class="link" data-act="chat-retry">Retry</button>` : ''}</div>`;
    return html + '</section>';
  }
  /** Tico's own mark: the Liquid Dot, flat. Bots get the bot avatar, never the dot. */
  const ticoDot = (thinking = false) => `<span class="avatar tico" aria-label="Tico"><i class="ldot ${thinking ? 'thinking' : ''}"></i></span>`;
  /** What the meeting's live state reads as: a string today, an object with a label if it grows one. */
  const liveLabel = (m) => (typeof m.live === 'string' ? m.live : m.live?.label || null);
  /** Bot turns that failed, wait, were put off or were retried (`live.notices`), newest last. */
  function liveNotices(m) {
    if (!m) return [];
    const list = S.live?.notices || (m.live && typeof m.live === 'object' ? m.live.notices : null) || [];
    return Array.isArray(list) ? list : [];
  }
  /** One quiet line in the chat: a failed turn in the warning tone, the rest muted. */
  function chatNotice(notice) {
    const failed = notice.kind === 'failed';
    const glyph = failed ? 'alert' : notice.kind === 'retried' ? 'retry' : 'clock';
    const bot = String(notice.bot || '');
    const text = String(notice.text || '');
    // The sentence usually names the bot already; only add the name when it does not.
    const lead = bot && !text.startsWith(bot) ? `<b>${esc(bot)}</b> · ` : '';
    const at = new Date(notice.at);
    return `<div class="chat-notice ${failed ? 'failed' : ''}" role="status">${icon(glyph, 13)}<span class="grow selectable">${lead}${esc(text)}</span>${notice.at && !isNaN(at) ? `<span class="time">${esc(timeOf(at))}</span>` : ''}</div>`;
  }
  function visibility(m) {
    const live = liveLabel(m);
    if (!live) return badge(`${icon('lock', 12)}Private`, 'muted', 'title="On this Mac only"');
    const team = live === 'Team can join';
    return `<button class="badge ${live === 'Ended in Tico' ? 'muted' : 'accent'} badge-btn" data-act="pop" data-pop="people" title="${esc(m.place)}">${icon(team ? 'globe' : 'lock', 12)}${esc(live)}</button>`;
  }
  function actions(a) {
    let aside = '';
    if (a.mode === 'ready' && a.multiple) {
      aside = `to ${esc(a.org)}${a.can_change ? ` · <button class="link" data-act="change-org">Change</button>` : ''}`;
    }
    let html = `<section class="section"><div class="section-head"><h2>Action items</h2>${a.waiting ? `<span class="count">${a.waiting}</span>` : ''}<span class="aside">${aside}</span></div>`;
    if (a.draft_status) html += `<div class="notice inline">${icon('alert')}<span class="grow">${esc(a.draft_status)}</span></div>`;
    if (a.mode === 'choose') {
      html += `<div class="choose-org">Send action items to ${a.orgs.map((o) => `<button class="btn small" data-act="choose-org" data-id="${esc(o.id)}">${esc(o.name)}</button>`).join('')}</div>`;
    }
    html += `<ul class="items">${a.items.map(item).join('')}</ul>`;
    html += `<div class="add-item"><span class="bullet">${icon('plus', 14)}</span><input data-model="addItem" data-enter="add-item" placeholder="Add an action item" spellcheck="false" aria-label="Add an action item"></div>`;
    if (!a.items.length) html += `<div class="hint">Drafted as people talk. Nothing goes to Tico until you send it.</div>`;
    else if (a.mode === 'signin') html += `<div class="hint"><button class="link" data-act="settings">Sign in to a Tico</button> to send these.</div>`;
    return html + '</section>';
  }
  function item(it) {
    const id = esc(it.id);
    const picking = S.ui.pop?.kind === 'owner' && S.ui.pop.id === it.id;
    const detail = it.quote ? `<div class="detail"><span class="at">${clock(it.at_ms)}</span>“${esc(it.quote)}”</div>` : '';
    if (it.sent) {
      const open = it.task_url ? ` · <button class="link" data-act="open-url" data-url="${esc(it.task_url)}">Open task</button>` : '';
      return `<li class="item sent"><span class="bullet">${icon('check', 14)}</span><div class="content"><div class="line1"><span class="title selectable">${esc(it.title)}</span></div>${detail}<div class="sent-line">Sent to ${esc(it.sent_to)}${open}</div></div></li>`;
    }
    const owner = it.owner_name
      ? `<button class="owner" data-act="pop" data-pop="owner" data-id="${id}" title="${esc(it.owner_name)}">${icon(it.owner.startsWith('bot:') ? 'bot' : 'person', 12)}<span>${esc(it.owner_name)}</span></button>`
      : `<button class="owner empty" data-act="pop" data-pop="owner" data-id="${id}">Who gets it?</button>`;
    const send = it.sending
      ? `<span class="spin" style="margin:7px 8px"></span>`
      : `<button class="btn small primary" data-act="send-item" data-id="${id}" ${it.can_send ? '' : 'disabled'} title="${it.can_send ? 'Send as a private Tico task' : it.owner ? '' : 'Choose who gets it'}">Send</button>`;
    return `<li class="item ${picking ? 'picking' : ''} ${it.sending ? 'busy' : ''}">
      <span class="bullet"><i></i></span>
      <div class="content"><div class="line1"><span class="title" contenteditable="true" data-defer data-edit-item="${id}" data-key="item-${id}" spellcheck="false" role="textbox" aria-label="Action item">${esc(it.title)}</span>${owner}</div>${detail}${it.error ? `<div class="error selectable">${esc(it.error)}</div>` : ''}</div>
      <div class="actions">${send}<button class="icon-btn" data-act="remove-item" data-id="${id}" title="Dismiss" aria-label="Dismiss">${icon('close', 13)}</button></div>
    </li>`;
  }

  // ---- Settings ----
  function settingsForm() {
    const s = S.settings;
    if (!S.ui.form) {
      S.ui.form = {
        microphone: s.microphone, meeting_audio: s.meeting_audio, transcription_url: s.transcription_url,
        transcription_model: s.transcription_model, transcription_language: s.transcription_language, calendar: s.calendar,
        streaming: s.streaming, streaming_model: s.streaming_model,
      };
      // Where speech goes: through the signed-in Tico, or to the person's own providers.
      const speech = S.status.speech;
      if (speech) S.ui.form.speech_source = speechSource(speech, s);
    }
    return S.ui.form;
  }
  const speechSource = (speech, s) => s.speech_source || (speech.source === 'own' || (speech.source === 'none' && !s.connections.length) ? 'own' : 'tico');
  const FORM_KEYS = ['microphone', 'meeting_audio', 'transcription_url', 'transcription_model', 'transcription_language', 'calendar', 'streaming', 'streaming_model'];
  function formDirty() {
    const s = S.settings;
    const f = S.ui.form;
    if (!f) return false;
    const speech = S.status.speech;
    if (speech && f.speech_source !== speechSource(speech, s)) return true;
    return S.ui.key !== '' || S.ui.xai !== '' || FORM_KEYS.some((k) => f[k] !== s[k]);
  }
  function settingsPage() {
    const s = S.settings;
    if (!s) return '';
    const f = settingsForm();
    const st = S.status;
    const line = (name, control) => `<div class="line"><div class="name">${name}</div>${control}</div>`;
    const field = (key, placeholder = '', type = 'text') =>
      `<input class="field" type="${type}" data-model="${key}" placeholder="${esc(placeholder)}" spellcheck="false" autocomplete="off">`;
    const toggle = (key, checked, disabled = false) =>
      `<label class="switch"><input type="checkbox" data-check="${key}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}><i></i></label>`;
    let html = `<div class="column narrow"><h1 class="page-title">Settings</h1>${errorNotice()}`;
    html += `<div class="settings-group"><div class="label">Tico</div><div class="inset">`;
    for (const c of s.connections) {
      const tail = c.default
        ? s.connections.length > 1 ? badge('Default', 'muted') : ''
        : `<button class="btn small quiet" data-act="make-default" data-id="${esc(c.id)}" title="Used for new live shares and private chat">Make default</button>`;
      html += `<div class="line"><div class="name">${esc(c.name)}${c.host ? `<small>${esc(c.host)}</small>` : ''}</div>${tail}<button class="btn small" data-act="sign-out" data-id="${esc(c.id)}">Sign out</button></div>`;
    }
    html += `<div class="line">${field('signin', 'your-tico.example').replace('class="field"', 'class="field" data-enter="signin"')}<button class="btn ${s.connections.length ? '' : 'primary'}" data-act="signin" ${st.busy ? 'disabled' : ''} title="Opens your browser to approve the recorder">${s.connections.length ? 'Add another' : 'Sign in with Tico'}</button></div>`;
    if (st.signing_in) html += `<div class="line"><div class="waiting" style="margin:0"><span class="spin"></span>Choose Allow in your browser…</div></div>`;
    html += line('<span title="Shows upcoming meetings and names recordings after them">Use my Tico calendar</span>', toggle('calendar', f.calendar, !s.calendar_available));
    html += `</div></div>`;
    html += speechGroup(s, f, line, field, toggle);
    const mics = ['', ...s.microphones.filter((name) => name)];
    if (f.microphone && !mics.includes(f.microphone)) mics.push(f.microphone);
    html += `<div class="settings-group"><div class="label">Audio</div><div class="inset">`;
    html += line('Microphone', `<select class="field" data-model="form.microphone">${mics.map((name) => `<option value="${esc(name)}" ${name === f.microphone ? 'selected' : ''}>${esc(name || 'System default')}</option>`).join('')}</select>`);
    html += line('Also record meeting audio', toggle('meeting_audio', f.meeting_audio));
    html += `</div></div>`;
    html += `<div class="settings-group"><details class="more" ${S.ui.importOpen ? 'open' : ''} data-details="importOpen"><summary>${icon('right', 12)}Import an older recorder library</summary><div class="inset" style="margin-top:8px"><div class="line">${field('legacy', 'Folder path')}<button class="btn" data-act="import" ${st.busy || s.recording ? 'disabled' : ''}>Import</button></div></div></details></div>`;
    const dirty = formDirty();
    const foot = S.status.message || (!S.status.speech || f.speech_source === 'own' ? 'Keys are kept in the macOS keychain.' : '');
    html += `<div class="settings-foot"><span class="grow">${esc(foot)}</span><button class="btn" data-act="back" ${dirty ? '' : 'hidden'}>Cancel</button><button class="btn primary" data-act="save-settings" ${dirty && !st.busy ? '' : 'disabled'}>Save</button></div>`;
    return html + '</div>';
  }

  /** Speech: through your Tico (no keys of your own), or your own providers and keys. */
  function speechGroup(s, f, line, field, toggle) {
    const speech = S.status.speech;
    const own = !speech || f.speech_source === 'own';
    let html = `<div class="settings-group"><div class="label">Speech</div><div class="inset">`;
    if (speech) {
      const seg = (value, label) => `<button class="${f.speech_source === value ? 'on' : ''}" data-act="speech-source" data-source="${value}" aria-pressed="${f.speech_source === value}">${label}</button>`;
      html += line('Source', `<div class="seg" role="group" aria-label="Speech source">${seg('tico', 'Your Tico')}${seg('own', 'Own keys')}</div>`);
    }
    if (!own) {
      const name = speech.tico || s.connections[0]?.name || '';
      const state = !s.connections.length
        ? `<span class="value faint">Sign in first</span>`
        : speech.transcribe ? `<span class="value">${esc(name)}</span>` : `<span class="value warn">Not available on ${esc(name || 'this Tico')}</span>`;
      html += line('Transcription', state);
      html += line('Language', field('form.transcription_language', 'Auto'));
      const stream = !!speech.stream;
      html += line(`<span ${stream ? '' : `title="Not available on ${esc(name || 'this Tico')}"`}>Live text</span>`, toggle('streaming', f.streaming && stream, !stream));
    } else {
      html += line('Endpoint', field('form.transcription_url', 'https://openrouter.ai/api/v1'));
      html += line('Model', field('form.transcription_model', 'elevenlabs/scribe-v2'));
      html += line('Language', field('form.transcription_language', 'Auto'));
      html += line('API key', field('key', s.key_saved ? 'Saved' : 'Not set', 'password'));
      html += line('Live text', toggle('streaming', f.streaming));
      if (f.streaming) {
        html += line('xAI key', field('xai', s.xai_saved ? 'Saved' : 'Not set', 'password'));
        html += line('Live model', field('form.streaming_model', 'grok-voice-transcribe-2.0'));
      }
    }
    return html + `</div></div>`;
  }

  // ---- Dock ----
  function levels(sources, count = 5) {
    // Bar heights and the clock are set by live(), so the dock markup stays the same as they move.
    return `<span class="levels" id="levels" style="color:var(--red)">${'<b></b>'.repeat(count)}</span>`;
  }
  function dock() {
    if (S.page === 'settings') return '';
    const st = S.status;
    const rec = st.recording;
    const caret = `<button class="caret ${S.ui.pop?.kind === 'source' ? 'on' : ''}" data-act="pop" data-pop="source" title="Audio sources" aria-label="Audio sources">${icon('down', 12)}</button>`;
    let bar;
    let foot = '';
    if (S.page === 'library') {
      if (rec) {
        bar = `<div class="dock-bar" style="width:fit-content;margin:0 auto;padding-right:7px"><button class="rec stop" data-act="stop" title="Stop" aria-label="Stop"><i></i>${streamState(rec).bad ? '<span class="rec-dot"></span>' : ''}</button>${levels(rec.sources)}<span class="elapsed" id="elapsed"></span>${caret}<button class="chip" data-act="open-active">Open</button></div>`;
      } else {
        bar = `<div class="dock-bar" style="width:fit-content;margin:0 auto;padding-right:6px"><button class="rec start" data-act="record" title="Record" aria-label="Record"><i></i></button><span class="record-label" data-act="record">Record</span>${caret}</div>`;
      }
      foot = st.message || (rec ? streamState(rec).note : '');
    } else {
      const m = S.meeting;
      if (!m) return '';
      const chat = m.chat;
      let left = '';
      if (rec && rec.id === m.id) {
        const stream = streamState(rec);
        left = `<button class="rec stop" data-act="stop" title="Stop recording" aria-label="Stop"><i></i>${stream.bad ? '<span class="rec-dot" title="Live text reconnecting"></span>' : ''}</button>${levels(rec.sources, 4)}<span class="elapsed" id="elapsed"></span>${caret}`;
      } else if (rec) {
        left = `<button class="rec" data-act="open-active" title="Open the recording in progress" aria-label="Open the recording in progress"><i class="pulse"></i></button>`;
      }
      // Messages go to the meeting; "Ask privately" (or /ask) asks Tico with only you seeing it.
      const placeholder = !chat.private ? 'Message the meeting' : chat.can_message ? 'Ask Tico privately' : 'Ask anything';
      const ready = chat.ready;
      const attention = m.audio.attention || m.setup.length;
      const toggle = chat.can_message ? `<button class="ask-private ${chat.private ? 'on' : ''}" data-act="toggle-private" title="Only you see the question and the answer" aria-pressed="${chat.private}">${icon('lock', 12)}<span>Ask privately</span></button>` : '';
      bar = `<div class="dock-bar">${left}<label class="ask ${S.ui.ask.trim() ? '' : 'blank'}" data-act="focus-ask"><input data-model="ask" data-enter="ask" placeholder="${placeholder}" spellcheck="false" aria-label="${placeholder}">${toggle}<button class="send" data-act="ask" ${ready ? '' : 'disabled'} aria-label="Send">${chat.thinking ? '<span class="spin" style="border-top-color:currentColor"></span>' : icon('arrowUp', 14)}</button></label><button class="chip ${S.ui.panel === 'transcript' ? 'on' : ''}" data-act="toggle-transcript" title="Transcript (⌘T)">${icon('transcript', 14)}Transcript${attention ? '<span class="dot"></span>' : ''}</button></div>`;
      const note = rec && rec.id === m.id ? streamState(rec).note : '';
      const reach = chat.private ? 'Only you see this' : chat.connected ? 'Everyone in the meeting sees this' : 'Sending connects this meeting to Tico, private to you';
      foot = st.message || note || reach;
    }
    // A recording problem worth knowing about, in plain words; closing it keeps it closed.
    const notice = st.notice && rec ? `<div class="dock-notice" role="status">${icon('alert', 14)}<span class="grow">${esc(st.notice)}</span><button class="icon-btn" data-act="dismiss-notice" title="Dismiss" aria-label="Dismiss">${icon('close', 12)}</button></div>` : '';
    return `<div class="dock"><div class="dock-inner">${notice}${bar}<div class="dock-foot ${st.message ? 'message' : ''}">${esc(foot)}</div></div></div>`;
  }

  // ---- Floating panels ----
  function panel() {
    if (S.page !== 'meeting' || !S.meeting) return '';
    if (S.ui.panel === 'transcript') return transcriptPanel();
    return '';
  }
  function speakerColors(blocks) {
    const colors = {};
    let n = 0;
    for (const b of blocks) if (!b.me && !(b.speaker in colors)) colors[b.speaker] = `var(--speaker-${(n++ % 5) + 1})`;
    return colors;
  }
  function highlight(text, q) {
    if (!q) return esc(text);
    const lower = text.toLowerCase();
    let out = '';
    let at = 0;
    for (let i = lower.indexOf(q); i >= 0; i = lower.indexOf(q, at)) {
      out += esc(text.slice(at, i)) + `<mark>${esc(text.slice(i, i + q.length))}</mark>`;
      at = i + q.length;
    }
    return out + esc(text.slice(at));
  }
  /** Live text per channel: nothing extra while it works, a quiet note while it reconnects. */
  function streamState(rec) {
    const list = rec?.stream || [];
    const bad = list.find((x) => x.state === 'retrying') || list.find((x) => x.state === 'stopped' && x.error);
    const note = !bad ? '' : bad.state === 'retrying' ? `Live text reconnecting${bad.error ? ` · ${bad.error}` : ''}` : bad.error;
    return { live: list.some((x) => x.state === 'live'), bad, note };
  }
  function transcriptBlocks() {
    const m = S.meeting;
    return S.transcript?.id === m.id ? S.transcript.blocks : [];
  }
  /** The panel's frame. Its turns are patched into #tbody one by one (see `patchTranscript`). */
  function transcriptPanel() {
    const m = S.meeting;
    const a = m.audio;
    const rec = S.status.recording;
    const stream = rec && rec.id === m.id ? streamState(rec) : null;
    let status = '';
    // A spinner only while transcription really runs; waiting on Settings is said plainly.
    if (a.pending && m.setup.length) status = `Waiting to transcribe ${a.pending}`;
    else if (a.pending) status = `<span class="spin"></span>Transcribing ${a.pending}…`;
    else if (a.finding_speakers) status = `<span class="spin"></span>Finding speakers…`;
    if (stream?.bad) status = `<i class="live-dot amber"></i>Reconnecting`;
    else if (stream?.live) status = `<i class="live-dot"></i>Live`;
    let html = `<div class="panel transcript"><div class="panel-head"><h3>Transcript</h3><span class="status">${status}</span><span class="grow"></span>`;
    if (a.failed) html += `<button class="btn small quiet" data-act="retry" data-id="${esc(m.id)}">${icon('retry', 13)}Retry ${a.failed} failed</button>`;
    html += `<button class="icon-btn ${S.ui.showAudio ? 'on' : ''}" data-act="toggle-audio" title="${a.attention ? 'Audio · needs attention' : 'Audio'}" aria-label="${a.attention ? 'Audio · needs attention' : 'Audio'}">${icon('mic')}${a.attention ? '<span class="dot"></span>' : ''}</button>`;
    html += `<button class="icon-btn ${S.ui.tsearch ? 'on' : ''}" data-act="tsearch" title="Search (⌘F)" aria-label="Search">${icon('search')}</button>`;
    html += `<button class="icon-btn" data-act="copy" title="Copy transcript" aria-label="Copy transcript">${icon(S.ui.copied ? 'check' : 'copy')}</button>`;
    html += `<button class="icon-btn" data-act="close-panel" title="Minimize" aria-label="Minimize">${icon('minimize')}</button></div>`;
    if (S.ui.tsearch) html += `<label class="panel-search">${icon('search', 14)}<input data-model="tq" data-enter="next-hit" placeholder="Search transcript" spellcheck="false"><span class="hits" id="thits"></span><button class="icon-btn tiny" data-act="hit" data-step="-1" title="Previous (⇧↩)" aria-label="Previous match">${icon('up', 12)}</button><button class="icon-btn tiny" data-act="hit" data-step="1" title="Next (↩)" aria-label="Next match">${icon('down', 12)}</button></label>`;
    if (S.ui.showAudio) {
      let box = '';
      if (a.warning) box += `<div class="warn selectable">${esc(a.warning)}</div>`;
      for (const input of a.inputs) box += `<div class="rowline"><b>${esc(input.label)}</b><span class="${input.ok ? 'ok' : ''}" style="${input.ok ? '' : 'color:var(--amber)'}">${esc(input.state)}</span></div>`;
      if (stream?.note) box += `<div class="warn selectable">${esc(stream.note)}</div>`;
      for (const line of a.failures) box += `<div class="warn selectable">${esc(line)}</div>`;
      if (a.more) box += `<div class="faint">+${a.more} more</div>`;
      if (!box) box = `<div class="faint">No audio yet</div>`;
      html += `<div class="audio-box">${box}</div>`;
    }
    return html + `<div class="panel-body" id="tbody"></div></div>`;
  }
  /** One string per row of the transcript: timestamps, turns, and the softer words still being spoken. */
  function transcriptItems() {
    const m = S.meeting;
    const a = m.audio;
    const blocks = transcriptBlocks();
    const q = S.ui.tsearch ? S.ui.tq.trim().toLowerCase() : '';
    const recording = m.state === 'recording' || S.status.recording?.id === m.id;
    // Search marks matches in place instead of filtering, so the list never reflows as you type.
    const shown = blocks;
    if (!blocks.length) {
      return [`<div class="panel-empty">${recording ? 'Listening. Speech appears here.' : a.pending ? 'Transcribing…' : 'No speech transcribed.'}</div>`];
    }
    const items = [];
    const colors = speakerColors(blocks);
    let stamp = -Infinity;
    let previous = null;
    for (const b of shown) {
      if (b.start_ms - stamp >= 60000) {
        items.push(`<div class="stamp">${clock(b.start_ms)}</div>`);
        stamp = b.start_ms;
        previous = null;
      }
      const who = !b.me && previous !== b.speaker ? `<div class="who" style="color:${colors[b.speaker]}">${esc(b.speaker)}</div>` : '';
      items.push(`<div class="turn ${b.me ? 'me' : ''} ${b.partial ? 'partial' : ''} ${b.removed ? 'removed' : ''}">${who}<div class="bubble">${sharedLine(b, q)}</div></div>`);
      previous = b.speaker;
    }
    const tail = blocks[blocks.length - 1];
    if (a.pending && !m.setup.length && !recording && !q && !tail.partial) items.push(`<div class="turn"><div class="bubble typing"><i></i><i></i><i></i></div></div>`);
    return items;
  }
  /** A line shared live: its text, or the edit form or removal question while one is open. */
  function sharedLine(b, q) {
    const seq = b.seq ?? null;
    const current = S.transcript?.id;
    if (seq !== null && S.ui.editLine?.id === current && S.ui.editLine?.seq === seq) {
      // The draft is typed into the box without a re-render, so the box keeps its place and focus.
      return `<textarea class="line-edit" data-model="editDraft" rows="3" aria-label="Edit line" spellcheck="false">${esc(S.ui.editInitial)}</textarea><div class="line-actions"><button class="btn small quiet" data-act="cancel-line">Cancel</button><button class="btn small" data-act="save-line" data-seq="${seq}">Save</button></div>`;
    }
    let html = b.paragraphs.map((p) => `<p>${highlight(p, q)}</p>`).join('');
    if (seq === null) return html;
    if (b.edited && !b.removed) html += `<i class="line-note">edited</i>`;
    if (S.ui.removeLine?.id === current && S.ui.removeLine?.seq === seq) {
      return html + `<div class="line-actions"><span class="faint">Remove this line in Tico?</span><button class="btn small quiet" data-act="cancel-remove-line">Cancel</button><button class="btn small danger" data-act="remove-line" data-seq="${seq}">Remove</button></div>`;
    }
    if (b.editable) {
      const remove = b.removed ? '' : `<button class="icon-btn tiny" data-act="ask-remove-line" data-seq="${seq}" title="Remove line" aria-label="Remove line">${icon('trash', 12)}</button>`;
      html += `<div class="line-tools"><button class="icon-btn tiny" data-act="edit-line" data-seq="${seq}" title="Edit line" aria-label="Edit line">${icon('pencil', 12)}</button>${remove}</div>`;
    }
    return html;
  }
  /** Replaces only the rows that changed, so a partial turn grows in place instead of jumping. */
  function patchTranscript(body) {
    const items = transcriptItems();
    const before = body._items || [];
    if (before.length === items.length && before.every((item, i) => item === items[i])) return;
    // A fresh panel body keeps where the person had scrolled to.
    const fresh = !body._items;
    const stick = fresh ? S.ui.stick : body.scrollHeight - body.scrollTop - body.clientHeight < 40;
    const template = document.createElement('template');
    items.forEach((item, i) => {
      if (before[i] === item && body.children[i]) return;
      template.innerHTML = item;
      const row = template.content.firstElementChild;
      if (body.children[i]) body.replaceChild(row, body.children[i]);
      else body.appendChild(row);
    });
    while (body.children.length > items.length) body.lastElementChild.remove();
    body._items = items;
    if (stick) body.scrollTop = body.scrollHeight;
    else if (fresh) body.scrollTop = S.ui.tTop || 0;
  }
  /** The current search match: outlined, counted, and scrolled to only when the search moves. */
  function showHit(body, step = 0) {
    const counter = document.getElementById('thits');
    const q = S.ui.tsearch ? S.ui.tq.trim().toLowerCase() : '';
    const marks = [...body.querySelectorAll('mark')];
    let jump = step !== 0;
    if (S.ui.hitKey !== q) {
      S.ui.hitKey = q;
      S.ui.hit = 0;
      jump = true;
    }
    if (!marks.length) {
      if (counter) counter.textContent = q ? '0/0' : '';
      return;
    }
    S.ui.hit = (Math.min(S.ui.hit, marks.length - 1) + step + marks.length) % marks.length;
    marks.forEach((mark, i) => mark.classList.toggle('now', i === S.ui.hit));
    if (counter) counter.textContent = `${S.ui.hit + 1}/${marks.length}`;
    if (jump) marks[S.ui.hit].scrollIntoView({ block: 'center' });
  }

  // ---- Popovers ----
  function popover() {
    const pop = S.ui.pop;
    if (!pop) return '';
    let body = '';
    let cls = '';
    if (pop.kind === 'send') body = sendPop(pop);
    else if (pop.kind === 'more') body = morePop(pop);
    else if (pop.kind === 'owner') body = ownerPop(pop);
    else if (pop.kind === 'people') {
      body = peoplePop();
      cls = 'people';
    } else if (pop.kind === 'source') body = sourcePop();
    if (!body) return '';
    return `<div class="popover ${cls}" id="popover" style="${pop.style}">${body}</div>`;
  }
  function sendPop(pop) {
    const fromMeeting = S.page === 'meeting' && S.meeting?.id === pop.id;
    const row = fromMeeting ? S.meeting : S.library?.rows.find((r) => r.id === pop.id);
    if (!row) return '';
    const connected = fromMeeting ? row.connected : S.library.connected;
    let html = `<div class="pop-title">Send to Tico</div>`;
    if (!connected) return html + `<div class="pop-text">Sign in to a Tico first.</div><button class="menu-item" data-act="settings">${icon('gear')}<span class="grow">Settings</span></button>`;
    if (fromMeeting && row.send_blocked) return html + `<div class="pop-text">${esc(row.send_blocked)}</div>`;
    return html + row.targets.map((t) => `<button class="menu-item" data-act="send-meeting" data-id="${esc(pop.id)}" data-dest="${esc(t.id)}" ${t.already ? 'disabled' : ''}>${icon(t.already ? 'check' : 'send')}<span class="grow">${t.already ? 'Already in' : 'Send to'} ${esc(t.name)}</span></button>`).join('');
  }
  function morePop(pop) {
    const m = S.meeting;
    if (!m) return '';
    if (pop.confirm) {
      return `<div class="pop-title">Delete recording?</div><div class="pop-text">Removes its audio and transcript from this Mac.</div><div class="pop-foot"><span class="grow"></span><button class="btn small quiet" data-act="close-pop">Cancel</button><button class="btn small danger" data-act="delete" data-id="${esc(m.id)}">Delete</button></div>`;
    }
    let html = '';
    if (m.audio.failed) html += `<button class="menu-item" data-act="retry" data-id="${esc(m.id)}">${icon('retry')}<span class="grow">Retry ${m.audio.failed} failed</span></button>`;
    html += `<button class="menu-item" data-act="audio-status">${icon('mic')}<span class="grow">Audio status</span></button>`;
    html += `<button class="menu-item" data-act="copy">${icon('copy')}<span class="grow">Copy transcript</span></button>`;
    html += `<div class="menu-sep"></div><button class="menu-item danger" data-act="confirm-delete" ${m.recording_here ? 'disabled title="Stop recording before deleting it"' : ''}>${icon('trash')}<span class="grow">Delete recording…</span></button>`;
    return html;
  }
  function ownerPop(pop) {
    const a = S.meeting?.actions;
    if (!a) return '';
    if (a.mode === 'signin') return `<div class="pop-text">Sign in to a Tico to choose who gets it.</div><button class="menu-item" data-act="settings">${icon('gear')}<span class="grow">Settings</span></button>`;
    if (a.mode === 'choose') return `<div class="pop-text">Choose a Tico first.</div>`;
    const it = a.items.find((i) => i.id === pop.id);
    const q = S.ui.ownerQuery.trim().toLowerCase();
    const owners = q ? ranked(a.owners, q) : a.owners;
    let html = `<input class="pop-input" data-model="ownerQuery" data-enter="owner-first" data-list placeholder="Who gets it?" spellcheck="false" aria-label="Who gets it?"><div class="pop-list">`;
    if (!a.owners.length) html += `<div class="pop-text"><span class="spin" style="display:inline-block;vertical-align:-2px;margin-right:6px"></span>Loading…</div>`;
    else if (!owners.length) html += `<div class="pop-text">No match</div>`;
    const hot = Math.min(S.ui.hot, owners.length - 1);
    html += owners.map((o, i) => `<button class="menu-item ${i === hot ? 'hot' : ''}" data-opt data-act="set-owner" data-id="${esc(pop.id)}" data-actor="${esc(o.actor)}">${icon(o.bot ? 'bot' : 'person')}<span class="grow">${esc(o.name)}</span>${it && it.owner === o.actor ? `<span class="check">${icon('check')}</span>` : ''}</button>`).join('');
    return html + `</div>`;
  }
  function peoplePop() {
    const m = S.meeting;
    if (!m) return '';
    const p = m.people;
    let html = `<div class="pop-title">In this meeting</div><div class="pop-list">`;
    for (const row of p.rows) {
      const tail = row.pending ? `<span class="spin" title="Adding…"></span>` : row.removable ? `<button class="btn small quiet" data-act="remove-access" data-actor="${esc(row.actor)}">Remove</button>` : row.local ? `<span class="tag">Not in Tico</span>` : row.kind === 'bot' ? `<span class="tag">Bot</span>` : '';
      html += `<div class="person ${row.pending ? 'pending' : ''}">${avatar(row)}<span class="grow">${esc(row.name)}</span>${tail}</div>`;
    }
    html += `</div>`;
    if (p.error) html += `<div class="pop-error">${icon('alert', 13)}<span>${esc(p.error)}</span></div>`;
    if (p.stage === 'invite') {
      return html + `<div class="pop-foot" style="padding:6px 4px 2px"><button class="btn block" data-act="start-adding">${icon('plus', 13)}Add people</button></div>`;
    }
    if (p.stage === 'org') {
      return html + `<div class="vis"><span class="grow">Which Tico?</span></div>` + p.orgs.map((o) => `<button class="menu-item" data-act="people-org" data-id="${esc(o.id)}">${icon('globe')}<span class="grow">${esc(o.name)}</span></button>`).join('');
    }
    if (p.org) {
      const label = p.team ? `Everyone signed in to ${esc(p.org)} can see this meeting as it happens` : 'Only you and people you add in Tico can see this meeting';
      const button = `<button class="btn small" data-act="set-team" data-team="${!p.team}" ${p.can_share ? '' : 'disabled'} title="${esc(p.blocked)}">${p.team ? 'Make private' : 'Make public'}</button>`;
      html += `<div class="vis">${icon(p.team ? 'globe' : 'lock')}<span class="grow">${label}</span>${button}</div>`;
    }
    if (p.blocked && p.org) html += `<div class="pop-text">${esc(p.blocked)}</div>`;
    if (p.blocked) return html;
    html += `<input class="pop-input" data-model="peopleQuery" data-enter="people-enter" data-list placeholder="Add a person or bot" spellcheck="false" aria-label="Type a name to add a person or bot">`;
    const q = S.ui.peopleQuery.trim().toLowerCase();
    if (q) {
      const matches = ranked(p.candidates, q).slice(0, 8);
      const hot = Math.min(S.ui.hot, matches.length - 1);
      html += `<div class="pop-list">` + matches.map((c, i) => `<button class="menu-item ${i === hot ? 'hot' : ''}" data-opt data-act="pick" data-actor="${esc(c.actor)}">${icon(c.bot ? 'bot' : 'person')}<span class="grow">${esc(c.name)}</span></button>`).join('') + `</div>`;
      if (!matches.length) html += `<div class="pop-text">Enter adds “${esc(S.ui.peopleQuery.trim())}” to this recording only.</div>`;
    }
    return html;
  }
  function sourcePop() {
    const s = S.settings;
    if (!s) return '';
    const rec = S.status.recording;
    let html = `<div class="pop-title">Microphone</div>`;
    const mics = ['', ...s.microphones.filter((name) => name)];
    if (s.microphone && !mics.includes(s.microphone)) mics.push(s.microphone);
    html += mics.map((name) => `<button class="menu-item" data-act="set-mic" data-mic="${esc(name)}">${icon('mic')}<span class="grow">${esc(name || 'System default')}</span>${name === s.microphone ? `<span class="check">${icon('check')}</span>` : ''}</button>`).join('');
    html += `<div class="menu-sep"></div><button class="menu-item" data-act="toggle-meeting-audio">${icon('speaker')}<span class="grow">Meeting audio</span>${s.meeting_audio ? `<span class="check">${icon('check')}</span>` : ''}</button>`;
    if (rec) {
      html += `<div class="menu-sep"></div><div class="pop-title">Now recording</div>`;
      html += rec.sources.map((src) => `<div class="pop-foot"><span class="grow">${esc(src.label)}${src.no_signal ? ` · <span style="color:var(--amber)">no signal</span>` : src.channel === 'them' && !src.level ? ` · <span class="faint">quiet</span>` : ''}</span><span class="meter"><i data-meter="${esc(src.channel)}" style="width:${Math.round(src.level * 100)}%"></i></span></div>`).join('');
      html += `<div class="pop-text faint">Changes apply to the next recording.</div>`;
    }
    return html;
  }

  function connectCard() {
    const address = S.status.connect;
    if (!address) return '';
    const host = address.split('://')[1] || address;
    return `<div class="connect"><div class="card"><div class="pop-title" style="padding:0 0 6px">Connect to Tico?</div><div class="host">${esc(host)}</div><p>Your browser opens this Tico to approve. Continue only if you chose Connect recorder there.</p><div class="buttons"><button class="btn" data-act="connect-cancel">Cancel</button><button class="btn primary" data-act="connect-ok" ${S.status.busy ? 'disabled' : ''}>Sign in</button></div></div></div>`;
  }

  // ---- Rendering ----
  function page() {
    if (S.page === 'meeting') return meeting();
    if (S.page === 'settings') return settingsPage();
    return library();
  }
  function render() {
    const root = document.getElementById('app');
    if (!root.dataset.ready) {
      root.innerHTML = '<div id="r-top"></div><main class="page" id="r-page"></main><div id="r-dock"></div><div id="r-panel"></div><div id="r-pop"></div><div id="r-mention"></div><div id="r-connect"></div>';
      root.dataset.ready = '1';
    }
    const html = { top: topbar(), page: page(), dock: dock(), panel: panel(), pop: popover(), mention: mentionPop(), connect: connectCard() };
    const active = document.activeElement;
    const key = active?.dataset?.key || active?.dataset?.model;
    const selection = key && 'selectionStart' in active ? [active.selectionStart, active.selectionEnd] : null;
    for (const [name, content] of Object.entries(html)) {
      if (regions[name] === content) continue;
      const element = document.getElementById(`r-${name}`);
      // Never replace text the person is editing; it renders once they leave it.
      if (element.contains(active) && active.matches('[data-defer]')) {
        S.deferred = true;
        continue;
      }
      const pageTop = name === 'page' && regions.pageKind === S.page + (S.meeting?.id || '') ? element.scrollTop : 0;
      const reopened = (name === 'pop' || name === 'mention') && regions[name];
      element.innerHTML = content;
      // A popover that was already open updates in place; only a fresh one rises in.
      if (reopened && element.firstElementChild) element.firstElementChild.style.animation = 'none';
      regions[name] = content;
      if (name === 'page') {
        element.scrollTop = pageTop;
        regions.pageKind = S.page + (S.meeting?.id || '');
      }
      for (const input of element.querySelectorAll('[data-model]')) {
        if (input.tagName !== 'SELECT') input.value = model(input.dataset.model);
      }
      // After sending, the chat's newest line comes into view.
      if (name === 'page' && S.ui.toChat && element.querySelector('#chat .msg')) {
        S.ui.toChat = false;
        element.querySelector('#chat').scrollIntoView({ block: 'end' });
      }
      for (const area of element.querySelectorAll('textarea.notes')) {
        grow(area);
        paintMentions(area);
      }
      if (key) {
        const again = element.querySelector(`[data-key="${CSS.escape(key)}"], [data-model="${CSS.escape(key)}"]`);
        if (again && again !== document.activeElement) {
          again.focus();
          if (selection && 'setSelectionRange' in again) {
            try { again.setSelectionRange(...selection); } catch {}
          }
        }
      }
    }
    const tbody = document.getElementById('tbody');
    if (tbody) {
      patchTranscript(tbody);
      showHit(tbody);
    }
    // Wide windows make room for the transcript beside the page instead of covering it.
    document.getElementById('r-page').classList.toggle('side', S.page === 'meeting' && S.ui.panel === 'transcript');
    if (S.ui.focusNext) {
      const target = document.querySelector(S.ui.focusNext);
      S.ui.focusNext = null;
      if (target) target.focus();
    }
    document.querySelector('.topbar')?.classList.toggle('scrolled', document.getElementById('r-page').scrollTop > 4);
    live();
  }
  /** Updates that change every few frames while recording: the clock and the level bars. */
  function live() {
    const rec = S.status.recording;
    if (!rec) return;
    const elapsed = document.getElementById('elapsed');
    if (elapsed) elapsed.textContent = clock(rec.elapsed_ms);
    const level = Math.max(0, ...rec.sources.map((s) => s.level || 0));
    const shape = [0.55, 0.85, 1, 0.75, 0.5];
    document.querySelectorAll('#levels b').forEach((bar, i) => (bar.style.height = `${Math.round(4 + 14 * level * shape[i])}px`));
    for (const src of rec.sources) {
      const meter = document.querySelector(`[data-meter="${CSS.escape(src.channel)}"]`);
      if (meter) meter.style.width = `${Math.round(src.level * 100)}%`;
    }
  }
  function grow(area) {
    area.style.height = 'auto';
    area.style.height = `${Math.max(120, area.scrollHeight)}px`;
  }
  const startsWord = (name, q) => name.toLowerCase().split(/[\s.@_-]+/).some((word) => word.startsWith(q));
  /** Names containing `q`, those with a word starting with it first. */
  const ranked = (rows, q) => {
    const hits = rows.filter((row) => row.name.toLowerCase().includes(q));
    return [...hits.filter((row) => startsWord(row.name, q)), ...hits.filter((row) => !startsWord(row.name, q))];
  };
  function moveHot(box, step) {
    const options = [...document.querySelectorAll(`${box} [data-opt]`)];
    if (!options.length) return;
    S.ui.hot = Math.max(0, Math.min(options.length - 1, Math.min(S.ui.hot, options.length - 1) + step));
    options.forEach((o, i) => o.classList.toggle('hot', i === S.ui.hot));
    options[S.ui.hot].scrollIntoView({ block: 'nearest' });
  }

  // ---- @mentions in Notes: stored as plain "@Name" text, drawn as chips behind the words ----
  const roster = () => S.meeting?.actions?.owners || [];
  function paintMentions(area) {
    const back = area.previousElementSibling;
    if (!back?.classList.contains('notes-backdrop')) return;
    const names = roster().map((o) => o.name).filter(Boolean).sort((a, b) => b.length - a.length);
    const text = area.value;
    if (!names.length || !text.includes('@')) {
      back.textContent = '';
      return;
    }
    const pattern = new RegExp(`@(?:${names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})(?![\\p{L}\\p{N}])`, 'gu');
    let html = '';
    let at = 0;
    for (const match of text.matchAll(pattern)) {
      html += esc(text.slice(at, match.index)) + `<mark>${esc(match[0])}</mark>`;
      at = match.index + match[0].length;
    }
    back.innerHTML = html + esc(text.slice(at)) + '\u200b';
  }
  /** The "@query" just before the caret, if the person is typing a mention. */
  function findMention(area) {
    const before = area.value.slice(0, area.selectionStart);
    const match = /(^|[\s(])@([\p{L}\p{N}._-]{0,24})$/u.exec(before);
    if (!match || !roster().length || area.selectionStart !== area.selectionEnd) {
      if (S.ui.mention) closeMention();
      return;
    }
    const start = before.length - match[2].length - 1;
    const query = match[2].toLowerCase();
    if (S.ui.mention?.start !== start || S.ui.mention.query !== query) S.ui.hot = 0;
    S.ui.mention = { start, query, left: 0, top: 0, max: 260 };
    Object.assign(S.ui.mention, caretPoint(area, start));
    render();
  }
  function closeMention() {
    if (!S.ui.mention) return;
    S.ui.mention = null;
    render();
  }
  function mentionMatches() {
    const q = S.ui.mention?.query || '';
    return (q ? ranked(roster(), q).filter((o) => startsWord(o.name, q)) : roster()).slice(0, 8);
  }
  function mentionPop() {
    const m = S.ui.mention;
    if (!m || S.page !== 'meeting') return '';
    const matches = mentionMatches();
    if (!matches.length) return `<div class="popover mention" id="mention" style="left:${m.left}px;top:${m.top}px"><div class="pop-text">No match</div></div>`;
    const hot = Math.min(S.ui.hot, matches.length - 1);
    return `<div class="popover mention" id="mention" style="left:${m.left}px;top:${m.top}px"><div class="pop-list" style="max-height:${m.max}px">${matches.map((o, i) => `<button class="menu-item ${i === hot ? 'hot' : ''}" data-opt data-name="${esc(o.name)}" tabindex="-1">${avatar(o, 'small')}<span class="grow">${esc(o.name)}</span>${o.bot ? '<span class="tag">Bot</span>' : ''}</button>`).join('')}</div></div>`;
  }
  function insertMention(area, name) {
    const m = S.ui.mention;
    if (!m) return;
    const value = area.value;
    const end = area.selectionStart;
    const text = `@${name} `;
    area.value = value.slice(0, m.start) + text + value.slice(end);
    const caret = m.start + text.length;
    area.setSelectionRange(caret, caret);
    S.ui.mention = null;
    area.dispatchEvent(new Event('input', { bubbles: true }));
    render();
  }
  /** Where a character of a textarea sits on screen, measured with a mirror of its text. */
  function caretPoint(area, index) {
    const style = getComputedStyle(area);
    const mirror = document.createElement('div');
    for (const prop of ['fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'paddingTop', 'paddingLeft', 'paddingRight', 'borderTopWidth', 'borderLeftWidth', 'boxSizing']) mirror.style[prop] = style[prop];
    Object.assign(mirror.style, { position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap', overflowWrap: 'break-word', width: `${area.clientWidth}px`, top: '0', left: '0' });
    mirror.textContent = area.value.slice(0, index);
    const mark = document.createElement('span');
    mark.textContent = '@';
    mirror.appendChild(mark);
    document.body.appendChild(mirror);
    const rect = area.getBoundingClientRect();
    const line = parseFloat(style.lineHeight) || 24;
    const lineTop = rect.top + mark.offsetTop;
    mirror.remove();
    const width = 260;
    const point = { left: Math.round(Math.max(12, Math.min(rect.left + mark.offsetLeft - 8, window.innerWidth - width - 12))) };
    // Below the line, like any text field's suggestions, scrolling if it is short of room; above
    // only when not even four names fit below.
    const want = Math.min(260, 34 * mentionCount()) + 10;
    const below = window.innerHeight - (lineTop + line + 4) - 12;
    const above = lineTop - 4 - 12;
    if (below >= Math.min(want, 34 * 4 + 10) || below >= above) {
      point.top = Math.round(lineTop + line + 4);
      point.max = Math.max(60, Math.min(260, below - 10));
    } else {
      const height = Math.min(want, above);
      point.top = Math.round(lineTop - 4 - height);
      point.max = height - 10;
    }
    return point;
  }
  const mentionCount = () => Math.max(1, mentionMatches().length);

  // ---- Navigation ----
  async function openMeeting(id) {
    S.ui.pop = null;
    S.ui.panel = null;
    S.ui.confirm = null;
    S.ui.titleDraft = null;
    S.ui.notesDraft = null;
    S.ui.showAudio = false;
    S.ui.tsearch = false;
    S.ui.tq = '';
    S.ui.ask = '';
    // Drafts and removal questions belong to the meeting they were started in.
    S.ui.editLine = null;
    S.ui.editDraft = '';
    S.ui.editInitial = '';
    S.ui.removeLine = null;
    S.page = 'meeting';
    S.meeting = null;
    S.hash.meeting = '';
    S.hash.transcript = '';
    await act('open_meeting', { id });
  }
  async function back() {
    S.ui.pop = null;
    S.ui.panel = null;
    if (S.page === 'settings') {
      S.ui.form = null;
      S.ui.key = '';
      S.ui.xai = '';
      S.page = S.ui.prevPage === 'meeting' && S.status.meeting ? 'meeting' : 'library';
      render();
      return;
    }
    if (S.page === 'meeting') {
      await flushNotes();
      S.page = 'library';
      S.ui.panel = null;
      await act('close_meeting');
    }
  }
  function settings() {
    if (S.page === 'settings') return;
    S.ui.prevPage = S.page;
    S.ui.pop = null;
    S.ui.panel = null;
    S.ui.form = null;
    S.page = 'settings';
    render();
  }

  // ---- Popover placement ----
  function openPop(kind, anchor, extra = {}) {
    if (S.ui.pop?.kind === kind && S.ui.pop.id === extra.id) {
      S.ui.pop = null;
      render();
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const width = kind === 'people' ? 320 : kind === 'owner' ? 260 : 240;
    let left = Math.min(Math.max(12, rect.right - width), window.innerWidth - width - 12);
    if (kind === 'owner' || kind === 'source') left = Math.min(Math.max(12, rect.left - 6), window.innerWidth - width - 12);
    let style = `left:${left}px;width:${width}px;`;
    if (rect.top > window.innerHeight * 0.6) style += `bottom:${window.innerHeight - rect.top + 8}px;`;
    else style += `top:${rect.bottom + 6}px;`;
    S.ui.pop = { kind, style, ...extra };
    S.ui.ownerQuery = '';
    S.ui.peopleQuery = '';
    S.ui.hot = 0;
    if (kind === 'owner' || kind === 'people') S.ui.focusNext = '#popover .pop-input';
    render();
  }

  // ---- Actions ----
  let notesTimer = null;
  async function flushNotes() {
    clearTimeout(notesTimer);
    if (S.ui.notesDraft !== null && S.meeting && S.ui.notesDraft !== S.meeting.notes.text) {
      await invoke('set_notes', { notes: S.ui.notesDraft });
    }
  }
  function copyTranscript() {
    const blocks = S.transcript?.blocks || [];
    const text = blocks.map((b) => `[${clock(b.start_ms)}] ${b.speaker}: ${b.paragraphs.join('\n\n')}`).join('\n\n');
    const done = () => {
      S.ui.copied = true;
      render();
      setTimeout(() => { S.ui.copied = false; render(); }, 1400);
    };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
    else fallbackCopy(text, done);
  }
  function fallbackCopy(text, done) {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
    done();
  }
  function submitAsk() {
    let text = S.ui.ask.trim();
    const chat = S.meeting?.chat;
    if (!text || !chat) return;
    // "/ask …" asks privately whatever the toggle says.
    const slash = /^\/ask\b\s*/i.exec(text);
    const privately = chat.private || !!slash;
    if (slash) text = text.slice(slash[0].length);
    if (!text || (privately ? !!chat.thinking : !chat.ready)) return;
    S.ui.ask = '';
    S.ui.focusNext = '[data-model="ask"]';
    S.ui.toChat = true;
    act(privately ? 'ask' : 'chat_send', privately ? { question: text } : { text });
  }
  const handlers = {
    back,
    settings,
    open: (el) => openMeeting(el.dataset.id),
    'open-active': () => S.status.recording && openMeeting(S.status.recording.id),
    record: async () => {
      const id = await act('record', { event: null });
      if (id) openMeeting(id);
    },
    'record-event': async (el) => {
      const event = S.library.events[Number(el.dataset.index)];
      const id = await act('record', { event });
      if (id) openMeeting(id);
    },
    stop: () => act('stop_recording'),
    'ask-delete': (el) => { S.ui.confirm = el.dataset.id; render(); },
    'cancel-delete': () => { S.ui.confirm = null; render(); },
    delete: async (el) => {
      S.ui.confirm = null;
      S.ui.pop = null;
      await act('delete_recording', { id: el.dataset.id });
    },
    'confirm-delete': () => { S.ui.pop = { ...S.ui.pop, confirm: true }; render(); },
    pop: (el) => openPop(el.dataset.pop, el, { id: el.dataset.id, anchorId: el.dataset.pop === 'send' ? el.dataset.id : undefined }),
    'close-pop': () => { S.ui.pop = null; render(); },
    'send-meeting': (el) => { S.ui.pop = null; act('send_meeting', { id: el.dataset.id, destination: el.dataset.dest }); },
    finish: () => act('finish_live'),
    'edit-line': (el) => {
      const seq = Number(el.dataset.seq);
      const id = S.transcript?.id;
      const block = (S.transcript?.blocks || []).find((b) => b.seq === seq);
      if (!block || !id) return;
      S.ui.editLine = { id, seq };
      S.ui.editInitial = block.paragraphs.join(' ');
      S.ui.editDraft = S.ui.editInitial;
      S.ui.removeLine = null;
      render();
      document.querySelector('.line-edit')?.focus();
    },
    'cancel-line': () => { S.ui.editLine = null; render(); },
    'save-line': async (el) => {
      const edit = S.ui.editLine;
      if (!edit) return;
      const text = (S.ui.editDraft || '').trim();
      await act('edit_shared_line', { id: edit.id, seq: edit.seq, text });
      // A refused save (being sent, ended in Tico, empty) keeps the draft open with the reason shown.
      const block = (S.transcript?.blocks || []).find((b) => b.seq === edit.seq);
      const saved = S.transcript?.id === edit.id && block && block.paragraphs[0] === text;
      if (saved && S.ui.editLine?.id === edit.id && S.ui.editLine?.seq === edit.seq) {
        S.ui.editLine = null;
        render();
      }
    },
    'ask-remove-line': (el) => { S.ui.removeLine = { id: S.transcript?.id, seq: Number(el.dataset.seq) }; S.ui.editLine = null; render(); },
    'cancel-remove-line': () => { S.ui.removeLine = null; render(); },
    'remove-line': async (el) => {
      const removal = S.ui.removeLine;
      if (!removal) return;
      S.ui.removeLine = null;
      await act('remove_shared_line', { id: removal.id, seq: removal.seq });
      // A refused removal asks again, with the reason shown, while the same meeting stays open.
      const block = (S.transcript?.blocks || []).find((b) => b.seq === removal.seq);
      if (S.transcript?.id === removal.id && !block?.removed && !S.ui.removeLine) {
        S.ui.removeLine = removal;
        render();
      }
    },
    retry: (el) => { S.ui.pop = null; act('retry', { id: el.dataset.id }); },
    'audio-status': () => { S.ui.pop = null; S.ui.panel = 'transcript'; S.ui.showAudio = true; render(); },
    'toggle-transcript': () => { S.ui.panel = S.ui.panel === 'transcript' ? null : 'transcript'; S.ui.stick = true; render(); },
    'toggle-audio': () => { S.ui.showAudio = !S.ui.showAudio; render(); },
    hit: (el) => {
      const body = document.getElementById('tbody');
      if (body) showHit(body, Number(el.dataset.step));
    },
    tsearch: () => {
      S.ui.tsearch = !S.ui.tsearch;
      if (!S.ui.tsearch) S.ui.tq = '';
      else S.ui.focusNext = '.panel-search input';
      render();
    },
    copy: () => { S.ui.pop = null; copyTranscript(); },
    'close-panel': () => { S.ui.panel = null; render(); },
    'focus-ask': () => {},
    ask: submitAsk,
    'chat-retry': () => {
      const c = S.meeting?.chat;
      if (c?.retry) act(c.private ? 'ask' : 'chat_send', c.private ? { question: c.retry } : { text: c.retry });
    },
    'toggle-private': () => act('set_chat_private', { private: !S.meeting.chat.private }),
    'send-item': (el) => {
      const it = S.meeting.actions.items.find((i) => i.id === el.dataset.id);
      if (it) act('send_item', { id: it.id, owner: it.owner });
    },
    'remove-item': (el) => act('remove_item', { id: el.dataset.id }),
    'set-owner': (el) => { S.ui.pop = null; act('set_owner', { id: el.dataset.id, owner: el.dataset.actor }); },
    'choose-org': (el) => act('choose_org', { id: el.dataset.id }),
    'change-org': () => act('change_org'),
    'start-adding': () => { S.ui.focusNext = '#popover .pop-input'; act('start_adding'); },
    'people-org': (el) => { S.ui.focusNext = '#popover .pop-input'; act('choose_people_org', { id: el.dataset.id }); },
    pick: (el) => { S.ui.peopleQuery = ''; act('pick', { actor: el.dataset.actor }); },
    'remove-access': (el) => act('remove_access', { actor: el.dataset.actor }),
    'set-team': (el) => act('set_team', { team: el.dataset.team === 'true' }),
    'open-url': (el) => act('open_url', { url: el.dataset.url }),
    'dismiss-error': () => act('dismiss_error'),
    'dismiss-notice': () => act('dismiss_notice'),
    'connect-ok': () => act('confirm_connect'),
    'connect-cancel': () => act('dismiss_connect'),
    signin: () => { const address = model('signin').trim(); if (address) act('sign_in', { address }); },
    'sign-out': (el) => act('sign_out', { id: el.dataset.id }),
    'make-default': (el) => act('make_default', { id: el.dataset.id }),
    import: () => { const directory = S.ui.legacy.trim(); if (directory) act('import_legacy', { directory }); },
    'save-settings': () => {
      S.ui.saving = true;
      act('save_settings', { form: { ...S.ui.form, key: S.ui.key, xai_key: S.ui.xai } });
    },
    'speech-source': (el) => { S.ui.form.speech_source = el.dataset.source; render(); },
    'set-mic': (el) => act('set_audio', { microphone: el.dataset.mic, meeting: S.settings.meeting_audio }),
    'toggle-meeting-audio': () => act('set_audio', { microphone: S.settings.microphone, meeting: !S.settings.meeting_audio }),
  };
  const enter = {
    rename: (el) => el.blur(),
    'add-item': () => {
      const title = S.ui.addItem.trim();
      if (!title) return;
      S.ui.addItem = '';
      S.ui.focusNext = '[data-model="addItem"]';
      act('add_item', { title });
    },
    ask: submitAsk,
    'next-hit': () => {
      const body = document.getElementById('tbody');
      if (body) showHit(body, 1);
    },
    signin: () => handlers.signin(),
    'owner-first': () => {
      const first = document.querySelector('#popover [data-opt].hot') || document.querySelector('#popover [data-act="set-owner"]');
      if (first) first.click();
    },
    'people-enter': () => {
      const text = S.ui.peopleQuery.trim();
      if (!text) return;
      const first = document.querySelector('#popover [data-opt].hot') || document.querySelector('#popover [data-act="pick"]');
      if (first) first.click();
      else {
        S.ui.peopleQuery = '';
        act('add_participant', { name: text });
      }
      S.ui.focusNext = '#popover .pop-input';
    },
  };

  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-act]');
    // Clicks outside an open popover close it.
    if (S.ui.pop && !event.target.closest('#popover') && !(target && target.dataset.act === 'pop')) {
      S.ui.pop = null;
      if (!target) render();
    }
    if (!target || target.disabled) {
      if (S.ui.confirm && !event.target.closest('.row.confirm')) {
        S.ui.confirm = null;
        render();
      }
      return;
    }
    const handler = handlers[target.dataset.act];
    if (handler) {
      event.preventDefault();
      handler(target, event);
    }
  });
  document.addEventListener('input', (event) => {
    const el = event.target;
    if (el.dataset.model) {
      setModel(el.dataset.model, el.value);
      if (el.dataset.model === 'ask') el.closest('.ask')?.classList.toggle('blank', !el.value.trim());
      if (el.dataset.model === 'notes') {
        grow(el);
        paintMentions(el);
        findMention(el);
        clearTimeout(notesTimer);
        notesTimer = setTimeout(() => flushNotes(), 700);
      }
      if (el.dataset.model === 'ownerQuery' || el.dataset.model === 'peopleQuery') S.ui.hot = 0;
      if (['search', 'ownerQuery', 'peopleQuery', 'tq', 'key', 'xai'].includes(el.dataset.model) || el.dataset.model.startsWith('form.')) render();
    }
  });
  document.addEventListener('mouseover', (event) => {
    const option = event.target.closest?.('[data-opt]');
    if (!option || option.classList.contains('hot')) return;
    const box = option.closest('#popover, #mention');
    const options = [...box.querySelectorAll('[data-opt]')];
    S.ui.hot = options.indexOf(option);
    options.forEach((o) => o.classList.toggle('hot', o === option));
  });
  // Choosing a mention with the mouse keeps the caret in Notes.
  document.addEventListener('mousedown', (event) => {
    const option = event.target.closest?.('#mention [data-opt]');
    if (!option) return;
    event.preventDefault();
    const area = document.querySelector('textarea.notes');
    if (area) insertMention(area, option.dataset.name);
  });
  document.addEventListener('change', (event) => {
    const el = event.target;
    if (el.dataset.check) {
      S.ui.form[el.dataset.check] = el.checked;
      render();
    } else if (el.tagName === 'SELECT' && el.dataset.model) {
      setModel(el.dataset.model, el.value);
      render();
    }
  });
  document.addEventListener('toggle', (event) => {
    if (event.target.dataset?.details) S.ui[event.target.dataset.details] = event.target.open;
  }, true);
  document.addEventListener('focusout', async (event) => {
    const el = event.target;
    if (el.dataset?.blur === 'rename' && S.ui.titleDraft !== null) {
      const title = S.ui.titleDraft;
      S.ui.titleDraft = null;
      if (title.trim() && title.trim() !== S.meeting?.title) await act('rename', { title });
    }
    if (el.dataset?.editItem) {
      const text = el.textContent.replace(/\s+/g, ' ').trim();
      const it = S.meeting?.actions.items.find((i) => i.id === el.dataset.editItem);
      if (it && text && text !== it.title) await act('edit_item', { id: it.id, title: text });
      else if (!text && it) el.textContent = it.title;
    }
    if (el.dataset?.model === 'notes') {
      closeMention();
      await flushNotes();
      S.ui.notesDraft = null;
    }
    if (S.deferred) {
      S.deferred = false;
      setTimeout(render, 0);
    }
  });
  document.addEventListener('keydown', (event) => {
    const el = event.target;
    // A list picked from a text field: ↑/↓ move the highlight, Enter takes it (below), Esc closes.
    if (el.dataset?.list !== undefined && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault();
      moveHot('#popover', event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (S.ui.mention && el.dataset?.model === 'notes' && !event.isComposing) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        moveHot('#mention', event.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        const hot = document.querySelector('#mention [data-opt].hot');
        if (hot) {
          event.preventDefault();
          insertMention(el, hot.dataset.name);
          return;
        }
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        closeMention();
        return;
      }
    }
    // Rows that open something take Enter and Space like a button.
    if ((event.key === 'Enter' || event.key === ' ') && el.getAttribute?.('role') === 'button' && el.dataset?.act && el === event.target && !event.repeat) {
      event.preventDefault();
      el.click();
      return;
    }
    if (event.key === 'Enter' && event.shiftKey && el.dataset?.model === 'tq') {
      event.preventDefault();
      const body = document.getElementById('tbody');
      if (body) showHit(body, -1);
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      if (el.dataset?.enter) {
        event.preventDefault();
        enter[el.dataset.enter]?.(el);
        return;
      }
      if (el.dataset?.editItem) {
        event.preventDefault();
        el.blur();
        return;
      }
    }
    const meta = event.metaKey || event.ctrlKey;
    if (meta && event.key === ',') {
      event.preventDefault();
      settings();
      return;
    }
    if (meta && S.page === 'meeting' && (event.key === 'f' || event.key === 't')) {
      event.preventDefault();
      S.ui.panel = 'transcript';
      if (event.key === 'f') {
        S.ui.tsearch = true;
        S.ui.focusNext = '.panel-search input';
      }
      render();
      return;
    }
    if (event.key !== 'Escape') return;
    if (S.ui.pop) {
      S.ui.pop = null;
      render();
    } else if (S.status.connect) {
      act('dismiss_connect');
    } else if (S.ui.confirm) {
      S.ui.confirm = null;
      render();
    } else if (el.dataset?.editItem) {
      const it = S.meeting?.actions.items.find((i) => i.id === el.dataset.editItem);
      if (it) el.textContent = it.title;
      el.blur();
    } else if (el.matches?.('input, textarea')) {
      el.blur();
    } else if (S.ui.panel) {
      S.ui.panel = null;
      render();
    } else if (S.page !== 'library') {
      back();
    }
  });
  document.addEventListener('scroll', (event) => {
    if (event.target.id === 'r-page') document.querySelector('.topbar')?.classList.toggle('scrolled', event.target.scrollTop > 4);
    if (event.target.id === 'tbody') {
      const body = event.target;
      S.ui.stick = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
      S.ui.tTop = body.scrollTop;
    }
  }, true);
  // Hidden: nothing animates and no levels are sent; shown again: catch up once.
  document.addEventListener('visibilitychange', () => {
    document.documentElement.classList.toggle('hidden', document.hidden);
    invoke('set_visible', { visible: !document.hidden }).catch(() => {});
    if (!document.hidden) poll();
  });
  document.addEventListener('compositionstart', () => (S.composing = true));
  document.addEventListener('compositionend', () => (S.composing = false));

  window.addEventListener('error', (event) => bootError(event.message));
  window.addEventListener('DOMContentLoaded', () => {
    const events = window.__TAURI__.event;
    events.listen('changed', () => poll());
    events.listen('level', (event) => onLevel(event.payload));
    poll();
  });
})();
