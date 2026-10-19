// Video and audio cards in chat and task conversations (Visual replies 3/4). The bodies are the task-files players
// (controls, playsinline, preload="metadata", the poster) inside the shared card frame (visual-cards.js); this file
// adds the video and audio bodies, one clip at a time, and a player that recovers from an expired address.
// Media loads only from the attachment's isolated `src` (no app sign-in, range requests), never through a blob URL,
// so seeking a large file asks for that part only. Nothing here ever calls play(): a clip starts when a person
// presses play. Classic script, loaded right after visual-cards.js.
'use strict';
const VM_TYPE = {'video/mp4': 'MP4', 'video/webm': 'WebM', 'video/quicktime': 'MOV', 'video/x-m4v': 'M4V',
  'audio/mpeg': 'MP3', 'audio/mp4': 'M4A', 'audio/x-m4a': 'M4A', 'audio/wav': 'WAV', 'audio/x-wav': 'WAV',
  'audio/wave': 'WAV', 'audio/ogg': 'OGG', 'audio/aac': 'AAC'};
const vmWord = kind => kind === 'video' ? 'video' : 'audio';
// Whether this device says it can't play the type at all ('' from canPlayType); 'maybe' and 'probably' get a try.
function vmUnplayable(kind, type) {
  if (!type) return false;
  return document.createElement(kind === 'video' ? 'video' : 'audio').canPlayType(type) === '';
}
function vmReason(kind, type, code) {
  const word = VM_TYPE[type] || '';
  if (code === 2) return `Couldn't load this ${vmWord(kind)}. Check the connection, or download it.`;
  return `This device can't play ${word ? word + ' ' : 'this '}${vmWord(kind)}.`;
}
// The card body. A video sits in a box of its own shape no taller than the card's cap (a portrait clip is narrower,
// never cropped); the frame's held space gives way to it (visual-cards.css, .vcard[data-kind=video]).
function vmBodyHTML(f) {
  const kind = f.kind === 'audio' ? 'audio' : 'video', src = visualSrc(f);
  if (vmUnplayable(kind, f.content_type)) return `<div class="vm-cant" data-vm-reason="${esc(vmReason(kind, f.content_type, 4))}"></div>`;
  const at = ` data-vm-src="${esc(src)}" data-vm-type="${esc(f.content_type || '')}"`;
  if (kind === 'audio') return `<div class="tf-media audio media-skel vm"><audio src="${esc(src)}"${at} controls preload="metadata"></audio></div>`;
  const w = Number(f.width) > 0 && Number(f.height) > 0 ? Number(f.width) : 16, h = w === 16 ? 9 : Number(f.height);
  const poster = visualSrc(f, 'poster_url');
  return `<div class="tf-media video media-skel vm${poster ? ' ready' : ''}" style="aspect-ratio:${w}/${h};width:min(100%,calc(var(--vcard-cap) * ${w} / ${h}))"><video src="${esc(src)}"${at}${poster ? ` poster="${esc(poster)}"` : ''} controls playsinline preload="metadata"></video></div>`;
}
// A clip this device can't play: the frame's failed card with the reason, and Download right there (the menu's
// signed-in download, a click like a chip's).
function vmFail(card, reason) {
  visualFail(card, reason);
  const dl = card.querySelector('.vcard-menu-list a[download]');
  if (dl) card.querySelector('.vcard-note')?.insertAdjacentHTML('beforeend',
    `<a class="link" href="${esc(dl.getAttribute('href'))}" download="${esc(dl.getAttribute('download') || '')}">Download</a>`);
}
// After the card is in the page: ready on metadata; on an error, first try the newest address the thread handed
// the card (visualKeep keeps a playing card and copies the new tree's src to data-fresh-src) at the same position
// and play state; only then show the reason and Download.
function vmWire(card) {
  const cant = card.querySelector('.vm-cant');
  if (cant) return vmFail(card, cant.dataset.vmReason);
  const m = card.querySelector('video[data-vm-src], audio[data-vm-src]');
  if (!m) return;
  const body = card.querySelector('.vcard-body'), wrap = m.closest('.tf-media');
  const ready = () => { wrap.classList.add('ready'); body.classList.add('ready'); };
  if (wrap.classList.contains('ready') || m.readyState >= 1) ready();
  m.addEventListener('loadedmetadata', ready);
  // Whether the person has it playing. A failed load pauses the element just before its error event, so a pause
  // only counts once the turn has passed without an error.
  m.addEventListener('play', () => { m.dataset.vmPlaying = '1'; });
  // Where the person is. A seek is where they chose, the start included; a time update at 0 is a load or a failed
  // load resetting the clip (it can read 0 by the time its error arrives), so that keeps the last place it showed.
  m.addEventListener('timeupdate', () => { if (m.currentTime) m.dataset.vmAt = m.currentTime; });
  m.addEventListener('seeking', () => { m.dataset.vmAt = m.currentTime; });
  m.addEventListener('pause', () => setTimeout(() => { if (!m.error) delete m.dataset.vmPlaying; }));
  m.addEventListener('error', () => {
    if (vmRecover(m, card)) return;
    vmFail(card, vmReason(m.tagName === 'AUDIO' ? 'audio' : 'video', m.dataset.vmType, m.error && m.error.code));
  });
}
function vmRecover(m, card) {
  const fresh = card.dataset.freshSrc;
  if (!fresh || fresh === m.dataset.vmSrc || m.dataset.vmTried === fresh) return false;
  const at = m.currentTime || Number(m.dataset.vmAt) || 0;
  // It was playing when the address ran out: it resumes, unless the person starts another clip meanwhile (the
  // one-clip listener drops the resume, since the new address loads paused and that listener skips paused clips).
  if (!m.ended && (!m.paused || m.dataset.vmPlaying === '1')) m.dataset.vmResume = '1';
  else delete m.dataset.vmResume;
  m.dataset.vmTried = fresh;
  m.dataset.vmSrc = fresh;
  m.addEventListener('loadedmetadata', () => {
    if (at) m.currentTime = at;
    if (m.dataset.vmResume === '1') m.play().catch(() => {});
    delete m.dataset.vmResume;
  }, {once: true});
  m.src = fresh;
  return true;
}
// One clip at a time: starting any video or audio on the page pauses the one already playing, and a clip that is
// reloading from a newer address no longer resumes when it loads.
document.addEventListener('play', e => {
  for (const m of document.querySelectorAll('video, audio')) {
    if (m === e.target) continue;
    delete m.dataset.vmResume;
    delete m.dataset.vmPlaying;
    if (!m.paused) m.pause();
  }
}, true);
VISUAL_BODY.video = VISUAL_BODY.audio = vmBodyHTML;
VISUAL_WIRE.video = VISUAL_WIRE.audio = vmWire;
