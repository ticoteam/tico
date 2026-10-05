/* ui/app/task-code.js — The task modal's right rail: Code (worktrees and pull requests) and Subtasks (the roll-up),
   and the one small PR badge a task row shows (the worst state across its pull requests).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A task's pr_state is the worst of its PR links (backend: 'failing' > 'conflict' > 'changes_requested' > 'open' > 'merged').
const PR_BADGE = {failing: ['PR ✕', 'fail', 'Checks failing'], conflict: ['PR conflict', 'fail', 'Merge conflict'],
  changes_requested: ['PR changes', 'waiting', 'Changes requested'], open: ['PR', 'run', 'Pull request open'],
  merged: ['PR ✓', 'ok', 'Merged']};
function prStateBadge(state) {
  const b = PR_BADGE[state]; if (!b) return '';
  return `<span class="pr-badge ${b[1]}" data-pr-state="${esc(state)}" title="${esc(b[2])}" aria-label="${esc(b[2])}">${esc(b[0])}</span>`;
}

// ---- Code: one line per worktree and per pull request
const taskLinkDetail = l => {
  let d = l.detail || l.detail_json || {};
  if (typeof d === 'string') { try { d = JSON.parse(d); } catch { d = {}; } }
  return d && typeof d === 'object' ? d : {};
};
const taskLinkNum = (l, key) => { const v = l[key] ?? taskLinkDetail(l)[key]; return Number.isFinite(+v) ? +v : 0; };
function taskLinkRepo(l) {
  if (l.repo) return String(l.repo);
  const m = /^https?:\/\/github\.com\/([\w.-]+\/[\w.-]+)/i.exec(String(l.url || ''));
  return m ? m[1] : '';
}
const repoShort = repo => String(repo || '').split('/').pop();
// A worktree row without a repo: the repo its folder names (tasks/<task>/<bot>__<repo>, or a folder named after a repo
// this task or the install knows); '' when it cannot be told. The Repo filter uses the same answer.
function taskWorktreeRepo(l, siblings = []) {
  const repo = taskLinkRepo(l);
  if (repo) return repoShort(repo);
  const detail = taskLinkDetail(l);
  const last = String(l.path || detail.path || '').split(/[\\/]/).filter(Boolean).pop() || '';
  if (last.includes('__')) return last.split('__').pop();
  const known = [...new Set([...siblings.map(taskLinkRepo), ...((typeof REPOS !== 'undefined' && REPOS?.repositories) || []).map(r => r.full_name)]
    .filter(Boolean).map(repoShort))];
  return known.includes(last) ? last : '';     // a branch name is never taken for a repo: it would be a guess
}
function taskWorktreeURL(l) {
  if (/^https?:/.test(String(l.url || ''))) return l.url;
  const repo = taskLinkRepo(l);
  return repo.includes('/') && l.branch ? `https://github.com/${repo}/tree/${String(l.branch).split('/').map(encodeURIComponent).join('/')}` : '';
}
function taskPRNumber(l) {
  if (l.number) return String(l.number);
  const m = /\/pull\/(\d+)/.exec(String(l.url || ''));
  return m ? m[1] : '';
}
// A chip that is only a glyph (✓ ✕ …) carries its words for screen readers and as a tooltip.
const chip = (text, tone = '', title = '') => `<span class="code-chip${tone ? ' ' + tone : ''}"${title ? ` title="${esc(title)}" role="img" aria-label="${esc(title)}"` : ''}>${esc(text)}</span>`;
function taskPRChips(l) {
  const out = [], comments = taskLinkNum(l, 'pending_comments');
  if (l.state === 'merged' || l.state === 'shipped') return chip('merged', 'ok');
  if (l.state === 'closed') return chip('closed');
  if (l.state === 'draft') out.push(chip('draft'));
  if (l.checks === 'passing') out.push(chip('✓', 'ok', 'Checks passing'));
  else if (l.checks === 'failing') out.push(chip('✕', 'fail', 'Checks failing'));
  else if (l.checks === 'pending') out.push(chip('checks running'));
  if (l.mergeable === 'conflict') out.push(chip('conflict', 'fail', 'Merge conflict'));
  if (l.review_state === 'changes_requested') out.push(chip('changes requested', 'waiting'));
  else if (l.review_state === 'approved') out.push(chip('approved', 'ok'));
  if (comments) out.push(chip(`${comments} comment${comments === 1 ? '' : 's'}`, 'waiting', 'Comments still to address'));
  return out.join('');
}
function taskCodeLineHTML(l, mover, siblings = []) {
  // A live worktree is cleaned up when its task closes (its computer saves and removes it); only a removed one, or a
  // pull request, can be taken off the task here.
  const wtRepo = l.kind === 'worktree' ? taskWorktreeRepo(l, siblings) : '';
  const what = l.kind === 'pr' ? (taskPRNumber(l) ? `#${taskPRNumber(l)}` : 'pull request') : [wtRepo || 'unknown repo', l.branch].filter(Boolean).join(' · ');
  const x = mover && l.id && (l.kind === 'pr' || l.state === 'removed')
    ? `<button type="button" class="code-x" data-code-drop="${esc(l.id)}" aria-label="Remove ${esc(what)}" title="Remove">✕</button>` : '';
  if (l.kind === 'worktree') {
    const repo = taskLinkRepo(l), ahead = taskLinkNum(l, 'ahead'), behind = taskLinkNum(l, 'behind'), files = taskLinkNum(l, 'dirty_files');
    const error = String(taskLinkDetail(l).error || '');
    const url = taskWorktreeURL(l);
    const bits = [wtRepo ? `<span class="code-repo">${esc(wtRepo)}</span>` : '<span class="code-repo unknown">unknown repo</span>'];
    if (l.branch) bits.push(`<span class="code-branch mono">${esc(l.branch)}</span>`);
    const sync = [ahead ? `↑${ahead}` : '', behind ? `↓${behind}` : ''].filter(Boolean).join(' ');
    if (sync) bits.push(`<span class="tnum">${sync}</span>`);
    if (files) bits.push(`<span class="tnum">${files} file${files === 1 ? '' : 's'}</span>`);
    const odd = ['missing', 'removed', 'unknown', 'pending'].includes(l.state) || error
      ? ` <span class="code-state${error ? ' err' : ''}">${esc(['missing', 'removed', 'unknown', 'pending'].includes(l.state) ? l.state : 'error')}</span>` : '';
    const title = [repo, l.branch, l.path].filter(Boolean).join(' · ');
    const explanation = error ? `<div class="props-msg code-error">${esc(error)}</div>` : '';
    const inner = bits.join('<span class="code-dot">·</span>');
    return `<li class="code-line wt${l.state === 'removed' ? ' gone' : ''}" data-code-link="${esc(l.id || '')}">${url
      ? `<a class="code-main" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(title)}">${inner}</a>` : `<span class="code-main" title="${esc(title)}">${inner}</span>`}${odd}${x}${explanation}</li>`;
  }
  const num = taskPRNumber(l), title = l.title && !/^[\w.-]+#\d+$/.test(l.title) ? l.title : '';
  return `<li class="code-line pr" data-code-link="${esc(l.id || '')}"><a class="code-main" href="${esc(l.url || '#')}" target="_blank" rel="noopener" title="${esc([taskLinkRepo(l), l.title || l.url].filter(Boolean).join(' · '))}">${num ? `<span class="code-num tnum">#${esc(num)}</span>` : ''}<span class="code-title">${esc(title || repoShort(taskLinkRepo(l)) || l.url || 'Pull request')}</span></a><span class="code-chips">${taskPRChips(l)}</span>${x}</li>`;
}
const taskCodeLinks = t => (t.links || []).filter(l => l.kind === 'pr' || l.kind === 'worktree');
function taskCodeHTML(t) {
  const links = taskCodeLinks(t);
  if (!links.length) return '';
  const mover = canMove();
  // Worktrees first, then pull requests; each in the order they were added.
  const sorted = [...links.filter(l => l.kind === 'worktree'), ...links.filter(l => l.kind === 'pr')];
  return `<section class="rail-sec task-code" aria-labelledby="task-code-h"><h3 class="rail-h" id="task-code-h">Code</h3>
    <ul class="code-list">${sorted.map(l => taskCodeLineHTML(l, mover, links)).join('')}</ul></section>`;
}

// ---- Subtasks: the children (one level shown, each expandable), with the roll-up line
// GET /v2/tasks/{id}/tree answers the nested children; an older server has only the detail's flat children.
function taskTreeKids(res, id) {
  let list = Array.isArray(res) ? res : res && (res.tree ?? res.children ?? (res.id ? [res] : []));
  if (!Array.isArray(list)) list = list ? [list] : [];
  if (list.length === 1 && String(list[0]?.id) === String(id)) return list[0].children || [];
  return list;
}
function taskSubCounts(t, kids) {
  const s = t.children_summary;
  if (s && s.total) return {done: Number(s.done) || 0, total: Number(s.total), prs: s.prs_total ? Number(s.prs_merged) || 0 : null};
  if (!kids.length) return null;
  return {done: kids.filter(k => ['done', 'closed', 'declined'].includes(String(k.status))).length, total: kids.length, prs: null};
}
function taskSubSummary(t, kids) {
  const c = taskSubCounts(t, kids);
  if (!c) return '';
  return `${c.done} of ${c.total} done${c.prs != null ? ` · ${c.prs} PR${c.prs === 1 ? '' : 's'} merged` : ''}`;
}
// One line per child: the same status words as the list, the title, its PR badge, then the owner's face.
function taskSubRowHTML(k, open) {
  const kids = k.children || [];
  const who = k.owner ? actorFace(k.owner, 16) : '';
  const key = String(k.id);
  return `<li class="sub-row${['done', 'closed', 'declined'].includes(String(k.status)) ? ' finished' : ''}" data-sub="${esc(key)}">
    <div class="sub-line">${kids.length ? `<button type="button" class="sub-chev${open.has(key) ? ' open' : ''}" data-sub-toggle="${esc(key)}" aria-expanded="${open.has(key)}" aria-label="${open.has(key) ? 'Hide' : 'Show'} subtasks of ${esc(k.title || '')}">›</button>` : '<span class="sub-chev-pad"></span>'}
      ${taskStatusText(k)}<button type="button" class="sub-open" data-sub-open="${esc(key)}" title="${esc(`${k.title || ''} · ${taskStatusLabel(k)} · ${actorLabel(k.owner)}`)}"><span class="sub-title">${esc(k.title || 'Untitled')}</span></button>
      ${prStateBadge(k.pr_state)}<span class="sub-who" title="${esc(actorLabel(k.owner))}">${who}</span></div>
    ${kids.length && open.has(key) ? `<ul class="sub-list">${kids.map(c => taskSubRowHTML(c, open)).join('')}</ul>` : ''}</li>`;
}
const taskSubAdder = t => canMove() && !['done', 'closed'].includes(String(t.status || ''));
function taskSubtasksHTML(t, kids, open, adding, owner) {
  if (!kids.length && !adding) return '';
  const summary = taskSubSummary(t, kids), counts = taskSubCounts(t, kids);
  // The heading counts the children shown here; the line under it rolls up the whole tree, with a thin bar.
  const count = t.children_summary?.direct_total ?? kids.length;
  const pick = owner || actorSlug(t.owner) || t.owner || '';
  const actor = pick.includes(':') ? pick : 'bot:' + pick;
  return `<section class="rail-sec task-subs" aria-labelledby="task-subs-h"><h3 class="rail-h" id="task-subs-h">Subtasks${count ? ` <span class="cnt">${count}</span>` : ''}</h3>
    ${summary ? `<div class="sub-sum tnum"><span>${esc(summary)}</span><span class="sub-bar" aria-hidden="true"><i style="width:${Math.round(100 * counts.done / counts.total)}%"></i></span></div>` : ''}
    ${kids.length ? `<ul class="sub-list">${kids.map(k => taskSubRowHTML(k, open)).join('')}</ul>` : ''}
    ${taskSubAdder(t) ? `<form class="sub-add" data-sub-add><input name="title" type="text" maxlength="300" autocomplete="off" placeholder="Add subtask" aria-label="Add subtask">
      <input type="hidden" name="owner" value="${esc(pick)}"><button type="button" class="sub-owner" data-sub-owner aria-haspopup="menu" aria-label="For ${esc(actorLabel(actor))}. Change" title="For ${esc(actorLabel(actor))}">${actorFace(actor, 18)}</button></form>` : ''}</section>`;
}

// The rail on the open task: drawn from what the modal knows, then again once the tree is in.
function taskRailPaint(d, t) {
  const rail = $('[data-task-rail]', d); if (!rail) return;
  const st = d.taskRail ||= {open: new Set()};
  const kids = st.kids || [];
  const old = $('[data-sub-add]', rail);
  if (old) st.owner = old.elements.owner.value;
  const typed = old?.elements.title.value || '', focused = !!old && old.contains(document.activeElement) ? document.activeElement.name : '';
  // Code is drawn from the full task only: list rows carry no worktree details (ahead, behind, files, errors).
  const code = st.full && String(st.full.id) === String(t.id) ? taskCodeHTML(st.full) : '';
  const html = code + taskSubtasksHTML(t, kids, st.open, st.adding, st.owner);
  rail.innerHTML = html;
  rail.hidden = !html;
  d.classList.toggle('has-rail', !!html);
  const form = $('[data-sub-add]', rail);
  if (form && typed) form.elements.title.value = typed;
  // st.focus holds through the modal's redraws after adding one, until the tree is in (taskRailLoad).
  if (form && (focused || st.focus)) form.elements[focused || 'title'].focus();
}
async function taskRailLoad(d, t, detail) {
  const id = String(t.id);
  const st = d.taskRail = d.taskRail?.id === id ? d.taskRail : {id, open: new Set()};
  const seq = st.seq = (st.seq || 0) + 1;            // two loads in a row: only the latest answer is drawn
  const flat = (detail?.children || []).map(k => ({...k, children: k.children || []}));
  if (!st.kids) st.kids = flat;
  taskRailPaint(d, t);
  const has = t.children_summary?.total || flat.length || t.parts?.total;
  if (!has) { st.focus = false; return; }
  let res = null;
  try { res = await get(`/v2/tasks/${encodeURIComponent(id)}/tree`); } catch { res = null; }   // an older server: the flat list
  if (!d.open || d.dataset.task !== id || d.taskRail !== st || st.seq !== seq) return;
  st.kids = res ? taskTreeKids(res, id) : flat;
  taskRailPaint(d, t);
  st.focus = false;
}
function taskRailFind(list, id) {
  for (const k of list || []) {
    if (String(k.id) === id) return k;
    const found = taskRailFind(k.children, id); if (found) return found;
  }
  return null;
}
function taskRailBind(d, t) {
  const rail = $('[data-task-rail]', d); if (!rail) return;
  rail.onclick = async ev => {
    const who = ev.target.closest('[data-sub-owner]');
    if (who) {
      const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll('option[value]:not([value=""])');
      const st = d.taskRail, current = $('[data-sub-add]', rail)?.elements.owner.value;
      propMenu(d, who, {label: 'For', find: 'Find a person or bot', items: [...opts].map(o => {
        const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
        return {value: o.value, text: o.textContent, html: `<span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent)}</span>`, checked: o.value === current};
      }), onPick: it => {
        const form = $('[data-sub-add]', rail); if (form) form.elements.owner.value = it.value;
        st.owner = it.value; taskRailPaint(d, t); $('[data-sub-owner]', rail)?.focus();
      }});
      return;
    }
    const toggle = ev.target.closest('[data-sub-toggle]');
    if (toggle) {
      const st = d.taskRail, key = toggle.dataset.subToggle;
      st.open.has(key) ? st.open.delete(key) : st.open.add(key);
      taskRailPaint(d, t);
      $(`[data-sub-toggle="${CSS.escape(key)}"]`, rail)?.focus();
      return;
    }
    const opener = ev.target.closest('[data-sub-open]');
    if (opener) {
      const k = taskRailFind(d.taskRail?.kids, opener.dataset.subOpen);
      if (k) void taskModalShow({...k, links: k.links || [], labels: k.labels || []}, d);
      return;
    }
    const drop = ev.target.closest('[data-code-drop]');
    if (drop) {
      drop.disabled = true;
      const base = `/v2/tasks/${encodeURIComponent(t.id)}/links`;
      try {
        try { await writeRequest('DELETE', `${base}/${encodeURIComponent(drop.dataset.codeDrop)}`); }
        catch (e) { if (![404, 405].includes(e.status)) throw e; await post(base, {remove: drop.dataset.codeDrop}); }   // an older server
        const data = await get(`/v2/tasks/${encodeURIComponent(t.id)}`);
        if (TASKS_ST) void tasksLoad(TASKS_ST);
        if (d.open && d.dataset.task === String(t.id)) { if (d.taskRail) d.taskRail.full = data.task; taskModalShow(data.task, d); }
      } catch (e) { toast(e.message, true); drop.disabled = false; }
    }
  };
  rail.onsubmit = async ev => {
    const form = ev.target.closest('[data-sub-add]'); if (!form) return;
    ev.preventDefault();
    const input = form.elements.title, title = input.value.trim(), owner = form.elements.owner.value;
    if (!title || form.dataset.busy) return;
    if (!owner) { form.elements.owner.focus(); return; }
    form.dataset.busy = '1'; input.disabled = true;
    d.taskRail.owner = owner;
    try {
      // The details are required; the title stands in until someone writes more (the modal does not repeat it).
      await post('/v2/tasks', {title, body: title, owner, relations: [{task: t.id, kind: 'parent'}]});
      input.value = '';
      const data = await get(`/v2/tasks/${encodeURIComponent(t.id)}`);
      if (TASKS_ST) void tasksLoad(TASKS_ST);
      if (d.open && d.dataset.task === String(t.id)) {
        d.taskRail.focus = true;
        taskModalShow(data.task, d);
      }
    } catch (e) { toast(e.message, true); }
    finally { delete form.dataset.busy; input.disabled = false; }
  };
}
// "Add subtask" in the task's controls: the rail's field, shown even before the first child.
function taskRailAdd(d, t) {
  const st = d.taskRail ||= {id: String(t.id), open: new Set()};
  st.adding = true; st.focus = true;
  taskRailPaint(d, t);
  st.focus = false;
}
