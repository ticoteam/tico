/* ui/app/learnings.ts — The Learnings page (docs/learnings.md, Learn (beta)): a rail of nights, then one
   night read top to bottom. Sections Bots, Docs and Market, one row per subject; a row opens to each change with
   its source and diff (learnDiff, ui/app/bot-learnings.ts). GET /api/v2/learnings and /api/v2/learnings/{id}. */
'use strict';

type LearnKind = 'bots' | 'docs' | 'market';
/** One source a change cites: the chat, task, mail, meeting, doc or commit it came from. */
interface LearnSource { kind: string; label: string; quote: string; link: string }
interface LearnChange { where: string; diff?: string }
interface LearnEntry { title: string; sources: LearnSource[]; change: LearnChange }
/** One row of a section: a bot, a doc or a market entity, with the changes it had that night. */
interface LearnSubject { id: string; name: string; saw: string; delivery_status?: string; entries: LearnEntry[] }
interface LearnNightSummary { id: string; night: string; status: string; updates: number }
interface LearnNightDetail { id: string; night: string; status: string; sections: Record<LearnKind, LearnSubject[]> }
interface LearnNights { nights: LearnNightSummary[] }

const LEARN_SECTIONS: [LearnKind, string, string][] = [['bots', 'Bots', 'smart_toy'], ['docs', 'Docs', 'description'], ['market', 'Market', 'monitoring']];
const LEARN_SRC_ICONS: Record<string, string> = {chat: 'forum', task: 'task_alt', slack: 'tag', mail: 'mail', meeting: 'groups', doc: 'description', commit: 'history'};
let LEARN_LOAD = 0;

const learnNight = (night: string): string => new Date(night + 'T12:00:00').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});

