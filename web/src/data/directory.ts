import { queryOptions, useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { GetOrgResponse } from '@/api/schema';
import { keys } from '@/data/keys';
import { QUIET_STALE_MS, quietRoot } from '@/data/optimistic';

/** A person or an agent, as every name and face in the app reads it. */
export interface Actor {
  /** 'human:<id>' | 'bot:<id>'. An agent's id is its slug for older agents and a uuid for newer ones. */
  id: string;
  kind: 'person' | 'agent';
  name: string;
  /** An agent's slug. */
  slug?: string;
  /** A photo URL; null while the server gives none (Avatar draws initials on `hue`). */
  avatar?: string | null;
  title?: string;
  /** The team's name. */
  team?: string;
  /** An agent's status line (what it is on now). */
  status?: string;
  /** For `Avatar`'s `hue`: the old app's colour per person (from the name) and per agent (from the slug). */
  hue?: number;
  /** An agent running a turn now: `Avatar`'s `live`. */
  working?: boolean;
}

/**
 * `byId` answers every form an id comes in: 'human:<id>' and 'bot:<id>' (tasks, messages, /me), a bare agent id
 * (status events, groups, reports_to), an agent's slug bare or as 'bot:<slug>' (routes), and a bare person id where
 * no agent answers to it.
 */
export interface Directory {
  actors: Actor[];
  byId: Record<string, Actor>;
}

/** A row of GET /api/v2/status, as the `bots` live topic also carries it (backend/events.py status_line). */
export interface StatusLine {
  bot: string;
  state?: string | null;
  focus?: string | null;
}

interface OrgGroup {
  id: string;
  name?: string;
}

// The old app's hues (ui/app/avatars.ts personHue and avHue), so a face keeps its colour across both apps.
function personHue(name: string): number {
  let h = 2166136261;
  for (let i = 0; i < name.length; i++) h = Math.imul(h ^ name.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10) * 36;
}
function agentHue(slug: string): number {
  let h = 7;
  for (let i = 0; i < slug.length; i++) h = (h * 31 + slug.charCodeAt(i)) % 360;
  return h;
}

const text = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() ? v : undefined);
const statusOf = (line: StatusLine | null | undefined) => ({
  status: text(line?.focus),
  working: line?.state === 'running',
});

function index(actors: Actor[]): Directory {
  const byId: Record<string, Actor> = {};
  for (const a of actors) {
    byId[a.id] = a;
    if (a.kind === 'agent') byId[a.id.slice(4)] = a;
  }
  for (const a of actors) {
    if (!a.slug) continue;
    byId[a.slug] ??= a;
    byId[`bot:${a.slug}`] ??= a;
  }
  for (const a of actors) {
    if (a.kind === 'person') byId[a.id.slice(a.id.indexOf(':') + 1)] ??= a;
  }
  return { actors, byId };
}

/** The directory from GET /api/v2/org and GET /api/v2/status. */
export function buildDirectory(org: GetOrgResponse, lines: StatusLine[]): Directory {
  const teams = new Map((org.org_groups as OrgGroup[]).map((g) => [g.id, text(g.name) ?? g.id]));
  const teamName = (id: unknown) => (text(id) ? teams.get(id as string) : undefined);
  const statuses = new Map(lines.map((l) => [l.bot, l]));
  const people: Actor[] = org.people.map((p) => {
    const name = text(p.name) ?? p.id;
    return {
      id: `human:${p.id}`,
      kind: 'person',
      name,
      avatar: null,
      title: text(p.title),
      team: teamName(p.team),
      hue: personHue(name),
    };
  });
  const agents: Actor[] = org.bots.map((b) => {
    const row = b as typeof b & { slug?: string; department?: string };
    const slug = text(row.slug) ?? b.id;
    return {
      id: `bot:${b.id}`,
      kind: 'agent',
      name: text(b.display_name) ?? slug,
      slug,
      avatar: null,
      team: teamName(b.team) ?? text(row.department),
      hue: agentHue(slug),
      ...statusOf(statuses.get(b.id)),
    };
  });
  return index([...people, ...agents]);
}

/**
 * The directory with one agent's status line changed (a `bots` event). The same directory when nothing changed;
 * null when it does not hold that agent (read it again). Only that agent's object is new, so only its rows redraw.
 */
export function withStatus(dir: Directory, bot: string, line: StatusLine | null | undefined): Directory | null {
  const held = dir.byId[`bot:${bot}`];
  if (!held) return null;
  const next = statusOf(line);
  if (held.status === next.status && held.working === next.working) return dir;
  const actor = { ...held, ...next };
  const byId = { ...dir.byId };
  for (const k of Object.keys(byId)) if (byId[k] === held) byId[k] = actor;
  return { actors: dir.actors.map((a) => (a === held ? actor : a)), byId };
}

// Renames, titles and team moves have no live topic: re-read after the person's own writes and on reconnect.
quietRoot(keys.directory()[0]);

/** Every person and agent the caller may see, with each agent's status line. */
export const directoryQuery = queryOptions({
  queryKey: keys.directory(),
  queryFn: async ({ signal }) => {
    const [org, status] = await Promise.all([
      api.get<GetOrgResponse>('/api/v2/org', { signal }),
      // Status lines are extra: the directory still opens without them.
      api.get<{ bots?: StatusLine[] }>('/api/v2/status', { signal }).catch(() => ({ bots: [] })),
    ]);
    return buildDirectory(org, status.bots ?? []);
  },
  staleTime: QUIET_STALE_MS,
});

const NONE: Actor[] = [];

/** Just the part of the directory a component shows (`(d) => !!d.byId[id]`, a few names): it draws again only when
    that part changes, not on every agent's status line. Undefined until the directory is read. */
export function useDirectory<T>(select: (d: Directory) => T): T | undefined {
  return useQuery({ ...directoryQuery, select }).data;
}

/** One person or agent by any id form `byId` answers; redraws only when that actor changes. */
export function useActor(id: string | null | undefined): Actor | undefined {
  const { data } = useQuery({ ...directoryQuery, select: (d) => (id ? d.byId[id] : undefined) });
  return data;
}

/** Everyone: people first, then agents. */
export function useActors(): Actor[] {
  const { data } = useQuery({ ...directoryQuery, select: (d) => d.actors });
  return data ?? NONE;
}
