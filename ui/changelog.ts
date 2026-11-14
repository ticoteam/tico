'use strict';

// The changelog's answers as the server sends them (ui/types/api.d.ts).
type ClEntry = import('./types/api').ChangelogEntry;
type ClList = import('./types/api').ChangelogList;
type ClRead = import('./types/api').ChangelogReadResult;

// The top of What's new: the Beta features, from the server's one list (backend/beta.py), each with its pages in the
// manual, and the known issues in the running version, as Tico HQ lists them with the update check (backend/releases.py).
function changelogBetaHTML() {
  const rows = S.config.beta || [];
  if (!rows.length) return '';
  const page = (doc: string) => `<a href="${GH}/blob/main/docs/${esc(doc)}" target="_blank" rel="noopener noreferrer">${esc(doc.replace(/\.md(#.*)?$/, ''))}</a>`;
  return `<details class="changelog-top changelog-beta" id="changelog-beta">
      <summary><strong>Beta features</strong></summary>
      <p>These work today and may still change. A support request's diagnostics say which of them your team has on.</p>
      <ul>${rows.map(f => `<li>${esc(f.name)}${betaPill(f.id)} <span class="muted">${(f.docs || []).map(page).join(' · ')}</span></li>`).join('')}</ul>
    </details>`;
}
function changelogKnownIssuesHTML() {
  const u: ConfigUpdate = S.config.update || {}, v = S.config.version ? ' ' + nvVersion(S.config.version) : '';
  const issue = (i: ConfigKnownIssue) => `<li>${/^https:\/\//.test(i.url || '') ? `<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>` : esc(i.title)}${i.fixed_in ? ` <span class="changelog-ki-fixed">Fixed in ${esc(nvVersion(i.fixed_in))}</span>` : ''}</li>`;
  const issues = u.known_issues || [];
  const body = !u.known_issues_checked ? '<p class="changelog-ki-none">Could not check: this server\'s update check goes to GitHub, not Tico HQ.</p>'
    : issues.length ? `<ul>${issues.map(issue).join('')}</ul>` : `<p class="changelog-ki-none">None known in${esc(v)}.</p>`;
  return `<section class="changelog-top changelog-known-issues" id="changelog-known-issues" aria-labelledby="changelog-ki-h"><h2 id="changelog-ki-h">Known issues in your version</h2>${body}</section>`;
}

