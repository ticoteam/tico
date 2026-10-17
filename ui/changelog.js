'use strict';

window.renderChangelogNotice = function () {
  const count = Number(S.config.changelog?.unread_count) || 0;
  const notice = document.querySelector('#changelog-notice');
  const badge = document.querySelector('#changelog-badge');
  if (notice) {
    notice.hidden = !count;
    notice.querySelector('.changelog-notice-label').textContent = `What's new · ${count} ${count === 1 ? 'update' : 'updates'}`;
  }
  if (badge) { badge.hidden = !count; badge.textContent = count; }
};

// The top of What's new: the Beta features, from the server's one list (backend/beta.py), each with its pages in the
// manual, and the known issues in the running version, as Tico HQ lists them with the update check (backend/releases.py).
function changelogBetaHTML() {
  const rows = S.config.beta || [];
  if (!rows.length) return '';
  const page = doc => `<a href="${GH}/blob/main/docs/${esc(doc)}" target="_blank" rel="noopener noreferrer">${esc(doc.replace(/\.md(#.*)?$/, ''))}</a>`;
  return `<details class="changelog-top changelog-beta" id="changelog-beta">
      <summary><strong>Beta features</strong></summary>
      <p>These work today and may still change. A support request's diagnostics say which of them your team has on.</p>
      <ul>${rows.map(f => `<li>${esc(f.name)}${betaPill(f.id)} <span class="muted">${(f.docs || []).map(page).join(' · ')}</span></li>`).join('')}</ul>
    </details>`;
}
function changelogKnownIssuesHTML() {
  const u = S.config.update || {}, v = S.config.version ? ' ' + nvVersion(S.config.version) : '';
  const issue = i => `<li>${/^https:\/\//.test(i.url || '') ? `<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>` : esc(i.title)}${i.fixed_in ? ` <span class="changelog-ki-fixed">Fixed in ${esc(nvVersion(i.fixed_in))}</span>` : ''}</li>`;
  const body = !u.known_issues_checked ? '<p class="changelog-ki-none">Could not check: this server\'s update check goes to GitHub, not Tico HQ.</p>'
    : (u.known_issues || []).length ? `<ul>${u.known_issues.map(issue).join('')}</ul>` : `<p class="changelog-ki-none">None known in${esc(v)}.</p>`;
  return `<section class="changelog-top changelog-known-issues" id="changelog-known-issues" aria-labelledby="changelog-ki-h"><h2 id="changelog-ki-h">Known issues in your version</h2>${body}</section>`;
}

