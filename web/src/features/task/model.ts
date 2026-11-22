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
  /** The version the ask pinned (`file:<id>@<n>`): the tab shows it, not a newer one. */
  version?: number;
  /** A file the ask is about that is not one of this task's (another task's): read by its id, as the library reads it. */
  other?: string;
}

const answerable = (r: TaskRequirement) => !r.met && (r.may.includes('ok') || r.may.includes('no'));

/** Whether a requirement belongs where `email` is open: that email's own (a string), or none's (null: a page off the
    email tabs never shows an email's OK); undefined: any. */
const belongs = (r: TaskRequirement, email: string | null | undefined) => email === undefined || (emailOf(r) ?? null) === email;

/** The requirement the person may answer now: Approve, Send back. With `email` (the email tab open), only that email's
    own OK, never another ask, so two sends on one task are each approved from their own tab; with null (another tab),
    never an email's OK. */
export function decisionOf(t: TaskData, email?: string | null): TaskRequirement | null {
  return (t.to_leave ?? []).find((r) => answerable(r) && belongs(r, email)) ?? null;
}

/** The task's requirement `rid` in any state (an answer being sent clears its `may`), or null when the read lacks it. */
export const requirementOf = (t: TaskData, rid: string): TaskRequirement | null => (t.to_leave ?? []).find((r) => r.requirement_id === rid) ?? null;

/** The task's open requirement `rid` the person may answer now, or null (answered, withdrawn, or not read yet). */
export const askOf = (t: TaskData, rid: string): TaskRequirement | null => (t.to_leave ?? []).find((r) => r.requirement_id === rid && answerable(r)) ?? null;

/** The files a requirement shows: the page it approves, and each `page:` or `file:` it names. */
export function thingsOf(r: TaskRequirement | null): Set<string> {
  const out = new Set<string>();
  if (!r) return out;
  for (const id of [r.approval?.file_id, r.page?.file_id, r.approval?.file_id || r.page?.file_id ? null : r.earlier?.file_id, ...(r.approval?.pages ?? []).map((p) => p.file_id), ...(r.page?.pages ?? []).map((p) => p.file_id)])
    if (id) out.add(id);
  for (const ref of r.shows ?? []) {
    const m = /^(?:page|file):([^@]+)/.exec(ref);
    if (m?.[1]) out.add(m[1]);
  }
  return out;
}

/** What a requirement is about: the page a stage's OK names, else the first page or file it shows (an OK's subject,
    task_asks pins it first). A review page it also shows is not it. */
export function targetOf(r: TaskRequirement | null): { id: string; version?: number } | null {
  // A stage's OK whose page was pinned on an earlier stage says it in `earlier` alone.
  const from = r?.approval?.file_id ? r.approval : r?.page?.file_id ? r.page : r?.earlier;
  const own = from?.file_id;
  const at = from?.version;
  if (own) return { id: own, ...(at && { version: at }) };
  for (const ref of r?.shows ?? []) {
    const m = /^(?:page|file):([^@]+)(?:@v?(\d+))?/.exec(ref);
    if (m?.[1]) return { id: m[1], ...(m[2] && { version: Number(m[2]) }) };
  }
  return null;
}

/** The second review (a sub-task filed with `--role review`): "Review running" while one is open, then "Reviewed". */
export function reviewOf(t: Pick<TaskData, 'children'>): string | null {
  const reviews = (t.children ?? []).filter((c) => c.role === 'review');
  if (reviews.some((c) => c.status === 'open')) return 'Review running';
  return reviews.some((c) => c.status === 'done') ? 'Reviewed' : null;
}

/** The email a requirement shows (`email:<id>`): its send's id. */
export const emailOf = (r: TaskRequirement | null | undefined): string | null => r?.shows?.find((x) => x.startsWith('email:'))?.slice(6) || null;

/** What waits on the person comes first, marked with the Dot (plan/approvals.html): the page, the file or the email it
    asks about, the one it is about (`targetOf`, at the version it pinned) before the others it shows; one that is not
    this task's opens as a tab of its own once the files are read (`files` given), never the review page beside it. Then the stage's other pages and Markdown files, each with its state (more.js taskflow); the task's
    other files but its pictures (Details walks those), so a PDF, a Word file or a sound opens as a tab too; the other
    emails its requirements show; the chat (a phone's tab); then Details. A page linked in two roles is one tab.
    `decision`: what the tabs are marked for and pinned at: the task's first ask that is not an email's (an email's OK
    has its own tab), unless a caller names another (a card for one ask). When an email's OK comes first, its tab still
    comes first. */
