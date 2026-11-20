// The Team tab's tree (plan/teams-menu.html, as the owner decided it): one tree by reporting line, rooted at you.
// Your direct reports are its first rows; each row's reports hang under it, and so on down. A team is part of the
// same tree: it shows on the rows that head it (a quiet second label), and only a team whose own teammates have no
// manager gets a row of its own, where the team sits. Pure: the root route builds it from the directory.
import type { Actor, Directory, Team } from '@/data';

export interface OrgTeamRef {
  id: string;
  name: string;
}

export type OrgNode =
  | {
      kind: 'mate';
      /** the actor's id */
      key: string;
      actor: Actor;
      /** the team this row heads: its teammates sit under it, or it is where the team starts on this line */
      team?: OrgTeamRef;
      /** the team's name beside the name, when the row heads a team its name does not already say */
      label?: string;
      kids: OrgNode[];
      /** everyone underneath, at any depth */
      size: number;
    }
  | {
      kind: 'team';
      /** `team:<id>` */
      key: string;
      team: OrgTeamRef;
      kids: OrgNode[];
      size: number;
    };

const ROOT = '';
const words = (s: string) => ` ${s.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean).join(' ')} `;
/** Whether a name already says the team's, word for word ("Engineering Manager" does; "Product Manager" is no "Pro"). */
const says = (name: string, team: string) => words(name).includes(words(team));
export const teamKey = (id: string) => `team:${id}`;

/**
 * The tree for `me`. Everyone but you and the Assistant is in it once:
 * - under who they report to;
 * - with no manager, under their team's lead, else under their team's row (which sits where its parent team sits),
 *   else, with no team either, at the top beside your direct reports;
 * - a line that runs in a circle is cut at the top.
 * Rows sort by team (the org's own order, a team inside another right after it), then people before agents, then name.
 */
