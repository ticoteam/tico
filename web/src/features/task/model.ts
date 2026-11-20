import { refusal, type RefusalToast, type Result } from '@/actions';
import type { TaskDocument, TaskFileView, TaskRequirement } from '@/api/schema';
import { PENDING_ANSWER, type TaskData } from './queries';

/* Plain rules of the open task: its tabs and their marks, the decision that waits on the person, and its addresses. */

export const slugOf = (name: string): string =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'page';

export const DETAILS = 'details';
/** The task's chat as a tab: a phone's (on a computer the chat sits beside the page). */
export const CHAT = 'chat';

/** A tab's words: a file's name without its extension (`letter.pdf` reads "letter"); a version number keeps its dot. */
export const tabLabel = (name: string): string => name.replace(/\.(?=[a-z0-9]*[a-z])[a-z0-9]{1,5}$/i, '') || name;

/** A tab's mark (parts-kit.js tab): approved, waits on you, not yet. */
export type TabMark = 'ok' | 'ask' | 'none';

export interface TaskTab {
  slug: string;
  label: string;
  mark?: TabMark;
  doc?: TaskDocument;
  /** One of the task's other files, shown by its type. */
  file?: TaskFileView;
  /** An email the task asks about (`email:<id>` in what a requirement shows): its send's id. */
  email?: string;
  /** The task's chat (a phone's tab). */
  chat?: boolean;
}

/** The requirement the person may answer now: Approve, Send back. */
export function decisionOf(t: TaskData): TaskRequirement | null {
  return t.to_leave?.find((r) => !r.met && (r.may.includes('ok') || r.may.includes('no'))) ?? null;
}

/** The files a requirement shows: the page it approves, and each `page:` or `file:` it names. */
export function thingsOf(r: TaskRequirement | null): Set<string> {
  const out = new Set<string>();
  if (!r) return out;
  for (const id of [r.approval?.file_id, r.page?.file_id, ...(r.approval?.pages ?? []).map((p) => p.file_id), ...(r.page?.pages ?? []).map((p) => p.file_id)])
    if (id) out.add(id);
  for (const ref of r.shows ?? []) {
    const m = /^(?:page|file):([^@]+)/.exec(ref);
    if (m?.[1]) out.add(m[1]);
  }
  return out;
}

/** The email a requirement shows (`email:<id>`): its send's id. */
export const emailOf = (r: TaskRequirement | null | undefined): string | null => r?.shows?.find((x) => x.startsWith('email:'))?.slice(6) || null;

/** What waits on the person comes first, marked with the Dot (plan/approvals.html): the page, the file or the email it
    asks about. Then the stage's other pages and Markdown files, each with its state (more.js taskflow); the task's
    other files but its pictures (Details walks those), so a PDF, a Word file or a sound opens as a tab too; the other
    emails its requirements show; the chat (a phone's tab); then Details. A page linked in two roles is one tab. */
export function tabsOf(t: TaskData, files: TaskFileView[] = []): TaskTab[] {
  const decision = decisionOf(t);
  const asked = thingsOf(decision);
  const used = new Set<string>([DETAILS, CHAT]);
  const free = (name: string) => {
    let slug = slugOf(name);
    while (used.has(slug)) slug += '-2';
    used.add(slug);
    return slug;
  };
  const seen = new Set<string>();
  const docs = (t.documents ?? []).flatMap((doc): TaskTab[] => {
    if (seen.has(doc.file_id)) return [];
    seen.add(doc.file_id);
    const slug = free(doc.name);
    const ok = doc.approved_version !== undefined && doc.approved_version === doc.version && !doc.changed_since_ok;
    return [{ slug, label: tabLabel(doc.title || doc.name), mark: asked.has(doc.file_id) ? 'ask' : ok ? 'ok' : 'none', doc }];
  });
  const others = files
    .filter((f) => !f.archived && !seen.has(f.id) && (asked.has(f.id) || !(f.versions.find((v) => v.n === f.current_version) ?? f.versions.at(-1))?.mime.startsWith('image/')))
    .map((f): TaskTab => ({ slug: free(f.title || f.name), label: tabLabel(f.title || f.name), file: f, ...(asked.has(f.id) && { mark: 'ask' as const }) }));
  const emails: TaskTab[] = [];
  for (const r of t.to_leave ?? []) {
    const email = emailOf(r);
    if (!email || emails.some((x) => x.email === email)) continue;
    emails.push({ slug: free('Email'), label: 'Email', mark: r === decision ? 'ask' : r.met ? 'ok' : 'none', email });
  }
  const all = [...docs, ...others, ...emails];
  return [
    ...all.filter((x) => x.mark === 'ask'),
    ...all.filter((x) => x.mark !== 'ask'),
    ...(t.conversation_id ? [{ slug: CHAT, label: 'Chat', chat: true }] : []),
    { slug: DETAILS, label: 'Details' },
  ];
}

/** A document the server keeps as a page (its pages read answers for it); a Markdown file is a plain file. */
export const isPage = (doc: TaskDocument): boolean => doc.kind !== 'markdown';

