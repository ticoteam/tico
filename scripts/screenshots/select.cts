'use strict';
// Which screenshot pages a change to the UI touches. Pure: index.cts reads the changed paths and the dark-theme lines
// from git, and the page table (PAGES, each entry's `ui` globs) from its own list. Every ui/ file is one of:
//   ignored   tests, baselines and the UI's README: they draw nothing
//   shared    the app shell, base styles, the router and the theme: any page can draw them, so every page is shot
//   page      named by one or more PAGES entries' `ui` globs: those pages are shot, and no others
//   unshown   a feature no PAGES entry shows (UNSHOWN): nothing is shot, and the reason lists the file
//   unmapped  none of the above, a file this table does not know yet: every page is shot, so nothing is missed
// Paths outside ui/ draw no page. A glob's `*` stays within one path segment; `**` crosses segments.

// One PAGES entry of index.cts: its name and the ui/ globs that draw it.
export interface Page {
  name: string;
  ui: string[];
}

export type FileKind = 'ignored' | 'shared' | 'page' | 'unshown' | 'unmapped';

export interface Classification {
  kind: FileKind;
  pages: string[];
}

export interface Selection {
  pages: string[];
  all: boolean;
  dark: boolean;
  reason: string;
  unshown: string[];
  byPage: Record<string, string[]>;
}

const NON_RENDERING: string[] = [
  'ui/tests/**', 'ui/README.md', 'ui/**/*.md', 'ui/*-baseline.json', 'ui/baselines/**', 'ui/types/**', 'ui/tsconfig.json', 'ui/tsconfig.*.json',
  'ui/package.json', 'ui/package-lock.json', 'ui/**/LICENSE*', 'ui/**/AUTHORS',
];

const SHARED: string[] = [
  'ui/index.html', 'ui/theme.ts', 'ui/sw.ts', 'ui/manifest.webmanifest', 'ui/observability*.ts',
  'ui/styles/tokens.css', 'ui/styles/base.css', 'ui/styles/layout.css', 'ui/styles/components.css',
  'ui/styles/sidebar.css', 'ui/styles/forms.css', 'ui/styles/motion.css', 'ui/styles/skeleton.css',
  'ui/styles/avatars.css', 'ui/styles/tree-lines.css', 'ui/styles/liquid-dot.css', 'ui/styles/brand.css',
  'ui/styles/rails.css', 'ui/styles/search.css', 'ui/styles/viewer.css', 'ui/styles/ask.css', 'ui/styles/page-head.css',
  'ui/app/router.ts', 'ui/app/core.ts', 'ui/app/state.ts', 'ui/app/format.ts', 'ui/app/api.ts', 'ui/app/errors.ts', 'ui/app/dom.ts', 'ui/app/page-head.ts', 'ui/app/confirm.ts',
  'ui/app/session.ts', 'ui/app/notices.ts', 'ui/app/sidebar.ts', 'ui/app/drawer.ts', 'ui/app/viewport.ts',
  'ui/app/refresh.ts', 'ui/app/boot.ts', 'ui/app/boot-snap.ts', 'ui/app/boot-timing.ts', 'ui/app/nav-events.ts',
  'ui/app/markdown.ts', 'ui/app/tooltip.ts', 'ui/app/avatars.ts', 'ui/app/liquid-dot.ts', 'ui/app/heartbeat.ts',
  'ui/app/pill.ts', 'ui/app/live.ts', 'ui/app/motion.ts', 'ui/app/rails.ts', 'ui/app/stick-scroll.ts',
  'ui/app/org-fan.ts', 'ui/app/native.ts', 'ui/app/ask-panel.ts', 'ui/app/ask-rules.ts', 'ui/assistant.ts',
  'ui/app/search.ts', 'ui/app/viewer.ts', 'ui/tool-icons.ts', 'ui/assets/**', 'ui/vendor/fonts/**',
  'ui/vendor/marked.min.js', 'ui/vendor/js-yaml.min.js', 'ui/vendor/livekit-client.umd.min.js',
];

