/* ui/app/pipeline-api.ts — The one client the pipeline views call: fetch wrappers over the pipeline routes. Every route
   path and payload the UI codes against is here, so a route that changes is changed in this file only. The server
   sends requirement states as met / waiting / out_of_date / failed / none; this file maps `none` to the UI's `missing`
   and reshapes the review queue and manager rail into the view models the views read. Classic script: its globals are
   shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type PipelineReqState = 'met' | 'waiting' | 'out_of_date' | 'failed' | 'missing';

/** One requirement on a ticket: the board's dot (id, name, state) or the ticket's full row, mapped to this shape. */
interface PipelineRequirement {
  id: string;
  name: string;
  state: PipelineReqState;
  /** The actor the requirement waits on (the reviewer), or null. The board dots do not carry it. */
  next_actor: string | null;
  /** What stands on the ticket for it: the decision's reason, or the attachment's source. */
  detail?: string;
  /** Whether this person may mark it met: the pipeline's owner or an Admin, the rule the task types' can_edit_rules carries (pipelineMayEditRules). */
  can_mark_met?: boolean;
  /** attachment, field, approval or run. Only the ticket's detail rows carry it. */
  kind?: string;
  /** The evidence attached to it, newest first (attachment requirements, on the ticket page). */
  attachments?: PipelineReqAttachment[];
  /** Who passed it and under which requirement version, when a decision met it (the ticket page). */
  passed?: { by: string | null; version: number; current: number };
}

/** One piece of evidence attached to a requirement, as the server's read view sends it (views in
    backend/requirement_attachments.py). `href` is the content's immutable address: a company file's pinned version, a blob,
    or a link's own URL. `format` says how to show it. */
interface PipelineReqAttachment {
  id: string;
  source: 'file' | 'blob' | 'link' | 'check_run' | 'receipt';
  format: 'image' | 'video' | 'page' | 'file' | 'link';
  name: string;
  href: string | null;
  url: string | null;
  file_id: string | null;
  file_version: number | null;
  digest: string;
  work_version: number;
  attached_by: string | null;
  created_at: string;
}

interface PipelineAttachment {
  id: string;
  label: string;
  kind: 'image' | 'video' | 'page' | 'file' | 'link' | 'check' | 'text';
  url?: string;
  detail?: string;
}

/** The ▶ queue's forms: a requirement to review, a question of the reader's that holds a card, or a subtask of theirs. */
type PipelineReviewForm = 'review' | 'question' | 'subtask';

/** One item of the ▶ queue: a requirement of one ticket that waits on this reviewer, or (question, subtask) a task of the
    reader's on that ticket. Built from the queue row. A question or subtask has no requirement and no attachments. */
