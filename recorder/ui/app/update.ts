// Tico Recorder's updates in the window. app.ts hands `status.update` to `ticoUpdate` on every view, and
// draws `ticoUpdateRow` in Settings.
// - Nothing shows while the app is held (`held`: a recording, a live or paused meeting, a send).
// - A copy that cannot update itself and is not in an Applications folder (`prompt`) asks once per launch,
//   before the first recording: Move or Not now. After Not now the smaller banner offers the move.
// - The banner shows when a downloaded build is ready, with one line of the release notes, and Restart.
// - The Settings row shows the version, Check for updates and its states, or Move to Applications.
import type { Invoke, RecorderWindow, UpdateStatus } from '../types/bridge.d.ts';

declare const window: RecorderWindow;

(() => {
  // The reply is the Rust command's, typed by the command map.
  const invoke: Invoke = (cmd: string, args: object = {}) => window.__TAURI__.core.invoke(cmd, args) as Promise<never>;
  const esc = (value: unknown) =>
    String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c as '&']);
  const DISMISSED = 'tico.recorder.move-dismissed';
  const stored = (key: string) => { try { return localStorage.getItem(key); } catch { return null; } };
  const store = (key: string, value: string) => { try { localStorage.setItem(key, value); } catch {} };

  let banner: HTMLDivElement | null = null;
  let shown = '';
  let last: UpdateStatus | null = null;
  // Move to Applications, on this page only: a newer copy found there, an error, or a move under way.
  const move = { ask: '', error: '', busy: false };
  let checkError = '';
  // The launch prompt: answered (Not now) or passed (a recording started) for this launch.
  let promptDone = false;

  /** The release notes line, or '' for none or the build's "Built from <sha>" placeholder. */
  const noteLine = (notes: string) => {
    const line = String(notes || '').split('\n').map((l) => l.trim()).find(Boolean) || '';
    return /^Built from [0-9a-f]+$/.test(line) ? '' : line;
  };

  const redraw = () => { shown = ''; render(last); window.ticoRedraw?.(); };

  async function doMove(how: string) {
    move.busy = true; move.error = ''; redraw();
    try {
      const reply = await invoke('move_to_applications', { how });
      move.ask = reply?.ask || '';
      move.error = reply?.error || '';
    } catch (error) {
      move.error = 'Couldn\'t move the app';
      console.error(error);
    }
    move.busy = false;
    redraw();
  }
  async function check() {
    checkError = '';
    try { await invoke('update_check'); } catch (error) { checkError = 'Couldn\'t check for updates'; console.error(error); }
    redraw();
  }

  /** Move to Applications and its question; shared by the banner and the Settings row. */
  function moveControls() {
    if (move.busy) return `<span class="value"><span class="spin"></span>Moving…</span>`;
    if (move.ask) {
      return `<span class="value">Applications has ${esc(move.ask)}</span>`
        + `<button class="btn small" data-upd="open">Open it</button><button class="btn small" data-upd="replace">Replace</button>`;
    }
    return (move.error ? `<span class="value warn">${esc(move.error)}</span>` : '')
      + `<button class="btn small primary" data-upd="move">Move to Applications</button>`;
  }

  /** The Settings row. */
  function row(update: UpdateStatus | undefined) {
    const u: Partial<UpdateStatus> = update || {};
    const name = `<div class="name">${u.current ? `Version ${esc(u.current)}` : 'Version'}</div>`;
    let tail;
    if (u.location) {
      tail = `<span class="value warn">${esc(u.location)}</span>${u.movable ? moveControls() : ''}`;
    } else {
      const button = (busy: boolean) => `<button class="btn small" data-upd="check" ${busy ? 'disabled' : ''}>Check for updates</button>`;
      const restart = `<button class="btn small primary" data-upd="restart">Restart</button>`;
      switch (u.state) {
        case 'checking': tail = `<span class="value"><span class="spin"></span>Checking…</span>${button(true)}`; break;
        case 'downloading': tail = `<span class="value">Downloading${u.progress == null ? '…' : ` ${u.progress}%`}</span>${button(true)}`; break;
        case 'ready':
        case 'installed':
          tail = `<span class="value">${u.state === 'ready' ? 'Update ready' : 'Update installed'}${u.version ? ` · ${esc(u.version)}` : ''}</span>`
            + (u.blocked ? `<span class="value faint">${esc(u.blocked)}</span>` : restart);
          break;
        case 'current': tail = `<span class="value">Up to date</span>${button(false)}`; break;
        case 'error': tail = `<span class="value warn">${esc(u.error || 'Couldn\'t check for updates')}</span>${button(false)}`; break;
        default: tail = (checkError ? `<span class="value warn">${esc(checkError)}</span>` : '') + button(false);
      }
    }
    return `<div class="settings-group" id="updates"><div class="label">Updates</div><div class="inset"><div class="line update-row">${name}${tail}</div></div></div>`;
  }

  /** Move and Not now; a newer copy in Applications or an error shows in their place. */
  function promptControls() {
    if (move.busy) return `<span class="value"><span class="spin"></span>Moving…</span>`;
    if (move.ask) {
      return `<span class="value">Applications has ${esc(move.ask)}</span>`
        + `<button class="btn" data-upd="open">Open it</button><button class="btn primary" data-upd="replace">Replace</button>`;
    }
    return (move.error ? `<span class="value warn">${esc(move.error)}</span>` : '')
      + `<button class="btn" data-upd="not-now">Not now</button><button class="btn primary" data-upd="move">Move</button>`;
  }

  function render(update: UpdateStatus | null | undefined) {
    last = update ?? null;
    if (last?.held) promptDone = true;
    const key = JSON.stringify([last, move, stored(DISMISSED), promptDone]);
    if (key === shown) return;
    shown = key;
    if (!banner) {
      banner = document.createElement('div');
      banner.className = 'update-banner';
      banner.hidden = true;
      banner.setAttribute('role', 'status');
      document.body.append(banner);
    }
    const installed = update?.state === 'installed';
    banner.classList.remove('update-prompt');
    if (update?.held || (update?.blocked && (update.state === 'ready' || installed))) {
      // Never during a recording, a live or paused meeting, or a send; Settings still says why it waits.
      banner.replaceChildren();
      banner.hidden = true;
    } else if (update?.state === 'ready' || installed) {
      const note = noteLine(update.notes);
      banner.innerHTML = `<span class="update-title">${installed ? 'Update installed' : 'Update ready'}</span>`
        + (note ? `<span class="update-note" title="${esc(note)}">${esc(note)}</span>` : '')
        + `<button type="button" class="btn small primary update-restart" data-upd="restart">Restart</button>`;
      banner.hidden = false;
    } else if (update?.prompt && update.movable && !promptDone) {
      banner.classList.add('update-prompt');
      banner.innerHTML = `<p>Move Tico Recorder to Applications so it can update.</p><div class="update-actions">${promptControls()}</div>`;
      banner.hidden = false;
    } else if (update?.movable && !stored(DISMISSED)) {
      banner.innerHTML = `<span>${esc(update.location)}</span>${moveControls()}<button type="button" class="update-close" data-upd="dismiss" title="Dismiss" aria-label="Dismiss">×</button>`;
      banner.hidden = false;
    } else {
      banner.replaceChildren();
      banner.hidden = true;
    }
  }

  document.addEventListener('click', (event) => {
    const el = (event.target as Element).closest<HTMLButtonElement>('[data-upd]');

    if (!el || el.disabled) return;
    event.preventDefault();
    const what = el.dataset.upd;
    if (what === 'restart') invoke('update_install').catch(() => {});
    else if (what === 'check') check();
    else if (what === 'move') doMove('copy');
    else if (what === 'replace' || what === 'open') doMove(what);
    else if (what === 'dismiss') { store(DISMISSED, '1'); redraw(); }
    else if (what === 'not-now') { promptDone = true; move.error = ''; redraw(); }
  });

  window.ticoUpdate = render;
  window.ticoUpdateRow = row;
})();