export function tabsOf(t: TaskData, files?: TaskFileView[], chosen?: TaskRequirement | null): TaskTab[] {
  // A caller's own ask (an address's `ask`, a card's) leads; else an email's OK that comes first keeps its tab first.
  const decision = chosen === undefined ? decisionOf(t, null) : chosen;
  const emailFirst = chosen === undefined && !!emailOf(decisionOf(t));
  const asked = thingsOf(decision);
  const target = targetOf(decision);
  const pinned = (id: string) => (target?.id === id && target.version ? { version: target.version } : {});
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
    return [{ slug, label: tabLabel(doc.title || doc.name), mark: asked.has(doc.file_id) ? 'ask' : ok ? 'ok' : 'none', doc, ...pinned(doc.file_id) }];
  });
  const others = (files ?? [])
    .filter((f) => !f.archived && !seen.has(f.id) && (asked.has(f.id) || !(f.versions.find((v) => v.n === f.current_version) ?? f.versions.at(-1))?.mime.startsWith('image/')))
    .map((f): TaskTab => ({ slug: free(f.title || f.name), label: tabLabel(f.title || f.name), file: f, ...pinned(f.id), ...(asked.has(f.id) && { mark: 'ask' as const }) }));
  const here = [...docs, ...others].some((x) => (x.doc?.file_id ?? x.file?.id) === target?.id);
  const away: TaskTab[] = target && files && !here ? [{ slug: free('File'), label: 'File', mark: 'ask', other: target.id, ...pinned(target.id) }] : [];
  const emails: TaskTab[] = [];
  for (const r of t.to_leave ?? []) {
    const email = emailOf(r);
    if (!email || emails.some((x) => x.email === email)) continue;
    emails.push({ slug: free('Email'), label: 'Email', mark: answerable(r) ? 'ask' : r.met ? 'ok' : 'none', email });
  }
  const all = [...docs, ...others, ...away, ...emails];
  // What the ask is about opens first, before any other page it shows (a review page).
  const isTarget = (x: TaskTab) => !!target && (x.doc?.file_id ?? x.file?.id ?? x.other) === target.id;
  const asks = all.filter((x) => x.mark === 'ask');
  return [
    ...(emailFirst ? asks.filter((x) => x.email) : []),
    ...asks.filter((x) => isTarget(x)),
    ...asks.filter((x) => !isTarget(x) && !(emailFirst && x.email)),
    ...all.filter((x) => x.mark !== 'ask'),
    ...(t.conversation_id ? [{ slug: CHAT, label: 'Chat', chat: true }] : []),
    { slug: DETAILS, label: 'Details' },
  ];
}

/** A document the server keeps as a page (its pages read answers for it); a Markdown file is a plain file. */
export const isPage = (doc: TaskDocument): boolean => doc.kind !== 'markdown';

/** With no tab in the address: what waits on the person, else, when `chat` (a phone, with a ❓ for the person in the
    task's chat: asksInChat), the chat, where they answer it; else the first page, else Details. */
export function defaultTab(tabs: TaskTab[], chat = false): string {
  const open = tabs.find((x) => x.mark === 'ask') ?? (chat ? tabs.find((x) => x.chat) : undefined) ?? tabs.find((x) => !x.chat);
  return open?.slug ?? DETAILS;
}

/** Whether it is `me`'s turn on the task for a ❓ an agent put to them in its chat (the turn's requirement is a question
    mark, `qmark:<id>`): they answer it there, in the chat's own message box. */
export const asksInChat = (t: Pick<TaskData, 'conversation_id' | 'turn'>, me: string | undefined): boolean =>
  !!me && !!t.conversation_id && t.turn?.state === 'person' && t.turn.who === me && !!t.turn.requirement_id?.startsWith('qmark:');

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

const sendDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });

/** What Approve does for an email held for a later day ("Sends Nov 25"): the preview's `not_before` (its matter's not
    before, in the company's time with its offset, so its date part is the company's day, whatever the browser's);
    null when it has none and goes once its undo window is over. */
export function heldUntil(notBefore: string | null | undefined): string | null {
  const day = /^\d{4}-\d{2}-\d{2}/.exec(notBefore ?? '')?.[0];
  return day ? `Sends ${sendDay.format(new Date(`${day}T12:00:00Z`))}` : null;
}

/** What Approve does, in three words or fewer (plan/approvals.html): an email sends; the last thing a stage waits on
    moves the task on. Nothing when it does neither. */
export function effectOf(t: TaskData, r: TaskRequirement): string {
  if (emailOf(r)) return 'Sends now';
  const rest = (t.to_leave ?? []).filter((x) => !x.met && x.requirement_id !== r.requirement_id);
  return !rest.length && t.next?.name ? `Moves to ${t.next.name}` : '';
}

/** A request the person asked changes on: it waits on the agent's new version (plan/approvals.html, Changes asked). */
export function changesAskedOf(t: TaskData, me: string | undefined, email?: string | null): TaskRequirement | null {
  return (me && t.to_leave?.find((r) => !r.met && r.answer?.value === 'no' && r.answer.by === me && belongs(r, email))) || null;
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
