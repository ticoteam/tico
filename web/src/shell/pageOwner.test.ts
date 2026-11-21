import { describe, expect, it } from 'vitest';
import { barFolded, fromPage, pageAt } from './pageOwner';

/* Every route reaches the page map (master plan P5): one address per route file, and who answers there. A new route
   file fails here until it is mapped. backend/tests/test_page_owners.py checks each company page's key is a PAGES key. */
const ROUTES: Record<string, [path: string, page: string, owner: 'page' | 'tico' | null]> = {
  '[_]dev.dot.tsx': ['/_dev/dot', '_dev', null],
  '[_]dev.kinds.tsx': ['/_dev/kinds', '_dev', null],
  'calendar.tsx': ['/calendar', 'calendar', 'tico'],
  'call.$room.tsx': ['/call/r1', 'call', null],
  'company.finance.tsx': ['/company/finance', 'finance', 'page'],
  'company.growth.tsx': ['/company/growth', 'growth', 'page'],
  'company.index.tsx': ['/company', 'company', 'page'],
  'company.listening.tsx': ['/company/listening', 'listening', 'page'],
  'company.products.tsx': ['/company/products', 'products', 'page'],
  'company.risk.tsx': ['/company/risk', 'risk', 'page'],
  'company.risk_.disputes.tsx': ['/company/risk/disputes', 'risk', 'page'],
  'company.routines.tsx': ['/company/routines', 'automations', 'page'],
  'company.rules.tsx': ['/company/rules', 'rules', 'page'],
  'company.rules_.links.tsx': ['/company/rules/links', 'rules', 'page'],
  'company.strategy.tsx': ['/company/strategy', 'strategy', 'page'],
  'company.strategy_.$tab.tsx': ['/company/strategy/goals', 'goals', 'page'],
  'company.usage.tsx': ['/company/usage', 'usage', 'page'],
  'flow.$task.tsx': ['/flow/t1', 'task', null],
  'flow.index.tsx': ['/flow', 'flow', 'tico'],
  'index.tsx': ['/', 'tasks', 'page'],
  'learned.tsx': ['/learned', 'learned', 'tico'],
  'library.$doc.tsx': ['/library/d1', 'docs', 'page'],
  'library.contacts.tsx': ['/library/contacts', 'contacts', 'page'],
  'library.docs.tsx': ['/library/docs', 'docs', 'page'],
  'library.files.tsx': ['/library/files', 'docs', 'page'],
  'library.index.tsx': ['/library', 'docs', 'page'],
  'library.skills.tsx': ['/library/skills', 'skills', 'page'],
  'library.tools.tsx': ['/library/tools', 'tools', 'page'],
  'roadmap.tsx': ['/roadmap', 'roadmap', 'page'],
  'settings.$section.tsx': ['/settings/agents', 'settings', 'page'],
  'settings.index.tsx': ['/settings', 'settings', 'page'],
  'settings.style-guide.tsx': ['/settings/style-guide', 'style-guide', null],
  'tags.$tag.tsx': ['/tags/vip', 'tags', 'page'],
  'tags.index.tsx': ['/tags', 'tags', 'page'],
  'task.$task.tsx': ['/task/t1', 'task', null],
  'tasks.$pipeline.$task.$tab.tsx': ['/tasks/eng/7/files', 'task', null],
  'tasks.$pipeline.$task.index.tsx': ['/tasks/eng/7', 'task', null],
  'tasks.$pipeline.edit.tsx': ['/tasks/eng/edit', 'tasks', 'page'],
  'tasks.$pipeline.index.tsx': ['/tasks/eng', 'tasks', 'page'],
  'tasks.index.tsx': ['/tasks', 'tasks', 'page'],
  'team.$who.tsx': ['/team/scout', 'teammate', null],
  'threads.$thread.tsx': ['/threads/th1', 'thread', null],
  'threads.catch-up.tsx': ['/threads/catch-up', 'threads', 'tico'],
  'threads.index.tsx': ['/threads', 'threads', 'tico'],
  'threads.mail.tsx': ['/threads/mail', 'mail', 'tico'],
  'threads.messaging.tsx': ['/threads/messaging', 'messaging', 'page'],
  'updates.$tab.tsx': ['/updates/daily', 'updates', 'page'],
  'updates.index.tsx': ['/updates', 'updates', 'page'],
};

describe('the page map', () => {
  it('maps every route file to its page and who answers there', () => {
    const files = Object.keys(import.meta.glob('../routes/*.tsx'))
      .map((f) => f.replace('../routes/', ''))
      .filter((f) => f !== '__root.tsx')
      .sort();
    expect(files).toEqual(Object.keys(ROUTES).sort());
    for (const [file, [path, page, owner]] of Object.entries(ROUTES)) {
      const at = pageAt(path);
      expect([file, at.page, at.owner]).toEqual([file, page, owner]);
      expect(pageAt(`/next${path === '/' ? '' : path}`)).toEqual(at);
    }
  });

  it('names the personal pages Tico’s, and Settings › Account and Computers by their own keys', () => {
    for (const path of ['/calendar', '/threads/mail', '/flow', '/learned', '/settings/account']) expect(pageAt(path).owner).toBe('tico');
    expect(pageAt('/settings/computers').page).toBe('computers');
    expect(pageAt('/settings/changelog').page).toBe('changelog');
  });

  it('reads only its own entries for an address part, never one an object inherits @4857-S12', () => {
    expect(pageAt('/company/constructor')).toEqual({ page: 'company', label: 'Company', owner: 'page' });
    expect(pageAt('/settings/__proto__').page).toBe('settings');
    expect(pageAt('/library/toString')).toEqual({ page: 'docs', label: 'Docs', owner: 'page', item: { kind: 'doc', id: 'toString' } });
  });

  it('names the mail open in Mail, as its box and thread, for Ask Tico @4857-S17', () => {
    expect(fromPage(pageAt('/threads/mail', '?box=help%40acme.example&open=th1'))).toEqual({
      page: 'mail',
      label: 'Mail',
      item: { kind: 'mail', id: 'help@acme.example/th1' },
    });
    expect(pageAt('/threads/mail', '?box=help%40acme.example').item).toBeUndefined();
  });

  it('carries the open item as a pointer, with no words, in from_page', () => {
    expect(fromPage(pageAt('/library/d%201'))).toEqual({ page: 'docs', label: 'Docs', item: { kind: 'doc', id: 'd 1' } });
    expect(fromPage(pageAt('/tasks/eng'))).toEqual({ page: 'tasks', label: 'Tasks', item: { kind: 'pipeline', id: 'eng' } });
    expect(fromPage(pageAt('/company/strategy/goals'))).toEqual({ page: 'goals', label: 'Goals' });
  });

  it('opens the bar on a wide window, folds it under, keeps a hand choice, and an ask opens it', () => {
    expect([barFolded(undefined, false, true), barFolded(undefined, false, false)]).toEqual([false, true]);
    expect([barFolded(true, false, true), barFolded(false, false, false)]).toEqual([true, false]);
    expect(barFolded(true, true, false)).toBe(false);
  });
});