/** With no tab in the address: what waits on the person, else the first page, else Details (never the chat). */
export function defaultTab(tabs: TaskTab[]): string {
  return (tabs.find((x) => x.mark === 'ask') ?? tabs.find((x) => !x.chat))?.slug ?? DETAILS;
}

/** An email's OK task, as the server titles it ("Send: <subject> → <first recipient> +n", backend/email_sending.py
    send_title): the subject without "Send:" and its "Re:"/"Fwd:", and who it goes to. Null for any other title. */
export function sendTitle(title: string): { subject: string; to?: string } | null {
  if (!/^send:/i.test(title)) return null;
  const rest = title.replace(/^send:\s*/i, '');
  const at = rest.lastIndexOf(' → ');
  const subject = (at < 0 ? rest : rest.slice(0, at)).replace(/^((re|fwd?|fw):\s*)+/i, '');
  return { subject: subject || rest, ...(at >= 0 && { to: rest.slice(at + 3).replace(/\s\+\d+$/, '') }) };
}

/** The decision's name for what it decides: the same name as its tab, with its version past the first ("Spec v2"),
    else the requirement's own line. An email is named by `to` ("Email to Dana Ruiz"). */
export function decisionName(t: TaskData, r: TaskRequirement, to?: string): string {
  if (emailOf(r)) return to ? `Email to ${to}` : 'Email';
  const file = r.approval?.file_id ?? r.page?.file_id;
  const doc = file ? t.documents?.find((d) => d.file_id === file) : undefined;
  const name = r.approval?.document || (doc && tabLabel(doc.title || doc.name)) || r.line;
  const v = r.approval?.version ?? r.page?.version ?? doc?.version;
  return v && v > 1 && (r.approval || doc) ? `${name} v${v}` : name;
}

/** What Approve does, in three words or fewer (plan/approvals.html): an email sends; the last thing a stage waits on
    moves the task on. Nothing when it does neither. */
export function effectOf(t: TaskData, r: TaskRequirement): string {
  if (emailOf(r)) return 'Sends now';
  const rest = (t.to_leave ?? []).filter((x) => !x.met && x.requirement_id !== r.requirement_id);
  return !rest.length && t.next?.name ? `Moves to ${t.next.name}` : '';
}

/** A request the person asked changes on: it waits on the agent's new version (plan/approvals.html, Changes asked). */
export function changesAskedOf(t: TaskData, me: string | undefined): TaskRequirement | null {
  return (me && t.to_leave?.find((r) => !r.met && r.answer?.value === 'no' && r.answer.by === me)) || null;
}

/** A request whose answer is being sent (answerAsk shows it at once, before the server takes it). */
export function answeringOf(t: TaskData): TaskRequirement | null {
  return t.to_leave?.find((r) => r.answer?.id === PENDING_ANSWER) ?? null;
}

/** "Dana Ruiz" from "Dana Ruiz <dana@example.com>"; a bare address stays as it is. */
export const personOf = (address: string): string => /^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$/.exec(address)?.[1] ?? address.trim();

/** The address of a task's tab: under its pipeline, or inside Flow. */
export function tabAddress(t: Pick<TaskData, 'number' | 'id' | 'pipeline_id'>, tab: string | undefined, flow: boolean): { to: string; search: { tab?: string } } {
  const n = String(t.number ?? t.id);
  if (flow) return { to: `/flow/${n}`, search: tab ? { tab } : {} };
  const base = `/tasks/${encodeURIComponent(t.pipeline_id ?? 'all')}/${n}`;
  return { to: tab ? `${base}/${tab}` : base, search: {} };
}

/** A refusal's reason, as the action layer carries it ("code: reason", then what is missing a line each). */
export function reasonOf(error: string | undefined): string {
  if (!error) return 'Not saved';
  if (error === 'not_allowed') return 'You can’t do that on this task';
  if (error.startsWith('network')) return 'Needs a connection';
  const head = error.split('\n')[0] ?? '';
  const i = head.indexOf(': ');
  return (i >= 0 && head.slice(i + 2).trim()) || 'Not saved';
}

/** A refused press's toast: "Not saved", then why. */
export const failedToast = (r: Pick<Result, 'error' | 'why'>): RefusalToast => refusal(r, 'Not saved', 'You can’t do that on this task');

export interface Refusal {
  code: string;
  reason: string;
  /** What the stage still needs, one line each ("QA report: not approved"). */
  missing: string[];
}

export function refusalOf(error: string | undefined): Refusal {
  const [head = '', ...rest] = (error ?? '').split('\n');
  const i = head.indexOf(': ');
  return {
    code: i >= 0 ? head.slice(0, i) : head,
    reason: reasonOf(error),
    missing: rest.filter((l) => l.startsWith('- ')).map((l) => l.slice(2)),
  };
}

/** The stages before the one it is in: where Move back can take it. */
export function earlierStages<S extends { id: string }>(stages: readonly S[], at: string | null | undefined): S[] {
  const i = stages.findIndex((s) => s.id === at);
  return i > 0 ? stages.slice(0, i) : [];
}
