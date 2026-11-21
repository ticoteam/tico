import { describe, expect, it } from 'vitest';
import type { TaskDocument, TaskFileView, TaskRequirement } from '@/api/schema';
import { decisionName, decisionOf, defaultTab, earlierStages, reasonOf, refusalOf, reviewOf, slugOf, tabAddress, tabsOf } from './model';
import type { TaskData } from './queries';

const doc = (file_id: string, name: string, more: Partial<TaskDocument> = {}) => ({ file_id, name, version: 2, ...more }) as TaskDocument;
const req = (id: string, more: Partial<TaskRequirement> = {}) =>
  ({ requirement_id: id, line: 'Spec approved', met: false, may: ['ok', 'no'], ...more }) as TaskRequirement;
const task = (more: Partial<TaskData> = {}) => ({ id: 't1', number: 12, pipeline_id: 'p1', documents: [], to_leave: [], ...more }) as TaskData;

describe('the open task', () => {
  it('lists what waits on the person first, then its other pages with their marks, the chat, then Details @approvals', () => {
    const t = task({
      conversation_id: 'c7',
      documents: [doc('f1', 'Spec', { approved_version: 2 }), doc('f2', 'QA report'), doc('f3', 'Notes', { approved_version: 1 })],
      to_leave: [req('r1', { approval: { file_id: 'f2' } as TaskRequirement['approval'] })],
    });
    expect(tabsOf(t).map((x) => [x.slug, x.mark])).toEqual([
      ['qa-report', 'ask'],
      ['spec', 'ok'],
      ['notes', 'none'],
      ['chat', undefined],
      ['details', undefined],
    ]);
  });

  it('keeps tab slugs apart, and never takes Details', () => {
    const t = task({ documents: [doc('f1', 'Details'), doc('f2', 'Spec'), doc('f3', 'spec')] });
    expect(tabsOf(t).map((x) => x.slug)).toEqual(['details-2', 'spec', 'spec-2', 'details']);
    expect(slugOf('!!')).toBe('page');
  });

  it('opens on the page that waits on the person, else the first, else Details', () => {
    const asking = task({ documents: [doc('f1', 'Spec'), doc('f2', 'Plan')], to_leave: [req('r1', { page: { file_id: 'f2' } } as Partial<TaskRequirement>)] });
    expect(defaultTab(tabsOf(asking))).toBe('plan');
    expect(defaultTab(tabsOf(task({ documents: [doc('f1', 'Spec')] })))).toBe('spec');
    expect(defaultTab(tabsOf(task()))).toBe('details');
  });

  it('opens on the file the ask is about, not a review page it also shows @approvals', () => {
    const pdf = { id: 'f9', name: 'declaration.pdf', title: 'declaration.pdf', archived: false, current_version: 1, versions: [{ n: 1, mime: 'application/pdf' }] } as unknown as TaskFileView;
    // The review page is the newest change, so it is listed first; the ask names the PDF first (its subject).
    const t = task({ documents: [doc('f1', 'Review')], to_leave: [req('r1', { shows: ['file:f9@1', 'page:f1@2'] })] });
    expect(defaultTab(tabsOf(t, [pdf]))).toBe('declaration-pdf');
    // A stage's own OK names its page: that page opens, whatever else it shows.
    const own = task({ documents: [doc('f1', 'Review'), doc('f2', 'Spec')], to_leave: [req('r1', { page: { file_id: 'f2' }, shows: ['page:f1@2'] } as Partial<TaskRequirement>)] });
    expect(defaultTab(tabsOf(own))).toBe('spec');
  });

  it('reads the second review: running while one is open, then reviewed @approvals', () => {
    const kid = (status: string, role?: string) => ({ id: `k-${status}`, title: 'Review: second opinion', status, owner_id: 'bot:lo', role });
    expect(reviewOf(task({ children: [] }))).toBeNull();
    expect(reviewOf(task({ children: [kid('open')] }))).toBeNull();
    expect(reviewOf(task({ children: [kid('done', 'review'), kid('open', 'review')] }))).toBe('Review running');
    expect(reviewOf(task({ children: [kid('done', 'review'), kid('open', 'check')] }))).toBe('Reviewed');
    expect(reviewOf(task({ children: [kid('closed', 'review')] }))).toBeNull();
  });

  it('decides only what is not met and the person may answer', () => {
    const t = task({ to_leave: [req('r1', { met: true }), req('r2', { may: [] }), req('r3', { line: 'Ship it' })] });
    expect(decisionOf(t)?.requirement_id).toBe('r3');
    expect(decisionName(t, decisionOf(t) as TaskRequirement)).toBe('Ship it');
    expect(decisionOf(task({ to_leave: [req('r1', { may: [] })] }))).toBeNull();
  });

  it('addresses a tab under its pipeline, or inside Flow', () => {
    const t = task();
    expect(tabAddress(t, undefined, false)).toEqual({ to: '/tasks/p1/12', search: {} });
    expect(tabAddress(t, 'details', false)).toEqual({ to: '/tasks/p1/12/details', search: {} });
    expect(tabAddress(t, 'spec', true)).toEqual({ to: '/flow/12', search: { tab: 'spec' } });
  });

  it('says why a change was refused', () => {
    expect(reasonOf('task_requirements: Needs the QA report first')).toBe('Needs the QA report first');
    expect(reasonOf('not_allowed')).toBe('You can’t do that on this task');
    expect(reasonOf('network')).toBe('Needs a connection');
    expect(reasonOf(undefined)).toBe('Not saved');
    expect(reasonOf('http_409: ')).toBe('Not saved');
  });

  it('reads a refusal: its code, its reason, and each thing missing', () => {
    const r = refusalOf('task_not_met: Not met: QA report.\n- QA report: not approved\n- Checks: 2 failing');
    expect(r).toEqual({ code: 'task_not_met', reason: 'Not met: QA report.', missing: ['QA report: not approved', 'Checks: 2 failing'] });
    expect(reasonOf('task_not_met: Not met: QA report.\n- QA report: not approved')).toBe('Not met: QA report.');
  });

  it('moves back only to the stages before the one it is in', () => {
    const stages = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    expect(earlierStages(stages, 'c').map((s) => s.id)).toEqual(['a', 'b']);
    expect(earlierStages(stages, 'a')).toEqual([]);
    expect(earlierStages(stages, null)).toEqual([]);
  });
});