async function pageLearnings(): Promise<void> {
  const load = ++LEARN_LOAD;
  const want = new URLSearchParams(S.route.split('?')[1] || '').get('night') || '';
  $('#main').innerHTML = pageHead({title: 'Learnings', hook: 'lrn-ph'}) + '<div class="lrn"><aside class="lrn-rail" aria-label="Nights"><div id="lrn-nights"></div></aside><section class="lrn-night" id="lrn-night" aria-live="polite"></section></div>';
  let data: LearnNights;
  try { data = await get<LearnNights>('/v2/learnings'); } catch (e) { $('#lrn-night').innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  if (load !== LEARN_LOAD) return;
  const nights = data.nights || [];
  if (!nights.length) {
    const empty = await learnEmptyHTML();
    if (load === LEARN_LOAD) $('#lrn-night').innerHTML = empty;
    return;
  }
  const cur = nights.find(n => n.id === want) || nights[0];
  if (!cur) return; // unreachable: nights is non-empty here
  $('#lrn-nights').innerHTML = nights.map(n => `<a class="lrn-n${n.id === cur.id ? ' on' : ''}" href="${LEARNINGS}?night=${encodeURIComponent(n.id)}"><span>${esc(learnNight(n.night))}</span><span class="c">${n.status === 'done' ? n.updates : '—'}</span></a>`).join('');
  await learnShowNight(cur, load);
}

// No nights yet: what the page is for and, by Learn's switch in Maintenance (anyone signed in may read it), the way on.
// Learn on: it says when the first night shows. Off: Turn on Learn for whoever may change it, else Open Maintenance.
async function learnEmptyHTML(): Promise<string> {
  let on = false, can = false;
  try {
    const m = await get<MaintView>('/v2/maintenance');
    can = !!m.can_change; on = !!m.steps?.find(s => s.key === 'learn')?.enabled;
  } catch { /* unread: the same line, and Open Maintenance */ }
  if (on) return `<div class="empty-state">${EMPTY_ART['learn-on']}<p>Learn is on. The first night shows here tomorrow.</p></div>`;
  return `<div class="empty-state">${EMPTY_ART.learn}<p>What your bots learned each night, and from where.</p><a class="${can ? 'primary' : 'ghost'}" href="#/automations/maintenance">${can ? 'Turn on Learn' : 'Open Maintenance'}</a></div>`;
}

async function learnShowNight(n: LearnNightSummary, load: number): Promise<void> {
  const box = $('#lrn-night');
  const head = (text: string): string => `<div class="lrn-head"><h2>${esc(learnNight(n.night))}</h2>${text ? `<p>${esc(text)}</p>` : ''}</div>`;
  if (n.status === 'running') { box.innerHTML = head('Running now.'); return; }
  if (n.status === 'quiet') { box.innerHTML = head('No new activity.'); return; }
  if (n.status === 'unconfigured') { box.innerHTML = head('Learnings need a decision model: add a Tico AI Gateway key, your own TypeSafe key, or an AI provider key.'); return; }
  if (n.status === 'capped') { box.innerHTML = head("Skipped: Decide reached this month's spend cap. The same activity is read again once it runs."); return; }
  if (n.status === 'failed') { box.innerHTML = head('The run failed.'); return; }
  let night: LearnNightDetail;
  try { night = await get<LearnNightDetail>(`/v2/learnings/${encodeURIComponent(n.id)}`); } catch (e) { box.innerHTML = head('') + `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  if (load !== LEARN_LOAD) return;
  const s: Partial<Record<LearnKind, LearnSubject[]>> = night.sections || {};
  const groups = LEARN_SECTIONS.map(([k, label, icon]) => ({k, label, icon, subs: s[k] || []})).filter(g => g.subs.length);
  const total = groups.reduce((a, g) => a + g.subs.reduce((b, x) => b + x.entries.length, 0), 0);
  const parts = groups.map(g => `${g.subs.length} ${g.label.toLowerCase()}`);
  box.innerHTML = head(total ? `${total} update${total === 1 ? '' : 's'} across ${parts.join(', ')}` : 'Nothing changed.')
    + groups.map(g => `<section class="lrn-grp"><h3><span class="nav-icon" aria-hidden="true">${g.icon}</span>${g.label}</h3>${g.subs.map(x => learnSubject(g.k, x)).join('')}</section>`).join('');
  learnFitNames(box);
  void document.fonts.ready.then(() => { if (load === LEARN_LOAD) learnFitNames(box); });
}
// Each section's name column is as wide as its longest name (its text, not its cell), 80-280px, so its second column
// starts at one x. Runs again once the fonts are in: measured in the fallback font, a name would come out short.
function learnFitNames(box: Element): void {
  for (const g of box.querySelectorAll<HTMLElement>('.lrn-grp')) {
    // Unwrapped while measured: a stacked (narrow) layout wraps names, and the width must not depend on the layout.
    const w = Math.max(0, ...[...g.querySelectorAll<HTMLElement>('.lrn-subj .nm')].map(n => {
      n.style.whiteSpace = 'nowrap';
      const r = document.createRange(); r.selectNodeContents(n);
      const width = Math.ceil(r.getBoundingClientRect().width);
      n.style.whiteSpace = '';
      return width;
    }));
    g.style.setProperty('--lrn-nm', `${Math.min(Math.max(w, 80), 280)}px`);
  }
}

function learnSubject(kind: LearnKind, x: LearnSubject): string {
  const more = Math.max(0, x.entries.length - 1);
  const av = kind === 'bots' ? botAvatar(x.id, 22) : `<span class="lrn-sq" aria-hidden="true">${esc((x.name || '?')[0])}</span>`;
  const name = kind === 'bots' ? `<a href="#/bot/${encodeURIComponent(x.id)}">${esc(x.name)}</a>` : esc(x.name);
  return `<details class="lrn-subj"><summary>${av}<span class="nm" title="${esc(x.name)}">${name}</span><span class="first">${esc(x.entries[0]?.title || x.delivery_status || '')}</span><span class="more">${more ? `+${more} more` : ''}<span class="nav-icon" aria-hidden="true">chevron_right</span></span></summary>
    <div class="lrn-body">${x.saw ? `<div class="saw">${esc(x.saw)}</div>` : ''}${x.delivery_status ? `<p>${esc(x.delivery_status)}</p>` : ''}${x.entries.map(learnEntry).join('')}</div></details>`;
}

function learnEntry(e: LearnEntry): string {
  const src = (e.sources || []).map((s: LearnSource) => {
    const label = `<span class="nav-icon" aria-hidden="true">${LEARN_SRC_ICONS[s.kind] || 'description'}</span>${esc(s.label)}`;
    const ext = /^https?:/.test(s.link || '');
    return `<div class="src">${s.link ? `<a href="${esc(s.link)}"${ext ? ' target="_blank" rel="noopener"' : ''}>${label}</a>` : `<span>${label}</span>`}${s.quote ? ` <q>${esc(s.quote)}</q>` : ''}</div>`;
  }).join('');
  const c: Partial<LearnChange> = e.change || {};
  return `<div class="lrn-entry"><div class="t">${esc(e.title)}</div><div class="kv">
    ${src ? `<span class="k">Source</span><span class="v">${src}</span>` : ''}
    <span class="k">Change</span><span class="v"><span class="where">${esc(c.where || '')}</span>${c.diff ? `<pre class="lrn-diff">${learnDiff(c.diff)}</pre>` : ''}</span></div></div>`;
}

document.addEventListener('click', ev => {             // a bot's name opens its page, not the row
  if (eventElement(ev)?.closest('.lrn-subj summary a')) ev.stopPropagation();
}, true);
