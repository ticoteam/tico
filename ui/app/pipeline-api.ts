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
}

/** One piece of evidence attached to a requirement: a file, an upload or a link. */
interface PipelineReqAttachment {
  id: string;
  source: 'file' | 'blob' | 'link' | 'check_run' | 'receipt';
  url: string | null;
  file_id: string | null;
  by: string | null;
  at: string;
  work_version: number;
}

interface PipelineAttachment {
  id: string;
  label: string;
  kind: 'image' | 'video' | 'page' | 'file' | 'link' | 'check' | 'text';
  url?: string;
  detail?: string;
}

/** One item of the ▶ queue: a requirement of one ticket that waits on this reviewer. Built from the queue row. */
interface PipelineReviewItem {
  task_id: string;
  requirement_id: string;
  task_title?: string;
  stage_name?: string;
  pipeline_id?: string;
  /** The assignee for "Message <assignee>", or null. */
  assignee?: { id: string; name: string } | null;
  why: string;
  /** The bot's note, or empty. */
  bot_note: string;
  /** The reviewer the item waits on (the claimant, else the configured one). */
  reviewer_id?: string | null;
  /** Whether the viewer may mark it met by hand. */
  can_mark_met?: boolean;
  attachments: PipelineAttachment[];
  /** Another reviewer holding the item, or null. */
  claim: { by: string } | null;
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

/** One requirement of GET /api/v2/tasks/{id}/requirements (the ticket's detail row). */
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
  decision: { id: string; verdict: string; requirement_version: number; at: string } | null;
  attachment: { id: string; source: 'file' | 'blob' | 'link'; digest: string; by: string; at: string } | null;
}

interface ServerQueueItem {
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
  attachment: { id: string; source: 'file' | 'blob' | 'link'; url: string | null; file_id?: string | null } | null;
  attachment_digest: string | null;
  why: string;
  since: string;
  late: boolean;
  claimed_by: string | null;
  claimed_by_me: boolean;
  assignee: { id: string; name: string } | null;
  bot_note: string;
  moves_to: { stage_id: string; name: string } | null;
  /** The claimant, else the configured reviewer. */
  reviewer_id: string | null;
  can_mark_met: boolean;
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

const ATTACH_KIND: Record<string, PipelineAttachment['kind']> = { file: 'file', blob: 'file', link: 'link' };

/** A board dot, as the requirement the board shows. The board has no reviewer, so next_actor is null. */
function requirementFromDot(dot: ServerDot): PipelineRequirement {
  return { id: dot.id, name: dot.name, state: REQ_STATE[dot.state] ?? 'missing', next_actor: null };
}

/** A queue row, as the review view shows it. The row has one attachment; the view takes a list. */
function reviewItemFromRow(row: ServerQueueItem): PipelineReviewItem {
  const a = row.attachment;
  return {
    task_id: row.task_id,
    requirement_id: row.requirement_id,
    task_title: row.title,
    stage_name: row.stage,
    pipeline_id: row.pipeline_id,
    assignee: row.assignee ?? null,
    why: row.why,
    bot_note: row.bot_note ?? '',
    reviewer_id: row.reviewer_id ?? null,
    can_mark_met: !!row.can_mark_met,
    attachments: a
      ? [{ id: a.id, label: a.url || a.file_id || 'Attachment', kind: ATTACH_KIND[a.source] ?? 'file', ...(a.url ? { url: a.url } : {}) }]
      : [],
    claim: row.claimed_by && !row.claimed_by_me ? { by: row.claimed_by } : null,
    moves_to: row.moves_to?.name ?? null,
    late: row.late,
    work_version: row.work_version,
    attachment_digest: row.attachment_digest,
    requirement_version: row.requirement_version,
  };
}

/** A ticket's requirement row, as the ticket view shows it. */
function requirementFromDetail(row: ServerRequirementDetail): PipelineRequirement {
  const detail = row.state === 'met' || row.state === 'none' ? undefined : row.why ?? undefined;
  return {
    id: row.id,
    name: row.name,
    state: REQ_STATE[row.state] ?? 'missing',
    next_actor: row.state === 'met' ? null : row.reviewer_id,
    ...(detail ? { detail } : {}),
    kind: row.kind,
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
function pipelineBoardDots(dots: ServerDot[]): PipelineRequirement[] {
  return dots.map(requirementFromDot);
}

/** The requirements that count on one ticket, with who decided and what is attached: the ticket page's list. */
async function pipelineTaskRequirements(taskId: string): Promise<PipelineRequirement[]> {
  const res = await get(`/v2/tasks/${encodeURIComponent(taskId)}/requirements`) as { requirements: ServerRequirementDetail[] };
  return res.requirements.map(requirementFromDetail);
}

/** The reviewer's queue for one pipeline: Late items first, then the oldest. */
async function pipelineReviewQueue(pipelineId: string): Promise<PipelineReviewQueue> {
  const res = await get(pipelineRoute(`/${encodeURIComponent(pipelineId)}/review/queue`)) as { items: ServerQueueItem[] };
  return { items: res.items.map(reviewItemFromRow) };
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

/** Take back an approval made in the last 10 seconds. */
async function pipelineUndo(decisionId: string): Promise<{ ok: boolean }> {
  const res = await post(pipelineRoute(`/decisions/${encodeURIComponent(decisionId)}/undo`), {}) as { undone: boolean };
  return { ok: res.undone };
}

/** Send the item back to its assignee with a note. The requirement reopens; nothing is approved. */
async function pipelineSendBack(taskId: string, requirementId: string, note: string): Promise<{ decision_id: string }> {
  return post(pipelineRoute(reviewPath(taskId, requirementId) + '/send-back'), { note }) as Promise<{ decision_id: string }>;
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
  if (!response.ok || !body.file_id) throw apiFailure(body as FailureBody, response);
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