interface PipelineReviewItem {
  form: PipelineReviewForm;
  /** The ticket on the board: the card a question holds or a subtask belongs to, else the reviewed ticket. */
  task_id: string;
  /** The requirement reviewed (review items only; '' for the other forms). */
  requirement_id: string;
  /** The question or subtask itself (the other forms). */
  subject_id?: string | null;
  subject_title?: string;
  subject_version?: number;
  /** A question's ask, as the question card reads it (the same shape as a Needs you item's ask). */
  ask?: Record<string, unknown> | null;
  /** The card a question holds ("holds up" on the question card). */
  holds_up?: { id: string; title: string } | null;
  since?: string;
  task_title?: string;
  stage_name?: string;
  pipeline_id?: string;
  /** The stage the item waits at: the editor's stage the reviewer is working through. */
  stage_id?: string;
  /** The assignee for "Message <assignee>", or null. */
  assignee?: { id: string; name: string } | null;
  /** The pipeline's manager bot for "Message <manager>", or null (the button is then not offered). */
  manager: { id: string; name: string } | null;
  /** The card, shown when nothing is attached: the ticket's body, acceptance criteria and checkpoint. */
  body: string;
  acceptance_criteria: string[];
  checkpoint: string;
  /** Why it is the viewer's, in words: the requirement and the pipeline ("Screenshots · Dev tickets"), with who sent it. */
  why: string;
  /** The bot's note, or empty. */
  bot_note: string;
  /** The reviewer the item waits on (the claimant, else the configured one). */
  reviewer_id?: string | null;
  /** Whether the viewer may mark it met by hand. */
  can_mark_met?: boolean;
  /** Whether the viewer may send it Not validated (the server's follow-up rule, D66). */
  can_not_validate?: boolean;
  /** The requirement's attachment (never a file on the ticket), and the stage visit the item shows: Not validated names
      them back to the server. */
  attachment_id?: string | null;
  stage_visit_id?: string | null;
  attachments: PipelineAttachment[];
  /** Another reviewer holding the item, or null. `label` is the server's words for it ("Ana is reviewing"). */
  claim: { by: string; label: string } | null;
  /** Where Approve moves the ticket on the stage's last requirement, or null. */
  moves_to?: string | null;
  /** Late items sort first in the queue. */
  late: boolean;
  /** The work version, attachment digest and requirement version the reviewer saw. Approve sends them back. */
  work_version: number;
  attachment_digest: string | null;
  requirement_version: number;
}

interface PipelineReviewQueue {
  items: PipelineReviewItem[];
}

interface PipelineApproveResult {
  decision_id: string;
  moved_to?: string | null;
}

interface PipelineManagerRun {
  id: string;
  started_at: string;
  status: 'ok' | 'failed' | 'skipped';
  error?: string | null;
}

interface PipelineManagerLate {
  task_id: string;
  title: string;
  stage_name: string;
  age_minutes: number;
  /** The stage's "Warn after", in minutes. */
  limit_minutes: number;
}

interface PipelineManagerLogLine {
  at: string;
  text: string;
  kind: 'error' | 'warn' | 'info';
  task_id?: string | null;
  /** What the manager did (an action row), e.g. propose_rule; absent for a pass. */
  action?: string;
}

interface PipelineManagerView {
  /** The manager bot's reference (task_types.manager), or null when the pipeline has none. */
  manager: string | null;
  last_run: PipelineManagerRun | null;
  missed_runs: number;
  late: PipelineManagerLate[];
  log: PipelineManagerLogLine[];
  actions: { id: string; label: string }[];
}

/** The server's requirement state, as the ticket and board send it. */
type ServerReqState = 'met' | 'waiting' | 'out_of_date' | 'failed' | 'none';

interface ServerDot {
  id: string;
  name: string;
  state: ServerReqState;
}

/** One requirement of GET /api/v2/tasks/{id}/requirements (the ticket's detail row). `decision` is who decided it (the judge
    and its actor) and under which requirement version; `attachments` is the newest few, as read views. */
interface ServerRequirementDetail {
  id: string;
  name: string;
  kind: string;
  stage_id: string;
  stage: string;
  state: ServerReqState;
  why: string | null;
  reviewed_by: string;
  reviewer_id: string | null;
  version: number;
  decision: { id: string; judge: string; judge_actor: string | null; verdict: string; requirement_version: number; at: string } | null;
  attachment: { id: string; source: 'file' | 'blob' | 'link'; digest: string; by: string; at: string } | null;
  attachments: PipelineReqAttachment[];
}