// Files of features no PAGES entry shows. A change to one says so in the reason instead of shooting every page. A
// page's own `ui` globs win over this list: a file both name belongs to the page.
const UNSHOWN: string[] = [
  'ui/vendor/**', 'ui/app/file-review.ts', 'ui/styles/file-review.css', 'ui/app/settings-*.js', 'ui/app/settings-*.ts', 'ui/app/vault.ts', 'ui/app/catalog.ts', 'ui/app/cloud-*.ts', 'ui/app/team-add.ts',
  'ui/app/integrations.ts', 'ui/app/mail.ts', 'ui/app/email-preview.ts', 'ui/app/messaging.ts', 'ui/app/sql.ts',
  'ui/app/assistant-page.ts', 'ui/app/skills*.ts', 'ui/app/tags.ts', 'ui/app/roadmaps.ts', 'ui/app/company-page.ts',
  'ui/app/computer-*.ts', 'ui/app/dashboard.ts', 'ui/app/overview*.ts', 'ui/app/pages.ts', 'ui/app/replay.ts',
  'ui/app/person-page.ts', 'ui/app/shared-edit.ts', 'ui/overview/**', 'ui/replay-player.ts', 'ui/first-run.ts',
  'ui/changelog.ts', 'ui/lenses-page.ts', 'ui/org-builder.ts', 'ui/connect-agent.ts', 'ui/credential-card.ts',
  'ui/directory-sync.ts', 'ui/github-connect.ts', 'ui/google-connect.ts', 'ui/model-login.ts',
  'ui/slack-channels.ts', 'ui/slack-connect.ts', 'ui/setup-strategy.ts',
  'ui/styles/company-page.css', 'ui/styles/computer-*.css', 'ui/styles/dashboard.css', 'ui/styles/overview*.css',
  'ui/styles/pages.css', 'ui/styles/skills.css', 'ui/styles/mail.css', 'ui/styles/messaging.css',
  'ui/styles/replay.css', 'ui/styles/roadmaps.css', 'ui/styles/tags.css', 'ui/styles/first-run.css',
  'ui/styles/lenses.css', 'ui/styles/credentials.css', 'ui/styles/cloud-agents.css', 'ui/styles/team-add.css',
  // The Settings Inboxes tab is gone (its page was removed from the table); its files are a feature no page shows now.
  'ui/app/inboxes*.js', 'ui/app/inboxes*.ts', 'ui/app/tools*.ts', 'ui/app/secret-drop.ts', 'ui/app/who-picker.ts', 'ui/app/redesign-library.ts', 'ui/styles/inboxes.css', 'ui/styles/inbound.css',
  'ui/styles/tools.css', 'ui/styles/redesign-library.css',
  // A website tool's live browser view (Open browser on the tool's page, or a browser ask): no page shows a live browser.
  'ui/app/browser-view.ts', 'ui/styles/browser-view.css',
];

// A change to one of these can change the dark theme on every page, whatever its lines say.
const DARK_FILES: string[] = ['ui/styles/tokens.css', 'ui/theme.ts'];

// A glob as a regular expression over a slash-separated path.
function globToRegExp(glob: string): RegExp {
  let out = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i]!;
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

const matchesAny = (globs: string[], file: string): boolean => globs.some(glob => globToRegExp(glob).test(file));

// The kind of one ui/ file, and for a page file the names of the pages that claim it.
function classify(file: string, pages: Page[]): Classification {
  if (matchesAny(NON_RENDERING, file)) return {kind: 'ignored', pages: []};
  if (matchesAny(SHARED, file)) return {kind: 'shared', pages: []};
  const claimed = pages.filter(page => matchesAny(page.ui, file)).map(page => page.name);
  if (claimed.length) return {kind: 'page', pages: claimed};
  if (matchesAny(UNSHOWN, file)) return {kind: 'unshown', pages: []};
  return {kind: 'unmapped', pages: []};
}

// paths: the changed paths of a diff. darkPaths: the changed paths with a changed line that mentions dark.
function selectPages(paths: string[], pages: Page[], darkPaths: string[] = []): Selection {
  const names = pages.map(page => page.name);
  const ui = paths.filter(path => path.startsWith('ui/'));
  const byKind: Record<FileKind, string[]> = {shared: [], unmapped: [], page: [], unshown: [], ignored: []};
  const claimed = new Set<string>();
  for (const file of ui) {
    const {kind, pages: named} = classify(file, pages);
    byKind[kind].push(file);
    for (const name of named) claimed.add(name);
  }
  const all = byKind.shared.length > 0 || byKind.unmapped.length > 0;
  const chosen = all ? names : names.filter(name => claimed.has(name));

  const parts: string[] = [];
  if (byKind.shared.length) parts.push(`shared ${byKind.shared.join(', ')}: every page`);
  if (byKind.unmapped.length) parts.push(`unmapped ${byKind.unmapped.join(', ')}: every page`);
  if (!all && claimed.size) parts.push(`pages ${chosen.join(', ')}`);
  if (byKind.unshown.length) parts.push(`not shown: ${byKind.unshown.join(', ')}`);
  let reason = parts.join('; ');
  if (!ui.length) reason = 'no ui/ change: no pages';
  else if (!reason) reason = 'only ui/ tests or baselines: no pages';

  const dark = ui.some(file => DARK_FILES.includes(file))
    || darkPaths.some(file => file.startsWith('ui/') && classify(file, pages).kind !== 'ignored');

  const byPage: Record<string, string[]> = {};
  for (const name of names) {
    byPage[name] = ui.filter(file => {
      const c = classify(file, pages);
      return c.kind === 'page' && c.pages.includes(name);
    });
  }
  return {pages: chosen, all, dark, reason, unshown: byKind.unshown, byPage};
}

module.exports = {NON_RENDERING, SHARED, UNSHOWN, DARK_FILES, globToRegExp, classify, selectPages};
