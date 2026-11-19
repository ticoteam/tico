import type { TaskDocument, TaskRequirement } from '@/api/schema';
import type { TaskData } from './queries';

/* Plain rules of the open task: its tabs and their marks, the decision that waits on the person, and its addresses. */

export const slugOf = (name: string): string =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'page';

export const DETAILS = 'details';

/** A tab's mark (parts-kit.js tab): approved, waits on you, not yet. */
export type TabMark = 'ok' | 'ask' | 'none';

export interface TaskTab {
  slug: string;
  label: string;
  mark?: TabMark;
  doc?: TaskDocument;
}

/** The requirement the person may answer now: Approve, Send back. */
export function decisionOf(t: TaskData): TaskRequirement | null {
  return t.to_leave?.find((r) => !r.met && (r.may.includes('ok') || r.may.includes('no'))) ?? null;
}

const fileOf = (r: TaskRequirement | null): string | null => r?.approval?.file_id ?? r?.page?.file_id ?? null;

/** The stage's pages, each with its state, then Details (more.js taskflow). */
export function tabsOf(t: TaskData): TaskTab[] {
  const asked = fileOf(decisionOf(t));
  const used = new Set<string>();
  const docs = (t.documents ?? []).map((doc): TaskTab => {
    let slug = slugOf(doc.name);
    while (used.has(slug) || slug === DETAILS) slug += '-2';
    used.add(slug);
    const ok = doc.approved_version !== undefined && doc.approved_version === doc.version && !doc.changed_since_ok;
    return { slug, label: doc.title || doc.name, mark: doc.file_id === asked ? 'ask' : ok ? 'ok' : 'none', doc };
  });
  return [...docs, { slug: DETAILS, label: 'Details' }];
}

/** With no tab in the address: the page that waits on the person, else the first page, else Details. */
export function defaultTab(tabs: TaskTab[]): string {
  return (tabs.find((x) => x.mark === 'ask') ?? tabs[0])?.slug ?? DETAILS;
}

/** The name the answer card gives what is decided: the page, else the requirement's own line. */
export function decisionName(t: TaskData, r: TaskRequirement): string {
  const file = fileOf(r);
  const doc = file ? t.documents?.find((d) => d.file_id === file) : undefined;
  return r.approval?.document || doc?.title || doc?.name || r.line;
}

/** The address of a task's tab: under its pipeline, or inside Flow. */
export function tabAddress(t: Pick<TaskData, 'number' | 'id' | 'pipeline_id'>, tab: string | undefined, flow: boolean): { to: string; search: { tab?: string } } {
  const n = String(t.number ?? t.id);
  if (flow) return { to: `/flow/${n}`, search: tab ? { tab } : {} };
  const base = `/tasks/${encodeURIComponent(t.pipeline_id ?? 'all')}/${n}`;
  return { to: tab ? `${base}/${tab}` : base, search: {} };
}

/** A refusal's reason, as the action layer carries it ("code: reason"). */
export function reasonOf(error: string | undefined): string {
  if (!error) return 'Not saved';
  if (error === 'not_allowed') return 'You can’t do that on this task';
  if (error.startsWith('network')) return 'Needs a connection';
  const i = error.indexOf(': ');
  return (i >= 0 && error.slice(i + 2).trim()) || 'Not saved';
}