interface ServerQueueItem {
  form: 'review';
  task_id: string;
  title: string;
  pipeline_id: string;
  pipeline: string;
  stage_id: string;
  stage: string;
  requirement_id: string;
  requirement: string;
  requirement_version: number;
  kind: string;
  work_version: number;
  /** What the item shows, with its content address (queue read view). `ticket` is a file on the ticket itself, shown
      when nothing is attached to the requirement: it is not the requirement's evidence. */
  attachment: { id: string; source: 'file' | 'blob' | 'link' | 'ticket'; url: string | null; file_id?: string | null;
    file_version?: number | null; format: PipelineReqAttachment['format'] | 'text'; name: string; href: string | null } | null;
  attachment_digest: string | null;
  why: string;
  since: string;
  late: boolean;
  claimed_by: string | null;
  claimed_by_me: boolean;
  assignee: { id: string; name: string } | null;
  /** The pipeline's manager bot, or null. */
  manager: { id: string; name: string } | null;
  /** The ticket's body, acceptance criteria and checkpoint: the card shown when nothing is attached. */
  body: string;
  acceptance_criteria: string[];
  checkpoint: string;
  bot_note: string;
  moves_to: { stage_id: string; name: string } | null;
  /** The claimant, else the configured reviewer. */
  reviewer_id: string | null;
  can_mark_met: boolean;
  can_not_validate: boolean;
  stage_visit_id: string | null;
}

/** A question or subtask of the reader's on an open card (backend pipeline_review._held_items). */
interface ServerHeldItem {
  form: 'question' | 'subtask';
  task_id: string;
  title: string;
  number: number | null;
  pipeline_id: string;
  pipeline: string;
  stage_id: string | null;
  stage: string;
  subject_id: string;
  subject_title: string;
  subject_version: number;
  ask: Record<string, unknown> | null;
  holds_up: { id: string; title: string } | null;
  why: string;
  since: string;
  late: boolean;
  reviewer_id: string;
}

interface ServerRun {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: 'ok' | 'failed' | 'skipped';
  error: string | null;
}

interface ServerAction {
  id: string;
  task_id: string | null;
  action: string;
  target_actor: string | null;
  note: string | null;
  created_at: string;
}

interface ServerRail {
  pipeline_id: string;
  manager?: string | null;
  missed_runs: number;
  last_run: ServerRun | null;
  /** limit_minutes (the stage's Warn after) is preferred when the server sends it; limit_hours stays for older servers. */
  over_limit: { task_id: string; title: string; stage: string; hours: number; limit_hours: number; limit_minutes?: number; since: string }[];
  log: (ServerRun | ServerAction)[];
}

const pipelineRoute = (path: string): string => '/v2/pipelines' + path;
/** A review write: the requirement of one ticket, under the pipelines prefix (C's route base). */
const reviewPath = (taskId: string, requirementId: string): string =>
  `/tasks/${encodeURIComponent(taskId)}/requirements/${encodeURIComponent(requirementId)}`;

const REQ_STATE: Record<ServerReqState, PipelineReqState> = {
  met: 'met', waiting: 'waiting', out_of_date: 'out_of_date', failed: 'failed', none: 'missing',
};

/** A board dot as the requirement the view shows: the server's `none` is the UI's `missing`; a dot already mapped passes
    through. The board has no reviewer, so next_actor is null. */
function requirementFromDot(dot: ServerDot | PipelineRequirement): PipelineRequirement {
  const state = dot.state as string;
  const mapped = (REQ_STATE as Record<string, PipelineReqState>)[state] ?? (state as PipelineReqState);
  return { ...dot, state: mapped, next_actor: ('next_actor' in dot ? dot.next_actor : null) ?? null } as PipelineRequirement;
}

/** Who sent the item to the viewer, for the server's code; a reviewer's own item names nobody. */
const REVIEW_SENT_BY: Record<string, string> = { sent_back: 'Sent back to you', backup: 'Asked as backup' };

/** "Why you": who sent it (if anyone), the requirement, and the pipeline, as "Spec approved · Product". */
function reviewWhy(row: ServerQueueItem): string {
  return [REVIEW_SENT_BY[row.why], row.requirement, row.pipeline].filter(Boolean).join(' · ');
}

