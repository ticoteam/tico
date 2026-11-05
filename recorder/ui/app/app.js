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
    desktopOrgs: [],
    hash: { library: '', meeting: '', transcript: '', settings: '' },
    statusKey: '',
    ui: {
      search: '', ask: '', addItem: '', ownerQuery: '', shareQuery: '', tq: '', signin: null, legacy: '', key: '', xai: '',
      editLine: null, editDraft: '', editInitial: '', removeLine: null, // { id, seq }: a draft or question belongs to one meeting
      hot: 0, mention: null, address: false,
      pop: null, tsearch: false, showAudio: false, confirm: null, hideSide: false,
      share: null, finish: null,
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
    if (row.kind === 'bot' || row.bot) return `<span class="avatar bot c5 ${extra}">${icon('bot', 11)}</span>`;
    return `<span class="avatar c${tone(row.name || '')} ${extra}">${esc(initials(row.me ? 'Me' : row.name))}</span>`;
  }
  const badge = (label, tone, extra = '') => `<span class="badge ${tone}" ${extra}>${label}</span>`;

  // ---- Orgs: the desktop rail's tiles, initials on a colour picked by the org's host ----
  const hostOf = (url) => String(url || '').replace(/^[a-z]+:\/\//i, '').replace(/[/?#].*$/, '');
  function tone(text) {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
    return (h >>> 0) % 8;
  }
  function orgInitials(name, host) {
    const words = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return String(words[0] || host || '?').slice(0, 2).toUpperCase();
  }
  function orgTile(org, size = '') {
    const host = hostOf(org?.url) || org?.host || org?.name || '';
    return `<span class="otile ${size} c${tone(host)}" aria-hidden="true">${esc(orgInitials(org?.name, host))}</span>`;
  }
  const macTile = (size = 's32') => `<span class="otile ${size} mac" aria-hidden="true">${icon('mac', size === 's32' ? 16 : 13)}</span>`;
  const orgs = () => S.settings?.connections || [];
  const orgById = (id) => orgs().find((o) => o.id === id) || null;
  const defaultOrg = () => orgs().find((o) => o.default) || orgs()[0] || null;
  /** The Recorder's mark: the Liquid Dot holding a mic, on ink. */
  const MARK = '<svg width="72" height="72" viewBox="0 0 64 64" role="img" aria-label="Tico Recorder"><rect width="64" height="64" rx="14" fill="#172221"/><path fill="#56a578" d="M29.9 15A17.1 13.2 0 0 1 47 28.2A16.5 16.8 0 0 1 30.5 45A13.5 18.6 0 0 1 17 26.4A12.9 11.4 0 0 1 29.9 15Z"/><g fill="none" stroke="#172221" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="28.4" y="20.5" width="6.4" height="11.6" rx="3.2" fill="#172221"/><path d="M25.3 27.6v.9a6.3 6.3 0 0 0 12.6 0v-.9"/><path d="M31.6 34.8v3.2"/></g></svg>';
  const WORDMARK = '<svg class="word" height="30" viewBox="0 -744 1424 754" role="img" aria-label="tico"><path fill="currentColor" d="M245 10Q159 10 118 -31Q77 -73 77 -157V-615L219 -674V-155Q219 -129 234 -115Q249 -102 281 -102Q292 -102 303 -104Q313 -107 323 -111V-3Q312 4 291 7Q270 10 245 10ZM0 -400V-510H320V-400Z M343 0V-510H485V0Z M729 10Q664 10 614 -22Q563 -54 534 -114Q505 -174 505 -256Q505 -339 535 -398Q565 -457 616 -488Q668 -520 732 -520Q789 -520 835 -496Q881 -473 909 -427Q936 -382 940 -315H809Q805 -366 785 -387Q764 -408 735 -408Q712 -408 693 -394Q673 -379 663 -346Q652 -312 652 -256Q652 -200 663 -166Q673 -132 693 -117Q712 -102 735 -102Q764 -102 785 -123Q805 -144 809 -195H940Q936 -129 907 -83Q878 -38 832 -14Q786 10 729 10Z M1187 10Q1117 10 1063 -23Q1010 -56 980 -117Q950 -177 950 -257Q950 -337 980 -396Q1010 -455 1063 -488Q1116 -520 1187 -520Q1259 -520 1312 -488Q1365 -455 1395 -396Q1424 -337 1424 -257Q1424 -177 1395 -117Q1365 -56 1312 -23Q1258 10 1187 10ZM1187 -102Q1213 -102 1233 -118Q1253 -134 1265 -169Q1277 -203 1277 -257Q1277 -337 1252 -373Q1226 -408 1187 -408Q1148 -408 1123 -372Q1098 -336 1098 -257Q1098 -203 1109 -169Q1121 -134 1141 -118Q1162 -102 1187 -102Z"/><circle fill="#56a578" cx="414" cy="-662" r="82"/></svg>';
  /** Tico's own mark: the Liquid Dot, flat. Bots get the bot avatar, never the dot. */
  const ticoDot = (thinking = false) => `<i class="ldot ${thinking ? 'thinking' : ''}" aria-label="Tico"></i>`;

  // ---- Models: inputs read and write these; values are set after rendering, never in HTML ----
  const models = {
    title: [() => S.ui.titleDraft ?? S.meeting?.title ?? '', (v) => (S.ui.titleDraft = v)],
    notes: [() => S.ui.notesDraft ?? S.meeting?.notes.text ?? '', (v) => (S.ui.notesDraft = v)],
    signin: [() => S.ui.signin ?? S.status.suggested ?? '', (v) => (S.ui.signin = v)],
  };
  const model = (name) => {
    if (models[name]) return models[name][0]();
    if (name.startsWith('form.')) return S.ui.form?.[name.slice(5)] ?? '';
    if (name.startsWith('finish.')) return S.ui.finish?.[name.slice(7)] ?? '';
    return S.ui[name] ?? '';
  };
  function setModel(name, value) {
    if (models[name]) models[name][1](value);
    else if (name.startsWith('form.')) S.ui.form[name.slice(5)] = value;
    else if (name.startsWith('finish.')) { if (S.ui.finish) S.ui.finish[name.slice(7)] = value; }
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
      // The desktop app's signed-in orgs, when this build gets them (older ones send nothing).
      const desktop = v.desktop_orgs || v.status?.desktop_orgs;
      S.desktopOrgs = Array.isArray(desktop) ? desktop : [];
      window.ticoUpdate?.(v.status.update);
      if (S.status.profile && S.status.recording && !S.ui.profiled) { S.ui.profiled = true; S.page = 'meeting'; }
      const key = statusKey(v.status) + JSON.stringify(S.desktopOrgs);
      if (key !== S.statusKey) {
        S.statusKey = key;
        changed = true;
      }
      if (S.page === 'meeting' && !S.status.meeting) {
        S.page = 'library';
        S.ui.pop = null;
        S.ui.share = null;
        if (S.ui.finish?.fromMeeting) S.ui.finish = null;
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
      if (settleShare()) changed = true;
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
    if (!st.screen?.active) document.getElementById('screen-preview')?.remove();
    let left = '';
    let right = '';
    if (S.page === 'signin') return `<header class="topbar bare" data-tauri-drag-region><div class="spacer" data-tauri-drag-region></div></header>`;
    if (S.page !== 'library') {
      const label = S.page === 'settings' && S.ui.prevPage === 'meeting' ? 'Back' : 'Recordings';
      left = `<button class="back" data-act="back" title="Back (Esc)">${icon('back', 14)}${label}</button>`;
    }
    const screen = st.screen;
    if (screen?.active) right += `<button class="btn small danger screen-stop" data-act="stop-screen" title="${esc(screen.title)} · ${esc(screen.source || screen.phase)}">${icon('screen', 13)}<span>${screen.phase === 'Sharing' ? 'Sharing screen' : 'Starting…'}</span><b>Stop</b></button>`;
    if (st.demo) right += badge('Demo library', 'amber');
    if (S.page === 'meeting' && S.meeting) right += meetingActions(S.meeting);
    if (S.page === 'library') {
      const org = defaultOrg();
      if (org) right += `<button class="org-pill ${S.ui.pop?.kind === 'orgs' ? 'on' : ''}" data-act="pop" data-pop="orgs" aria-label="Org">${orgTile(org)}<span class="name">${esc(org.name)}</span>${icon('down', 12)}</button>`;
      else right += `<button class="btn small" data-act="settings">Sign in</button>`;
      right += `<button class="icon-btn" data-act="settings" title="Settings (⌘,)" aria-label="Settings">${icon('gear')}</button>`;
    }
    return `<header class="topbar" data-tauri-drag-region>${left}<div class="spacer" data-tauri-drag-region></div>${right}</header>`;
  }
  /** Share live, Finish and More. Share live shows who can join once it is shared. */
  function meetingActions(m) {
    const st = S.status;
    const sending = st.sending?.id === m.id ? st.sending : null;
    let html = '';
    const ended = m.live_state === 'ended';
    if (m.live && !ended && !m.discarded) {
      const who = m.people.team ? `Anyone on ${m.people.org}` : `${peopleIn(m).length || 'Only'} ${peopleIn(m).length === 1 ? 'person' : peopleIn(m).length ? 'people' : 'you'}`;
      const paused = m.live_state === 'paused';
      html += `<button class="share-btn live ${S.ui.share ? 'on' : ''}" data-act="share-open" title="${esc(m.place)}"><i class="live-dot ${paused ? 'amber' : ''}"></i><span>${paused ? 'Paused' : 'Live'}<span class="who"> · ${esc(who)}</span></span></button>`;
    } else if (m.discarded && m.discard_pending) {
      html += badge('Ending in Tico', 'amber');
    } else if (ended && !m.sent) {
      html += badge('Ended in Tico', '');
    } else if (!m.sent && !m.kept_local && !m.discarded && m.connected) {
      html += `<button class="share-btn ${S.ui.share ? 'on' : ''}" data-act="share-open">${icon('people', 15)}<span>Share live</span></button>`;
    }
    if (m.sent) html += badge(`${icon('check', 12)}In Tico`, '');
    else if (sending) html += `<button class="btn small" disabled><span class="spin"></span>${esc(sending.label)}</button>`;
    else html += `<button class="btn" data-act="finish-open" data-id="${esc(m.id)}" ${st.busy ? 'disabled' : ''}>Finish</button>`;
    html += `<button class="icon-btn ${S.ui.pop?.kind === 'more' ? 'on' : ''}" data-act="pop" data-pop="more" title="More" aria-label="More">${icon('more')}${m.audio.attention ? '<span class="dot"></span>' : ''}</button>`;
    return html;
  }
  /** Teammates in the live meeting other than me, and names typed here. */
  const peopleIn = (m) => m.people.rows.filter((row) => !row.me);

  // ---- Notices ----
  function errorNotice() {
    const error = S.status.error || S.status.screen?.error;
    if (!error) return '';
    return `<div class="notice">${icon('alert')}<span class="grow selectable">${esc(error)}</span><button class="icon-btn dismiss" data-act="dismiss-error" title="Dismiss" aria-label="Dismiss">${icon('close', 12)}</button></div>`;
  }

  // ---- Sign in ----
  function signinPage() {
    const st = S.status;
    const desktop = S.desktopOrgs;
    const address = S.ui.address || !desktop.length;
    let intro = `<div class="intro">${MARK}<div class="brand">${WORDMARK}<span class="sub">Recorder</span></div><p>Record any meeting on this Mac. It goes to the Tico you pick.</p>`;
    if (st.signing_in) intro += `<div class="waiting"><span class="spin"></span>Choose Allow in ${S.ui.signingDesktop ? 'Tico' : 'your browser'}…</div>`;
    else if (address) {
      intro += `<div class="address"><input class="field" data-model="signin" data-enter="signin" placeholder="your-tico.example" spellcheck="false" aria-label="Tico address"><button class="btn primary" data-act="signin" ${st.busy ? 'disabled' : ''}>Sign in</button></div>`;
      if (desktop.length) intro += `<button class="link" data-act="address-off">Cancel</button>`;
    }
    intro += errorNotice() + '</div>';
    if (!desktop.length) return `<div class="signin single">${intro}</div>`;
    // One row per org of the Tico desktop app; Allow signs in to that org.
    let card = `<div class="orgs">`;
    for (const org of desktop) {
      const tail = org.connected
        ? `<span class="check" aria-label="Added">${icon('check')}</span>`
        : st.signing_in && S.ui.signingDesktop === org.url
          ? `<span class="spin"></span>`
          : `<button class="btn small primary" data-act="signin-desktop" data-url="${esc(org.url)}" ${st.busy ? 'disabled' : ''} aria-label="Allow ${esc(org.name)}">Allow</button>`;
      card += `<div class="org-row">${orgTile(org, 's32')}<div class="grow"><div class="name">${esc(org.name)}</div>${org.email ? `<div class="sub">${esc(org.email)}</div>` : ''}</div>${tail}</div>`;
    }
    if (!address) card += `<button class="org-row dim" data-act="address-on"><span class="otile s32 add">+</span><div class="grow"><div class="name">Another Tico</div></div></button>`;
    return `<div class="signin">${intro}${card}</div>`;
  }

  // ---- Library ----
  function library() {
    const lib = S.library;
    if (!lib) return '';
    const st = S.status;
    const q = S.ui.search.trim().toLowerCase();
    const rows = lib.rows.filter((row) => !q || row.title.toLowerCase().includes(q));
    let html = `<div class="column"><div class="lib-head"><h1 class="page-title">Recordings</h1>`;
    if (lib.rows.length) html += `<label class="search">${icon('search', 14)}<input data-model="search" placeholder="Search" spellcheck="false" aria-label="Search"></label>`;
    html += `</div>${errorNotice()}`;
    if (!st.recording && lib.events.length && !q) {
      html += `<div class="label group">Coming up${lib.calendar_stale ? ` · <span style="color:var(--amber)">Calendar out of date</span>` : ''}</div><div class="events">`;
      html += lib.events.map((event, index) => {
        const start = new Date(event.start);
        return `<div class="event"><span class="time">${isNaN(start) ? '' : esc(timeOf(start))}</span><span class="name">${esc(event.title || 'Meeting')}</span><button class="btn small" data-act="record-event" data-index="${index}"><i class="reddot"></i>Record</button></div>`;
      }).join('');
      html += '</div>';
    }
    if (!lib.rows.length) html += `<div class="empty"><div class="big">No recordings yet</div></div>`;
    else if (!rows.length) html += `<div class="empty"><div class="big">No matches</div></div>`;
    let day = '';
    let open = false;
    for (const row of rows) {
      const date = new Date(row.started_at);
      const label = isNaN(date) ? '' : dayOf(date);
      if (label !== day) {
        if (open) html += '</div>';
        html += `<div class="label group">${esc(label)}</div><div class="rows">`;
        day = label;
        open = true;
      }
      html += libraryRow(row, date);
    }
    if (open) html += '</div>';
    return html + '</div>';
  }
  /** A row's pill, only when it says something: live, in Tico, still transcribing, or a problem. */
  function rowStatus(row) {
    const status = row.status;
    if (!status) return '';
    const tone = status.tone === 'live' ? 'live' : status.tone === 'red' ? 'red' : status.tone === 'amber' ? 'amber' : '';
    const lead = status.label === 'Recording' ? '<i class="pulse"></i>' : '';
    return badge(lead + esc(status.label), tone, row.sync_error ? `title="${esc(row.sync_error)}"` : '');
  }
  function libraryRow(row, date) {
    const id = esc(row.id);
    if (S.ui.confirm === row.id) {
      return `<div class="row confirm"><span class="otile s32 rec">${icon('trash', 15)}</span><div class="body"><div class="title">Delete “${esc(row.title)}”?</div></div><div class="tail"><button class="btn small quiet" data-act="cancel-delete">Cancel</button><button class="btn small danger" data-act="delete" data-id="${id}">Delete</button></div></div>`;
    }
    const org = row.org ? { ...row.org, ...(orgById(row.org.id) || {}) } : null;
    const menu = S.ui.pop?.anchorId === row.id ? 'menu-open' : '';
    const sub = [isNaN(date) ? '' : timeOf(date), length(row.duration_ms), org ? org.name : 'This Mac only'].filter(Boolean).map(esc).join(' · ');
    const save = row.local && row.can_send ? `<button class="btn small" data-act="finish-open" data-id="${id}">Save to Tico</button>` : '';
    const trash = row.recording ? '' : `<button class="icon-btn trash hover-only" data-act="ask-delete" data-id="${id}" title="Delete" aria-label="Delete ${esc(row.title)}">${icon('trash', 15)}</button>`;
    return `<div class="row ${menu}" data-act="open" data-id="${id}" role="button" tabindex="0" aria-label="${esc(row.title)}">
      ${org ? orgTile(org, 's32') : macTile()}
      <div class="body"><div class="title">${esc(row.title)}</div><div class="sub">${sub}</div></div>
      <div class="tail">${trash}${rowStatus(row)}${save}</div>
    </div>`;
  }

  // ---- Meeting: notes and items on the left, the transcript and private questions on the right ----
  function meeting() {
    const m = S.meeting;
    if (!m) return '';
    const names = peopleIn(m).map((row) => row.name);
    const line = [fullDate(m.started_at), names.join(', ')].filter(Boolean).map(esc).join(' · ');
    let html = `<div class="main" id="main"><div class="main-inner"><input class="title-input" data-model="title" data-enter="rename" data-blur="rename" spellcheck="false" ${m.title_editable ? '' : 'disabled'} aria-label="Title">`;
    html += `<div class="people-line">${line}${m.live_url ? ` · <button class="link" data-act="open-url" data-url="${esc(m.live_url)}">Open in Tico</button>` : ''}</div>`;
    if (m.sync_error) html += `<div class="notice">${icon('alert')}<span class="grow selectable">${esc(m.sync_error)}</span></div>`;
    for (const note of m.setup) html += `<div class="notice">${icon('alert')}<span class="grow">${esc(note)}</span><button class="link" data-act="settings">Settings</button></div>`;
    html += errorNotice();
    html += actions(m.actions);
    html += `<section class="section"><div class="label">Notes</div><div class="notes-wrap"><div class="notes-backdrop" aria-hidden="true"></div><textarea class="notes" data-model="notes" data-defer placeholder="Write notes… @ to mention" ${m.notes.editable ? '' : 'disabled'} aria-label="Notes"></textarea></div></section>`;
    html += '</div></div>';
    if (!S.ui.hideSide) html += sidebar(m);
    return html;
  }
  /** Tasks: ones already in Tico by number, then proposals drafted as people talk, each created or dismissed. */
  function actions(a) {
    if (a.saved) return sharedActions(a);
    let html = `<section class="section"><div class="label head">Tasks<span class="grow"></span><button class="btn small" data-act="add-open">${icon('plus', 12)}Add</button></div>`;

    if (a.draft_status) html += `<div class="notice inline">${icon('alert')}<span class="grow">${esc(a.draft_status)}</span></div>`;
    if (a.mode === 'choose' && a.items.some((it) => !it.sent)) {
      html += `<div class="choose-org"><span class="faint">Owners from</span>${a.orgs.map((o) => `<button class="btn small" data-act="choose-org" data-id="${esc(o.id)}">${esc(o.name)}</button>`).join('')}</div>`;
    }
    const sent = a.items.filter((it) => it.sent);
    const proposed = a.items.filter((it) => !it.sent);
    if (sent.length) html += `<ul class="items tasks">${sent.map(task).join('')}</ul>`;
    if (proposed.length) html += `<ul class="items">${proposed.map(proposal).join('')}</ul>`;
    if (S.ui.adding || !a.items.length) html += `<div class="add-item"><span class="check">${icon('plus', 13)}</span><input data-model="addItem" data-enter="add-item" data-blur="add-close" placeholder="Add a task" spellcheck="false" aria-label="Add a task"></div>`;
    return html + '</section>';
  }
  const sharedDrafts = new Map();
  const sharedKey = id => `${S.meeting.id}:${S.meeting.actions.shared_scope || ''}:${id}`;
  function sharedDraft(id) {
    const key = sharedKey(id);
    if (!sharedDrafts.has(key)) sharedDrafts.set(key, {patch: {}, flight: null, error: '', timer: 0, version:null, current:null});
    return sharedDrafts.get(key);
  }
  const mergeShared = (a, b) => ({...a, ...b, detail: {...a.detail, ...b.detail}});
  function sharedActions(a) {
    const data = a.shared;
    if (data?.gone) return '';
    if (!data || !data.sections) return '<section class="section"><h2>Tasks</h2>' + (data?.gone ? '' : '<span>Loading…</span>') + '</section>';
    const here = (data.presence?.actors || []).filter(p => p.actor !== a.actor).map(p => p.name);
    return `<section class="section"><div class="section-head"><h2>Tasks</h2><span class="aside">${here.length ? esc(here.join(', ') + (here.length === 1 ? ' is here' : ' are here')) : ''}</span></div>` +
      Object.values(data.sections).flat().filter(i => i.status !== 'dismissed').map(it => {
        if (it.status === 'pushed') return `<div class="item sent">${esc(it.text)} · Sent${it.result_ref ? ` · <button class="link" data-act="open-url" data-url="${esc(a.shared_url + '/#/task/' + encodeURIComponent(it.result_ref))}">Open</button>` : ''}</div>`;
        const draft = sharedDraft(it.id), current = mergeShared(mergeShared(it, draft.inflightPatch || {}), draft.patch), detail = current.detail || {};
        if (!draft.flight && !draft.sending && !Object.keys(draft.patch).length && ![...document.querySelectorAll('[data-shared-item]')].find(n=>n.dataset.sharedItem===it.id)?.contains(document.activeElement)) { draft.version = it.version; draft.base = it; }
        const fields = it.section === 'task' ? ['owner', 'due', 'body'] : it.section === 'doc' ? ['document', 'change', 'why'] : ['side', 'app', 'area'];
        const owners = a.owners.filter(o => !data.audience || data.audience.includes(o.actor));
        return `<div class="shared-item" data-shared-item="${esc(it.id)}"><label>Title<textarea data-defer data-shared-field="text" aria-label="Title">${esc(current.text)}</textarea></label>` + fields.map(key => {
          const label = ({body:'Details', owner:'Owner', due:'Due'})[key] || key;
          if (key === 'owner') return `<label>Owner<select data-defer data-shared-field="owner" aria-label="Owner"><option value="">Choose</option>${owners.map(o => `<option value="${esc(o.actor)}" ${o.actor === detail.owner ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}</select></label>`;
          const value = key === 'due' ? String(detail[key] || '').slice(0, 10) : detail[key] || '';
          return `<label>${label}<${key === 'body' ? 'textarea' : 'input'} data-defer data-shared-field="${key}" aria-label="${label}" ${key === 'due' ? 'type="date"' : ''} ${key === 'body' ? '' : `value="${esc(value)}"`}>${key === 'body' ? esc(detail.body || '') + '</textarea>' : ''}</label>`;
        }).join('') + `<div class="aside">${it.updated_by ? esc((a.owners.find(o => o.actor === it.updated_by)?.name || it.updated_by) + ' edited · just now') : ''}</div>
          <span class="error" data-shared-error ${draft.error ? '' : 'hidden'}>Not saved · <button class="link" data-act="shared-retry" data-id="${esc(it.id)}">Retry</button></span>
          ${draft.current ? `<div class="aside">Current: ${esc(draft.current.text)} · ${esc(Object.values(draft.current.detail || {}).join(' · '))}</div>` : ''}
          <div class="actions"><button class="btn small" data-act="shared-dismiss" data-id="${esc(it.id)}">Dismiss</button><button class="btn small primary" data-act="shared-send" data-id="${esc(it.id)}" ${draft.sending || !data.can_send ? 'disabled' : ''}>Send${detail.owner ? ' to ' + esc(owners.find(o => o.actor === detail.owner)?.name || detail.owner) : ''}</button></div></div>`;
      }).join('') + '</section>';
  }
  const sharedFieldValue = field => field.dataset.sharedField === 'due' && field.value ? new Date(field.value + 'T12:00:00').toISOString() : field.value;
  function sharedChanged(field) {
    const id = field.closest('[data-shared-item]').dataset.sharedItem, draft = sharedDraft(id), key = field.dataset.sharedField;
    if (key === 'text') draft.patch.text = field.value;
    else (draft.patch.detail ||= {})[key] = sharedFieldValue(field);
    clearTimeout(draft.timer);
    if (!draft.error) draft.timer = setTimeout(() => sharedSave(id), 700);
  }
  function sharedDifference(value, base = {}) {
    const patch = {};
    if (value.text !== base.text) patch.text = value.text;
    for (const [key, part] of Object.entries(value.detail || {})) {
      const old = base.detail?.[key] || '';
      if (key === 'due' ? String(part).slice(0,10) !== String(old).slice(0,10) : part !== old) (patch.detail ||= {})[key] = part;
    }
    return patch;
  }
  async function sharedSave(id) {
    const draft = sharedDraft(id);
    clearTimeout(draft.timer);
    if (draft.flight || draft.sending || !Object.keys(draft.patch).length) return draft.flight?.catch(() => {});
    const patch = draft.patch; draft.patch = {}; draft.error = '';
    draft.inflightPatch = patch;
    draft.flight = invoke('shared_item', {id, body:{...patch, version:draft.version}});
    try { const reply = await draft.flight; if (reply.conflict) { draft.current=reply.conflict; draft.version=reply.conflict.version; draft.base=reply.conflict; throw new Error('This item changed'); } draft.version=reply.item.version; draft.base=reply.item; draft.current=null; }
    catch (e) { draft.patch = mergeShared(patch, draft.patch); draft.error = String(e); }
    finally { draft.flight = null; draft.inflightPatch = null; }
    if (!draft.error && Object.keys(draft.patch).length && !draft.sending) return sharedSave(id);
    await poll();
    const node = [...document.querySelectorAll('[data-shared-item]')].find(n => n.dataset.sharedItem === id);
    if (node) node.querySelector('[data-shared-error]').hidden = !draft.error;
    render();
  }
  async function sharedFinish(button, send) {
    const id = button.dataset.id, draft = sharedDraft(id), node = button.closest('[data-shared-item]');
    if (draft.sending) return;
    draft.sending = true; clearTimeout(draft.timer); node.querySelectorAll('button,input,textarea,select').forEach(f => f.disabled = true);
    const body = {detail:{}};
    for (const f of node.querySelectorAll('[data-shared-field]')) {
      if (f.dataset.sharedField === 'text') body.text = f.value;
      else body.detail[f.dataset.sharedField] = sharedFieldValue(f);
    }
    try {
      if (draft.flight) { try { await draft.flight; } catch {} if(draft.error) throw new Error(draft.error); }
      const reply = await invoke('shared_item', {id, action:send ? 'send' : null, body:send ? {...sharedDifference(body,draft.base),version:draft.version} : {status:'dismissed',version:draft.version}});
      if (reply.conflict) { draft.current=reply.conflict; draft.version=reply.conflict.version; draft.patch=mergeShared(sharedDifference(body,draft.base),draft.patch); draft.base=reply.conflict; throw new Error('This item changed'); }
      draft.patch = {}; draft.error = ''; draft.current=null;
    } catch (e) { draft.error = String(e); }
    finally { draft.sending = false; node.querySelectorAll('button,input,textarea,select').forEach(f => f.disabled = false); }
    await poll(); render();
  }
  /** Tico's task number, when the task's id or link ends in one. */
  function taskNumber(it) {
    const id = String(it.task_id || '');
    if (/^\d+$/.test(id)) return id;
    const tail = /\/(\d+)\/?(?:[?#].*)?$/.exec(String(it.task_url || ''));
    return tail ? tail[1] : '';
  }
  function task(it) {
    const n = taskNumber(it);
    const open = it.task_url ? `<button class="link" data-act="open-url" data-url="${esc(it.task_url)}">Open</button>` : '';
    const sub = [it.owner_name, it.sent_to].filter(Boolean).map(esc).join(' · ');
    return `<li class="item task"><span class="check done">${icon('check', 11)}</span><div class="content"><div class="title selectable">${n ? `<span class="num">#${esc(n)}</span> ` : ''}${esc(it.title)}</div><div class="sub">${sub}${sub && open ? ' · ' : ''}${open}</div></div></li>`;
  }
  function proposal(it) {

    const id = esc(it.id);
    const picking = S.ui.pop?.kind === 'owner' && S.ui.pop.id === it.id;
    const at = it.quote || it.at_ms ? `<span class="at" title="${esc(it.quote)}">${clock(it.at_ms)}</span><span>·</span>` : '';
    const doc = it.document ? `<span>· ${esc(it.document)}</span>` : '';
    const owner = it.owner_name
      ? `<button class="owner" data-act="pop" data-pop="owner" data-id="${id}">${esc(it.owner_name)}</button>`
      : `<button class="owner empty" data-act="pop" data-pop="owner" data-id="${id}">Owner</button>`;
    const anyway = S.ui.createOk === it.id;
    const why = it.can_send ? '' : it.send_blocked || (it.owner ? '' : 'Choose an owner');
    const create = it.sending
      ? `<span class="spin" style="margin:0 8px"></span>`
      : `<button class="btn small" data-act="create-item" data-id="${id}" ${it.can_send ? '' : 'disabled'} title="${esc(why)}">${anyway ? 'Create anyway' : 'Create'}</button>`;
    return `<li class="item proposal ${picking ? 'picking' : ''}">
      <span class="check dashed"></span>
      <div class="content"><span class="title" contenteditable="true" data-defer data-edit-item="${id}" data-key="item-${id}" spellcheck="false" role="textbox" aria-label="Task">${esc(it.title)}</span><div class="sub">${at}${owner}${doc}</div>${anyway ? `<div class="error">May quote paused speech</div>` : ''}${it.error ? `<div class="error selectable">${esc(it.error)}</div>` : ''}</div>
      <div class="item-tail">${create}<button class="icon-btn tiny" data-act="remove-item" data-id="${id}" title="Dismiss" aria-label="Dismiss">${icon('close', 12)}</button></div>
    </li>`;
  }

  // ---- Transcript sidebar ----
  function speakerColors(blocks) {
    const colors = {};
    let n = 0;
    for (const b of blocks) if (!(b.speaker in colors)) colors[b.speaker] = `var(--speaker-${(n++ % 6) + 1})`;
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
  /** The sidebar's frame. Its rows are patched into #tbody one by one (see `patchTranscript`). */
  function sidebar(m) {
    const a = m.audio;
    const rec = S.status.recording;
    const stream = rec && rec.id === m.id ? streamState(rec) : null;
    let status = '';
    // A spinner only while transcription really runs; waiting on Settings is said plainly.
    if (stream?.bad) status = badge('Reconnecting', 'amber');
    else if (stream?.live || (rec && rec.id === m.id)) status = badge('Live', 'live');
    else if (a.pending && m.setup.length) status = `<span class="status">Waiting ${a.pending}</span>`;
    else if (a.pending) status = `<span class="status"><span class="spin"></span>Transcribing ${a.pending}</span>`;
    else if (a.finding_speakers) status = `<span class="status"><span class="spin"></span>Speakers</span>`;
    let html = `<aside class="sidebar"><div class="side-head"><h3>Transcript</h3>${status}<span class="grow"></span>`;
    if (a.attention) html += `<button class="icon-btn ${S.ui.showAudio ? 'on' : ''}" data-act="toggle-audio" title="Audio" aria-label="Audio">${icon('mic', 15)}<span class="dot"></span></button>`;
    html += `<button class="icon-btn ${S.ui.tsearch ? 'on' : ''}" data-act="tsearch" title="Search (⌘F)" aria-label="Search transcript">${icon('search', 15)}</button></div>`;
    if (S.ui.tsearch) html += `<label class="side-search">${icon('search', 13)}<input data-model="tq" data-enter="next-hit" placeholder="Search transcript" spellcheck="false" aria-label="Search transcript"><span class="hits" id="thits"></span><button class="icon-btn tiny" data-act="hit" data-step="-1" title="Previous (⇧↩)" aria-label="Previous match">${icon('up', 12)}</button><button class="icon-btn tiny" data-act="hit" data-step="1" title="Next (↩)" aria-label="Next match">${icon('down', 12)}</button></label>`;
    if (S.ui.showAudio) {
      let box = '';
      if (a.warning) box += `<div class="warn selectable">${esc(a.warning)}</div>`;
      for (const input of a.inputs) box += `<div class="rowline"><b>${esc(input.label)}</b><span class="${input.ok ? 'ok' : 'warn'}">${esc(input.state)}</span></div>`;
      if (stream?.note) box += `<div class="warn selectable">${esc(stream.note)}</div>`;
      for (const line of a.failures) box += `<div class="warn selectable">${esc(line)}</div>`;
      if (a.more) box += `<div class="faint">+${a.more} more</div>`;
      if (a.failed) box += `<div><button class="link" data-act="retry" data-id="${esc(m.id)}">Retry ${a.failed} failed</button></div>`;
      if (!box) box = `<div class="faint">No audio yet</div>`;
      html += `<div class="audio-box">${box}</div>`;
    }
    html += `<div class="side-body" id="tbody"></div>`;
    const chat = m.chat;
    let foot = '';
    if (chat.error) foot += `<div class="ask-error">${icon('alert', 13)}<span class="grow selectable">${esc(chat.error)}</span>${chat.retry && chat.ready ? `<button class="link" data-act="ask-retry">Retry</button>` : ''}</div>`;
    foot += `<label class="ask"><input data-model="ask" data-enter="ask" placeholder="Ask Tico about this meeting" spellcheck="false" aria-label="Ask Tico about this meeting"><button class="send" data-act="ask" ${chat.thinking ? 'disabled' : ''} aria-label="Ask">${chat.thinking ? '<span class="spin"></span>' : icon('arrowUp', 14)}</button></label>`;
    return html + `<div class="side-foot">${foot}</div></aside>`;
  }
  /** Private questions to Tico and their answers, as cards between the turns they followed. */
  function askCards(m) {
    const cards = [];
    let question = '';
    for (const msg of m.chat.messages) {
      if (!msg.private) continue;
      if (msg.pending) {
        cards.push({ at: Infinity, html: `<div class="asked pending" title="${esc(msg.text)}">${ticoDot(true)}<div class="grow"><div class="head">You asked · only you see this</div><div class="typing"><i></i><i></i><i></i></div></div></div>` });
      } else if (msg.me) {
        question = msg.text;
      } else {
        const at = new Date(msg.time).getTime();
        const text = esc(msg.text).split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
        cards.push({ at: isNaN(at) ? Infinity : at, html: `<div class="asked" title="${esc(question)}">${ticoDot()}<div class="grow"><div class="head">You asked · only you see this</div><div class="text">${text}</div></div></div>` });
        question = '';
      }
    }
    return cards;
  }
  /** One string per row of the sidebar: turns, private answers, and the softer words still being spoken. */
  function transcriptItems() {
    const m = S.meeting;
    const a = m.audio;
    const blocks = transcriptBlocks();
    const q = S.ui.tsearch ? S.ui.tq.trim().toLowerCase() : '';
    const recording = m.state === 'recording' || S.status.recording?.id === m.id;
    const cards = askCards(m);
    if (!blocks.length && !cards.length) {
      return [`<div class="side-empty">${recording ? 'Listening' : a.pending ? 'Transcribing…' : 'No speech transcribed'}</div>`];
    }
    const items = [];
    const colors = speakerColors(blocks);
    const start = new Date(m.started_at).getTime();
    let card = 0;
    for (const b of blocks) {
      const at = start + b.start_ms;
      while (card < cards.length && cards[card].at <= at) items.push(cards[card++].html);
      const when = isNaN(at) ? clock(b.start_ms) : timeOf(new Date(at));
      items.push(`<div class="turn ${b.partial ? 'partial' : ''} ${b.removed ? 'removed' : ''}"><div class="who"><b style="color:${colors[b.speaker]}">${esc(b.speaker)}</b><span class="time">${esc(when)}</span></div><div class="said">${sharedLine(b, q)}</div></div>`);
    }
    while (card < cards.length) items.push(cards[card++].html);
    const tail = blocks[blocks.length - 1];
    if (tail && a.pending && !m.setup.length && !recording && !q && !tail.partial) items.push(`<div class="turn"><div class="typing"><i></i><i></i><i></i></div></div>`);
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
    // A fresh body keeps where the person had scrolled to.
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

  // ---- Floating bars ----
  function levels(count = 5) {
    // Bar heights and the clock are set by live(), so the markup stays the same as they move.
    return `<span class="levels" id="levels">${'<b></b>'.repeat(count)}</span>`;
  }
  function dock() {
    if (S.page === 'settings' || S.page === 'signin') return '';
    const st = S.status;
    const rec = st.recording;
    const caret = `<button class="caret ${S.ui.pop?.kind === 'source' ? 'on' : ''}" data-act="pop" data-pop="source" title="Audio sources" aria-label="Audio sources">${icon('down', 12)}</button>`;
    const stop = (title) => `<button class="rec stop" data-act="stop" title="${title}" aria-label="Stop"><i></i>${streamState(rec).bad ? '<span class="rec-dot"></span>' : ''}</button>`;
    let bar = '';
    let foot = st.message || '';
    let side = 'center';
    if (S.page === 'library') {
      if (rec) {
        bar = `<div class="bar">${stop('Stop')}${levels()}<span class="elapsed" id="elapsed"></span>${caret}<button class="chip" data-act="open-active">Open</button></div>`;
        foot = foot || streamState(rec).note;
      } else {
        bar = `<div class="record-pill" data-act="record" role="button" tabindex="0" aria-label="Record"><span class="rec"><i></i></span>Record${caret}</div>`;
      }
    } else {
      const m = S.meeting;
      if (!m) return '';
      side = 'left';
      if (rec && rec.id === m.id) {
        bar = `<div class="bar">${stop('Stop recording')}${levels()}<span class="elapsed" id="elapsed"></span>${caret}</div>`;
        foot = foot || streamState(rec).note;
      } else if (rec) {
        bar = `<div class="bar" style="padding-right:8px"><button class="rec" data-act="open-active" title="Open the recording in progress" aria-label="Open the recording in progress"><i></i></button><span class="elapsed" id="elapsed"></span><button class="chip" data-act="open-active">Open</button></div>`;
      }
    }
    // A recording problem worth knowing about, in plain words; closing it keeps it closed.
    const notice = st.notice && rec ? `<div class="dock-notice" role="status">${icon('alert', 14)}<span class="grow">${esc(st.notice)}</span><button class="icon-btn" data-act="dismiss-notice" title="Dismiss" aria-label="Dismiss">${icon('close', 12)}</button></div>` : '';
    const toast = foot ? `<div class="toast">${esc(foot)}</div>` : '';
    if (!bar && !notice && !toast) return '';
    const style = side === 'left' ? `style="right:${S.ui.hideSide ? 0 : 'min(400px, 46%)'}"` : '';
    return `<div class="dock ${side}" ${style}>${notice}${toast}${bar}</div>`;
  }

  // ---- Share live: nothing reaches Tico until Share live or Copy link ----
  function openShare() {
    const m = S.meeting;
    if (!m) return;
    if (S.ui.share) { S.ui.share = null; render(); return; }
    S.ui.pop = null;
    const org = m.live_org || (orgById(m.people?.orgs?.[0]?.id) && m.targets?.[0]?.id) || defaultOrg()?.id || '';
    S.ui.share = { id: m.id, org, team: false, picks: [], busy: false, error: '', copied: false, after: '' };
    S.ui.shareQuery = '';
    S.ui.hot = 0;
    S.ui.focusNext = '#sheet .add-field input';
    render();
    // Names to add come from the org it would go to; loading them sends nothing.
    if (!m.live && org) act('choose_people_org', { id: org });
  }
  /** Settles a share the app is starting: closes once live, or shows why it did not start. */
  function settleShare() {
    const d = S.ui.share;
    if (!d?.busy || !S.meeting || S.meeting.id !== d.id) return false;
    if (S.meeting.live) {
      d.busy = false;
      if (d.after === 'copy') { d.after = ''; copyMeetingLink(d); return true; }
      S.ui.share = null;
      return true;
    }
    const error = S.status.error || S.meeting.people.error;
    if (error && !S.status.busy) {
      d.busy = false;
      d.error = error;
      return true;
    }
    return false;
  }
  function shareSheet() {
    const d = S.ui.share;
    const m = S.meeting;
    if (!d || !m || m.id !== d.id || S.page !== 'meeting') return '';
    const p = m.people;
    const live = !!m.live;
    const org = orgById(live ? m.live_org : d.org) || (live ? { name: p.org } : null);
    const team = live ? p.team : d.team;
    const many = !live && orgs().length > 1;
    let html = `<div class="sheet share" id="sheet" role="dialog" aria-label="Share live"><h2>Share this meeting live</h2>`;
    html += `<${many ? 'button' : 'div'} class="org-choice" ${many ? 'data-act="pop" data-pop="share-org"' : ''}>${org ? orgTile(org, 's28') : ''}<span class="grow">${esc(org?.name || 'Choose an org')}</span>${many ? icon('down', 12) : ''}</${many ? 'button' : 'div'}>`;
    const blocked = live && !p.can_share;
    html += `<div class="radios" role="radiogroup" aria-label="Who can join"><div class="label">Who can join</div>`;
    html += `<button class="radio ${team ? 'on' : ''}" data-act="share-team" data-team="true" role="radio" aria-checked="${team}" ${blocked ? 'disabled' : ''}><i></i>Anyone on ${esc(org?.name || 'the org')}</button>`;
    html += `<button class="radio ${team ? '' : 'on'}" data-act="share-team" data-team="false" role="radio" aria-checked="${!team}" ${blocked ? 'disabled' : ''}><i></i>Only people I add</button></div>`;
    if (!p.blocked || !live) {
      html += `<div class="add-field"><input class="field" data-model="shareQuery" data-enter="share-enter" data-list placeholder="Add a person or bot" spellcheck="false" aria-label="Add a person or bot">`;
      const q = S.ui.shareQuery.trim().toLowerCase();
      if (q) {
        const taken = new Set(d.picks.map((x) => x.actor));
        const matches = ranked(p.candidates.filter((c) => !taken.has(c.actor)), q).slice(0, 8);
        const hot = Math.min(S.ui.hot, matches.length - 1);
        html += `<div class="matches">${matches.length ? matches.map((c, i) => `<button class="menu-item ${i === hot ? 'hot' : ''}" data-opt data-act="share-pick" data-actor="${esc(c.actor)}" data-name="${esc(c.name)}" data-bot="${c.bot ? 1 : ''}">${avatar(c)}<span class="grow">${esc(c.name)}</span></button>`).join('') : `<div class="pop-text">${p.candidates.length ? 'No match' : '<span class="spin" style="display:inline-block;vertical-align:-2px"></span>'}</div>`}</div>`;
      }
      html += `</div>`;
    }
    const chips = live
      ? peopleIn(m).map((row) => `<span class="chip-person ${row.pending ? 'pending' : ''} ${row.removable ? '' : 'plain'}">${avatar(row)}<span class="name">${esc(row.name)}</span>${row.removable ? `<button class="x" data-act="remove-access" data-actor="${esc(row.actor)}" aria-label="Remove ${esc(row.name)}">${icon('close', 10)}</button>` : ''}</span>`)
      : d.picks.map((row) => `<span class="chip-person">${avatar(row)}<span class="name">${esc(row.name)}</span><button class="x" data-act="share-unpick" data-actor="${esc(row.actor)}" aria-label="Remove ${esc(row.name)}">${icon('close', 10)}</button></span>`);
    if (chips.length) html += `<div class="chips">${chips.join('')}</div>`;
    const error = d.error || (live ? p.error || p.blocked : '');
    if (error) html += `<div class="sheet-error">${icon('alert', 13)}<span class="selectable">${esc(error)}</span></div>`;
    if (d.copied) html += `<div class="sheet-ok">Link copied</div>`;
    const busy = d.busy || S.status.busy;
    html += `<div class="sheet-foot"><button class="link-btn" data-act="share-copy" ${busy || (!live && !d.org) ? 'disabled' : ''}>${icon('link', 14)}Copy link</button>`;
    html += live
      ? `<button class="btn primary" data-act="share-close">Done</button>`
      : `<button class="btn primary" data-act="share-go" ${busy || !d.org ? 'disabled' : ''}>${d.busy ? '<span class="spin"></span>' : ''}Share live</button>`;
    return html + `</div></div>`;
  }
  async function copyMeetingLink(d) {
    const m = S.meeting;
    if (!m) return;
    try {
      const link = await invoke('meeting_link', { id: m.id, destination: m.live_org || d.org, confirmed: true, copy: true });
      if (!link?.url) throw new Error('Update Tico to copy meeting links.');
      if (!link.copied) await copyText(link.url);
      d.copied = true;
      d.error = '';
    } catch (error) {
      d.error = String(error?.message || error);
    }
    await poll();
    render();
  }
  async function copyText(text) {
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(text); return; } catch { /* Use the selected text fallback. */ }
    }
    const area = document.createElement('textarea'); area.value = text; document.body.appendChild(area); area.select();
    const copied = document.execCommand('copy'); area.remove();
    if (!copied) throw new Error('Could not copy the link.');
  }

  // ---- Finish: where it is saved and who can see it ----
  function finishSource(id) {
    if (S.meeting?.id === id) {
      const m = S.meeting;
      return { id, title: m.title, live_org: m.live_org, team: m.people.team, live: !!m.live && !m.kept_local, targets: m.targets, paused: m.paused_warning, blocked: m.send_blocked, sent: m.sent, connected: m.connected };
    }
    const row = S.library?.rows.find((r) => r.id === id);
    if (!row) return null;
    return { id, title: row.title, live_org: row.live_org, team: true, live: !!row.live_org, targets: row.targets, paused: row.paused_warning, blocked: null, sent: row.sent, connected: S.library.connected };
  }
  function openFinish(id) {
    const src = finishSource(id);
    if (!src) return;
    S.ui.pop = null;
    S.ui.share = null;
    const choices = src.targets.filter((t) => !t.already);
    const org = src.live_org || (choices.find((t) => t.id === defaultOrg()?.id) || choices[0])?.id || '';
    // Shared live with only the people added: saving keeps it with them.
    const audience = src.live && src.live_org && !src.team ? 'participants' : 'team';
    S.ui.finish = { id, title: src.title, dest: org || 'local', org, audience, confirmDelete: false, pausedOk: false, fromMeeting: S.page === 'meeting' };
    render();
  }
  function finishSheet() {
    const f = S.ui.finish;
    if (!f) return '';
    const src = finishSource(f.id);
    if (!src) return '';
    const org = orgById(f.org) || src.targets.find((t) => t.id === f.org);
    const choices = src.live ? src.targets.filter((t) => t.id === src.live_org) : src.targets.filter((t) => !t.already);
    let html = `<div class="scrim" data-act="finish-close"></div><div class="sheet finish" id="sheet" role="dialog" aria-modal="true" aria-label="Finish">`;
    if (f.confirmDelete) {
      html += `<h2>Delete “${esc(f.title || src.title)}”?</h2><div class="sheet-foot"><button class="btn quiet" data-act="finish-keep">Cancel</button><button class="btn danger" data-act="finish-delete">Delete</button></div></div>`;
      return html;
    }
    html += `<div class="title-row"><input data-model="finish.title" data-enter="finish-save" spellcheck="false" aria-label="Title"><button class="icon-btn trash" data-act="finish-ask-delete" title="Delete" aria-label="Delete">${icon('trash', 16)}</button></div>`;
    html += `<div class="field-group"><div class="label">Save to</div><div class="seg2" role="radiogroup" aria-label="Save to">`;
    html += `<button class="${f.dest === 'local' ? 'on' : ''}" data-act="finish-dest" data-dest="local" role="radio" aria-checked="${f.dest === 'local'}">${icon('mac', 14)}<span class="name">This Mac</span></button>`;
    if (org) {
      const more = choices.length > 1;
      html += `<button class="${f.dest !== 'local' ? 'on' : ''}" data-act="finish-dest" data-dest="${esc(org.id)}" data-more="${more ? 1 : ''}" role="radio" aria-checked="${f.dest !== 'local'}">${orgTile(orgById(org.id) || org, 's18')}<span class="name">${esc(org.name)}</span>${more ? icon('down', 11) : ''}</button>`;
    } else {
      html += `<button data-act="settings">Sign in</button>`;
    }
    html += `</div></div>`;
    if (f.dest !== 'local' && org) {
      html += `<div class="field-group"><div class="label">Who can see it</div><div class="seg2" role="radiogroup" aria-label="Who can see it">`;
      html += `<button class="${f.audience === 'team' ? 'on' : ''}" data-act="finish-audience" data-audience="team" role="radio" aria-checked="${f.audience === 'team'}"><span class="name">${esc(org.name)}</span></button>`;
      html += `<button class="${f.audience === 'participants' ? 'on' : ''}" data-act="finish-audience" data-audience="participants" role="radio" aria-checked="${f.audience === 'participants'}"><span class="name">Participants only</span></button>`;
      html += `</div></div>`;
    }
    const toOrg = f.dest !== 'local';
    const blocked = toOrg ? src.blocked : null;
    if (blocked) html += `<div class="sheet-error">${icon('alert', 13)}<span>${esc(blocked)}</span></div>`;
    const paused = toOrg && src.paused && f.pausedOk;
    if (paused) html += `<div class="finish warn">A kept task may quote paused speech.</div>`;
    html += `<button class="btn primary big block" data-act="finish-save" ${S.status.busy || blocked ? 'disabled' : ''}>${paused ? 'Save anyway' : 'Save'}</button>`;
    return html + `</div>`;
  }
  function saveFinish() {
    const f = S.ui.finish;
    const src = f && finishSource(f.id);
    if (!src || S.status.busy) return;
    const toOrg = f.dest !== 'local';
    if (toOrg && src.paused && !f.pausedOk) { f.pausedOk = true; render(); return; }
    S.ui.finish = null;
    const title = (f.title || '').trim() || src.title;
    act('save_meeting', { id: f.id, destination: toOrg ? f.dest : 'local', audience: toOrg ? f.audience : '', title, pausedConfirmed: toOrg && src.paused && f.pausedOk });
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
    html += `<div class="settings-group"><div class="label">Orgs</div><div class="inset">`;
    for (const c of s.connections) {
      const tail = c.default
        ? s.connections.length > 1 ? badge('Default', 'accent') : ''
        : `<button class="btn small quiet" data-act="make-default" data-id="${esc(c.id)}">Make default</button>`;
      html += `<div class="line">${orgTile(c, 's28')}<div class="name">${esc(c.name)}${c.host ? `<small>${esc(c.host)}</small>` : ''}</div>${tail}<button class="btn small" data-act="sign-out" data-id="${esc(c.id)}">Sign out</button></div>`;
    }
    html += `<div class="line">${field('signin', 'your-tico.example').replace('class="field"', 'class="field" data-enter="signin"')}<button class="btn ${s.connections.length ? '' : 'primary'}" data-act="signin" ${st.busy ? 'disabled' : ''}>${s.connections.length ? 'Add org' : 'Sign in'}</button></div>`;
    if (st.signing_in) html += `<div class="line"><div class="waiting" style="height:auto"><span class="spin"></span>Choose Allow in your browser…</div></div>`;
    html += line('Use my Tico calendar', toggle('calendar', f.calendar, !s.calendar_available));
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

  // ---- Popovers ----
  function popover() {
    const pop = S.ui.pop;
    if (!pop) return '';
    let body = '';
    if (pop.kind === 'more') body = morePop(pop);
    else if (pop.kind === 'owner') body = ownerPop(pop);
    else if (pop.kind === 'source') body = sourcePop();
    else if (pop.kind === 'orgs') body = orgsPop();
    else if (pop.kind === 'share-org') body = orgList('share-set-org', S.ui.share?.org);
    else if (pop.kind === 'finish-org') {
      const src = S.ui.finish && finishSource(S.ui.finish.id);
      body = src ? orgList('finish-set-org', S.ui.finish.org, src.targets.filter((t) => !t.already).map((t) => t.id)) : '';
    }
    if (!body) return '';
    return `<div class="popover" id="popover" style="${pop.style}">${body}</div>`;
  }
  function orgList(act, current, only = null) {
    return orgs().filter((o) => !only || only.includes(o.id)).map((o) => `<button class="menu-item" data-act="${act}" data-id="${esc(o.id)}">${orgTile(o, 's18')}<span class="grow">${esc(o.name)}</span>${o.id === current ? `<span class="check">${icon('check')}</span>` : ''}</button>`).join('');
  }
  function orgsPop() {
    return orgList('make-default', defaultOrg()?.id) + `<div class="menu-sep"></div><button class="menu-item" data-act="settings">${icon('plus')}<span class="grow">Add org</span></button>`;
  }
  function morePop(pop) {
    const m = S.meeting;
    if (!m) return '';
    if (pop.confirm === 'delete') {
      return `<div class="pop-title">Delete recording?</div><div class="pop-foot"><span class="grow"></span><button class="btn small quiet" data-act="close-pop">Cancel</button><button class="btn small danger" data-act="delete" data-id="${esc(m.id)}">Delete</button></div>`;
    }
    if (pop.confirm === 'discard') {
      return `<div class="pop-title">Remove it from Tico?</div><div class="pop-text">It stays on this Mac.</div><div class="pop-foot"><span class="grow"></span><button class="btn small quiet" data-act="close-pop">Cancel</button><button class="btn small danger" data-act="keep-local">Discard</button></div>`;
    }
    const item = (act, glyph, label, extra = '') => `<button class="menu-item" data-act="${act}" ${extra}>${icon(glyph)}<span class="grow">${label}</span></button>`;
    let html = '';
    if (!S.status.screen?.active && m.can_share_screen && m.connected) html += item('share-screen', 'screen', 'Share screen');
    if (m.connected && !m.kept_local && !m.discarded) html += item('copy-link', 'link', 'Copy meeting link');
    if (m.can_control) html += item('control-live', m.live_state === 'paused' ? 'retry' : 'clock', m.live_state === 'paused' ? 'Resume sharing' : 'Pause sharing', `data-action="${m.live_state === 'paused' ? 'resume' : 'pause'}"`);
    html += item('audio-status', 'mic', 'Audio details');
    if (m.audio.failed) html += item('retry', 'retry', `Retry ${m.audio.failed} failed`, `data-id="${esc(m.id)}"`);
    html += item('copy', 'copy', S.ui.copied ? 'Copied' : 'Copy transcript');
    html += item('toggle-side', 'transcript', S.ui.hideSide ? 'Show transcript' : 'Hide transcript');
    html += `<div class="menu-sep"></div>`;
    if (m.can_discard) html += `<button class="menu-item danger" data-act="confirm-more" data-confirm="discard">${icon('close')}<span class="grow">Discard live share…</span></button>`;
    html += `<button class="menu-item danger" data-act="confirm-more" data-confirm="delete" ${m.recording_here ? 'disabled title="Stop recording first"' : ''}>${icon('trash')}<span class="grow">Delete recording…</span></button>`;
    return html;
  }
  function ownerPop(pop) {
    const a = S.meeting?.actions;
    if (!a) return '';
    if (a.mode === 'signin') return `<div class="pop-text">Sign in to choose an owner.</div><button class="menu-item" data-act="settings">${icon('gear')}<span class="grow">Settings</span></button>`;
    if (a.mode === 'choose') return a.orgs.map((o) => `<button class="menu-item" data-act="choose-org" data-id="${esc(o.id)}">${orgTile(orgById(o.id) || o, 's18')}<span class="grow">${esc(o.name)}</span></button>`).join('');
    const it = a.items.find((i) => i.id === pop.id);
    const q = S.ui.ownerQuery.trim().toLowerCase();
    const owners = q ? ranked(a.owners, q) : a.owners;
    let html = `<input class="pop-input" data-model="ownerQuery" data-enter="owner-first" data-list placeholder="Owner" spellcheck="false" aria-label="Owner"><div class="pop-list">`;
    if (!a.owners.length) html += `<div class="pop-text"><span class="spin" style="display:inline-block;vertical-align:-2px"></span></div>`;
    else if (!owners.length) html += `<div class="pop-text">No match</div>`;
    const hot = Math.min(S.ui.hot, owners.length - 1);
    html += owners.map((o, i) => `<button class="menu-item ${i === hot ? 'hot' : ''}" data-opt data-act="set-owner" data-id="${esc(pop.id)}" data-actor="${esc(o.actor)}">${avatar(o)}<span class="grow">${esc(o.name)}</span>${it && it.owner === o.actor ? `<span class="check">${icon('check')}</span>` : ''}</button>`).join('');
    return html + `</div>`;
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
    return `<div class="connect"><div class="card"><div class="host">Connect to ${esc(host)}?</div><p>Continue only if you chose Connect recorder in your browser.</p><div class="buttons"><button class="btn" data-act="connect-cancel">Cancel</button><button class="btn primary" data-act="connect-ok" ${S.status.busy ? 'disabled' : ''}>Sign in</button></div></div></div>`;
  }
  // Screen sharing: a private live share in the chosen org first, then the system picker.
  function screenCard() {
    const d = S.ui.screen;
    if (!d) return '';
    const m = S.meeting;
    const targets = m?.targets || [];
    const choose = !m?.live && targets.length > 1 ? `<div class="buttons" style="justify-content:flex-start;margin-bottom:12px">${targets.map((t) => `<button class="btn small ${d.destination === t.id ? 'on' : ''}" data-act="screen-org" data-id="${esc(t.id)}">${esc(t.name)}</button>`).join('')}</div>` : '';
    const who = m?.live ? (m.people.team ? `Anyone on ${esc(m.people.org)} sees it.` : 'Only people you add see it.') : 'It shares this meeting live, only to you, first.';
    return `<div class="connect" role="dialog" aria-modal="true" aria-label="Share screen"><div class="card"><div class="host">Share a screen or window</div><p>${who} Never saved.</p>${choose}${d.error ? `<p class="error selectable" role="alert">${esc(d.error)}</p>` : ''}<div class="buttons"><button class="btn small quiet" data-act="screen-cancel">Cancel</button><button class="btn small primary" data-act="screen-go" ${d.busy || !d.destination ? 'disabled' : ''}>${d.busy ? 'Connecting…' : 'Choose'}</button></div></div></div>`;
  }
  async function shareScreen() {
    const d = S.ui.screen;
    const m = S.meeting;
    if (!d || d.busy || !d.destination || !m) return;
    d.busy = true; d.error = ''; render();
    try {
      await invoke('meeting_link', { id: m.id, destination: d.destination, confirmed: true, copy: false });
      // Cancellation is available while the picker is open; it never changes the audience.
      await invoke('share_screen', { id: m.id, expectedTeam: !!m.people.team });
      S.ui.screen = null;
      await poll();
    } catch (error) { if (S.ui.screen === d) d.error = String(error?.message || error); }
    finally { d.busy = false; render(); }
  }

  // ---- Rendering ----
  function page() {
    if (S.page === 'meeting') return meeting();
    if (S.page === 'settings') return settingsPage();
    if (S.page === 'signin') return signinPage();
    return library();
  }
  function render() {
    const root = document.getElementById('app');
    if (!root.dataset.ready) {
      root.innerHTML = '<div id="r-top"></div><main class="page" id="r-page"></main><div id="r-dock"></div><div id="r-sheet"></div><div id="r-pop"></div><div id="r-mention"></div><div id="r-connect"></div>';
      root.dataset.ready = '1';
    }
    // First run: no org yet and nothing recorded, so the window is the sign-in screen.
    const first = S.status.first_run && !S.library?.rows?.length;
    if (first && S.page === 'library') S.page = 'signin';
    else if (!first && S.page === 'signin') S.page = 'library';
    const html = { top: topbar(), page: page(), dock: dock(), sheet: shareSheet() + finishSheet(), pop: popover(), mention: mentionPop(), connect: connectCard() + screenCard() };
    const active = document.activeElement;
    const key = active?.dataset?.key || active?.dataset?.model;
    const selection = key && 'selectionStart' in active ? [active.selectionStart, active.selectionEnd] : null;
    const pageEl = document.getElementById('r-page');
    pageEl.classList.toggle('meeting', S.page === 'meeting');
    pageEl.classList.toggle('signin-page', S.page === 'signin');
    for (const [name, content] of Object.entries(html)) {
      if (regions[name] === content) continue;
      const element = document.getElementById(`r-${name}`);
      // Never replace text the person is editing; it renders once they leave it.
      if (element.contains(active) && active.matches('[data-defer]') && !(name === 'page' && S.meeting?.actions?.shared?.gone)) {
        S.deferred = true;
        continue;
      }
      const scroller = name === 'page' ? (S.page === 'meeting' ? document.getElementById('main') : element) : null;
      const sameKind = regions.pageKind === S.page + (S.meeting?.id || '');
      const top = scroller && sameKind ? scroller.scrollTop : 0;
      const reopened = (name === 'pop' || name === 'mention') && regions[name];
      element.innerHTML = content;
      // A popover that was already open updates in place; only a fresh one rises in.
      if (reopened && element.firstElementChild) element.firstElementChild.style.animation = 'none';
      if (name === 'sheet' && regions.sheet && element.querySelector('.sheet')) element.querySelector('.sheet').style.animation = 'none';
      regions[name] = content;
      if (name === 'page') {
        const again = S.page === 'meeting' ? document.getElementById('main') : element;
        if (again) again.scrollTop = top;
        regions.pageKind = S.page + (S.meeting?.id || '');
      }
      for (const input of element.querySelectorAll('[data-model]')) {
        if (input.tagName !== 'SELECT') input.value = model(input.dataset.model);
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
    if (S.ui.focusNext) {
      const target = document.querySelector(S.ui.focusNext);
      S.ui.focusNext = null;
      if (target) target.focus();
    }
    live();
  }
  /** Updates that change every few frames while recording: the clock and the level bars. */
  function live() {
    const rec = S.status.recording;
    if (!rec) return;
    const elapsed = document.getElementById('elapsed');
    if (elapsed) elapsed.textContent = clock(rec.elapsed_ms);
    const level = Math.max(0, ...rec.sources.map((s) => s.level || 0));
    const shape = [0.45, 0.8, 1, 0.65, 0.35];
    document.querySelectorAll('#levels b').forEach((bar, i) => (bar.style.height = `${Math.round(4 + 14 * level * shape[i])}px`));
    for (const src of rec.sources) {
      const meter = document.querySelector(`[data-meter="${CSS.escape(src.channel)}"]`);
      if (meter) meter.style.width = `${Math.round(src.level * 100)}%`;
    }
  }
  function grow(area) {
    area.style.height = 'auto';
    area.style.height = `${Math.max(96, area.scrollHeight)}px`;
  }
  const startsWord = (name, q) => name.toLowerCase().split(/[\s.@_-]+/).some((word) => word.startsWith(q));
  /** Names containing `q`, those with a word starting with it first. */
  const ranked = (rows, q) => {
    const hits = rows.filter((row) => row.name.toLowerCase().includes(q));
    return [...hits.filter((row) => startsWord(row.name, q)), ...hits.filter((row) => !startsWord(row.name, q))];
  };
  function moveHot(box, step) {
    const options = box ? [...box.querySelectorAll('[data-opt]')] : [];
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
    back.innerHTML = html + esc(text.slice(at)) + '​';
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
    return `<div class="popover mention" id="mention" style="left:${m.left}px;top:${m.top}px"><div class="pop-list" style="max-height:${m.max}px">${matches.map((o, i) => `<button class="menu-item ${i === hot ? 'hot' : ''}" data-opt data-name="${esc(o.name)}" tabindex="-1">${avatar(o)}<span class="grow">${esc(o.name)}</span></button>`).join('')}</div></div>`;
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
    S.ui.share = null;
    S.ui.finish = null;
    S.ui.screen = null;
    S.ui.confirm = null;
    S.ui.titleDraft = null;
    S.ui.notesDraft = null;
    S.ui.showAudio = false;
    S.ui.tsearch = false;
    S.ui.tq = '';
    S.ui.ask = '';
    S.ui.stick = true;
    S.ui.adding = false;
    S.ui.createOk = null;
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
    S.ui.share = null;
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
      await act('close_meeting');
    }
  }
  function settings() {
    if (S.page === 'settings') return;
    S.ui.prevPage = S.page === 'signin' ? 'library' : S.page;
    S.ui.pop = null;
    S.ui.share = null;
    S.ui.finish = null;
    S.ui.form = null;
    S.page = 'settings';
    render();
  }

  // ---- Popover placement ----
  const POP_WIDTH = { owner: 240, orgs: 240, 'share-org': 240, 'finish-org': 220, more: 230, source: 250 };
  function openPop(kind, anchor, extra = {}) {
    if (S.ui.pop?.kind === kind && S.ui.pop.id === extra.id) {
      S.ui.pop = null;
      render();
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const width = POP_WIDTH[kind] || 240;
    let left = Math.min(Math.max(12, rect.right - width), window.innerWidth - width - 12);
    if (['owner', 'source', 'share-org', 'finish-org'].includes(kind)) left = Math.min(Math.max(12, rect.left - 6), window.innerWidth - width - 12);
    let style = `left:${left}px;width:${width}px;`;
    if (rect.top > window.innerHeight * 0.6) style += `bottom:${window.innerHeight - rect.top + 8}px;`;
    else style += `top:${rect.bottom + 6}px;`;
    S.ui.pop = { kind, style, ...extra };
    S.ui.ownerQuery = '';
    S.ui.hot = 0;
    if (kind === 'owner') S.ui.focusNext = '#popover .pop-input';
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
      S.status.message = 'Transcript copied';
      render();
      setTimeout(() => { S.ui.copied = false; poll(); }, 1400);
    };
    copyText(text).then(done, () => {});
  }
  function submitAsk() {
    const text = S.ui.ask.trim();
    const chat = S.meeting?.chat;
    if (!text || !chat || chat.thinking) return;
    S.ui.ask = '';
    S.ui.stick = true;
    S.ui.focusNext = '[data-model="ask"]';
    act('ask', { question: text });
  }
  function sharePick(actor, name, bot) {
    const d = S.ui.share;
    const m = S.meeting;
    if (!d || !m) return;
    S.ui.shareQuery = '';
    S.ui.hot = 0;
    S.ui.focusNext = '#sheet .add-field input';
    // Once live, a pick is applied there and then; before, it waits for Share live.
    if (m.live) { act('pick', { actor }); return; }
    if (!d.picks.some((x) => x.actor === actor)) d.picks.push({ actor, name, bot });
    render();
  }
  function startShare(after = '') {
    const d = S.ui.share;
    if (!d || d.busy || !d.org) return;
    if (d.team && S.status.screen?.active) { d.error = 'Stop screen sharing first'; render(); return; }
    d.busy = true;
    d.error = '';
    d.after = after;
    render();
    act('share_live', { destination: d.org, team: d.team, actors: d.picks.map((x) => x.actor) });
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
    'signin-desktop': async (el) => {
      S.ui.signingDesktop = el.dataset.url;
      // Builds without the desktop contract fall back to typing an address.
      try { await invoke('sign_in_desktop', { url: el.dataset.url }); await poll(); } catch (error) { console.error(error); S.ui.signingDesktop = ''; S.ui.address = true; render(); }
    },
    'address-on': () => { S.ui.address = true; S.ui.focusNext = '[data-model="signin"]'; render(); },
    'address-off': () => { S.ui.address = false; render(); },
    'share-open': () => openShare(),
    'share-close': () => { S.ui.share = null; render(); },
    'share-team': (el) => {
      const team = el.dataset.team === 'true';
      const d = S.ui.share;
      if (!d) return;
      if (S.meeting?.live) {
        // Stop first so widening the audience cannot expose an already captured frame.
        if (team && S.status.screen?.active) { d.error = 'Stop screen sharing first'; render(); return; }
        act('set_team', { team });
      } else { d.team = team; render(); }
    },
    'share-pick': (el) => sharePick(el.dataset.actor, el.dataset.name, !!el.dataset.bot),
    'share-unpick': (el) => { const d = S.ui.share; if (d) { d.picks = d.picks.filter((x) => x.actor !== el.dataset.actor); render(); } },
    'share-set-org': (el) => {
      const d = S.ui.share;
      S.ui.pop = null;
      if (!d || S.meeting?.live) return render();
      d.org = el.dataset.id;
      d.picks = [];
      act('choose_people_org', { id: d.org });
    },
    'share-go': () => startShare(),
    'share-copy': () => {
      const d = S.ui.share;
      if (!d) return;
      if (S.meeting?.live) copyMeetingLink(d);
      else startShare('copy');
    },
    'copy-link': () => {
      S.ui.pop = null;
      openShare();
      if (S.meeting?.live && S.ui.share) copyMeetingLink(S.ui.share);
    },
    'finish-open': (el) => openFinish(el.dataset.id),
    'finish-close': () => { S.ui.finish = null; render(); },
    'finish-dest': (el) => {
      const f = S.ui.finish;
      if (!f) return;
      if (el.dataset.dest !== 'local' && f.dest !== 'local' && el.dataset.more) { openPop('finish-org', el); return; }
      f.dest = el.dataset.dest;
      f.pausedOk = false;
      render();
    },
    'finish-set-org': (el) => { const f = S.ui.finish; S.ui.pop = null; if (f) { f.org = el.dataset.id; f.dest = f.org; } render(); },
    'finish-audience': (el) => { if (S.ui.finish) { S.ui.finish.audience = el.dataset.audience; render(); } },
    'finish-save': saveFinish,
    'finish-ask-delete': () => { if (S.ui.finish) { S.ui.finish.confirmDelete = true; render(); } },
    'finish-keep': () => { if (S.ui.finish) { S.ui.finish.confirmDelete = false; render(); } },
    'finish-delete': () => { const f = S.ui.finish; S.ui.finish = null; if (f) act('delete_recording', { id: f.id }); },
    'keep-local': () => { S.ui.pop = null; if (S.meeting) act('keep_local', { id: S.meeting.id }); },
    'confirm-more': (el) => { S.ui.pop = { ...S.ui.pop, confirm: el.dataset.confirm }; render(); },
    'share-screen': () => {
      const m = S.meeting;
      if (!m) return;
      S.ui.pop = null;
      const targets = m.targets || [];
      S.ui.screen = { destination: m.live_org || (targets.length === 1 ? targets[0].id : defaultOrg()?.id || ''), busy: false, error: '' };
      render();
    },
    'screen-org': (el) => { if (S.ui.screen && !S.ui.screen.busy) { S.ui.screen.destination = el.dataset.id; render(); } },
    'screen-cancel': () => { S.ui.screen = null; render(); },
    'screen-go': () => shareScreen(),
    'stop-screen': () => act('stop_screen'),
    stop: () => act('stop_recording'),
    'ask-delete': (el) => { S.ui.confirm = el.dataset.id; render(); },
    'cancel-delete': () => { S.ui.confirm = null; render(); },
    delete: async (el) => {
      S.ui.confirm = null;
      S.ui.pop = null;
      await act('delete_recording', { id: el.dataset.id });
    },
    pop: (el) => openPop(el.dataset.pop, el, { id: el.dataset.id }),
    'close-pop': () => { S.ui.pop = null; render(); },
    'control-live': (el) => { S.ui.pop = null; act('control_live', { action: el.dataset.action }); },
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
    'save-line': async () => {
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
    'audio-status': () => { S.ui.pop = null; S.ui.hideSide = false; S.ui.showAudio = !S.ui.showAudio; render(); },
    'toggle-audio': () => { S.ui.showAudio = !S.ui.showAudio; render(); },
    'toggle-side': () => { S.ui.pop = null; S.ui.hideSide = !S.ui.hideSide; S.ui.stick = true; render(); },
    hit: (el) => {
      const body = document.getElementById('tbody');
      if (body) showHit(body, Number(el.dataset.step));
    },
    tsearch: () => {
      S.ui.tsearch = !S.ui.tsearch;
      if (!S.ui.tsearch) S.ui.tq = '';
      else S.ui.focusNext = '.side-search input';
      render();
    },
    copy: () => { S.ui.pop = null; copyTranscript(); },
    ask: submitAsk,
    'ask-retry': () => {
      const c = S.meeting?.chat;
      if (c?.retry) act('ask', { question: c.retry });
    },
    'shared-retry': el => sharedSave(el.dataset.id),
    'shared-send': el => sharedFinish(el, true),
    'shared-dismiss': el => sharedFinish(el, false),
    'remove-item': (el) => act('remove_item', { id: el.dataset.id }),
    'add-open': () => { S.ui.adding = true; S.ui.focusNext = '[data-model="addItem"]'; render(); },
    // Create files the proposal as a private Tico task; one that may quote paused speech asks twice.
    'create-item': (el) => {
      const it = S.meeting?.actions.items.find((i) => i.id === el.dataset.id);
      if (!it?.can_send) return;
      if (it.paused_warning && S.ui.createOk !== it.id) { S.ui.createOk = it.id; render(); return; }
      const confirmed = S.ui.createOk === it.id;
      S.ui.createOk = null;
      act('send_item', { id: it.id, owner: it.owner, pausedConfirmed: confirmed });
    },
    'set-owner': (el) => { S.ui.pop = null; act('set_owner', { id: el.dataset.id, owner: el.dataset.actor }); },
    'choose-org': (el) => { S.ui.pop = null; act('choose_org', { id: el.dataset.id }); },
    'remove-access': (el) => act('remove_access', { actor: el.dataset.actor }),
    'open-url': (el) => act('open_url', { url: el.dataset.url }),
    'dismiss-error': () => act('dismiss_error'),
    'dismiss-notice': () => act('dismiss_notice'),
    'connect-ok': () => act('confirm_connect'),
    'connect-cancel': () => act('dismiss_connect'),
    signin: () => { const address = model('signin').trim(); if (address) act('sign_in', { address }); },
    'sign-out': (el) => act('sign_out', { id: el.dataset.id }),
    'make-default': (el) => { S.ui.pop = null; act('make_default', { id: el.dataset.id }); },
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
    'finish-save': saveFinish,
    'owner-first': () => {
      const first = document.querySelector('#popover [data-opt].hot') || document.querySelector('#popover [data-act="set-owner"]');
      if (first) first.click();
    },
    'share-enter': () => {
      const text = S.ui.shareQuery.trim();
      if (!text) return;
      const first = document.querySelector('#sheet [data-opt].hot') || document.querySelector('#sheet [data-act="share-pick"]');
      if (first) first.click();
      else {
        // Someone not in the org: a name on this recording only.
        S.ui.shareQuery = '';
        S.ui.focusNext = '#sheet .add-field input';
        act('add_participant', { name: text });
      }
    },
  };

  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-act]');
    const act = target?.dataset.act;
    // Clicks outside an open popover close it.
    if (S.ui.pop && !event.target.closest('#popover') && act !== 'pop' && act !== 'finish-dest') {
      S.ui.pop = null;
      if (!target) render();
    }
    // Clicks outside the Share live sheet close it.
    if (S.ui.share && !event.target.closest('#sheet, #popover') && act !== 'share-open' && act !== 'copy-link' && event.target.isConnected) {
      S.ui.share = null;
      if (!target) render();
    }
    if (!target || target.disabled) {
      if (S.ui.confirm && !event.target.closest('.row.confirm')) {
        S.ui.confirm = null;
        render();
      }
      return;
    }
    const handler = handlers[act];
    if (handler) {
      event.preventDefault();
      handler(target, event);
    }
  });
  document.addEventListener('input', (event) => {
    const el = event.target;
    if (el.dataset.sharedField) sharedChanged(el);
    if (el.dataset.model) {
      setModel(el.dataset.model, el.value);
      if (el.dataset.model === 'notes') {
        grow(el);
        paintMentions(el);
        findMention(el);
        clearTimeout(notesTimer);
        notesTimer = setTimeout(() => flushNotes(), 700);
      }
      if (el.dataset.model === 'ownerQuery' || el.dataset.model === 'shareQuery') S.ui.hot = 0;
      if (['search', 'ownerQuery', 'shareQuery', 'tq', 'key', 'xai'].includes(el.dataset.model) || el.dataset.model.startsWith('form.')) render();
    }
  });
  document.addEventListener('mouseover', (event) => {
    const option = event.target.closest?.('[data-opt]');
    if (!option || option.classList.contains('hot')) return;
    const box = option.closest('#popover, #mention, #sheet');
    if (!box) return;
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
    if (el.dataset.sharedField) { const id = el.closest('[data-shared-item]').dataset.sharedItem; if (!sharedDraft(id).error) sharedSave(id); }
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
    if (el.dataset.sharedField) { const id = el.closest('[data-shared-item]').dataset.sharedItem; if (!sharedDraft(id).error) await sharedSave(id); }
    if (el.dataset?.blur === 'rename' && S.ui.titleDraft !== null) {
      const title = S.ui.titleDraft;
      S.ui.titleDraft = null;
      if (title.trim() && title.trim() !== S.meeting?.title) await act('rename', { title });
    }
    if (el.dataset?.blur === 'add-close' && !S.ui.addItem.trim() && S.ui.adding) {
      S.ui.adding = false;
      setTimeout(render, 0);
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
      moveHot(el.closest('#popover, #sheet'), event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (S.ui.mention && el.dataset?.model === 'notes' && !event.isComposing) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        moveHot(document.getElementById('mention'), event.key === 'ArrowDown' ? 1 : -1);
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
      if (event.key === 't') S.ui.hideSide = !S.ui.hideSide;
      else {
        S.ui.hideSide = false;
        S.ui.tsearch = true;
        S.ui.focusNext = '.side-search input';
      }
      S.ui.stick = true;
      render();
      return;
    }
    if (event.key !== 'Escape') return;
    // Innermost first: a menu over a sheet closes before the sheet.
    if (S.ui.pop) {
      S.ui.pop = null;
      render();
    } else if (S.ui.share || S.ui.finish || S.ui.screen) {
      S.ui.share = null; S.ui.finish = null; S.ui.screen = null; render();
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
    } else if (S.page === 'meeting' || S.page === 'settings') {
      back();
    }
  });
  document.addEventListener('scroll', (event) => {
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

  window.addEventListener('error', (event) => bootError(event.message));
  window.addEventListener('DOMContentLoaded', () => {
    const events = window.__TAURI__.event;
    events.listen('changed', () => poll());
    events.listen('screen-preview', async (event) => {
      await poll();
      if (event.payload?.capture_id !== S.status.screen?.capture_id) return;
      let preview = document.getElementById('screen-preview');
      if (!event.payload.url || !S.status.screen?.active) { preview?.remove(); return; }
      if (!preview) { preview = document.createElement('div'); preview.id = 'screen-preview'; preview.innerHTML = '<img alt="Your shared screen"><div>Screen · a few times a second</div>'; document.getElementById('app').appendChild(preview); }
      preview.querySelector('img').src = event.payload.url;
    });
    events.listen('level', (event) => onLevel(event.payload));
    poll();
  });
})();
