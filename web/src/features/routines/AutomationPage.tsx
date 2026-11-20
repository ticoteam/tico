import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { MenuItem, MenuRule } from '@/kit/Menu';
import { useCards } from '@/kit/Popover';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { fmtWhen } from '@/lib';
import { Empty } from '@/kit/Empty';
import { Segmented } from '@/kit/Segmented';
import { ItemHead, PageMore, PanelSlot, SidePanel } from '@/pagekinds';
import { automationHref, ROUTINES } from './actions';
import { BoardItemEditor, type ItemRef } from './BoardEditor';
import { routineBoard, takesTemplate, type Part } from './board';
import { findStep, money, STEP_WORDS, took, type FlowStep } from './flow';
import { FlowView, useNamer } from './FlowView';
import { Face, PageOwner, PanelRead, press, useConfirmRemove, Word } from './parts';
import { routineQuery, routinesQuery, runPagesQuery } from './queries';
import { HoldLine, LimitEditor, TestRun, Versions } from './RoutinePanel';
import { StepPanel } from './StepPanel';
import { ApplyTemplate } from './Templates';
import type { Routine, Run } from './types';
import { runLink, runWords } from './words';
import './routines.css';
import './automations.css';

/* One automation, the content beside the sidebar (ui/app/automations.ts auDrawHead, auDrawPane): its name, on or off
   and Run now in the head; its owner, version and spend limit under it, with Flow and Runs. Flow draws what starts it and
   each step (automations-flow.ts), Runs its run log, a run opening to its steps (automations-runs.ts). A card opens its
   editor in the panel (`?edit=`), as the old page's side did. */

export interface AutomationSearch {
  view?: 'runs';
  edit?: string;
}

export function AutomationPage({ id, search }: { id: string; search: AutomationSearch }) {
  const listed = useQuery(routinesQuery).data?.find((r) => r.id === id);
  const one = useQuery({ ...routineQuery(id), enabled: !id.startsWith('tmp:') });
  // the routine's own read carries what the list leaves out (a set's questions); the list's copy draws first
  const r = one.data ?? listed;
  if (!r) {
    const gone = one.isError && [403, 404].includes((one.error as { status?: number }).status ?? 0);
    return (
      <>
        <ItemHead title="Automation" back={{ to: ROUTINES, label: 'Automations' }} />
        <div className="conv-body au-body" data-scroll>
          {gone ? <Empty title="Not shared with you" /> : <PanelRead q={one} what="Automation" />}
        </div>
      </>
    );
  }
  return <One key={r.id} r={r} search={search} />;
}

