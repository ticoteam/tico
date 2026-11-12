/* ui/app/thread-connect.ts — A thread's Slack link, drawn in its Who can read sheet: an admin links the thread to a Slack
   channel or unlinks it (GET/POST/DELETE /api/v2/slack/links, backend/slack_threads.py). renderThreadConnect(thread, el)
   draws into `el`, and hides it for anyone who may not link. The rows here are shared with Slack's page under Tools
   (ui/app/redesign-library.ts lcSlackLinks).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */

'use strict';

const TCN_SLACK: Record<import('../types/api').SlackLink['state'], [string, string]> = {requested: ['Waiting', 'wait'], active: ['Live', 'ok'], suspended: ['Suspended', 'fail'], refused: ['Refused', 'fail'],
                   unlinked: ['Unlinked', 'idle']};
const tcnAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// Slack links a root thread whose audience is the company (a public channel) or its members (a private one).
const tcnSlackLinkable = (t: ThreadRow | null | undefined) => !!t && !t.parent_id && ['company', 'members'].includes(t.audience);
const tcnChannel = (link: import('../types/api').SlackLink) => '#' + String(link?.channel?.name || link?.channel?.id || '').replace(/^#/, '');
const tcnStatusHTML = ([label, tone]: [string, string]) => `<span class="cn-status cn-${tone}"><span class="cn-dot" aria-hidden="true"></span>${esc(label)}</span>`;

// The part of a sheet's markup that must be there: its lookup, or a plain error naming what is missing.
function tcnPart<T extends HTMLElement = HTMLElement>(root: ParentNode, selector: string): T {
  const part = root.querySelector<T>(selector);
  if (!part) throw new Error(`The Slack sheet has no ${selector}`);
  return part;
}

// ----------------------------------------------------------------- rows
function tcnSlackRowHTML(link: import('../types/api').SlackLink, title: string | null | undefined, {manage = true}: {manage?: boolean} = {}) {
  const state: [string, string] = TCN_SLACK[link.state] || [link.state, 'idle'];
  const resume = ['suspended', 'refused'].includes(link.state);
  return `<li class="cn-row cn-xrow" data-link="${esc(link.id)}">
    <span class="nav-icon" aria-hidden="true">tag</span>
    <span class="cn-name"><strong>${title == null ? '' : `${esc(title)} → `}${esc(tcnChannel(link))}</strong>${
      link.reason && link.state !== 'active' ? `<span class="muted">${esc(link.reason)}</span>` : ''}</span>
    ${tcnStatusHTML(state)}
    <span class="cn-acts">${manage ? `${resume ? '<button type="button" class="ghost small" data-slack-resume>Resume</button>' : ''}
      <button type="button" class="ghost small" data-slack-unlink>Unlink</button>` : ''}</span></li>`;
}
// Unlink a Slack link, or ask the gateway to check a suspended or refused one again.
function tcnSlackAct(link: import('../types/api').SlackLink, act: 'unlink' | 'resume') {
  const path = '/v2/slack/links/' + encodeURIComponent(link.id);
  return act === 'resume' ? post(path + '/resume', {}) : writeRequest('DELETE', path);
}

// A thread's title for a row that names only its id: from the sidebar's lists, else the thread itself. A thread this
// reader may not open has no title to show.
const TCN_TITLES = new Map<string, Promise<string>>();
async function tcnThreadTitle(id: string) {
  const known = [...(THREADS.pinned || []), ...(THREADS.threads || [])].find(t => t?.id === id);
  if (known) return threadTitle(known);
  if (!TCN_TITLES.has(id)) TCN_TITLES.set(id, get<ThreadRow>('/v2/threads/' + encodeURIComponent(id)).then(threadTitle, () => 'Thread'));
  return TCN_TITLES.get(id);
}
// ----------------------------------------------------------------- the thread's sheet
// `thread` is the thread view (docs/dev/threads-contract.md §10). Draws nothing, and hides `el`, for anyone who is not an
// admin, or a thread Slack does not apply to.
function renderThreadConnect(thread: ThreadRow | null | undefined, el: HTMLElement | null) {
  if (!el) return;
  const slack = tcnAdmin() && !!thread && !thread.parent_id;
  el.hidden = !slack;
  if (el.hidden || !thread) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="tcn"><section class="tcn-sec" data-tcn="slack" hidden><h3 class="thd-lbl">Slack</h3><div class="tcn-body"></div></section></div>`;
  // The sheet is a form: Enter in one of these fields does its own action, never the sheet's Save.
  el.addEventListener('keydown', (ev: KeyboardEvent) => {
    const target = eventElement(ev);
    if (ev.key !== 'Enter' || !target?.closest('.tcn input')) return;
    ev.preventDefault();
    target.closest('.tcn-sec')?.querySelector<HTMLElement>('[data-tcn-go]')?.click();
  });
  void tcnSlackDraw(thread, el.querySelector<HTMLElement>('[data-tcn="slack"]'));
}

async function tcnSlackDraw(thread: ThreadRow, sec: HTMLElement | null): Promise<void> {
  if (!sec) throw new Error('The Slack sheet is missing from the thread');
  const body = tcnPart(sec, '.tcn-body');
  const fail = (e: unknown) => { sec.hidden = false; body.innerHTML = `<span class="err" role="status">${esc(errorMessage(e, ''))}</span>`; };
  let link: import('../types/api').SlackLink | null, slackOn: boolean;
  try {
    const r = await get<import('../types/api').SlackLinkList>('/v2/slack/links?limit=1&thread=' + encodeURIComponent(thread.id), {fresh: true});
    link = r.links?.[0] || null; slackOn = r.available !== false;
  } catch (e) { fail(e); return; }
  if (!sec.isConnected) return;
  if (link) {
    sec.hidden = false;
    body.innerHTML = `<ul class="cn-list tcn-list">${tcnSlackRowHTML(link, null)}</ul><span class="err" role="status" data-tcn-err></span>`;
    tcnPart(body, 'ul').onclick = async (ev: MouseEvent) => {
      const b = eventElement(ev)?.closest('button');
      if (!b) return;
      const drop = b.matches('[data-slack-unlink]');
      if (drop && !(await askConfirm(`Unlink ${tcnChannel(link)}?`, b))) return;
      b.disabled = true;
      try { await tcnSlackAct(link, drop ? 'unlink' : 'resume'); await tcnSlackDraw(thread, sec); }
      catch (e) { tcnPart(body, '[data-tcn-err]').textContent = errorMessage(e, ''); b.disabled = false; }
    };
    return;
  }
  // With Slack off a link has nothing to carry it; Connections says how to turn it on.
  sec.hidden = !slackOn || !tcnSlackLinkable(thread);
  if (sec.hidden) return;
  body.innerHTML = `<div class="tcn-form"><input name="tcn-channel" placeholder="#channel" aria-label="Slack channel" maxlength="200" autocomplete="off">
      <label class="tcn-check"><input type="checkbox" name="tcn-history">Copy history</label>
      <button type="button" class="ghost small" data-tcn-go>Link</button></div><span class="err" role="status" data-tcn-err></span>`;
  tcnPart<HTMLButtonElement>(body, '[data-tcn-go]').onclick = async (ev: MouseEvent) => {
    const b = ev.currentTarget as HTMLButtonElement, channel = tcnPart<HTMLInputElement>(body, '[name=tcn-channel]').value.trim();
    const err = tcnPart(body, '[data-tcn-err]');
    if (!channel) { err.textContent = 'Name a channel'; return; }
    b.disabled = true; err.textContent = '';
    try {
      await post('/v2/slack/links', {thread_id: thread.id, channel, history: tcnPart<HTMLInputElement>(body, '[name=tcn-history]').checked});
      await tcnSlackDraw(thread, sec);
    } catch (e) { err.textContent = errorMessage(e, ''); b.disabled = false; }
  };
}