/** A queue row, as the review view shows it. The row has one attachment; the view takes a list. */
function reviewItemFromRow(row: ServerQueueItem): PipelineReviewItem {
  const a = row.attachment;
  return {
    form: 'review',
    task_id: row.task_id,
    requirement_id: row.requirement_id,
    task_title: row.title,
    stage_name: row.stage,
    pipeline_id: row.pipeline_id,
    stage_id: row.stage_id,
    assignee: row.assignee ?? null,
    manager: row.manager ?? null,
    body: row.body ?? '',
    acceptance_criteria: row.acceptance_criteria ?? [],
    checkpoint: row.checkpoint ?? '',
    why: reviewWhy(row),
    bot_note: row.bot_note ?? '',
    reviewer_id: row.reviewer_id ?? null,
    can_mark_met: !!row.can_mark_met,
    can_not_validate: !!row.can_not_validate,
    attachments: a
      ? [{ id: a.id, label: a.name || 'Attachment', kind: a.format, ...(a.href ? { url: a.href } : {}) }]
      : [],
    claim: row.claimed_by && !row.claimed_by_me ? { by: row.claimed_by, label: `${actorLabel(row.claimed_by)} is reviewing` } : null,
    moves_to: row.moves_to?.name ?? null,
    late: row.late,
    work_version: row.work_version,
    attachment_digest: row.attachment_digest,
    requirement_version: row.requirement_version,
    attachment_id: a && a.source !== 'ticket' ? a.id : null,
    stage_visit_id: row.stage_visit_id ?? null,
  };
}

/** A ticket's requirement row, as the ticket view shows it: what stands, who decided it, under which version, and the
    evidence attached (its newest few, each with its content address). */
function requirementFromDetail(row: ServerRequirementDetail): PipelineRequirement {
  const detail = row.state === 'met' || row.state === 'none' ? undefined : row.why ?? undefined;
  const decision = row.decision;
  const passed = row.state === 'met' && decision
    ? { by: decision.judge_actor, version: decision.requirement_version, current: row.version }
    : undefined;
  return {
    id: row.id,
    name: row.name,
    state: REQ_STATE[row.state] ?? 'missing',
    next_actor: row.state === 'met' ? null : row.reviewer_id,
    ...(detail ? { detail } : {}),
    kind: row.kind,
    attachments: row.attachments ?? [],
    ...(passed ? { passed } : {}),
  };
}

/** The manager rail, as the manager view shows it: one log, errors first, then newest first. */
function managerViewFromRail(rail: ServerRail): PipelineManagerView {
  const log: PipelineManagerLogLine[] = rail.log.map(entry => {
    if ('started_at' in entry) {
      return entry.status === 'failed'
        ? { at: entry.started_at, text: entry.error || 'Manager pass failed', kind: 'error' as const }
        : { at: entry.started_at, text: `Manager pass ${entry.status}`, kind: 'info' as const };
    }
    return {
      at: entry.created_at,
      text: entry.note ? `${entry.action}: ${entry.note}` : entry.action,
      kind: 'info' as const,
      task_id: entry.task_id,
      action: entry.action,
    };
  });
  const rank = (line: PipelineManagerLogLine): number => (line.kind === 'error' ? 0 : 1);
  log.sort((a, b) => rank(a) - rank(b) || b.at.localeCompare(a.at));
  return {
    manager: rail.manager ?? null,
    last_run: rail.last_run,
    missed_runs: rail.missed_runs,
    late: rail.over_limit.map(item => ({
      task_id: item.task_id,
      title: item.title,
      stage_name: item.stage,
      age_minutes: Math.round(item.hours * 60),
      limit_minutes: item.limit_minutes ?? Math.round(item.limit_hours * 60),
    })),
    log,
    actions: [],
  };
}

/** The ticket's requirements on the board (their dots), for the card's requirement dots. */
function pipelineBoardDots(dots: (ServerDot | PipelineRequirement)[]): PipelineRequirement[] {
  return dots.map(requirementFromDot);
}

