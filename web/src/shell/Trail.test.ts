import { describe, expect, it } from 'vitest';
import { trailOf, upOf, type TrailNames, type TrailPart } from './Trail';

/* The trail is the address read as the menu names it, then the open page's own name (its h1) when the address does
   not already say it. Written "label → to"; the last part is the open page, with nowhere to go. */
const show = (parts: TrailPart[]) => parts.map((p) => (p.to ? `${p.label} → ${p.to}` : p.label));
const names = (page = '', more: Partial<TrailNames> = {}): TrailNames => ({
  company: 'TIDY',
  pipelines: [{ id: 'legal', name: 'Legal' }],
  page,
  ...more,
});

describe('the trail over every page', () => {
  it.each<[string, string, string[]]>([
    ['/tasks/legal', 'Tasks', ['TIDY → /', 'Tasks → /tasks', 'Legal']],
    ['/tasks/legal/12/details', 'Small-claims redline: customer agreement', ['TIDY → /', 'Tasks → /tasks', 'Legal → /tasks/legal', 'Small-claims redline: customer agreement']],
    ['/tasks/all/12/details', 'A task with no pipeline', ['TIDY → /', 'Tasks → /tasks', 'A task with no pipeline']],
    ['/tasks/legal/edit', 'Legal', ['TIDY → /', 'Tasks → /tasks', 'Legal → /tasks/legal', 'Edit']],
    ['/flow', 'Tasks', ['TIDY → /', 'Flow']],
    ['/flow/12', 'Sign the lease', ['TIDY → /', 'Flow → /flow', 'Sign the lease']],
    ['/roadmap', 'Roadmap', ['TIDY → /', 'Tasks → /tasks', 'Roadmap']],
    ['/threads/mail', 'Mail', ['TIDY → /', 'Threads → /threads', 'Mail']],
    ['/threads/catch-up', '', ['TIDY → /', 'Threads → /threads', 'Catch up']],
    ['/threads/th_9', 'Launch plan', ['TIDY → /', 'Threads → /threads', 'Launch plan']],
    ['/library/doc_1', 'Returns and refunds', ['TIDY → /', 'Library → /library', 'Docs → /library/docs', 'Returns and refunds']],
    ['/library/files', 'report.pdf', ['TIDY → /', 'Library → /library', 'Files → /library/files', 'report.pdf']],
    ['/settings', 'Settings', ['TIDY → /', 'Settings']],
    ['/settings/people', 'Settings', ['TIDY → /', 'Settings → /settings', 'People']],
    ['/company', 'Company', ['TIDY → /', 'Company']],
    ['/company/growth', 'Growth', ['TIDY → /', 'Company → /company', 'Growth']],
    ['/company/strategy/market', 'Strategy', ['TIDY → /', 'Company → /company', 'Strategy']],
    ['/company/risk/disputes', 'Disputes', ['TIDY → /', 'Company → /company', 'Risk → /company/risk', 'Disputes']],
    ['/updates/daily', 'Updates', ['TIDY → /', 'Updates']],
    ['/updates/archive', 'Archive', ['TIDY → /', 'Updates → /updates', 'Archive']],
    ['/tags/urgent', 'urgent', ['TIDY → /', 'Tags → /tags', 'urgent']],
    ['/calendar', 'Calendar', ['TIDY → /', 'Calendar']],
    ['/next/calendar', 'Calendar', ['TIDY → /', 'Calendar']],
  ])('%s, its page named "%s"', (path, page, want) => {
    expect(show(trailOf(path, names(page)))).toEqual(want);
  });

  it('names a teammate from the directory; Team has no page of its own', () => {
    expect(show(trailOf('/team/maya', names('Maya Okafor', { who: 'Maya Okafor' })))).toEqual(['TIDY → /', 'Team', 'Maya Okafor']);
    expect(show(trailOf('/team/qa', names('Ubuntu laptop', { who: 'QA Owner' })))).toEqual(['TIDY → /', 'Team', 'QA Owner → /team/qa', 'Ubuntu laptop']);
  });

  it('leaves out what it cannot name yet: the company, a pipeline, a page still loading', () => {
    expect(show(trailOf('/tasks/legal/12/details', { company: '', page: '' }))).toEqual(['Tasks']);
    expect(show(trailOf('/tasks/legal/12/details', names()))).toEqual(['TIDY → /', 'Tasks → /tasks', 'Legal']);
  });
});

describe("a phone's Back", () => {
  const up = (path: string, page: string) => upOf(trailOf(path, names(page)));
  it('goes one step up the trail', () => {
    expect(up('/tasks/legal/12/details', 'A task')).toEqual({ label: 'Legal', to: '/tasks/legal' });
    expect(up('/settings/people', 'Settings')).toEqual({ label: 'Settings', to: '/settings' });
    expect(up('/library/files', 'report.pdf')).toEqual({ label: 'Files', to: '/library/files' });
  });
  it('is not there when the step up only passes on (the company, Tasks, Library) or has no page (Team)', () => {
    expect(up('/calendar', 'Calendar')).toBeUndefined();
    expect(up('/tasks/legal', 'Tasks')).toBeUndefined();
    expect(up('/library/docs', 'Docs')).toBeUndefined();
    expect(upOf(trailOf('/team/maya', names('Maya Okafor', { who: 'Maya Okafor' })))).toBeUndefined();
  });
});
