import { queryClient } from '@/data';
import { fmtDate } from '@/lib';
import { defineSearch } from '@/search/define';
import { fetchFor, HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { contactSearchQuery, countsQuery, docsListQuery, fileSearchQuery, skillsQuery, toolsQuery, typesQuery } from './queries';

/* What each Library address may carry, and the `validateSearch` for its route: keeps the words it knows, drops the rest. */

const str = (v: unknown) => (typeof v === 'string' && v ? v : typeof v === 'number' ? String(v) : undefined);
const num = (v: unknown) => {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
  return Number.isInteger(n) && n > 0 ? n : undefined;
};
const flag = (v: unknown) => (v === 1 || v === '1' || v === true || v === 'true' ? (1 as const) : undefined);
const clean = <T extends object>(o: T): T => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as T;

/** `tab=sources`: External sources, the second tab; `panel`: the source open beside them; `archived`: archived docs. */
export interface DocsSearch {
  q?: string;
  tab?: 'sources';
  panel?: string;
  archived?: 1;
}
export const docsSearch = (raw: Record<string, unknown>): DocsSearch =>
  clean({ q: str(raw.q), tab: raw.tab === 'sources' ? ('sources' as const) : undefined, panel: str(raw.panel), archived: flag(raw.archived) });

/** A doc: `edit`, or an old version `v`. */
export interface DocSearch {
  edit?: 1;
  v?: number;
}
export const docSearch = (raw: Record<string, unknown>): DocSearch => clean({ edit: flag(raw.edit), v: num(raw.v) });

/** `kind`: one library kind; `task`: where an upload lands; `file` (and `v`): the file open in the page. */
export interface FilesSearch {
  kind?: string;
  q?: string;
  task?: string;
  file?: string;
  v?: number;
}
export const filesSearch = (raw: Record<string, unknown>): FilesSearch =>
  clean({ kind: str(raw.kind), q: str(raw.q), task: str(raw.task), file: str(raw.file), v: num(raw.v) });

/**
 * `skill` (a name) or `proposal` (an id) open in the page. `connect`: Connect Claude Code, `setup` for the steps or the
 * code the helper on a computer shows.
 */
export interface SkillsSearch {
  q?: string;
  skill?: string;
  proposal?: string;
  connect?: string;
}
export const skillsSearch = (raw: Record<string, unknown>): SkillsSearch =>
  clean({ q: str(raw.q), skill: str(raw.skill), proposal: str(raw.proposal), connect: str(raw.connect) });

/** `kind`: one kind of tool (api, website...); `needs`: only the tools that need someone. */
export interface ToolsSearch {
  q?: string;
  tool?: string;
  kind?: string;
  needs?: 1;
}
export const toolsSearch = (raw: Record<string, unknown>): ToolsSearch =>
  clean({ q: str(raw.q), tool: str(raw.tool), kind: str(raw.kind), needs: flag(raw.needs) });

/** `type`: person, organization, place...; `panel`: the contact open beside the list. */
export interface ContactsSearch {
  type?: string;
  q?: string;
  panel?: string;
}
export const contactsSearch = (raw: Record<string, unknown>): ContactsSearch => clean({ type: str(raw.type), q: str(raw.q), panel: str(raw.panel) });

/* ---------------------------------------------------------------- Cmd+K: one group per Library page */

/** Contact types searched at most, so one pause in the typing is a few reads, not one per type. */
const CONTACT_TYPES = 4;

export const docsFind = defineSearch({
  group: 'Docs',
  order: 60,
  find: async (q) => {
    if (!q.trim()) return [];
    const { docs } = await queryClient.ensureQueryData(docsListQuery);
    return docs
      .filter((d) => matches(d.title, q))
      .slice(0, HITS)
      .map((d) => ({
        key: `doc:${d.id}`,
        title: d.title,
        meta: fmtDate(d.updated_at) || undefined,
        lead: { icon: 'file' as const },
        run: { id: 'docs.open', input: { doc: d.id } },
      }));
  },
});

/** The server's file search (titles and text). */
export const filesFind = defineSearch({
  group: 'Files',
  order: 61,
  remote: true,
  find: async (q, signal) => {
    const words = q.trim().slice(0, 200);
    if (words.length < 2) return [];
    const { results } = await fetchFor(fileSearchQuery(words), signal);
    return results.slice(0, HITS).map((f) => ({
      key: `file:${f.id}`,
      title: f.title,
      lead: { icon: 'file' as const },
      run: { id: 'files.open', input: { file: f.id } },
    }));
  },
});

export const skillsFind = defineSearch({
  group: 'Skills',
  order: 62,
  find: async (q) => {
    if (!q.trim()) return [];
    const skills = await queryClient.ensureQueryData(skillsQuery);
    return skills
      .filter((k) => matches(k.name, q))
      .slice(0, HITS)
      .map((k) => ({ key: `skill:${k.name}`, title: k.name, lead: { icon: 'spark' as const }, run: { id: 'skills.open', input: { skill: k.name } } }));
  },
});

export const toolsFind = defineSearch({
  group: 'Tools',
  order: 63,
  find: async (q) => {
    if (!q.trim()) return [];
    const { rows } = await queryClient.ensureQueryData(toolsQuery);
    return rows
      .filter((t) => matches(t.name, q))
      .slice(0, HITS)
      .map((t) => ({
        key: `tool:${t.id}`,
        title: t.name,
        meta: t.vendor_name && t.vendor_name !== t.name ? t.vendor_name : undefined,
        lead: { icon: 'link' as const },
        run: { id: 'tools.open', input: { tool: t.id } },
      }));
  },
});

/** Contacts by name from the server: one read for each of the (at most) four types with the most contacts. */
export const contactsFind = defineSearch({
  group: 'Contacts',
  order: 64,
  remote: true,
  find: async (q, signal) => {
    const words = q.trim().slice(0, 200);
    if (!words) return [];
    const [counts, types] = await Promise.all([queryClient.ensureQueryData(countsQuery), queryClient.ensureQueryData(typesQuery).catch(() => [])]);
    const found = await Promise.all(
      counts
        .filter((c) => c.count > 0)
        .sort((a, b) => b.count - a.count)
        .slice(0, CONTACT_TYPES)
        .map((c) =>
          fetchFor(contactSearchQuery(c.type, words, HITS), signal)
            .then(({ rows }) => rows.map((r) => ({ r, type: c.type })))
            .catch(() => []),
        ),
    );
    return found
      .flat()
      .slice(0, HITS)
      .map(({ r, type }) => ({
        key: `contact:${r.id}`,
        title: r.name,
        meta: types.find((t) => t.key === type)?.name,
        lead: { icon: 'people' as const },
        run: { id: 'contacts.open', input: { contact: r.id, type } },
      }));
  },
});