window.pageChangelog = async function () {
  const root = document.querySelector('#main');
  root.innerHTML = `<div class="release-page"><header class="release-header"><div><h1>Changelog</h1><p class="muted release-summary" role="status"></p></div></header>${changelogBetaHTML()}${changelogKnownIssuesHTML()}<div class="release-filters"><input type="search" aria-label="Search changes" autocomplete="off" placeholder="Search changes…"><select aria-label="Filter changes"><option value="unread">Since last look</option><option value="product">All product updates</option><option value="">All activity</option></select><button class="primary round-add" type="button" aria-label="Add product update" title="Add product update" hidden data-add-update>+</button></div><div class="release-list" aria-live="polite">Loading changes…</div><button class="ghost" type="button" data-more-changes hidden>Load older changes</button></div>`;
  const page = root.firstElementChild;
  const search = page.querySelector('input'), filter = page.querySelector('select'), list = page.querySelector('.release-list');
  const summary = page.querySelector('.release-summary'), add = page.querySelector('[data-add-update]');
  const more = page.querySelector('[data-more-changes]');
  let data, shown = [], generation = 0;
  // Entries this visit has marked read stay listed and labelled New until the person leaves, so nothing
  // vanishes mid-read; a later visit starts from the server's read state.
  const visit = new Map(), saving = new Set();
  const isNew = r => r.unread || visit.has(r.id);
  filter.value = Number(S.config.changelog?.unread_count) ? 'unread' : 'product';
  const query = () => {
    const params = new URLSearchParams({kind: filter.value ? 'product' : 'all'});
    if (filter.value === 'unread') params.set('unread', 'true');
    if (search.value.trim()) params.set('q', search.value.trim());
    return params;
  };
  const notice = count => {
    S.config.changelog = {unread_count: count};
    window.renderChangelogNotice();
  };
  const bullet = b => esc(typeof plainMd === 'function' ? plainMd(b) : b);
  const render = () => {
    if (!page.isConnected) return;
    const q = search.value.toLowerCase().trim(), words = q.split(/\s+/);
    const known = new Set(data.entries.map(r => r.id));
    const pool = filter.value === 'unread'
      ? data.entries.concat([...visit.values()].filter(r => !known.has(r.id))).sort((a, b) => (b.shipped_at || '').localeCompare(a.shipped_at || ''))
      : data.entries;
    shown = pool.filter(r => (!filter.value || (r.kind === 'product' && (filter.value !== 'unread' || isNew(r))))
      && (!q || words.every(word => (r.title + ' ' + r.bullets.join(' ')).toLowerCase().includes(word))));
    summary.textContent = data.unread_count ? `${data.unread_count} product ${data.unread_count === 1 ? 'update' : 'updates'} you haven't read.` : 'You’re caught up on product updates.';
    add.hidden = !data.can_add;
    more.hidden = data.next_offset == null;
    list.innerHTML = shown.map(r => {
      const date = r.shipped_at ? new Date(r.shipped_at) : null;
      const safeUrl = /^https:\/\//.test(r.url || '') ? r.url : '';
      return `<article class="release-entry${isNew(r) ? ' release-unread' : ''}"><div class="release-date"><time datetime="${esc(r.shipped_at || '')}">${date && !isNaN(date) ? esc(date.toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'})) : ''}</time><span class="release-dot" aria-hidden="true"></span></div><div class="release-card"><span class="release-area">${r.kind === 'activity' ? 'Bot activity' : r.source === 'release' ? 'Release' : 'Product'}</span>${isNew(r) ? '<span class="release-new">New</span>' : ''}<h2>${esc(r.title)}</h2><ul>${r.bullets.slice(0, 4).map(b => `<li>${bullet(b)}</li>`).join('')}</ul>${r.bullets.length > 4 ? `<details class="release-more"><summary>${r.bullets.length - 4} more ${r.bullets.length === 5 ? 'change' : 'changes'}</summary><ul>${r.bullets.slice(4).map(b => `<li>${bullet(b)}</li>`).join('')}</ul></details>` : ''}${safeUrl ? `<a class="release-source" href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">Full release notes ↗</a>` : ''}${r.task_id ? `<button class="linkish release-source" data-activity-task="${esc(r.task_id)}">View outcome</button>` : ''}</div></article>`;
    }).join('') || `<div class="empty">${filter.value === 'unread' && !q ? 'You’re all caught up. Choose All product updates to browse earlier changes.' : 'No changes match this view.'}</div>`;
  };
  const load = async (initial = false) => {
    const request = ++generation;
    try {
      const result = await get('/v2/changelog?' + query());
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
        list.innerHTML = `<p class="err">${esc(e.message)}</p><button class="ghost" type="button" data-retry-changes>Try again</button>`;
        list.querySelector('button').onclick = () => load(initial);
      }
    }
  };
  search.oninput = () => { if (data) render(); void load(); };
  filter.onchange = () => { if (data) render(); void load(); };
  // Opening the page is reading it: the product entries it shows are acknowledged without a click.
  let saved = Promise.resolve();
  const markShown = () => {
    const items = shown.filter(r => r.kind === 'product' && r.unread && !saving.has(r.id));
    if (!items.length) return saved;
    items.forEach(r => saving.add(r.id));
    saved = saved.then(async () => {
      try {
        for (let i = 0; i < items.length; i += 250) {
          const chunk = items.slice(i, i + 250), ids = new Set(chunk.map(r => r.id));
          const result = await post('/v2/changelog/read', {ids: [...ids]});
          chunk.forEach(r => visit.set(r.id, r));
          [...chunk, ...data.entries].forEach(r => { if (ids.has(r.id)) r.unread = false; });
          data.unread_count = result.unread_count;
          notice(result.unread_count);
        }
      } catch { /* post() has already retried; the badge stays and the next load tries again. */ }
      finally { items.forEach(r => saving.delete(r.id)); render(); }
    });
    return saved;
  };
  list.onclick = async e => {
    const button = e.target.closest('[data-activity-task]');
    if (!button) return;
    try {
      const result = await get('/v2/tasks/' + encodeURIComponent(button.dataset.activityTask));
      if (!page.isConnected) return;
      const dialog = document.createElement('dialog'); dialog.className = 'tmodal docs-proposals';
      dialog.innerHTML = `<div class="tmodal-head"><h2>Reported task outcome</h2><span class="spacer"></span><button aria-label="Close">✕</button></div><div class="docs-proposals-body"><h3>${esc(result.task.title)}</h3>${safeMd(result.task.note || 'No outcome recorded.')}</div>`;
      document.body.append(dialog); dialog.showModal(); dialog.querySelector('button').onclick = () => dialog.close(); dialog.onclose = () => dialog.remove();
    } catch (error) { toast(error.message, true); }
  };
  more.onclick = async () => {
    more.disabled = true;
    await saved;
    const request = generation, params = query();
    // Acknowledging shrinks the server's unread list, so the next unread page starts after the ones still unread.
    params.set('offset', filter.value === 'unread' ? data.entries.filter(r => r.unread).length : data.next_offset);
    try {
      const result = await get('/v2/changelog?' + params);
      if (!page.isConnected || request !== generation) return;
      const known = new Set(data.entries.map(r => r.id));
      data.entries.push(...result.entries.filter(r => !known.has(r.id)));
      data.next_offset = result.next_offset;
      data.unread_count = result.unread_count;
      notice(result.unread_count);
      render();
      void markShown();
    } catch (e) { toast(e.message, true); }
    finally { more.disabled = false; }
  };
  add.onclick = () => {
    const dialog = document.createElement('dialog'); dialog.className = 'tmodal docs-proposals';
    dialog.innerHTML = '<div class="tmodal-head"><h2>Product update</h2><span class="spacer"></span><button aria-label="Close">✕</button></div><div class="docs-proposals-body"><p class="muted">Share a team update. Shipped Tico releases appear automatically.</p><label>Title<input class="draft-title" style="width:100%" maxlength="90"></label><label>Bullets — one per line<textarea class="draft-bullets" style="width:100%" rows="4"></textarea></label><p class="row"><button class="primary" data-publish>Publish</button></p><p class="draft-status" role="status"></p></div>';
    document.body.append(dialog); dialog.showModal();
    dialog.querySelector('[aria-label="Close"]').onclick = () => dialog.close(); dialog.onclose = () => dialog.remove();
    dialog.querySelector('[data-publish]').onclick = async () => {
      const title = dialog.querySelector('.draft-title').value.trim();
      const bullets = dialog.querySelector('.draft-bullets').value.split('\n').map(b => b.trim()).filter(Boolean);
      const status = dialog.querySelector('.draft-status'), publish = dialog.querySelector('[data-publish]');
      publish.disabled = true;
      try { await post('/changelog', {title, bullets}); dialog.close(); await load(); }
      catch (e) { status.textContent = e.message; }
      finally { publish.disabled = false; }
    };
  };
  await load(true);
};
