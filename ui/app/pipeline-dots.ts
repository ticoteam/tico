/* ui/app/pipeline-dots.ts — The requirement dots on a ticket's card: one per stage requirement, in the state the server
   gives it (met, waiting on a reviewer, out of date, failed, missing). A tap or a long press on the dots, or hovering
   them, lists each requirement's name, state and next actor. A ticket with no requirements gets no dots at all, so a
   pipeline without requirements looks as it always did. Classic script: its globals are shared with the other files
   under ui/app/, loaded in the order index.html lists them. */
'use strict';

const PIPELINE_DOT_WORDS: Record<PipelineReqState, string> = {
  met: 'met', waiting: 'waiting', out_of_date: 'out of date', failed: 'failed', missing: 'missing',
};

/** The label a dot names: "Screenshots · waiting · Ana". The next actor (an actor id) is left out when nobody is next. */
function pipelineDotLabel(req: PipelineRequirement): string {
  const parts = [req.name, PIPELINE_DOT_WORDS[req.state] || req.state];
  if (req.next_actor && req.state !== 'met') parts.push(actorLabel(req.next_actor));
  return parts.join(' · ');
}

/** How many of a ticket's requirements are met, for the group's accessible name. */
function pipelineDotsSummary(reqs: PipelineRequirement[]): string {
  const met = reqs.filter(req => req.state === 'met').length;
  return `${met} of ${reqs.length} requirement${reqs.length === 1 ? '' : 's'} met`;
}

/** The dots on a card, or '' when the ticket has no requirements (the server sends the key only when it has some). */
function pipelineDotsHTML(task: { requirements?: PipelineRequirement[] | null }): string {
  const reqs = task.requirements ?? [];
  if (!reqs.length) return '';
  const dots = reqs.map(req => {
    const label = pipelineDotLabel(req);
    return `<button type="button" class="pdot pdot-${esc(req.state)}" data-pdot aria-label="${esc(label)}" title="${esc(label)}"></button>`;
  }).join('');
  const names = reqs.map(req => `<li>${esc(pipelineDotLabel(req))}</li>`).join('');
  return `<span class="pdots" role="group" aria-label="${esc(pipelineDotsSummary(reqs))}">${dots}`
    + `<span class="pdots-tip" role="tooltip"><ul>${names}</ul></span></span>`;
}

// A tap (or a long press) opens the list of names on that card; a tap anywhere else closes it. The capture phase keeps
// the card's own click (which opens the ticket) from running for a dot.
let pipelineDotPress: { timer: number; el: HTMLElement } | null = null;
function pipelineDotsOpen(group: HTMLElement | null, open: boolean): void {
  document.querySelectorAll<HTMLElement>('.pdots[data-open]').forEach(el => { if (el !== group) el.removeAttribute('data-open'); });
  if (group) { if (open) group.setAttribute('data-open', ''); else group.removeAttribute('data-open'); }
}
document.addEventListener('click', (event: MouseEvent) => {
  const target = event.target instanceof Element ? event.target : null;
  const dot = target?.closest<HTMLElement>('[data-pdot]');
  if (dot) {
    event.stopPropagation(); event.preventDefault();
    const group = dot.closest<HTMLElement>('.pdots');
    pipelineDotsOpen(group, !group?.hasAttribute('data-open'));
    return;
  }
  if (!target?.closest('.pdots')) pipelineDotsOpen(null, false);
}, true);
document.addEventListener('pointerdown', (event: PointerEvent) => {
  if (event.pointerType !== 'touch') return;
  const dot = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-pdot]') : null;
  if (!dot) return;
  // A long press opens the names and the click that follows the release is swallowed by the open state.
  pipelineDotPress = { el: dot, timer: window.setTimeout(() => {
    pipelineDotsOpen(dot.closest<HTMLElement>('.pdots'), true);
    pipelineDotPress = null;
  }, 450) };
}, true);
document.addEventListener('pointerup', () => {
  if (pipelineDotPress) { window.clearTimeout(pipelineDotPress.timer); pipelineDotPress = null; }
}, true);