function One({ r, search }: { r: Routine; search: AutomationSearch }) {
  const [testing, setTesting] = useState(false);
  const [applying, setApplying] = useState(false);
  const remove = useConfirmRemove();
  const name = useNamer();
  const phone = useCards();
  const view = search.view === 'runs' ? 'runs' : 'flow';
  const board = routineBoard(r);
  const can = !!r.can_edit && !r.readonly;
  const tmp = r.id.startsWith('tmp:');
  const on = r.enabled !== false && !r.paused;
  const owner = r.owner_id ?? '';
  const href = (edit: string) => `${automationHref(r.id)}?edit=${encodeURIComponent(edit)}`;
  const close = () => void press('automations.open', { routine: r.id });
  const limit = r.spend_cap?.per_30m != null ? `${money(Number(r.spend_cap.per_30m))} / 30 min` : 'Agent’s limit';
  const copy = () =>
    void navigator.clipboard.writeText(location.origin + automationHref(r.id)).then(
      () => toast({ text: 'Link copied', kind: 'done' }),
      () => toast({ text: "Couldn't copy", kind: 'failed' }),
    );

  const more = (
    <PageMore>
      {/* a phone's head keeps Run now only: on and off move here */}
      {phone && r.can_pause !== false && (
        <MenuItem label={on ? 'Turn off' : 'Turn on'} icon="pause" onSelect={() => void press(on ? 'automations.pause' : 'automations.resume', { routine: r.id })} />
      )}
      {can && <MenuItem label="Test run" icon="play" onSelect={() => setTesting(true)} />}
      {takesTemplate(r) && <MenuItem label="Apply template" icon="mail" onSelect={() => setApplying(true)} />}
      <MenuItem label="Versions" icon="clock" onSelect={() => void press('automations.edit', { routine: r.id, edit: 'versions' })} />
      <MenuItem label="Copy link" icon="link" onSelect={() => copy()} />
      {r.can_edit && (
        <>
          <MenuRule />
          <MenuItem
            label={r.builtin ? 'Built in' : 'Remove'}
            icon="archive"
            disabled={!!r.builtin || tmp}
            onSelect={() => remove.confirm(r.name || 'this automation', () => void press('automations.remove', { routine: r.id }))}
          />
        </>
      )}
    </PageMore>
  );
  return (
    <>
      <ItemHead
        title={r.name || 'Untitled'}
        target={`routine:${r.id}`}
        back={{ to: ROUTINES, label: 'Automations' }}
        onRename={can ? (next) => void press('automations.rename', { routine: r.id, name: next }) : undefined}
        renameLabel="Automation name"
        actions={
          <>
            {!phone && (
              <span className="au-head-sw">
                <Switch
                  label={on ? 'On' : r.paused ? 'Paused' : 'Off'}
                  labelHidden
                  checked={on}
                  disabled={r.can_pause === false || tmp}
                  onCheckedChange={(next) => void press(next ? 'automations.resume' : 'automations.pause', { routine: r.id })}
                />
              </span>
            )}
            <PageOwner page="automations" />
            <Button variant="primary" disabled={tmp || r.readonly} onClick={() => press('automations.runNow', { routine: r.id })}>
              Run now
            </Button>
          </>
        }
        more={more}
      />
      <div className="au-subbar">
        <span className="au-sub-line">
          {owner && (
            <span className="au-owner">
              <Face id={owner} size={16} />
              {name(owner)}
            </span>
          )}
          <button type="button" className="au-subbtn" disabled={tmp} onClick={() => void press('automations.edit', { routine: r.id, edit: 'versions' })}>
            {`v${r.version ?? 1}`}
          </button>
          {!!r.pending && <Word icon="edit">Proposed</Word>}
          {!r.readonly && (
            <button type="button" className="au-subbtn" disabled={!r.can_edit || tmp} onClick={() => void press('automations.edit', { routine: r.id, edit: 'limit' })}>
              {limit}
            </button>
          )}
        </span>
        <Segmented
          label="View"
          options={[
            { value: 'flow', label: 'Flow' },
            { value: 'runs', label: 'Runs' },
          ]}
          value={view}
          onValueChange={(v) => void press('automations.open', { routine: r.id, view: v })}
        />
      </div>
      <div className="conv-body au-body" data-scroll>
        {(r.hold || (r.problems ?? []).length > 0 || (r.builtin && !r.can_pause)) && (
          <div className="au-lines">
            {r.hold && <HoldLine routine={r} held={r.hold.held} dropped={r.hold.dropped} />}
            {r.builtin && !r.can_pause && (
              <p className="au-lock">
                <Icon name="lock" />
                Built in: can’t be turned off
              </p>
            )}
            {(r.problems ?? []).length > 0 && (
              <p className="au-lock">
                <Icon name="alert" />
                {`Names a step its type does not have: ${[...new Set((r.problems ?? []).map((p) => p.step))].join(', ')}`}
              </p>
            )}
          </div>
        )}
        {view === 'runs' ? <RunLog r={r} /> : <FlowView routine={r} board={board} edit={search.edit} href={href} />}
      </div>
      {remove.node}
      {testing && <TestRun routine={r} onClose={() => setTesting(false)} />}
      {applying && <ApplyTemplate routine={r} onClose={() => setApplying(false)} />}
      {search.edit && (
        <PanelSlot>
          <EditPanel r={r} edit={search.edit} onClose={close} />
        </PanelSlot>
      )}
    </>
  );
}