/** The requirements that count on one ticket, with who decided and what is attached: the ticket page's list. One read
    carries the attachments too, so the page makes no request per requirement. */
async function pipelineTaskRequirements(taskId: string): Promise<PipelineRequirement[]> {
  const res = await get(`/v2/tasks/${encodeURIComponent(taskId)}/requirements`) as { requirements: ServerRequirementDetail[] };
  return res.requirements.map(requirementFromDetail);
}

/** The reviewer's queue for one pipeline: Late items first, then the oldest. */
/** A question or subtask of the reader's, as the ▶ view shows it. The review-only fields take their empty values. */
function heldItemFromRow(row: ServerHeldItem): PipelineReviewItem {
  return {
    form: row.form,
    task_id: row.task_id,
    requirement_id: '',
    task_title: row.title,
    stage_name: row.stage,
    pipeline_id: row.pipeline_id,
    stage_id: row.stage_id ?? '',
    subject_id: row.subject_id,
    subject_title: row.subject_title,
    subject_version: row.subject_version,
    ask: row.ask,
    holds_up: row.holds_up,
    since: row.since,
    assignee: null,
    manager: null,
    body: '',
    acceptance_criteria: [],
    checkpoint: '',
    why: row.why,
    bot_note: '',
    reviewer_id: row.reviewer_id,
    can_mark_met: false,
    can_not_validate: false,
    attachments: [],
    claim: null,
    moves_to: null,
    late: row.late,
    work_version: 0,
    attachment_digest: null,
    requirement_version: 0,
    attachment_id: null,
    stage_visit_id: null,
  };
}

async function pipelineReviewQueue(pipelineId: string): Promise<PipelineReviewQueue> {
  const res = await get(pipelineRoute(`/${encodeURIComponent(pipelineId)}/review/queue`)) as { items: (ServerQueueItem | ServerHeldItem)[] };
  return { items: res.items.map(row => row.form === 'review' ? reviewItemFromRow(row) : heldItemFromRow(row)) };
}

/** Done on a subtask of the reader's: the task is finished, as its own Done does (the Decide card's call, decide.ts). */
async function pipelineSubjectDone(item: PipelineReviewItem): Promise<void> {
  await post(`/v2/tasks/${encodeURIComponent(item.subject_id ?? '')}`,
    { status: 'done', version: item.subject_version ?? 1 }, newRequestId());
}

/** Send back on a subtask of the reader's: it is declined with the note, which goes to its owner as the answer does. */
async function pipelineSubjectBack(item: PipelineReviewItem, note: string): Promise<void> {
  await post(`/v2/tasks/${encodeURIComponent(item.subject_id ?? '')}`,
    { status: 'declined', version: item.subject_version ?? 1, note }, newRequestId());
}

/**
 * Approve a requirement, as the reviewer saw it: the work version, attachment digest and requirement version come from
 * the item. A 409 means the work changed since the item was shown: the caller reloads the queue.
 */
async function pipelineApprove(item: PipelineReviewItem, note: string): Promise<PipelineApproveResult> {
  const res = await post(pipelineRoute(reviewPath(item.task_id, item.requirement_id) + '/approve'), {
    work_version: item.work_version,
    attachment_digest: item.attachment_digest,
    requirement_version: item.requirement_version,
    note: note || null,
  }) as { decision_id: string; advanced: boolean };
  return { decision_id: res.decision_id, moved_to: res.advanced ? item.moves_to ?? 'next stage' : null };
}

/** Start reviewing an item for ten minutes. A 409 means another reviewer holds it; the caller shows who. */
async function pipelineClaim(taskId: string, requirementId: string): Promise<{ expires_at: string }> {
  return post(pipelineRoute(reviewPath(taskId, requirementId) + '/claim'), {}) as Promise<{ expires_at: string }>;
}

