import { describe, expect, it } from 'vitest';
import type { Actor, Team } from '@/data';
import { orgTree, revealTeam, type OrgNode } from './orgTree';

const person = (id: string, name: string, more: Partial<Actor> = {}): Actor => ({ id: `human:${id}`, kind: 'person', name, ...more });
const agent = (id: string, name: string, more: Partial<Actor> = {}): Actor => ({ id: `bot:${id}`, kind: 'agent', name, slug: id, ...more });
const team = (id: string, name: string, order: number, more: Partial<Team> = {}): Team => ({ id, name, parent: '', order, lead: '', ...more });
const ME = 'human:me';

/* what a tree reads as: each row's name, its label or the team it heads, and how many sit under it */
function shape(nodes: readonly OrgNode[], depth = 9): unknown[] {
  return nodes.map((n) => {
    const head = n.kind === 'team' ? `[${n.team.name}]` : `${n.actor.name}${n.label ? ` · ${n.label}` : ''}`;
    const line = n.kids.length ? `${head} (${n.size})` : head;
    return n.kids.length && depth > 1 ? [line, shape(n.kids, depth - 1)] : line;
  });
}

const TEAMS = [
  team('general', 'General', 0),
  team('product', 'Product', 1),
  team('experience', 'Experience', 2, { parent: 'product' }),
  team('engineering', 'Engineering', 3),
  team('corporate', 'Corporate', 4),
];

