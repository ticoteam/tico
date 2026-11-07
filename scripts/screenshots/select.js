'use strict';
// Which screenshot pages a change to the UI touches. Pure: index.js reads the changed paths and the dark-theme lines
// from git, and the page table (PAGES, each entry's `ui` globs) from its own list. Every ui/ file is one of:
//   ignored   tests, baselines and the UI's README: they draw nothing
//   shared    the app shell, base styles, the router and the theme: any page can draw them, so every page is shot
//   page      named by one or more PAGES entries' `ui` globs: those pages are shot, and no others
//   unshown   a feature no PAGES entry shows (UNSHOWN): nothing is shot, and the reason lists the file
//   unmapped  none of the above, a file this table does not know yet: every page is shot, so nothing is missed
// Paths outside ui/ draw no page. A glob's `*` stays within one path segment; `**` crosses segments.

const NON_RENDERING = [
  'ui/tests/**', 'ui/README.md', 'ui/**/*.md', 'ui/*-baseline.json', 'ui/baselines/**', 'ui/types/**', 'ui/tsconfig.json', 'ui/tsconfig.*.json', 'ui/strict-legacy.json',
  'ui/package.json', 'ui/package-lock.json', 'ui/**/LICENSE*', 'ui/**/AUTHORS',
];

const SHARED = [
  'ui/index.html', 'ui/theme.js', 'ui/sw.js', 'ui/manifest.webmanifest', 'ui/observability*.js',
  'ui/styles/tokens.css', 'ui/styles/base.css', 'ui/styles/layout.css', 'ui/styles/components.css',
  'ui/styles/sidebar.css', 'ui/styles/forms.css', 'ui/styles/motion.css', 'ui/styles/skeleton.css',
  'ui/styles/avatars.css', 'ui/styles/tree-lines.css', 'ui/styles/liquid-dot.css', 'ui/styles/brand.css',
  'ui/styles/rails.css', 'ui/styles/search.css', 'ui/styles/viewer.css', 'ui/styles/ask.css',
  'ui/app/router.ts', 'ui/app/core.ts', 'ui/app/state.ts', 'ui/app/format.js', 'ui/app/api.ts', 'ui/app/errors.ts', 'ui/app/dom.ts',
  'ui/app/session.js', 'ui/app/notices.js', 'ui/app/sidebar.js', 'ui/app/drawer.ts', 'ui/app/viewport.js',
  'ui/app/refresh.ts', 'ui/app/boot.ts', 'ui/app/boot-snap.js', 'ui/app/boot-timing.js', 'ui/app/nav-events.js',
  'ui/app/markdown.js', 'ui/app/tooltip.js', 'ui/app/avatars.js', 'ui/app/liquid-dot.js', 'ui/app/heartbeat.ts',
  'ui/app/pill.js', 'ui/app/live.js', 'ui/app/motion.js', 'ui/app/rails.js', 'ui/app/stick-scroll.js',
  'ui/app/org-fan.js', 'ui/app/native.js', 'ui/app/ask-panel.js', 'ui/app/ask-rules.js', 'ui/assistant.js',
  'ui/app/search.js', 'ui/app/viewer.js', 'ui/tool-icons.js', 'ui/assets/**', 'ui/vendor/fonts/**',
  'ui/vendor/marked.min.js', 'ui/vendor/js-yaml.min.js', 'ui/vendor/livekit-client.umd.min.js',
];