export function orgTree(dir: Pick<Directory, 'actors' | 'teams'>, me: string | undefined): OrgNode[] {
  const teams = new Map((dir.teams ?? []).map((t) => [t.id, t]));
  const actors = dir.actors.filter((a) => a.id !== me && !a.assistant);
  const byId = new Map(actors.map((a) => [a.id, a]));
  const parent = new Map<string, string>();
  const teamRows = new Set<string>();

  // a team's lead while the lead is in it and on the chart
  const leadOf = (t: Team) => {
    const lead = t.lead ? byId.get(t.lead) : undefined;
    return lead?.teamId === t.id ? lead.id : undefined;
  };
  // where a team's teammates with no manager go: its lead, else its own row
  const place = (t: Team, seen: Set<string>): string => leadOf(t) ?? teamRow(t, seen);
  // a team's row sits where its parent team sits, or at the top
  const above = (t: Team, seen: Set<string>): string => {
    const up = t.parent && !seen.has(t.parent) ? teams.get(t.parent) : undefined;
    return up ? place(up, new Set(seen).add(t.id)) : ROOT;
  };
  const teamRow = (t: Team, seen: Set<string>): string => {
    const key = teamKey(t.id);
    if (!teamRows.has(key)) {
      teamRows.add(key);
      parent.set(key, above(t, new Set(seen).add(t.id)));
    }
    return key;
  };

  for (const a of actors) {
    const boss = a.reportsTo;
    if (boss === me && me) parent.set(a.id, ROOT);
    else if (boss && byId.has(boss)) parent.set(a.id, boss);
    else {
      const t = a.teamId ? teams.get(a.teamId) : undefined;
      if (!t) parent.set(a.id, ROOT);
      else if (leadOf(t) === a.id) parent.set(a.id, above(t, new Set()));
      else parent.set(a.id, place(t, new Set()));
    }
  }

  // A line that comes back to itself is cut where it closes: that row moves to the top.
  for (const start of parent.keys()) {
    const path = new Set<string>();
    let at = start;
    while (at !== ROOT && !path.has(at)) {
      path.add(at);
      at = parent.get(at) ?? ROOT;
    }
    if (at !== ROOT) parent.set(at, ROOT);
  }

  // the team each place stands for, to say a row's team only where it changes (at the top, every row says its own)
  const teamAt = (key: string): string | undefined => (key.startsWith('team:') ? key.slice(5) : byId.get(key)?.teamId);

  const kids = new Map<string, string[]>();
  for (const [key, up] of parent) kids.set(up, [...(kids.get(up) ?? []), key]);

  // a team's place in the org's order: its parents' orders, then its own
  const pathOf = (id: string | undefined): number[] => {
    const out: number[] = [];
    const seen = new Set<string>();
    for (let t = id ? teams.get(id) : undefined; t && !seen.has(t.id); t = t.parent ? teams.get(t.parent) : undefined) {
      seen.add(t.id);
      out.unshift(t.order);
    }
    return out.length ? out : [Number.POSITIVE_INFINITY];
  };
  const byPath = (a: number[], b: number[]) => {
    for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i]! - b[i]!;
    return a.length - b.length;
  };
  const rank = (key: string) => {
    if (key.startsWith('team:')) return { path: pathOf(key.slice(5)), first: 0, kind: 0, name: teams.get(key.slice(5))?.name ?? '' };
    const a = byId.get(key)!;
    const t = a.teamId ? teams.get(a.teamId) : undefined;
    return { path: pathOf(a.teamId), first: t && leadOf(t) === a.id ? 0 : 1, kind: a.kind === 'person' ? 0 : 1, name: a.name };
  };
  const order = (keys: string[]) =>
    keys
      .map((key) => ({ key, r: rank(key) }))
      .sort((x, y) => byPath(x.r.path, y.r.path) || x.r.first - y.r.first || x.r.kind - y.r.kind || x.r.name.localeCompare(y.r.name))
      .map((x) => x.key);

  const node = (key: string): OrgNode => {
    const under = order(kids.get(key) ?? []).map(node);
    const size = under.reduce((n, k) => n + (k.kind === 'mate' ? 1 : 0) + k.size, 0);
    if (key.startsWith('team:')) {
      const t = teams.get(key.slice(5))!;
      return { kind: 'team', key, team: { id: t.id, name: t.name }, kids: under, size };
    }
    const a = byId.get(key)!;
    const t = a.teamId ? teams.get(a.teamId) : undefined;
    const up = parent.get(key) ?? ROOT;
    // it heads its team on this line when the row above it is in another team (or is you, or the top)
    const heads = !!t && teamAt(up) !== t.id;
    const said = !!t && says(a.name, t.name);
    return {
      kind: 'mate',
      key,
      actor: a,
      ...(heads && t ? { team: { id: t.id, name: t.name }, ...(said ? {} : { label: t.name }) } : {}),
      kids: under,
      size,
    };
  };
  return order(kids.get(ROOT) ?? []).map(node);
}

/** The shape the menu's rows share with these: a key, the team a row stands for, the rows under it. */
interface Line<N> {
  kind: 'mate' | 'team';
  key: string;
  team?: { id: string };
  kids: readonly N[];
}

/** Every row's key with the keys above it: what opens to show a row. */
export function linesOf<N extends Line<N>>(tree: readonly N[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  const walk = (nodes: readonly N[], above: string[]) => {
    for (const n of nodes) {
      out.set(n.key, above);
      walk(n.kids, [...above, n.key]);
    }
  };
  walk(tree, []);
  return out;
}

/** What opens to show a team (an old `/team/<team>` link, `?team=<id>`): the rows that head it and the rows above
    them, its own row when it has one, and the rows inside it that hold more of it. The first row to scroll to. */
export function revealTeam<N extends Line<N>>(tree: readonly N[], team: string): { open: string[]; first?: string } {
  const lines = linesOf(tree);
  const open = new Set<string>();
  let first: string | undefined;
  const walk = (nodes: readonly N[]) => {
    for (const n of nodes) {
      const here = n.team?.id === team;
      if (here) {
        first ??= n.key;
        for (const k of lines.get(n.key) ?? []) open.add(k);
        if (n.kids.length) open.add(n.key);
      }
      walk(n.kids);
    }
  };
  walk(tree);
  return { open: [...open], first };
}
