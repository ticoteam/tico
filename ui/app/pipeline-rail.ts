/* ui/app/pipeline-rail.ts — The pipeline manager is the Tasks page's chat (docs/dev/pipelines-v3.md S10; style guide "Where
   chat lives"). There is no rail of its own: while a pipeline is shown, the Tasks page's owner chat (page-chat.ts, the
   column docked where the page fits beside it and the tab on the right edge where it does not) answers as the pipeline's manager, and each
   message carries the board on screen as a chip above the box and as `refs.board` (pipeline-ask.ts paBoard), as the
   phone's sheet does. Another pipeline is another manager, so another chat. A pipeline with no manager keeps the page's
   own owner, with the board. A manager that is archived or still being set up, or a Tasks page a person set to "No
   owner", gives no chat from the manager (page-chat.ts pgcOwner). On a phone the header's face (#task-manager,
   pipeline-ask.ts) is the chat mark once the board is drawn and opens the board's bots, so page chat draws no mark of
   its own there then; while the board loads or failed to, the page's mark (the manager's face) opens the chat.
   The chat is the ordinary direct thread with the manager (thread-view.ts threadMount), never a chat of its own. Task
   links in its messages open the task as in any chat (chat.ts). The manager's log stays in the API
   (GET /api/v2/pipelines/{id}/log) for bots and the CLI, and no screen reads it.
   The page (tasks-page.ts) calls these through the pipeline view's rail hook (task-pipelines.ts).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A manager's actor id from its reference, stored with or without bot:. */
const prActor = (ref: string): string => 'bot:' + ref.replace(/^bot:/, '');

/** The pipeline in view: its manager answers the page's chat, with the board on screen. `drawn`: the board is drawn
    (not loading, not failed), so its own face is the phone's way in. */
function pipelineRailSync(rec: PipelineRec, drawn: boolean): void {
  const board: PaState = {host: document.body, rec, stage: ''};
  pageChatUse('tasks', {
    owner: rec.manager_id ? prActor(rec.manager_id) : undefined,
    context: {id: rec.id, label: () => paBoardWords(board), refs: () => ({board: paBoard(board)})},
    mark: drawn && paBots(rec).length ? false : undefined,
  });
}

/** No pipeline in view (You, the archive): the page's own owner, with no board. */
function pipelineRailLeave(): void {
  pageChatUse('tasks', {owner: undefined, context: undefined, mark: undefined});
}
