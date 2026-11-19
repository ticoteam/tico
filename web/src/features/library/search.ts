/* What each Library address may carry, and the `validateSearch` for its route: keeps the words it knows, drops the rest. */

const str = (v: unknown) => (typeof v === 'string' && v ? v : typeof v === 'number' ? String(v) : undefined);
const num = (v: unknown) => {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
  return Number.isInteger(n) && n > 0 ? n : undefined;
};
const flag = (v: unknown) => (v === 1 || v === '1' || v === true || v === 'true' ? (1 as const) : undefined);
const clean = <T extends object>(o: T): T => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as T;

export interface DocsSearch {
  q?: string;
}
export const docsSearch = (raw: Record<string, unknown>): DocsSearch => clean({ q: str(raw.q) });

/** A doc (`edit`, an old version `v`), and External sources (`panel`: the source open beside the list). */
export interface DocSearch {
  edit?: 1;
  v?: number;
  panel?: string;
}
export const docSearch = (raw: Record<string, unknown>): DocSearch => clean({ edit: flag(raw.edit), v: num(raw.v), panel: str(raw.panel) });

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

export interface ToolsSearch {
  q?: string;
  tool?: string;
}
export const toolsSearch = (raw: Record<string, unknown>): ToolsSearch => clean({ q: str(raw.q), tool: str(raw.tool) });

/** `type`: person, organization, place...; `panel`: the contact open beside the list. */
export interface ContactsSearch {
  type?: string;
  q?: string;
  panel?: string;
}
export const contactsSearch = (raw: Record<string, unknown>): ContactsSearch => clean({ type: str(raw.type), q: str(raw.q), panel: str(raw.panel) });