/** Take back an approval made in the last 10 seconds. */
async function pipelineUndo(decisionId: string): Promise<{ ok: boolean }> {
  const res = await post(pipelineRoute(`/decisions/${encodeURIComponent(decisionId)}/undo`), {}) as { undone: boolean };
  return { ok: res.undone };
}

/** Send the item back to its assignee with a note. The requirement reopens; nothing is approved. */
async function pipelineSendBack(taskId: string, requirementId: string, note: string): Promise<{ decision_id: string }> {
  return post(pipelineRoute(reviewPath(taskId, requirementId) + '/send-back'), { note }) as Promise<{ decision_id: string }>;
}

/** Validate's "Not validated": the item goes back to the stage before Validate and one follow-up ticket is filed. */
async function pipelineNotValidated(item: PipelineReviewItem, reason: string):
  Promise<{ decision_id: string; moved_back_to: string | null; follow_up: string | null }> {
  return post(pipelineRoute(reviewPath(item.task_id, item.requirement_id) + '/not-validated'), {
    note: reason,
    work_version: item.work_version,
    requirement_version: item.requirement_version,
    attachment_id: item.attachment_id ?? null,
    attachment_digest: item.attachment_digest,
    stage_visit_id: item.stage_visit_id ?? null,
  }) as Promise<{ decision_id: string; moved_back_to: string | null; follow_up: string | null }>;
}

/** Message the pipeline manager about one item. The manager decides or forwards it. */
async function pipelineMessageManager(taskId: string, requirementId: string, text: string): Promise<{ ok: boolean }> {
  await post(pipelineRoute(reviewPath(taskId, requirementId) + '/message-manager'), { text });
  return { ok: true };
}

/** Mark one requirement met by hand, with a reason. Logged; new work makes it out of date like any approval. */
async function pipelineMarkMet(taskId: string, requirementId: string, why: string): Promise<{ decision_id: string }> {
  return post(pipelineRoute(reviewPath(taskId, requirementId) + '/mark-met'), { why }) as Promise<{ decision_id: string }>;
}

/** How many open tickets at a stage a requirement with this label would apply to right now: the rule editor's note. */
async function pipelineAffectedCount(stageId: string, label: string): Promise<{ count: number; stage: string }> {
  const query = label ? `?label=${encodeURIComponent(label)}` : '';
  return get(`/v2/pipeline-stages/${encodeURIComponent(stageId)}/affected${query}`) as Promise<{ count: number; stage: string }>;
}

/** A live ticket past its stage's Warn after, clocked by the server (the Late list): when its step began, the hours it has
    spent there (holds and waits left out) and the limit. */
interface PipelineLateClock {
  since: string;
  hours: number;
  limit_hours: number;
}

/** Ticket ids asked for per request (GET .../late?ids=): the server answers at most 500 at a time, and a short URL stays well under it. */
const LATE_ASK_CHUNK = 100;

/** The Late clocks of the named tickets of a pipeline (GET .../late?ids=), keyed by task id, from the server's one Late rule.
    Only the tickets on screen are asked for, in chunks, so a pipeline of any size shows its late time for each of them. A
    ticket the server does not clock (not late, or not one this person may see) is absent. It needs no manager. */
async function pipelineLateTickets(pipelineId: string, ids: string[]): Promise<Map<string, PipelineLateClock>> {
  const out = new Map<string, PipelineLateClock>();
  for (let start = 0; start < ids.length; start += LATE_ASK_CHUNK) {
    const part = ids.slice(start, start + LATE_ASK_CHUNK);
    const res = await get(pipelineRoute(`/${encodeURIComponent(pipelineId)}/late?ids=${encodeURIComponent(part.join(','))}`)) as { late?: Record<string, PipelineLateClock> };
    Object.entries(res.late ?? {}).forEach(([id, clock]) => { out.set(id, clock); });
  }
  return out;
}

