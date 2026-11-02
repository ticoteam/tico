/* ui/app/issues.js — Shared issue and request rows, closeIssue, issue composer, Issues/Runs/Access tables
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- shared fragments
const issueLink = i => `<a href="${i.url}" target="_blank" rel="noopener" class="mono">#${i.number}</a>`;
const statusPill = i => i.state === 'CLOSED' ? `<span class="pill">closed</span>` : `<span class="pill ${i.status || ''}">${esc(i.status || 'no status')}</span>${i.needs_human ? ` <span class="pill needs">${esc(needsLabel(i.owner_id))}</span>` : ''}`;
// "Needs you" only when the viewer is the one it waits on; otherwise "Needs <Name>".
const needsLabel = owner => {
  const slug = String(owner || '').toLowerCase();
  const p = slug && (S.people || []).find(x => (x.email || '').split('@')[0].toLowerCase() === slug);
  if (!p || slug === myHandle()) return 'Needs you';
  return 'Needs ' + String(p.name || slug).trim().split(/\s+/)[0];
};
const prioPill = i => i.priority ? `<span class="pill ${i.priority}">${i.priority}</span>` : '';
// Memoized per roster: task rows and sort comparators call this many times per render. Rebuilt when S.emps is
// replaced (every refresh), grows in place (bot-branches.js) or the assistant's name changes.
// A bot is named everywhere by its id (S.emps rows' `id`, the `bot:<id>` actor, routes and API paths); its slug is a
// label people type and read, and it can change. `botRow(id)` is the roster row for an id (a leading `bot:` is
// dropped); `botByRef(text)` also takes a slug, for what a person typed or an old link; `botSlug(id)` is the label.
const botRow = id => { const key = String(id || '').replace(/^bot:/, ''); return key ? (S.emps || []).find(e => e.id === key) : undefined; };
const botByRef = ref => { const key = String(ref || '').replace(/^bot:/, ''); return key ? botRow(key) || (S.emps || []).find(e => e.slug === key) : undefined; };
const botSlug = id => botRow(id)?.slug || String(id || '').replace(/^bot:/, '');
let BOT_NAMES = {emps: null, len: -1, key: '', map: new Map()};
const botDisplayName = id => {
  const key = assistantBot() + '\n' + assistantName();
  if (BOT_NAMES.emps !== S.emps || BOT_NAMES.len !== S.emps.length || BOT_NAMES.key !== key)
    BOT_NAMES = {emps: S.emps, len: S.emps.length, key, map: new Map()};
  const hit = BOT_NAMES.map.get(id);
  if (hit !== undefined) return hit;
  const row = botRow(id);
  const name = id === assistantBot() ? assistantName() : row?.display_name || row?.slug || id || '—';
  const out = S.emps.filter(e => e.status !== 'archived' && (e.display_name || e.slug || e.id).toLowerCase() === name.toLowerCase()).length > 1
    ? `${name} (${row?.slug || id})` : name;
  BOT_NAMES.map.set(id, out);
  return out;
};
const empName = id => esc(botDisplayName(id));
const empChip = id => id && botRow(id) ? `<a class="chip" href="#/bot/${encodeURIComponent(id)}">${avatar(id, 16)}<span>${empName(id)}</span></a>` : `<span class="chip">${empName(id)}</span>`;

// A bot's code lives in a repository named by the environment. The server sends repo_url when it
// knows the full address; otherwise a bare name or owner/name is resolved against github_owner, and
// an environment with no Git hosting simply shows the name.
const repoHostUrl = repo => {
  if (/^https?:\/\//i.test(repo)) return repo;
  const owner = String(S.config.github_owner || '').trim();
  if (repo.includes('/')) return owner ? `https://github.com/${repo}` : '';
  return owner ? `https://github.com/${owner}/${repo}` : '';
};
function botRepoInfo(e) {
  const repo = String(e?.repo || '').trim();
  if (!repo) return null;
  const url = String(e?.repo_url || '').trim() || repoHostUrl(repo);
  return {url, label: repo.replace(/^https?:\/\/[^/]+\//i, '').replace(/\.git$/i, '')};
}
function botRepoHTML(e) {
  const info = botRepoInfo(e);
  if (!info) return '';
  return info.url
    ? `Repository <a href="${esc(info.url)}" target="_blank" rel="noopener">${esc(info.label)}</a>`
    : `Repository <span class="mono">${esc(info.label)}</span>`;
}
// Human owner slugs: an owner:<slug> item that waits on a person, not a bot. A person's slug is
// the local part of their roster email.
const isHuman = slug => !!slug && (S.people || []).some(p =>
  (p.email || '').split('@')[0].toLowerCase() === String(slug).toLowerCase());
const requestText = i => (isHuman(i.owner_id) ? (i.body || i.last_comment) : (i.last_comment || i.body)) || '';
const requestContext = text => String(text || '').split('\n').filter(line => !/^\s*(?:Parent:|Inbox-request:|Requested by\s*$|Full proposal:\s*s3:\/\/\S+\s*$)/i.test(line)).join('\n').trim();
const REQUEST_HEADS = ['Decision needed','Question','Decision','Why now','Why','If yes','If no or edit','If no','Needed by','By when','Deadline'];
function requestParts(text) {
  const out = {}, hits = [];
  const re = new RegExp('(?:^|\\n)\\s*(?:\\*\\*)?(' + REQUEST_HEADS.join('|') + ')\\s*:?(?:\\*\\*)?\\s*:?\\s*', 'gi');
  let m;
  while ((m = re.exec(String(text || '')))) hits.push({key: m[1].toLowerCase(), start: re.lastIndex, head: m.index});
  hits.forEach((hit, n) => { out[hit.key] = String(text || '').slice(hit.start, hits[n + 1]?.head ?? undefined).trim(); });
  return out;
}
const requestField = (text, key) => requestParts(text)[String(key).toLowerCase()]?.split('\n')[0].trim() || '';
const requestPart = (parts, ...keys) => keys.map(k => parts[k.toLowerCase()]).find(Boolean) || '';
const requestPreviewKeys = text => {
  const keys = [];
  for (const m of String(text || '').matchAll(/s3:\/\/acme-tico-hub\/[^\s<>"'`)\]]+?\.(?:md|markdown|txt)\b/gi)) {
    const key = m[0].replace(/^s3:\/\/[^/]+\//, '');
    if (key && !keys.includes(key)) keys.push(key);
  }
  return keys;
};
function requestDue(i) {
  const raw = (requestField(requestText(i), 'Needed by') || requestField(requestText(i), 'By when') || requestField(requestText(i), 'Deadline')).replace(/\.$/, '').trim();
  if (!raw) return {time: Infinity, label: '', iso: ''};
  const isoMatch = raw.match(/\b(\d{4})-(\d{2})-(\d{2})\b/);
  const namedMatch = raw.match(/\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)?\s*(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b/i);
  let date;
  if (isoMatch) {
    date = new Date(+isoMatch[1], +isoMatch[2] - 1, +isoMatch[3], 12);
  } else if (namedMatch) {
    const month = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'].indexOf(namedMatch[1].slice(0, 3).toLowerCase());
    const explicitYear = namedMatch[3] ? +namedMatch[3] : null;
    const year = explicitYear || new Date().getFullYear();
    date = new Date(year, month, +namedMatch[2], 12);
    if (!explicitYear && date.getTime() < Date.now() - 30 * 86400000) date.setFullYear(year + 1);
  }
  if (!date || Number.isNaN(date.getTime())) return {time: Infinity, label: '', iso: ''};
  const showYear = date.getFullYear() !== new Date().getFullYear();
  return {
    time: date.getTime(),
    label: date.toLocaleDateString(undefined, {month:'short', day:'numeric', ...(showYear ? {year:'numeric'} : {})}),
    iso: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`,
  };
}
// A request list folds anything past `limit` behind "Show N more"; the fold stays open for the
// session once it has been opened (S.needsExpanded).
function needsYouBlock(items, limit = Infinity) {
  if (!items.length) return `<div class="empty">Nothing needs you.</div>`;
  const more = Math.max(0, items.length - limit);
  return items.map((i, n) => needsYouItem(i, n >= limit)).join('')
    + (more ? `<div class="needs-more"><button class="morechip" type="button" data-needs-more aria-expanded="false">Show ${more} more</button></div>` : '');
}
function needsYouItem(i, folded, showName = true) {
  {
    const mine = isHuman(i.owner_id);
    // a request for the owner: the Issue body if it is their Issue, else the bot's latest comment
    const text = requestText(i);
    const asker = mine ? (text.match(/Requested by\s*\n+\s*([^\n]+)/i) || [])[1] : null;
    const parentNo = +((text.match(/Parent:?\**\s*#?(\d+)/i) || [])[1] || 0);
    const parentOwner = parentNo ? S.issues.find(x => x.number === parentNo)?.owner_id : null;
    const bucketOwner = mine ? (text.match(/s3:\/\/[^/]+\/([^/\s]+)/i) || [])[1] : null;
    const askerSlug = (asker && S.emps.find(e => String(e.display_name || '').toLowerCase() === asker.trim().toLowerCase())?.id)
      || (bucketOwner && botByRef(bucketOwner)?.id)
      || (mine ? parentOwner : i.owner_id);
    // The title is the plain-English decision. Keep the useful context visible, fold away the
    // handoff boilerplate, and retain the authored request in full below it.
    const parts = requestParts(text);
    const decision = requestPart(parts, 'Decision needed', 'Question', 'Decision');
    const why = requestPart(parts, 'Why now', 'Why');
    const next = requestPart(parts, 'If yes');
    const preview = text.replace(/[#*>`]/g, '').replace(/\s+/g, ' ').trim().slice(0, 220);
    const ask = i.title || decision || preview || `Task #${i.number}`;
    const due = requestDue(i);
    const context = requestContext(why || (!parts['decision needed'] && !parts.question && !parts.decision ? text : ''));
    const previews = requestPreviewKeys(text);
    return `<details class="req" data-n="${i.number}"${folded ? ' hidden data-folded' : ''}>
      <summary>
        ${askerSlug ? avatar(askerSlug, 24) : personCircle(personHandle(asker), 24)}
        <span class="req-text">${showName ? `<span class="req-from">${askerSlug ? empName(askerSlug) : 'A bot'}:</span> ` : ''}${esc(ask)}</span>
        ${due.label ? `<time class="req-meta" datetime="${due.iso}" title="Needed by ${esc(due.label)}">${esc(due.label)}</time>` : '<span class="req-meta"></span>'}
        <span class="req-arrow" aria-hidden="true">›</span>
      </summary>
      <div class="req-body">
        <div class="req-title">${esc(ask)}</div>
        ${context ? `<div class="req-context"><div class="md">${safeMd(context)}</div></div>` : decision ? `<div class="req-context"><div class="md">${safeMd(decision)}</div></div>` : ''}
        ${next ? `<div class="req-next"><span>Next</span><div class="md">${safeMd(next)}</div></div>` : ''}
        <div class="issue-actions">
          ${i.state === 'OPEN' ? `<button class="primary" type="button" data-issue-mode="comment" data-issue="${i.number}">Comment</button>
          <button class="ghost danger" type="button" data-issue-mode="close" data-issue="${i.number}">Close</button>` : ''}

        </div>
        <div class="issue-compose" hidden></div>
        ${previews.length ? `<div class="req-previews">${previews.map((key, n) => `<button class="ghost" type="button" data-preview-key="${esc(key)}" aria-expanded="false">View ${previews.length > 1 ? `proposal ${n + 1}` : 'proposal'}</button>`).join('')}</div>${previews.map(key => `<div class="req-preview" data-preview-for="${esc(key)}" hidden></div>`).join('')}` : ''}
        <details class="req-original"><summary>Full request</summary><div class="md" data-full-request>${safeMd(text)}</div></details>
      </div>
    </details>`;
  }
}

const CLOSE_WITHOUT_APPROVAL = 'Closed without approval. Do not treat this as authorization to send, publish, spend, or change live systems.';
const pendingClosedIssues = new Set();
async function closeIssue(button, n) {
  if (button.disabled) return;
  const label = button.textContent;
  button.disabled = true; button.textContent = 'Closing…';
  try {
    await post(`/issues/${n}/comment`, {body: CLOSE_WITHOUT_APPROVAL, close: true, clear_needs_human: false});
    pendingClosedIssues.add(n);
    const issue = S.issues.find(i => i.number === n);
    if (issue) Object.assign(issue, {state: 'CLOSED', needs_human: false, closedAt: new Date().toISOString()});
    document.querySelectorAll(`.req[data-n="${n}"], .bcard[data-n="${n}"], .trow[data-n="${n}"]`).forEach(el => el.remove());
    if ($('#needs') && !$('#needs .req')) $('#needs').innerHTML = '<div class="empty">Nothing needs you.</div>';
    renderTree();
    toast(`Closed #${n}`);
    void refresh(false);
  } catch (e) {
    button.disabled = false; button.textContent = label;
    toast(`Could not close #${n}: ${e.message}`, true);
  }
}
function issueComposer(host, n, mode) {
  if (!host) return;
  if (!host.hidden && host.dataset.mode === mode) { host.hidden = true; host.innerHTML = ''; return; }
  const closing = mode === 'close';
  host.hidden = false; host.dataset.mode = mode;
  host.innerHTML = `<form>
    <textarea aria-label="${closing ? 'Optional closing note' : 'Comment'} on #${esc(n)}" placeholder="${closing ? 'Optional closing note…' : 'What should the bot know?'}"></textarea>
    <div class="row"><button class="${closing ? 'ghost danger' : 'primary'}" type="submit">${closing ? 'Close task' : 'Send to bot'}</button>
      <button class="ghost" type="button" data-issue-cancel>Cancel</button><span class="muted" data-issue-msg></span></div>
    <p class="issue-help">${closing ? 'Closing does not approve the proposed action.' : 'Leaves Needs you until the bot needs another decision.'}</p>
  </form>`;
  const form = $('form', host), box = $('textarea', host), msg = $('[data-issue-msg]', host);
  $('[data-issue-cancel]', host).onclick = () => { host.hidden = true; host.innerHTML = ''; };
  form.onsubmit = async ev => {
    ev.preventDefault();
    let body = box.value.trim();
    if (!closing && !body) { box.focus(); return; }
    if (closing) body = body ? `${body}\n\n${CLOSE_WITHOUT_APPROVAL}` : CLOSE_WITHOUT_APPROVAL;
    [...form.elements].forEach(el => el.disabled = true); msg.textContent = closing ? 'Closing…' : 'Sending…';
    try {
      await post(`/issues/${n}/comment`, closing
        ? {body, close: true, clear_needs_human: false}
        : {body, await_bot: true});
      toast(closing ? `Closed #${n} without approval` : `Sent comment on #${n} to the bot`);
      host.hidden = true; host.innerHTML = '';
      host.dispatchEvent(new CustomEvent('issue-acted', {bubbles: true}));
      await refresh(true);
    } catch (e) {
      msg.innerHTML = `<span class="err">${esc(e.message)}</span>`;
      [...form.elements].forEach(el => el.disabled = false);
    }
  };
  box.focus();
}

document.addEventListener('click', async ev => {
  const moreNeeds = ev.target.closest('[data-needs-more]');
  if (moreNeeds) {
    const box = moreNeeds.closest('#needs');
    const folded = box.querySelectorAll('.req[data-folded]');
    const open = moreNeeds.getAttribute('aria-expanded') !== 'true';
    folded.forEach(el => { el.hidden = !open; });
    S.needsExpanded = open;
    moreNeeds.setAttribute('aria-expanded', String(open));
    moreNeeds.textContent = open ? 'Show less' : `Show ${folded.length} more`;
    return;
  }
  const expand = ev.target.closest('[data-expand-preview]');
  if (expand) {
    const full = expand.closest('.req-preview').classList.toggle('expanded');
    expand.textContent = full ? 'Collapse document' : 'Expand full document';
    expand.setAttribute('aria-expanded', String(full));
    return;
  }
  const action = ev.target.closest('[data-issue-mode]');
  if (action) {
    ev.preventDefault();
    if (action.dataset.issueMode === 'close') {
      await closeIssue(action, +action.dataset.issue);
      return;
    }
    const scope = action.closest('.req,.bcard,.trow');
    issueComposer($(scope.matches('.bcard') ? ':scope > .issue-compose' : '.issue-compose', scope), +action.dataset.issue, action.dataset.issueMode);
    return;
  }
  const button = ev.target.closest('[data-preview-key]');
  if (!button) return;
  ev.preventDefault();
  const key = button.dataset.previewKey;
  const target = button.closest('.req-body')?.querySelector(`[data-preview-for="${CSS.escape(key)}"]`);
  if (!target) return;
  if (!target.hidden) {
    target.hidden = true; button.setAttribute('aria-expanded', 'false'); button.textContent = button.textContent.replace(/^Hide/, 'View'); return;
  }
  target.hidden = false; button.setAttribute('aria-expanded', 'true'); button.textContent = button.textContent.replace(/^View/, 'Hide');
  if (target.dataset.loaded) return;
  target.innerHTML = '<div class="empty">Loading proposal…</div>';
  try {
    const doc = await get(`/preview?key=${encodeURIComponent(key)}`);
    target.innerHTML = `<button class="ghost" type="button" data-expand-preview aria-expanded="false">Expand full document</button><div class="md">${safeMd(doc.text || '')}</div>`; target.dataset.loaded = 'true';
  } catch (e) { target.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
});

// The issue list is deliberately compact and truncated. Fetch one complete issue only when its
// request opens so “Full request” really is the authored body/comment, without making the inbox
// download every comment on every ticket. Only a request with an issue number fetches one.
document.addEventListener('toggle', async ev => {
  const details = ev.target;
  if (!details.matches?.('.req[data-n]') || !details.open || details.dataset.fullLoaded || details.dataset.fullLoading) return;
  const n = Number(details.dataset.n), original = $('[data-full-request]', details);
  if (!Number.isInteger(n) || n < 1) return;
  details.dataset.fullLoading = 'true';
  try {
    const issue = await get(`/issues/${n}`), listed = S.issues.find(i => i.number === n);
    const comments = Array.isArray(issue.comments) ? issue.comments : [];
    const last = comments.length ? String(comments[comments.length - 1]?.body || '') : '';
    const text = isHuman(listed?.owner_id) ? String(issue.body || last) : String(last || issue.body || '');
    if (original) original.innerHTML = safeMd(text);
    const body = $('.req-body', details), originalDetails = $('.req-original', details);
    const known = new Set([...details.querySelectorAll('[data-preview-key]')].map(el => el.dataset.previewKey));
    const fresh = requestPreviewKeys(text).filter(key => !known.has(key));
    if (fresh.length && body && originalDetails) {
      let controls = $('.req-previews', body);
      if (!controls) {
        controls = document.createElement('div'); controls.className = 'req-previews';
        originalDetails.before(controls);
      }
      for (const key of fresh) {
        const button = document.createElement('button');
        button.className = 'ghost'; button.type = 'button'; button.dataset.previewKey = key;
        button.setAttribute('aria-expanded', 'false'); button.textContent = 'View proposal'; controls.append(button);
        const preview = document.createElement('div'); preview.className = 'req-preview'; preview.dataset.previewFor = key; preview.hidden = true;
        originalDetails.before(preview);
      }
    }
    details.dataset.fullLoaded = 'true';
  } catch (e) {
    if (original) original.insertAdjacentHTML('afterbegin', `<div class="err">Could not load the complete request: ${esc(e.message)}</div>`);
  } finally { delete details.dataset.fullLoading; }
}, true);
function issuesTable(list, showOwner) {
  if (!list.length) return `<div class="empty">None.</div>`;
  return `<div class="scroll"><table><tr><th>#</th><th>Title</th>${showOwner ? '<th>Owner</th>' : ''}<th>Status</th><th>Priority</th><th>Updated</th></tr>
  ${list.map(i => `<tr><td>${issueLink(i)}</td><td>${esc(i.title)}${i.last_comment ? `<details><summary>last comment</summary><div class="q">${esc(i.last_comment)}</div></details>` : ''}</td>
    ${showOwner ? `<td>${empChip(i.owner_id)}</td>` : ''}<td>${statusPill(i)}</td><td>${prioPill(i)}</td><td class="muted tnum">${ago(i.updatedAt)}</td></tr>`).join('')}</table></div>`;
}
// The models that did a run's work, each with its effort (not the bot's configured one); empty when nothing was counted.
const ranOn = (made, effort = true) => [...new Set(((made || {}).segments || []).filter(s => s.model)
  .map(s => s.model + (effort && s.effort ? ` (${s.effort})` : '')))].join(' then ');
// Why a task run was on that model: the bot's task model, the fallback from it, or a check kept (backend/task_models.py).
const ranOnNote = made => ((made || {}).task_model || {}).note || '';
// A run that failed or whose lease ran out (run.state, docs/status-and-state.md Runs): its reason shows with it.
const runUnfinished = r => ['failed', 'expired'].includes(r.state);
// A failed run offers Report this: Support opens with it attached (ui/support.js).
function runsReportHTML(r, icon = false) {
  if (!window.supportOn?.() || !window.reportThisHTML) return '';
  const why = r.failure_reason || r.outcome || `exit ${r.exit}`;
  return `<div>${window.reportThisHTML({kind: 'run', title: `${r.employee ? botDisplayName(r.employee) : 'Run'} failed: ${why}`, detail: why, ref: r.id || '',
    group: 'run:' + String(why).replace(/\d+/g, '#').slice(0, 80)}, icon)}</div>`;
}
function runsTable(list, showEmp=true) {
  if (!list.length) return `<div class="empty">No runs yet.</div>`;
  return `<div class="scroll"><table><tr>${showEmp ? '<th>Bot</th>' : ''}<th>Finished</th><th>Result</th><th>Duration</th><th>Cost</th><th>Model</th><th>Picked up</th><th></th></tr>
  ${list.map(r => `<tr>${showEmp ? `<td>${empChip(r.employee)}</td>` : ''}
    <td class="muted tnum">${ago(r.finished_at)}</td><td>${runUnfinished(r) ? `<span class="pill fail">${esc(r.outcome)} run</span><div class="mono muted">${esc(r.id)}</div>${r.failure_reason ? `<div class="muted">${esc(r.failure_reason)}</div>` : ''}${runsReportHTML(r)}` : r.exit === 0 ? '<span class="pill ok">Done</span>' : `<span class="pill fail">${esc(r.outcome || `exit ${r.exit}`)}</span>${runsReportHTML(r)}`}</td>
    <td class="tnum">${r.duration_s != null ? Math.round(r.duration_s/60) + 'm ' + (r.duration_s%60) + 's' : ''}</td>
    <td class="tnum">${r.cost_usd != null ? '$' + r.cost_usd.toFixed(2) : (r.output_tokens != null ? r.output_tokens.toLocaleString() + ' tokens out' : '')}</td>
    <td class="mono muted" title="${esc(ranOnNote(r.ran_on) || 'the model that actually ran it, which a fallback changes')}">${esc(ranOn(r.ran_on))}${(r.ran_on?.task_model || {}).source === 'fallback' ? ` <span class="pill waiting" title="${esc(ranOnNote(r.ran_on))}">task model off</span>` : ''}</td>
    <td>${r.fallback ? `<span class="pill waiting" title="ran on the fallback harness after the primary was unavailable">${esc(r.fallback)}</span>` : r.resumed ? 'Where it left off' : 'From the start'}</td><td><a href="${API}/runs/${r.run}/log" target="_blank" rel="noopener">Log</a></td></tr>`).join('')}</table></div>`;
}

// ----------------------------------------------------------------- access card
const SERVICE_NAMES = {gmail:'Gmail', 'google-calendar':'Google Calendar', 'google-workspace-admin':'Google Workspace admin', 'google-ads':'Google Ads', 'meta-ads':'Meta Ads', linkedin:'LinkedIn', x:'X', reddit:'Reddit', 'close-crm':'Close CRM', calendly:'Calendly', posthog:'PostHog', brex:'Brex', mercury:'Mercury', stripe:'Stripe', upfluence:'Upfluence', pangram:'Pangram', postiz:'Postiz', 'ein-presswire':'EIN Presswire', 'product-hunt':'Product Hunt', wellfound:'Wellfound', postjobfree:'PostJobFree', 'hugging-face':'Hugging Face', slack:'Slack', 'web-search':'Web search', cursor:'Cursor', 'marketing-email-sender':'Marketing email sender', elevenlabs:'ElevenLabs', gemini:'Gemini', heygen:'HeyGen', xai:'xAI'};
const SEND_VERBS = new Set(['send', 'post', 'publish', 'spend', 'write']);
function accessTable(e) {
  const acc = e.access || [];
  const mail = acc.filter(a => /gmail|email/.test(a.service));
  const emailLine = mail.length
    ? mail.map(a => `can ${a.can.includes('send') && e.outbound_send ? '<strong>send</strong>' : 'read and draft'} as <strong>${esc(a.identity || 'unknown address')}</strong>${a.can.includes('send') && !e.outbound_send ? ' <span class="muted">(send is declared but outbound is off)</span>' : ''}`).join('; ')
    : '<strong>none</strong>';
  const verb = (v, a) => `<span class="pill ${SEND_VERBS.has(v) ? (e.outbound_send ? 'ok' : 'waiting') : ''}" title="${SEND_VERBS.has(v) && !e.outbound_send ? 'gated: outbound_send is off' : ''}">${v}</span>`;
  const rows = acc.map(a => `<tr><td><strong>${esc(SERVICE_NAMES[a.service] || a.service)}</strong></td><td>${esc(a.identity || '—')}</td>
    <td class="tags">${(a.can || []).map(v => verb(v, a)).join(' ')}</td>
    <td>${a.connected ? '<span class="pill ok">connected</span>' : '<span class="pill">not connected</span>'}</td><td class="muted">${esc(a.note || '')}</td></tr>`).join('');
  return `<div class="acc-summary">
      <div><span class="k">Email</span> ${emailLine}</div>
      <div><span class="k">Outbound</span> ${e.outbound_send ? '<strong>on</strong>' : '<strong>off</strong>: drafts only'}</div>
      ${e.secrets_file ? '' : '<div><span class="muted">No credentials file.</span></div>'}
    </div>
    ${rows ? `<div class="scroll"><table><tr><th>Service</th><th>As</th><th>Can</th><th>Status</th><th></th></tr>${rows}</table></div>` : ''}`;
}
