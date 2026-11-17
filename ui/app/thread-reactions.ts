/* ui/app/thread-reactions.ts — A message's emoji reactions from its chat (thread-view.ts thvClick): the emoji menu under the
   react button, and the long-press sheet on touch (React, Reply, Copy, and Good or Not good on a bot's words). Classic script:
   its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): it loads on the first reaction, so a cold open of Home does not download it (P05b). */
'use strict';
async function thvReact(view: ThvView, m: ThvMessage, emoji: string) {
  const before = (m.reactions || []).map(r => ({...r, actors: [...(r.actors || [])]}));
  const me = myActor(), r = before.find(x => x.emoji === emoji), on = !thvReactedMine(r);
  // Shown at once; the server's answer (the message's reactions, as a list) settles it.
  m.reactions = r ? before.map(x => x.emoji !== emoji ? x : {...x, count: Math.max(0, x.count + (on ? 1 : -1)),
                                                                 actors: on ? [...x.actors, me] : x.actors.filter(a => a !== me)})
    : [...before, {emoji, count: 1, actors: [me]}];
  thvDraw(view);
  try {
    const path = '/v2/messages/' + encodeURIComponent(m.id) + '/reactions/' + encodeURIComponent(emoji);
    const back = on ? await put(path, {}) : await writeRequest('DELETE', path);
    const fresh = Array.isArray(back) ? back : back?.reactions || back?.message?.reactions;
    if (Array.isArray(fresh)) { m.reactions = fresh; thvDraw(view); }
  } catch (e) {
    m.reactions = before;
    thvDraw(view); toast(errorMessage(e, ''), true);
  }
}
// The emoji to pick from: six common ones, then any already used in this thread.
function thvEmojiChoices(view: ThvView) {
  const used = new Set(THV_EMOJI);
  for (const x of view.messages) for (const r of x.reactions || []) if (r?.emoji && r.count > 0) used.add(r.emoji);
  return [...used].slice(0, 18);
}
function thvEmojiPick(view: ThvView, m: ThvMessage, button: HTMLElement) {
  thvMenuClose();
  thvMenuShow(button, thvEmojiChoices(view).map(e => [e, e]), emoji => thvReact(view, m, emoji), 'thv-emoji');
}
function thvSheet(view: ThvView, m: ThvMessage) {
  const d = fbDialog('fb-menu thv-sheet', 'Message actions');
  const key = fbRates(m.sender_id) ? fbKey('message', m.id) : '', mine = key ? fbMine(key)?.rating || '' : '';
  d.innerHTML = `<div class="thv-sheet-emoji" role="group" aria-label="React">${thvEmojiChoices(view).slice(0, 6).map(e =>
      `<button type="button" data-pick-emoji="${esc(e)}" aria-label="${esc(e)}">${esc(e)}</button>`).join('')}</div>
    <div class="fb-menu-row">${thvCanPost(view) ? `<button type="button" data-pick="reply" aria-label="Reply">${THV_SVG.reply}</button>` : ''}${
      m.body ? `<button type="button" data-pick="copy" aria-label="Copy">${THV_SVG.copy}</button>` : ''}${key ? `
      <button type="button" data-pick="up" aria-pressed="${mine === 'up'}" aria-label="Good">${FB_ICON.up}</button>
      <button type="button" data-pick="down" aria-pressed="${mine === 'down'}" aria-label="Not good">${FB_ICON.down}</button>` : ''}</div>`;
  // The click the lift makes lands on the backdrop: it is not a tap to close.
  d.addEventListener('click', ev => { if (view.pressQuiet > Date.now()) { ev.stopImmediatePropagation(); view.pressQuiet = 0; } }, true);
  d.showModal();
  d.onclick = ev => {
    const target = eventElement(ev), e = target?.closest<HTMLElement>('[data-pick-emoji]'), b = target?.closest<HTMLElement>('[data-pick]');
    if (!e && !b) return;
    d.close();
    if (e) { void thvReact(view, m, e.dataset.pickEmoji!); return; }
    const k = b!.dataset.pick;
    if (k === 'reply') thvReplyStart(view, m);
    else if (k === 'copy') thvCopy(m);
    else if (k === 'up') void fbUp(key);
    else if (mine === 'down') fbShow(key); else void fbDown(key);
  };
  d.querySelector('button')?.focus();
}
