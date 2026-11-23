import type { HealthFix } from './queries';

/* A health check's fix names an old-app address (backend/health.py `_fix`). Here it becomes the new app's address,
   from the menu's pages (actions/copy.ts `pages`, which features may not import) and Settings' own sections. A fix
   whose page has no new address yet keeps its words and loses its link. */

const SETTINGS_TAB: Record<string, string> = {
  devices: '/settings/computers',
  bots: '/settings/agents',
  models: '/settings/providers',
  providers: '/settings/providers',
  subscriptions: '/settings/providers',
};

const OLD: ReadonlyArray<readonly [RegExp, (m: RegExpExecArray) => string]> = [
  [/^#\/tasks\/?$/, () => '/tasks'],
  [/^#\/tasks\/(\d+)$/, (m) => `/task/${m[1]}`],
  [/^#\/usage$/, () => '/company/usage'],
  [/^#\/listening$/, () => '/company/listening'],
  [/^#\/changelog$/, () => '/settings/changelog'],
  [/^#\/tools(\/.*)?$/, () => '/library/tools'],
  [/^#\/settings\/(?:models|providers|subscriptions)(\/.*)?$/, () => '/settings/providers'],
  [/^#\/bot\/([^/?#]+)\/computer$/, (m) => `/team/${m[1]}?tab=computer`],
  [/^#\/bot\/([^/?#]+)(\/.*)?$/, (m) => `/team/${m[1]}`],
  [/^#\/computers\/([^/?#]+)(\/.*)?$/, (m) => `/settings/computers?open=${m[1]}`],
];

/** Where a fix goes in this app, an outside page, or null for words only. */
export function fixHref(f: HealthFix): string | null {
  const href = f.href ?? '';
  if (/^https:\/\//.test(href)) return href;
  if (href === '#/settings') return (f.tab && SETTINGS_TAB[f.tab]) || null;
  for (const [re, to] of OLD) {
    const m = re.exec(href);
    if (m) return to(m);
  }
  return null;
}