// The What's new page. This file is a page part (ui/parts.json "changelog"); the sidebar's notice is in ui/app/notices.ts.
async function pageChangelog() {
  const root = document.querySelector<HTMLElement>('#main');
  if (!root) return;
  root.innerHTML = `<div class="release-page"><header class="release-header"><div><h1>Changelog</h1><p class="muted release-summary" role="status"></p></div></header>${changelogBetaHTML()}${changelogKnownIssuesHTML()}<div class="release-filters"><input type="search" aria-label="Search changes" autocomplete="off" placeholder="Search changes…"><select aria-label="Filter changes"><option value="unread">Since last look</option><option value="product">All product updates</option><option value="">All activity</option></select><button class="primary add-ctl" type="button" aria-label="Add product update" title="Add product update" hidden data-add-update>${TL_ICON.plus}</button></div><div class="release-list" aria-live="polite">Loading changes…</div><button class="ghost" type="button" data-more-changes hidden>Load older changes</button></div>`;
  const page = root.firstElementChild;
  if (!(page instanceof HTMLElement)) return;
  const search = page.querySelector<HTMLInputElement>('input'), filter = page.querySelector<HTMLSelectElement>('select'), list = page.querySelector<HTMLElement>('.release-list');
  const summary = page.querySelector<HTMLElement>('.release-summary'), add = page.querySelector<HTMLButtonElement>('[data-add-update]');
  const more = page.querySelector<HTMLButtonElement>('[data-more-changes]');
  if (!search || !filter || !list || !summary || !add || !more) return;
  let data: ClList, shown: ClEntry[] = [], generation = 0;
  // Entries this visit has marked read stay listed and labelled New until the person leaves, so nothing
  // vanishes mid-read; a later visit starts from the server's read state.
  const visit = new Map<string, ClEntry>(), saving = new Set<string>();
  const isNew = (r: ClEntry) => r.unread || visit.has(r.id);
  filter.value = Number(S.config.changelog?.unread_count) ? 'unread' : 'product';
  const query = () => {
    const params = new URLSearchParams({kind: filter.value ? 'product' : 'all'});
    if (filter.value === 'unread') params.set('unread', 'true');
    if (search.value.trim()) params.set('q', search.value.trim());
    return params;
  };
  const notice = (count: number) => {
    S.config.changelog = {unread_count: count};
    renderChangelogNotice();
  };
  const bullet = (b: string) => esc(typeof plainMd === 'function' ? plainMd(b) : b);
  const renderSummary = () => {
    summary.textContent = data.unread_count ? `${data.unread_count} product ${data.unread_count === 1 ? 'update' : 'updates'} you haven't read.` : 'You’re caught up on product updates.';
  };
  // A release with its GitHub publish time shows that day and time in the viewer's own zone. Without one (no time
  // known, or not a release) it shows the day of shipped_at in UTC, as before, and no time.
  const releaseWhen = (r: ClEntry) => {
    const published = r.published_at ? new Date(r.published_at) : null;
    if (published && !Number.isNaN(published.getTime())) return {
      day: published.toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'}),
      time: new Intl.DateTimeFormat(undefined, {hour:'numeric', minute:'2-digit', timeZoneName:'short'}).format(published),
    };
    const date = r.shipped_at ? new Date(r.shipped_at) : null;
    return {day: date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'}) : '', time: ''};
  };
  const render = () => {
    if (!page.isConnected) return;
    const q = search.value.toLowerCase().trim(), words = q.split(/\s+/);
    const known = new Set(data.entries.map(r => r.id));
    const pool = filter.value === 'unread'
      ? data.entries.concat([...visit.values()].filter(r => !known.has(r.id))).sort((a, b) => (b.shipped_at || '').localeCompare(a.shipped_at || ''))
      : data.entries;
    shown = pool.filter(r => (!filter.value || (r.kind === 'product' && (filter.value !== 'unread' || isNew(r))))
      && (!q || words.every(word => (r.title + ' ' + r.bullets.join(' ')).toLowerCase().includes(word))));
    renderSummary();
    add.hidden = !data.can_add;
    more.hidden = data.next_offset == null;
    list.innerHTML = shown.map(r => {
      const when = releaseWhen(r);
      const safeUrl = /^https:\/\//.test(r.url || '') ? r.url : '';
      return `<article class="release-entry${isNew(r) ? ' release-unread' : ''}"><div class="release-date"><time datetime="${esc(r.published_at || r.shipped_at || '')}">${esc(when.day)}</time>${when.time ? `<span class="release-time">${esc(when.time)}</span>` : ''}<span class="release-dot" aria-hidden="true"></span></div><div class="release-card"><span class="release-area">${r.kind === 'activity' ? 'Bot activity' : r.source === 'release' ? 'Release' : 'Product'}</span>${isNew(r) ? '<span class="release-new">New</span>' : ''}<h2>${esc(r.title)}</h2><ul>${r.bullets.slice(0, 4).map(b => `<li>${bullet(b)}</li>`).join('')}</ul>${r.bullets.length > 4 ? `<details class="release-more"><summary>${r.bullets.length - 4} more ${r.bullets.length === 5 ? 'change' : 'changes'}</summary><ul>${r.bullets.slice(4).map(b => `<li>${bullet(b)}</li>`).join('')}</ul></details>` : ''}${safeUrl ? `<a class="release-source" href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">Full release notes ↗</a>` : ''}${r.task_id ? `<button class="linkish release-source" data-activity-task="${esc(r.task_id)}">View outcome</button>` : ''}</div></article>`;
    }).join('') || `<div class="empty">${filter.value === 'unread' && !q ? 'You’re all caught up. Choose All product updates to browse earlier changes.' : 'No changes match this view.'}</div>`;
  };
  const load = async (initial = false) => {
    const request = ++generation;
    try {
      const result = await get<ClList>('/v2/changelog?' + query());
      if (!page.isConnected || request !== generation) return;
      data = result;
      data.unread_count = Number(data.unread_count) || 0;
      if (initial) filter.value = data.unread_count ? 'unread' : 'product';
      notice(data.unread_count);
      render();
      void markShown();
    } catch (e) {
      if (page.isConnected && request === generation) {
        shown = []; more.hidden = true;
        list.innerHTML = `<p class="err">${esc(errorMessage(e, ''))}</p><button class="ghost" type="button" data-retry-changes>Try again</button>`;
        const retry = list.querySelector<HTMLButtonElement>('button');
        if (retry) retry.onclick = () => load(initial);
      }
    }
  };
  let typing = 0;     // the server's search runs once typing pauses, not per keystroke
  search.oninput = () => { if (data) render(); clearTimeout(typing); typing = setTimeout(() => void load(), 200); };
  filter.onchange = () => { if (data) render(); void load(); };
  // Opening the page is reading it: the product entries it shows are acknowledged without a click.
  let saved = Promise.resolve();
  const markShown = () => {
    const items = shown.filter(r => r.kind === 'product' && r.unread && !saving.has(r.id));
    if (!items.length) return saved;
    // Remembered before the write: the server may commit it while the reply is slow or lost.
    items.forEach(r => { saving.add(r.id); visit.set(r.id, r); });
    saved = saved.then(async () => {
      try {
        for (let i = 0; i < items.length; i += 250) {
          const chunk = items.slice(i, i + 250), ids = new Set(chunk.map(r => r.id));
          const result: ClRead = await post('/v2/changelog/read', {ids: [...ids]});
          [...chunk, ...data.entries].forEach(r => { if (ids.has(r.id)) r.unread = false; });
          data.unread_count = result.unread_count;
          notice(result.unread_count);
        }
      } catch { /* post() has already retried; the badge stays and the next load tries again. */ }
      // The cards look the same either way, so only the summary changes; a rebuild would close what is being read.
      finally { items.forEach(r => saving.delete(r.id)); if (page.isConnected) renderSummary(); }
    });
    return saved;
  };
  list.onclick = async e => {
    if (!(e.target instanceof Element)) return;
    const button = e.target.closest<HTMLElement>('[data-activity-task]');
    if (!button) return;
    try {
      const result = await get('/v2/tasks/' + encodeURIComponent(String(button.dataset.activityTask)));
      if (!page.isConnected) return;
      const dialog = document.createElement('dialog'); dialog.className = 'tmodal docs-proposals';
      dialog.innerHTML = `<div class="tmodal-head"><h2>Reported task outcome</h2><span class="spacer"></span><button aria-label="Close">✕</button></div><div class="docs-proposals-body"><h3>${esc(result.task.title)}</h3>${safeMd(result.task.note || 'No outcome recorded.')}</div>`;
      document.body.append(dialog); dialog.showModal();
      const close = dialog.querySelector<HTMLButtonElement>('button');
      if (close) close.onclick = () => dialog.close();
      dialog.onclose = () => dialog.remove();
    } catch (error) { toast(errorMessage(error, ''), true); }
  };
  more.onclick = async () => {
    more.disabled = true;
    await saved;
    const request = generation, params = query();
    // Acknowledging shrinks the server's unread list, so the next unread page starts after the ones still unread.
    params.set('offset', String(filter.value === 'unread' ? data.entries.filter(r => r.unread).length : data.next_offset));
    try {
      const result = await get<ClList>('/v2/changelog?' + params);
      if (!page.isConnected || request !== generation) return;
      const known = new Set(data.entries.map(r => r.id));
      data.entries.push(...result.entries.filter(r => !known.has(r.id)));
      data.next_offset = result.next_offset;
      data.unread_count = result.unread_count;
      notice(result.unread_count);
      render();
      void markShown();
    } catch (e) { toast(errorMessage(e, ''), true); }
    finally { more.disabled = false; }
  };
  add.onclick = () => {
    const dialog = document.createElement('dialog'); dialog.className = 'tmodal docs-proposals';
    dialog.innerHTML = '<div class="tmodal-head"><h2>Product update</h2><span class="spacer"></span><button aria-label="Close">✕</button></div><div class="docs-proposals-body"><p class="muted">Share a team update.</p><label>Title<input class="draft-title" style="width:100%" maxlength="90"></label><label>Bullets — one per line<textarea class="draft-bullets" style="width:100%" rows="4"></textarea></label><p class="row"><button class="primary" data-publish>Publish</button></p><p class="draft-status" role="status"></p></div>';
    document.body.append(dialog); dialog.showModal();
    const close = dialog.querySelector<HTMLButtonElement>('[aria-label="Close"]'), publish = dialog.querySelector<HTMLButtonElement>('[data-publish]');
    const titleField = dialog.querySelector<HTMLInputElement>('.draft-title'), bulletsField = dialog.querySelector<HTMLTextAreaElement>('.draft-bullets');
    const status = dialog.querySelector<HTMLElement>('.draft-status');
    if (!close || !publish || !titleField || !bulletsField || !status) return;
    close.onclick = () => dialog.close(); dialog.onclose = () => dialog.remove();
    publish.onclick = async () => {
      const title = titleField.value.trim();
      const bullets = bulletsField.value.split('\n').map(b => b.trim()).filter(Boolean);
      publish.disabled = true;
      try { await post('/changelog', {title, bullets}); dialog.close(); await load(); }
      catch (e) { status.textContent = errorMessage(e, ''); }
      finally { publish.disabled = false; }
    };
  };
  await load(true);
}