describe('orgTree', () => {
  it('starts from your direct reports, each with who reports to them under it, and leaves out you and the Assistant', () => {
    const actors = [
      person('me', 'Maya', { teamId: 'general' }),
      agent('assistant', 'Assistant', { reportsTo: ME, assistant: true }),
      agent('gm', 'General Manager', { teamId: 'general', reportsTo: ME }),
      agent('librarian', 'Librarian', { teamId: 'general', reportsTo: 'bot:gm' }),
      agent('em', 'Engineering Manager', { teamId: 'engineering', reportsTo: ME }),
      agent('builder', 'Builder', { teamId: 'engineering', reportsTo: 'bot:em' }),
      agent('qa', 'QA Owner', { teamId: 'engineering', reportsTo: 'bot:em' }),
      agent('e2e', 'E2E Runner', { teamId: 'engineering', reportsTo: 'bot:qa' }),
    ];
    expect(shape(orgTree({ actors, teams: TEAMS }, ME))).toEqual([
      ['General Manager (1)', ['Librarian']],
      ['Engineering Manager (3)', ['Builder', ['QA Owner (1)', ['E2E Runner']]]],
    ]);
  });

  it('says a row’s team where it starts on the line, unless its name already does, and offers that team on the row', () => {
    const actors = [
      agent('pm', 'Product Manager', { teamId: 'product', reportsTo: ME }),
      agent('triage', 'Bug Triage', { teamId: 'product', reportsTo: 'bot:pm' }),
      agent('ux', 'UX Owner', { teamId: 'experience', reportsTo: 'bot:pm' }),
      agent('finance', 'Finance Owner', { teamId: 'corporate', reportsTo: ME }),
      agent('ops', 'BotOps Owner', { reportsTo: ME }),
    ];
    const tree = orgTree({ actors, teams: TEAMS }, ME);
    expect(shape(tree)).toEqual([['Product Manager (2)', ['Bug Triage', 'UX Owner · Experience']], 'Finance Owner · Corporate', 'BotOps Owner']);
    const [pm, finance, ops] = tree as Extract<OrgNode, { kind: 'mate' }>[];
    expect(pm!.team).toEqual({ id: 'product', name: 'Product' });
    expect(pm!.label).toBeUndefined();
    expect(finance!.team).toEqual({ id: 'corporate', name: 'Corporate' });
    // an agent with no team sits where it reports, with no team to offer
    expect(ops!.team).toBeUndefined();
    // the row under its manager in the same team heads nothing
    expect((pm!.kids[0] as Extract<OrgNode, { kind: 'mate' }>).team).toBeUndefined();
  });

  it('puts those with no manager under their team’s lead, else in a row for the team where the team sits, else at the top', () => {
    const actors = [
      agent('em', 'Engineering Manager', { teamId: 'engineering', reportsTo: ME }),
      person('iris', 'Iris', { teamId: 'engineering' }),
      person('omar', 'Omar', { teamId: 'product' }),
      agent('omar-pm', 'Omar PM', { teamId: 'product', reportsTo: 'human:omar' }),
      agent('ux', 'UX Owner', { teamId: 'experience' }),
      person('ben', 'Ben'),
    ];
    // no leads: Product and Engineering get rows of their own; Experience's row sits in Product's
    expect(shape(orgTree({ actors, teams: TEAMS }, ME))).toEqual([
      ['[Product] (3)', [['Omar (1)', ['Omar PM']], ['[Experience] (1)', ['UX Owner']]]],
      ['[Engineering] (1)', ['Iris']],
      'Engineering Manager',
      'Ben',
    ]);
    // with leads, they hang from the lead; a lead with no manager sits where its team would
    const led = TEAMS.map((t) => (t.id === 'engineering' ? { ...t, lead: 'bot:em' } : t.id === 'product' ? { ...t, lead: 'human:omar' } : t));
    expect(shape(orgTree({ actors, teams: led }, ME))).toEqual([['Omar · Product (2)', ['Omar PM', ['[Experience] (1)', ['UX Owner']]]], ['Engineering Manager (1)', ['Iris']], 'Ben']);
  });

  it('cuts a reporting line that runs in a circle, and ignores a lead who is not in the team', () => {
    const actors = [
      agent('a', 'Alpha', { teamId: 'general', reportsTo: 'bot:b' }),
      agent('b', 'Beta', { teamId: 'general', reportsTo: 'bot:a' }),
      agent('c', 'Gamma', { teamId: 'corporate' }),
    ];
    const teams = TEAMS.map((t) => (t.id === 'corporate' ? { ...t, lead: 'bot:a' } : t));
    const tree = orgTree({ actors, teams }, ME);
    const names = JSON.stringify(shape(tree));
    for (const n of ['Alpha', 'Beta', 'Gamma']) expect(names.split(n).length - 1).toBe(1);
    expect(shape(tree)).toContainEqual(['[Corporate] (1)', ['Gamma']]);
  });

  it('orders rows by team in the org’s order, a team inside another right after it, then people before agents, then name', () => {
    const actors = [
      agent('z', 'Zed', { reportsTo: ME }),
      agent('fin', 'Finance Owner', { teamId: 'corporate', reportsTo: ME }),
      agent('ux', 'UX Owner', { teamId: 'experience', reportsTo: ME }),
      agent('eng', 'Builder', { teamId: 'engineering', reportsTo: ME }),
      person('kim', 'Kim', { teamId: 'engineering', reportsTo: ME }),
      agent('pm', 'Product Manager', { teamId: 'product', reportsTo: ME }),
    ];
    expect(shape(orgTree({ actors, teams: TEAMS }, ME))).toEqual([
      'Product Manager',
      'UX Owner · Experience',
      'Kim · Engineering',
      'Builder · Engineering',
      'Finance Owner · Corporate',
      'Zed',
    ]);
  });
});

describe('revealTeam', () => {
  it('opens the rows above a team and the rows that hold more of it, and names the first to bring into view', () => {
    const actors = [
      agent('gm', 'Growth Manager', { teamId: 'general', reportsTo: ME }),
      agent('sm', 'Sales Manager', { teamId: 'corporate', reportsTo: 'bot:gm' }),
      agent('rep', 'Sales Rep', { teamId: 'corporate', reportsTo: 'bot:sm' }),
      person('iris', 'Iris', { teamId: 'engineering' }),
    ];
    const tree = orgTree({ actors, teams: TEAMS }, ME);
    expect(revealTeam(tree, 'corporate')).toEqual({ open: ['bot:gm', 'bot:sm'], first: 'bot:sm' });
    expect(revealTeam(tree, 'engineering')).toEqual({ open: ['team:engineering'], first: 'team:engineering' });
    expect(revealTeam(tree, 'nowhere')).toEqual({ open: [], first: undefined });
  });
});
