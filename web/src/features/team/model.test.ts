import { describe, expect, it } from 'vitest';
import type { Actor, Directory } from '@/data';
import { computerStatus, GB, historyOf, managerOf, membersUnder, modelLine, resolveWho, runFigures, startOfWeek, teamSlug, whoHref } from './model';
import type { AgentRow, Group, PersonRow } from './queries';

const ana: Actor = { id: 'bot:0190-ana', kind: 'agent', name: 'Ana', slug: 'ana' };
const pat: Actor = { id: 'human:p1', kind: 'person', name: 'Pat Doe' };
const dir: Directory = { actors: [ana, pat], byId: { 'bot:0190-ana': ana, ana, 'human:p1': pat, p1: pat } };
const group = (id: string, name: string, parent: string, bots: string[] = [], people: string[] = [], lead = ''): Group => ({
  id,
  name,
  parent,
  bots,
  people,
  lead,
});
const groups = [group('g1', 'Product & Design', ''), group('g2', 'Design', 'g1', ['0190-ana']), group('g3', 'Brand', 'g2', [], ['p1'])];

describe('team model', () => {
  it('reads an address as an agent, a person or a team', () => {
    expect(resolveWho('ana', dir, groups)).toEqual({ kind: 'agent', actor: ana });
    expect(resolveWho('p1', dir, groups)).toEqual({ kind: 'person', actor: pat });
    expect(resolveWho('product-design', dir, groups)).toEqual({ kind: 'team', group: groups[0] });
    expect(resolveWho('g2', dir, groups)).toEqual({ kind: 'team', group: groups[1] });
    expect(resolveWho('0190-ana', dir, groups)).toEqual({ kind: 'agent', actor: ana });
    expect(resolveWho('nobody', dir, groups)).toBeNull();
    expect(resolveWho('ana', undefined, undefined)).toBeNull();
  });

  it('gives each teammate the address the menu uses', () => {
    expect(whoHref(ana)).toBe('/team/ana');
    expect(whoHref(pat)).toBe('/team/p1');
    expect(teamSlug('Product & Design')).toBe('product-design');
  });

  it('finds the manager: the lead, else the teammate most members report to', () => {
    expect(managerOf(group('x', 'X', '', ['0190-ana'], ['p1'], 'person:p1'), [], [])).toBe('human:p1');
    expect(managerOf(group('x', 'X', '', ['0190-ana'], [], 'bot:0190-ana'), [], [])).toBe('bot:0190-ana');
    const agents = [
      { id: '0190-ana', slug: 'ana', reports_to: 'p1' },
      { id: 'bo', slug: 'bo', reports_to: 'p1' },
      { id: 'cy', slug: 'cy', reports_to: '0190-ana' },
    ] as AgentRow[];
    expect(managerOf(group('x', 'X', '', ['0190-ana', 'bo'], ['p1']), agents, [] as PersonRow[])).toBe('human:p1');
    expect(managerOf(group('x', 'X', '', ['0190-ana', 'cy']), agents, [])).toBe('bot:0190-ana');
    expect(managerOf(group('x', 'X', '', ['0190-ana', 'bo']), agents, [])).toBeNull();
  });

  it('counts everyone in the teams inside a team', () => {
    expect(membersUnder(groups, 'g1')).toEqual({ bots: ['0190-ana'], people: ['p1'] });
    expect(membersUnder(groups, 'g3')).toEqual({ bots: [], people: ['p1'] });
  });

  it('writes the model line from the server’s model list', () => {
    const row = { model: 'm-1', reasoning_effort: 'xhigh' } as AgentRow;
    expect(modelLine(row, [{ id: 'm-1', label: 'Opus 5.5' } as never])).toBe('Opus 5.5, XHigh');
    expect(modelLine({ resolved_model_label: 'haiku 5.5' } as AgentRow)).toBe('Haiku 5.5, Default');
    expect(modelLine({} as AgentRow)).toBe('');
  });

  it('counts turns today and spend this month', () => {
    const now = new Date(2026, 10, 19, 15);
    const runs = [
      { id: '1', finished_at: new Date(2026, 10, 19, 9).toISOString(), cost_usd: 1.5 },
      { id: '2', finished_at: new Date(2026, 10, 2).toISOString(), cost_usd: 2 },
      { id: '3', finished_at: new Date(2026, 9, 30).toISOString(), cost_usd: 9 },
    ];
    expect(runFigures(runs, now)).toEqual({ turns: 1, spend: 3.5 });
    expect(startOfWeek(now).getDay()).toBe(1);
  });

  it('merges runs, changes and what it learned, newest first, a stopped run marked', () => {
    const h = historyOf(
      [{ id: 'r1', issue: '42', finished_at: '2026-11-19T10:00:00Z', outcome: 'failed', duration_s: 480 }],
      [{ sha: 'a', subject: 'Prefers short replies', when: '2026-11-19T11:00:00Z' }],
      [{ id: 'c1', field: 'model', created_at: '2026-11-19T09:00:00Z' }],
    );
    expect(h.map((x) => x.title)).toEqual(['Prefers short replies', 'Task 42', 'Model changed']);
    expect(h[1]).toMatchObject({ bad: 'Stopped', dur: '8m', task: '42' });
  });

  it('says how a computer is doing in short facts', () => {
    expect(computerStatus({ online: true, readiness: { disk: { total_bytes: 100 * GB, free_bytes: 40 * GB } } }, () => '')).toEqual([
      'Online',
      'Disk 60 of 100 GB',
    ]);
    expect(computerStatus({ online: false, last_seen_at: '2026-11-19T10:41:00Z' }, () => '10:41 AM')).toEqual(['Offline since 10:41 AM']);
  });
});
