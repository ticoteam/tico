/* ui/app/pipeline-api.ts — The one client the pipeline views call: fetch wrappers over the pipeline routes. Every route
   shape the UI codes against is here, so a route that changes is changed in this file only. Classic script: its
   globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type PipelineReqState = 'met' | 'waiting' | 'out_of_date' | 'failed' | 'missing';

/** One requirement on a ticket: the server sends it only on a ticket whose stage has requirements. */
interface PipelineRequirement {
  id: string;
  name: string;
  state: PipelineReqState;
  /** The actor id of whoever the requirement waits on (human:ana, bot:b2), or null. */
  next_actor: string | null;
  /** What stands on the ticket for it, e.g. "3 images" or "Approved by Chris". */
  detail?: string;
  /** Whether this person may mark it met (a pipeline editor, not the manager bot). */
  can_mark_met?: boolean;
}

interface PipelineAttachment {
  id: string;
  label: string;
  kind: 'image' | 'video' | 'page' | 'file' | 'link' | 'check' | 'text';
  url?: string;
  detail?: string;
}

interface PipelineReviewItem {
  task_id: string;
  requirement_id: string;
  task_title?: string;
  stage_name?: string;
  pipeline_id?: string;
  /** The assignee (an actor, bot or person) whose work this is, for "Message <assignee>". */
  assignee?: { id: string; name: string } | null;
  why: string;
  bot_note: string;
  attachments: PipelineAttachment[];
  /** Who holds the review right now, or null when nobody does. */
  claim: { by: string; expires_at: string } | null;
  /** Set on the last requirement of a stage: where Approve moves the ticket. */
  moves_to?: string | null;
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
}

interface PipelineManagerLogLine {
  at: string;
  text: string;
  kind: 'error' | 'warn' | 'info';
  task_id?: string | null;
}

interface PipelineManagerView {
  last_run: PipelineManagerRun | null;
  missed_runs: number;
  late: PipelineManagerLate[];
  log: PipelineManagerLogLine[];
  actions: { id: string; label: string }[];
}

const pipelineRoute = (path: string): string => '/v2/pipelines' + path;
const reviewPath = (taskId: string, requirementId: string): string =>
  `/review/${encodeURIComponent(taskId)}/${encodeURIComponent(requirementId)}`;

/** The reviewer's queue for one pipeline: over-limit items first, then the oldest. */
async function pipelineReviewQueue(pipelineId: string): Promise<PipelineReviewQueue> {
  return get(pipelineRoute(`/${encodeURIComponent(pipelineId)}/review-queue`)) as Promise<PipelineReviewQueue>;
}

/** Approve a requirement. A 409 means the work changed since the item was shown: the caller reloads the queue. */
async function pipelineApprove(taskId: string, requirementId: string, note: string): Promise<PipelineApproveResult> {
  return post(pipelineRoute(reviewPath(taskId, requirementId) + '/approve'), { note }) as Promise<PipelineApproveResult>;
}

/** Take back an approval made in the last 10 seconds. */
async function pipelineUndo(decisionId: string): Promise<{ ok: boolean }> {
  return post(pipelineRoute(`/decisions/${encodeURIComponent(decisionId)}/undo`), {}) as Promise<{ ok: boolean }>;
}

/** Send the item back to its assignee with a note. The requirement reopens; nothing is approved. */
async function pipelineSendBack(taskId: string, requirementId: string, note: string): Promise<{ decision_id: string }> {
  return post(pipelineRoute(reviewPath(taskId, requirementId) + '/send-back'), { note }) as Promise<{ decision_id: string }>;
}

/** Message the pipeline manager about one item. The manager decides or forwards it. */
async function pipelineMessageManager(taskId: string, requirementId: string, note: string): Promise<{ ok: boolean }> {
  return post(pipelineRoute(reviewPath(taskId, requirementId) + '/message-manager'), { note }) as Promise<{ ok: boolean }>;
}

/** Mark one requirement met by hand, with a reason. Logged; new work makes it out of date like any approval. */
async function pipelineMarkMet(taskId: string, requirementId: string, why: string): Promise<{ decision_id: string }> {
  return post(pipelineRoute(`/tasks/${encodeURIComponent(taskId)}/requirements/${encodeURIComponent(requirementId)}/mark-met`),
    { why }) as Promise<{ decision_id: string }>;
}

/** The manager's status for one pipeline: last run, late cards, the log. */
async function pipelineManagerView(pipelineId: string): Promise<PipelineManagerView> {
  return get(pipelineRoute(`/${encodeURIComponent(pipelineId)}/manager`)) as Promise<PipelineManagerView>;
}