/** What `?edit=` opens: a step or the trigger, a board part, the versions or the spend limit. */
function EditPanel({ r, edit, onClose }: { r: Routine; edit: string; onClose: () => void }) {
  if (edit === 'versions') return <Versions routine={r} onDone={onClose} onClose={onClose} />;
  if (edit === 'limit')
    return (
      <SidePanel className="rr au-ed" title="Spend limit" target={`limit:${r.id}`} onClose={onClose}>
        <section className="rail-sec">
          <LimitEditor routine={r} onDone={onClose} />
        </section>
      </SidePanel>
    );
  if (edit.startsWith('board|')) {
    const board = routineBoard(r);
    const [, part = '', at = ''] = edit.split('|');
    if (!board) return null;
    const it: ItemRef = { part: part as Part, ...(at && at !== 'new' ? { at: Number(at) } : {}) };
    return <BoardItemEditor key={edit} routine={r} board={board} it={it} onDone={onClose} onClose={onClose} />;
  }
  return <StepPanel routine={r} edit={edit} onClose={onClose} />;
}

// ---------------------------------------------------------------- runs

const MARKED = new Set(['failed', 'waiting', 'held', 'partial']);

/** A run's one line: the server's summary, else its status. */
const outcome = (run: Run) => run.error || run.summary || runWords(run.status);

/** Its runs, newest first, 25 a page; a run opens to what each step did. */
function RunLog({ r }: { r: Routine }) {
  const q = useInfiniteQuery(runPagesQuery(r.id));
  const [open, setOpen] = useState<string[]>([]);
  const name = useNamer();
  const runs = (q.data?.pages.flatMap((p) => p.runs) ?? []) as Run[];
  if (q.isPending) return <p className="au-note">Loading</p>;
  if (q.isError)
    return (
      <p className="au-note">
        {'Runs did not load '}
        <Button variant="quiet" onClick={() => void q.refetch()}>
          Try again
        </Button>
      </p>
    );
  if (!runs.length) return <p className="au-note">No runs yet</p>;
  return (
    <div className="au-runs" role="list" aria-label="Runs">
      {runs.map((run) => {
        const at = run.started_at ?? run.finished_at ?? '';
        const shown = open.includes(run.id);
        const link = runLink(run);
        return (
          <div key={run.id} className={shown ? 'au-run is-open' : 'au-run'} role="listitem" data-target={`run:${run.id}`}>
            <button type="button" className="au-run-row" aria-expanded={shown} onClick={() => setOpen(shown ? open.filter((x) => x !== run.id) : [...open, run.id])}>
              <Icon name="chevron" />
              <span className="au-run-at" title={at ? fmtWhen(at) : ''}>
                {at ? fmtWhen(at) : ''}
              </span>
              <span className="au-run-st">{MARKED.has(run.status) && <Word icon={run.status === 'failed' ? 'alert' : 'pause'}>{runWords(run.status)}</Word>}</span>
              <span className="au-run-out">{outcome(run)}</span>
              <span className="au-run-n">{took(run.started_at, run.finished_at)}</span>
              <span className="au-run-n">{run.cost ? money(run.cost) : ''}</span>
            </button>
            {shown && (
              <div className="au-run-steps">
                {(run.steps ?? []).map((st, i) => {
                  const flow = st.id ? findStep(r.steps, String(st.id))?.step : undefined;
                  const raw = st as { status?: string; outputs?: { error?: unknown } | null };
                  const err = raw.outputs && typeof raw.outputs.error === 'string' ? raw.outputs.error.split('\n')[0] : '';
                  return (
                    <div key={i} className="au-run-step">
                      <span className="au-tag">{STEP_WORDS[String(st.type ?? flow?.type ?? '')] ?? 'Step'}</span>
                      <span>{flow ? stepName(flow, name) : ''}</span>
                      <span className="au-quiet">{[st.status && st.status !== 'ok' ? runWords(st.status) : 'Done', err].filter(Boolean).join(' · ')}</span>
                    </div>
                  );
                })}
                {link && (
                  <a className="au-run-link" href={link}>
                    Open result
                  </a>
                )}
                {!(run.steps ?? []).length && !link && <p className="au-quiet">No steps ran</p>}
              </div>
            )}
          </div>
        );
      })}
      {q.hasNextPage && (
        <div className="au-more">
          <Button variant="quiet" busy={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
            More
          </Button>
        </div>
      )}
    </div>
  );
}

const stepName = (s: FlowStep, name: (id: string) => string) =>
  s.type === 'bot_turn' ? name(s.bot ? (s.bot.includes(':') ? s.bot : `bot:${s.bot}`) : '') : s.type === 'action' ? String(s.do ?? '') : s.type === 'find' ? String(s.source ?? '') : '';