// Files of features no PAGES entry shows. A change to one says so in the reason instead of shooting every page. A
// page's own `ui` globs win over this list: a file both name belongs to the page.
const UNSHOWN = [
  'ui/vendor/**', 'ui/app/settings-*.js', 'ui/app/vault.js', 'ui/app/catalog.js', 'ui/app/cloud-*.js', 'ui/app/team-add.js',
  'ui/app/integrations.js', 'ui/app/mail.js', 'ui/app/email-preview.js', 'ui/app/messaging.js', 'ui/app/sql.js',
  'ui/app/assistant-page.js', 'ui/app/skills*.js', 'ui/app/tags.js', 'ui/app/roadmaps.js', 'ui/app/company-page.js',
  'ui/app/computer-*.js', 'ui/app/dashboard.js', 'ui/app/overview*.js', 'ui/app/pages.js', 'ui/app/replay.js',
  'ui/app/person-page.js', 'ui/app/shared-edit.js', 'ui/overview/**', 'ui/replay-player.js', 'ui/first-run.js',
  'ui/changelog.js', 'ui/lenses-page.js', 'ui/org-builder.js', 'ui/connect-agent.js', 'ui/credential-card.js',
  'ui/directory-sync.js', 'ui/github-connect.js', 'ui/google-connect.js', 'ui/model-login.js',
  'ui/slack-channels.js', 'ui/slack-connect.js', 'ui/setup-strategy.js',
  'ui/styles/company-page.css', 'ui/styles/computer-*.css', 'ui/styles/dashboard.css', 'ui/styles/overview*.css',
  'ui/styles/pages.css', 'ui/styles/skills.css', 'ui/styles/mail.css', 'ui/styles/messaging.css',
  'ui/styles/replay.css', 'ui/styles/roadmaps.css', 'ui/styles/tags.css', 'ui/styles/first-run.css',
  'ui/styles/lenses.css', 'ui/styles/credentials.css', 'ui/styles/cloud-agents.css', 'ui/styles/team-add.css',
  // The Settings Inboxes tab is gone (its page was removed from the table); its files are a feature no page shows now.
  'ui/app/inboxes*.js', 'ui/app/tools*.js', 'ui/app/redesign-library.js', 'ui/styles/inboxes.css', 'ui/styles/inbound.css',
  'ui/styles/tools.css', 'ui/styles/redesign-library.css',
];

// A change to one of these can change the dark theme on every page, whatever its lines say.
const DARK_FILES = ['ui/styles/tokens.css', 'ui/theme.js'];

// A glob as a regular expression over a slash-separated path.
function globToRegExp(glob) {
  let out = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') { out += '.*'; i++; } else out += '[^/]*';
    } else if (c === '?') {
      out += '[^/]';
    } else {
      out += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    }
  }
  // A UI script is .js until it converts to TypeScript, so a glob ending in .js also matches the .ts of the same name:
  // a conversion changes no table here.
  if (out.endsWith('\\.js')) out = out.slice(0, -'\\.js'.length) + '\\.(?:js|ts)';
  return new RegExp('^' + out + '$');
}

const matchesAny = (globs, file) => globs.some(glob => globToRegExp(glob).test(file));

// The kind of one ui/ file, and for a page file the names of the pages that claim it.
function classify(file, pages) {
  if (matchesAny(NON_RENDERING, file)) return {kind: 'ignored', pages: []};
  if (matchesAny(SHARED, file)) return {kind: 'shared', pages: []};
  const claimed = pages.filter(page => matchesAny(page.ui, file)).map(page => page.name);
  if (claimed.length) return {kind: 'page', pages: claimed};
  if (matchesAny(UNSHOWN, file)) return {kind: 'unshown', pages: []};
  return {kind: 'unmapped', pages: []};
}

// paths: the changed paths of a diff. darkPaths: the changed paths with a changed line that mentions dark.
function selectPages(paths, pages, darkPaths = []) {
  const names = pages.map(page => page.name);
  const ui = paths.filter(path => path.startsWith('ui/'));
  const byKind = {shared: [], unmapped: [], page: [], unshown: [], ignored: []};
  const claimed = new Set();
  for (const file of ui) {
    const {kind, pages: named} = classify(file, pages);
    byKind[kind].push(file);
    for (const name of named) claimed.add(name);
  }
  const all = byKind.shared.length > 0 || byKind.unmapped.length > 0;
  const chosen = all ? names : names.filter(name => claimed.has(name));

  const parts = [];
  if (byKind.shared.length) parts.push(`shared ${byKind.shared.join(', ')}: every page`);
  if (byKind.unmapped.length) parts.push(`unmapped ${byKind.unmapped.join(', ')}: every page`);
  if (!all && claimed.size) parts.push(`pages ${chosen.join(', ')}`);
  if (byKind.unshown.length) parts.push(`not shown: ${byKind.unshown.join(', ')}`);
  let reason = parts.join('; ');
  if (!ui.length) reason = 'no ui/ change: no pages';
  else if (!reason) reason = 'only ui/ tests or baselines: no pages';

  const dark = ui.some(file => DARK_FILES.includes(file))
    || darkPaths.some(file => file.startsWith('ui/') && classify(file, pages).kind !== 'ignored');

  const byPage = {};
  for (const name of names) {
    byPage[name] = ui.filter(file => {
      const c = classify(file, pages);
      return c.kind === 'page' && c.pages.includes(name);
    });
  }
  return {pages: chosen, all, dark, reason, unshown: byKind.unshown, byPage};
}

module.exports = {NON_RENDERING, SHARED, UNSHOWN, DARK_FILES, globToRegExp, classify, selectPages};