/** The manager's status for one pipeline: last run, late cards, the log. */
async function pipelineManagerView(pipelineId: string): Promise<PipelineManagerView> {
  const rail = await get(pipelineRoute(`/${encodeURIComponent(pipelineId)}/manager`)) as ServerRail;
  return managerViewFromRail(rail);
}

// ------------------------------------------------------------------ evidence attached to a requirement

const attachPath = (taskId: string, requirementId: string): string =>
  `/v2/tasks/${encodeURIComponent(taskId)}/requirements/${encodeURIComponent(requirementId)}/attachments`;

/** The evidence on one requirement of a ticket, newest first. */
async function pipelineAttachments(taskId: string, requirementId: string): Promise<PipelineReqAttachment[]> {
  const res = await get(attachPath(taskId, requirementId)) as { attachments: PipelineReqAttachment[] };
  return res.attachments;
}

/** Attach a link as evidence. */
async function pipelineAttachLink(taskId: string, requirementId: string, url: string): Promise<void> {
  await post(attachPath(taskId, requirementId), { source: 'link', url });
}

/** Attach a company file by id (its current version). */
async function pipelineAttachFile(taskId: string, requirementId: string, fileId: string, version?: number): Promise<void> {
  await post(attachPath(taskId, requirementId), { source: 'file', file_id: fileId, ...(version ? { file_version: version } : {}) });
}

/** Upload a file to the ticket (POST /v2/tasks/{id}/files), then attach what the server stored as evidence. */
async function pipelineAttachUpload(taskId: string, requirementId: string, file: File): Promise<void> {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('name', file.name);
  const response = await formFetch(API + `/v2/tasks/${encodeURIComponent(taskId)}/files`, form);
  const body = await response.json().catch(() => ({})) as { file_id?: string; version?: number; error?: unknown };
  if (!response.ok || !body.file_id) throw apiFailure(failureBody(body), response);
  await pipelineAttachFile(taskId, requirementId, body.file_id, body.version);
}

// ------------------------------------------------------------------ settings and factories

/** A pipeline's settings, as GET /v2/pipelines/{id}/settings sends them: what the editor's settings block shows. */
interface PipelineSettings {
  id: string;
  name: string;
  prefix: string | null;
  goal: string | null;
  /** A key of judge_models, or null for the default judge. */
  judge_model: string | null;
  visibility: 'company' | 'members';
  factory_id: string | null;
  owner_id: string | null;
  manager_id: string | null;
  /** Actor references, human:<id> or bot:<slug>. Only a members-only pipeline keeps a list. */
  members: string[];
  /** The judge models a pipeline may be given, in the order the server lists them. */
  judge_models: string[];
  /** The judge a pipeline has when its judge_model is null. */
  default_judge_model: string;
}

/** A live factory, as GET /v2/factories lists it. */
interface PipelineFactory {
  id: string;
  name: string;
  goal: string | null;
  owner_id: string | null;
  manager_id: string | null;
  position: number | null;
}

const pipelineSettingsPath = (pipelineId: string): string => pipelineRoute(`/${encodeURIComponent(pipelineId)}/settings`);

/** A pipeline's settings. The editor reads them whole and saves only the fields it changed. */
async function pipelineSettings(pipelineId: string): Promise<PipelineSettings> {
  return get(pipelineSettingsPath(pipelineId), {fresh: true}) as Promise<PipelineSettings>;
}

/**
 * Save some of a pipeline's settings: a field left out stays as it is. An empty goal or a null judge_model clears it;
 * visibility "members" takes the pipeline out of its factory, which the server clears.
 */
async function pipelinePatchSettings(pipelineId: string, fields: Record<string, unknown>): Promise<void> {
  await writeRequest('PATCH', pipelineSettingsPath(pipelineId), fields);
}

/** The live factories, in their order: the editor's Factory choice. */
async function pipelineFactories(): Promise<PipelineFactory[]> {
  const res = await get('/v2/factories') as { factories: PipelineFactory[] };
  return res.factories;
}
