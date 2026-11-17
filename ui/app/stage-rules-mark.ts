/* ui/app/stage-rules-mark.ts — a stage's rules mark on a pipeline board (stage-rules.ts has the rest): the quiet gavel at
   the far right of a stage header, drawn only when the stage has rules of its own, and the board's rules read once for
   its count. Kept out of the stage-rules page part because the Tasks board draws it on Home's first open (P05b).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** The rules that apply in each pipeline the board has drawn: pipeline id -> its rules, or the read in flight. */
const SR_BOARD = new Map<string, StageRule[] | Promise<void>>();

/** How many rules a stage has of its own (made On that stage; the whole pipeline's and the company's are shared), or
 * null while the board's read is out. */
function srCount(pipelineId: string, stageId: string): number | null {
  const rows = SR_BOARD.get(pipelineId);
  return Array.isArray(rows) ? rows.filter(r => r.scope === 'stage' && r.stages.includes(stageId)).length : null;
}

/** The board's rules, read once; each stage's count is written in place when they come. */
function srBoardLoad(pipelineId: string, fresh = false): void {
  if (!fresh && SR_BOARD.has(pipelineId)) return;
  const read = get<StageRulesList>(`/v2/stage-rules?pipeline=${encodeURIComponent(pipelineId)}`, {fresh: true})
    .then(got => { SR_BOARD.set(pipelineId, got.items || []); srCountsPaint(pipelineId); })
    .catch(() => { SR_BOARD.delete(pipelineId); });
  SR_BOARD.set(pipelineId, read);
}

function srCountsPaint(pipelineId: string): void {
  document.querySelectorAll<HTMLElement>(`[data-sr-open][data-sr-pipeline="${CSS.escape(pipelineId)}"]`).forEach(b => {
    const n = srCount(pipelineId, b.dataset['srOpen'] ?? ''), words = srCountWords(n);
    b.hidden = !n;
    b.dataset['srCount'] = n === null ? '' : String(n);
    b.setAttribute('aria-label', srMarkName(b.dataset['srName'] ?? '', words));
    b.title = words;
  });
}

/** The mark's name: the stage, then its count, "Build: 3 rules of its own", "Build: none of its own". */
const srMarkName = (stage: string, words: string): string => `${stage}: ${words.charAt(0).toLowerCase()}${words.slice(1)}`;
/** The mark's tip, its own rules in words: "3 rules of its own"; "Rules" while the board's read is out. */
const srCountWords = (n: number | null): string => n === null ? 'Rules' : n ? `${n} rule${n === 1 ? '' : 's'} of its own` : 'None of its own';

/** The stage's rules mark, for a stage header, last (at its far right): a quiet gavel, drawn only when the stage has
 * rules of its own (hidden while the board's rules are read, and when only shared and built-in rules apply there). One
 * click opens the stage's editor at its first phase with rules. */
function stageRulesButtonHTML(pipelineId: string, stage: {id?: string | null; name: string}): string {
  const id = stage.id ?? '';
  if (!id) return '';
  srBoardLoad(pipelineId);
  const n = srCount(pipelineId, id), words = srCountWords(n);
  return `<button type="button" class="sr-mark" data-sr-open="${esc(id)}" data-sr-pipeline="${esc(pipelineId)}"${n ? '' : ' hidden'}`
    + ` data-sr-name="${esc(stage.name)}" data-sr-count="${n === null ? '' : n}" aria-label="${esc(srMarkName(stage.name, words))}" title="${esc(words)}">`
    + '<span class="nav-icon" aria-hidden="true">gavel</span></button>';
}
