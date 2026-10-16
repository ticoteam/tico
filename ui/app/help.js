/* ui/app/help.js — Help page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Help is short on purpose: what Tico is, where to start and how to get help. The map, who does what, the platforms and
// the glossary live in the docs (docs/how-it-works.md, docs/glossary.md).
const HELP_DOCS = [['How Tico works', 'how-it-works.md'], ['Glossary', 'glossary.md'], ['Connect an agent', 'connect-an-agent.md'], ['All docs', 'README.md']];
const helpDoc = (words, file, cls = '') => `<a${cls ? ` class="${cls}"` : ''} href="${GH}/blob/main/docs/${file}" target="_blank" rel="noopener noreferrer">${esc(words)}</a>`;
// The Beta features, from the server's one list (backend/beta.py), each with its pages in the manual.
function helpBetaHTML() {
  const rows = S.config.beta || [];
  if (!rows.length) return '';
  const page = doc => `<a href="${GH}/blob/main/docs/${esc(doc)}" target="_blank" rel="noopener noreferrer">${esc(doc.replace(/\.md(#.*)?$/, ''))}</a>`;
  return `<details class="help-tools help-beta" id="help-beta">
      <summary><strong>Beta features</strong></summary>
      <p>These work today and may still change. A support request's diagnostics say which of them your team has on.</p>
      <ul>${rows.map(f => `<li>${esc(f.name)}${betaPill(f.id)} <span class="muted">${(f.docs || []).map(page).join(' · ')}</span></li>`).join('')}</ul>
    </details>`;
}

// The server's version, and inside the desktop app its own version (app/src/main.rs bridge_script) and a way to its
// log file (app/src/applog.rs), which a shell that keeps one says in ticoNativeFeatures.
function helpVersionHTML() {
  const parts = [S.config.version && `<span id="help-version">Version ${esc(nvVersion(S.config.version))}</span>`,
    window.ticoNativeVersion && `<span id="help-app-version">App ${esc(nvVersion(window.ticoNativeVersion))}</span>`,
    (window.ticoNativeFeatures || []).includes('showLog') && '<button class="help-log" type="button" id="help-app-log">Show the app\'s log</button>'];
  return parts.filter(Boolean).join(' · ');
}
// Known issues in the running version, as Tico HQ lists them with the update check (backend/releases.py).
function helpKnownIssuesHTML() {
  const u = S.config.update || {}, v = S.config.version ? ' ' + nvVersion(S.config.version) : '';
  const issue = i => `<li>${/^https:\/\//.test(i.url || '') ? `<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>` : esc(i.title)}${i.fixed_in ? ` <span class="help-ki-fixed">Fixed in ${esc(nvVersion(i.fixed_in))}</span>` : ''}</li>`;
  const body = !u.known_issues_checked ? '<p class="help-ki-none">Could not check: this server\'s update check goes to GitHub, not Tico HQ.</p>'
    : (u.known_issues || []).length ? `<ul>${u.known_issues.map(issue).join('')}</ul>` : `<p class="help-ki-none">None known in${esc(v)}.</p>`;
  return `<section class="help-known-issues" id="help-known-issues" aria-labelledby="help-ki-h"><h2 id="help-ki-h">Known issues in your version</h2>${body}</section>`;
}
function pageHelp() {
  $('#main').innerHTML = `<article class="help-page" id="help-page">
    <p class="help-docs-first">${helpDoc('Read the Tico docs', 'README.md', 'gs-link')}</p>
    <header class="help-hero">
      <h1>Your human and AI team</h1>
      <p class="help-lede">Tico brings your human and AI teammates together around shared goals, tasks and conversations. People set direction and review results; bots do the work with the tools you give them, on your computers.</p>
      <div class="help-actions">${helpDoc('Your first result', 'install.md#your-first-result', 'gs-link')}<button class="ghost" type="button" data-gs-tour>Take the tour</button></div>
    </header>
    <ul class="help-essentials">
      <li><strong>Start</strong> with your first result, or ask the Assistant (sidebar or ⌘K) what needs you today.</li>
      <li data-help-support><strong>Get help</strong> from a person on the Tico team: write to Support on this page.</li>
      <li><strong>Learn more</strong> in the docs: ${HELP_DOCS.map(([words, file]) => helpDoc(words, file)).join(' · ')}.</li>
    </ul>
    ${helpBetaHTML()}
    ${helpKnownIssuesHTML()}
    <p class="help-foot">${helpVersionHTML()}</p>
  </article>`;
  $('#help-app-log')?.addEventListener('click', () => nativeHandler('showLog')?.postMessage(null));
  window.supportHelp?.($('#help-page'));    // Contact support and Your requests (ui/support.js)
}
