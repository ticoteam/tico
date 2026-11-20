import { Fragment, type ReactNode } from 'react';
import { useActors } from '@/data';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { Menu, MenuItem } from '@/kit/Menu';
import { Segmented } from '@/kit/Segmented';
import { Switch } from '@/kit/Switch';
import { actKey, actLine, destName, llmModes, OPS, type Act, type Board, type Cond, type FlowRule } from './board';
import {
  actionWho,
  condChip,
  condsChip,
  FIND_SOURCES,
  KIND_ICONS,
  KIND_WORDS,
  questionIds,
  sourceWords,
  STEP_WORDS,
  stepTitle,
  thenWords,
  typesAt,
  type FlowStep,
} from './flow';
import { press } from './parts';
import type { Routine, Step } from './types';
import { scheduleWords } from './words';

/* An automation's flow, drawn as the old page drew it (automations-flow.ts auFlowHTML, auBoardHTML): the trigger card,
   then each step as a card with its type, what it does and its own switch, a "+" between two steps and Add step at the
   end of a list; a Paths step forks into columns side by side (a phone swipes through them). A flow shaped as an inbox
   board (rules → questions → LLM → destinations) reads as that board. A card opens its editor in the panel
   (`?edit=`). Greys only: what the old page coloured is a mark here. */

/** The address of the flow with something open in the panel: a step's id, `trigger`, a new step, or a board part. */
export type EditHref = (edit: string) => string;

/** A person's or agent's name for an owner or agent in any id form. */
export function useNamer() {
  const actors = useActors();
  const by = new Map<string, string>();
  for (const a of actors) {
    by.set(a.id, a.name);
    by.set(a.id.replace(/^human:/, 'person:'), a.name);
    if (a.slug) by.set(`bot:${a.slug}`, a.name);
  }
  return (id: string) => {
    if (id.startsWith('assistant:')) {
      const who = by.get(`human:${id.slice(10)}`);
      return who ? `${who.split(' ')[0]}’s Assistant` : 'Assistant';
    }
    return by.get(id) ?? by.get(`bot:${id}`) ?? '';
  };
}

export function FlowView({ routine, board, edit, href }: { routine: Routine; board: Board | null; edit?: string; href: EditHref }) {
  const can = !!routine.can_edit && !routine.readonly;
  return (
    <div className="au-flow">
      {routine.builtin && routine.readonly && (
        <p className="au-lock">
          <Icon name="lock" />
          Built in: can’t be edited or deleted
        </p>
      )}
      {board ? <BoardDraw routine={routine} board={board} edit={edit} href={href} can={can} /> : <Track routine={routine} edit={edit} href={href} can={can} />}
    </div>
  );
}

// ---------------------------------------------------------------- the track

function Track({ routine, edit, href, can }: { routine: Routine; edit?: string; href: EditHref; can: boolean }) {
  const name = useNamer();
  const t = routine.trigger;
  const src = sourceWords(t, { who: name, schedule: (c) => cap(scheduleWords(c)) });
  const filters = (t.filters ?? []) as Cond[];
  return (
    <div className="au-track">
      <div className={edit === 'trigger' ? 'au-card au-trig is-open' : 'au-card au-trig'}>
        <div className="au-card-h">
          <Icon name={KIND_ICONS[t.type] ?? 'clock'} />
          <Link className="au-card-t" href={href('trigger')} data-target={`trigger:${routine.id}`}>
            {KIND_WORDS[t.type] ?? cap(t.type)}
          </Link>
        </div>
        {(src || filters.length > 0) && (
          <div className="au-chips">
            {src && (
              <span className="au-chip" title={t.type === 'event' ? t.name : undefined}>
                {src}
              </span>
            )}
            {filters.map((c, i) => (
              <span key={i} className="au-chip">
                {condChip(c)}
              </span>
            ))}
          </div>
        )}
      </div>
      <Steps routine={routine} steps={routine.steps ?? []} listKey="root" edit={edit} href={href} can={can} name={name} />
    </div>
  );
}

function Steps({ routine, steps, listKey, edit, href, can, name }: { routine: Routine; steps: Step[]; listKey: string; edit?: string; href: EditHref; can: boolean; name: (id: string) => string }) {
  const list = steps as FlowStep[];
  return (
    <>
      <Insert routine={routine} listKey={listKey} at={0} end={!list.length} can={can} />
      {list.map((s, i) => (
        <Fragment key={s.id}>
          {s.type === 'paths' ? (
            <PathsBlock routine={routine} s={s} edit={edit} href={href} can={can} name={name} />
          ) : (
            <>
              <StepCard routine={routine} s={s} open={edit === s.id} href={href} can={can} name={name} />
              <Insert routine={routine} listKey={listKey} at={i + 1} end={i + 1 === list.length} can={can} />
            </>
          )}
        </Fragment>
      ))}
    </>
  );
}

/** Where a step goes in: a small "+" between two steps, Add step at the end of a list. A viewer who may not edit sees
    the line between steps only. */
function Insert({ routine, listKey, at, end, can }: { routine: Routine; listKey: string; at: number; end: boolean; can: boolean }) {
  if (!can) return end ? null : <div className="au-ln" />;
  const types = typesAt(routine.steps ?? [], listKey, at);
  const items = types.map((t) => <MenuItem key={t} label={STEP_WORDS[t] ?? t} onSelect={() => go(routine.id, `new|${listKey}|${at}|${t}`)} />);
  return (
    <div className={end ? 'au-ln is-end' : 'au-ln'}>
      <Menu
        align="center"
        trigger={
          end ? (
            <button type="button" className="au-add-end">
              <Icon name="plus" />
              Add step
            </button>
          ) : (
            <button type="button" className="au-add" aria-label="Insert step">
              <Icon name="plus" />
            </button>
          )
        }
      >
        {items}
      </Menu>
    </div>
  );
}

/** The menu's pick opens the new step's editor: the address carries it, so Back closes it. */
function go(routine: string, edit: string) {
  void press('automations.edit', { routine, edit });
}

const Tag = ({ type }: { type: string }) => <span className={type === 'llm' || type === 'bot_turn' || type === 'paths' ? 'au-tag is-strong' : 'au-tag'}>{STEP_WORDS[type] ?? type}</span>;

function StepSwitch({ routine, s, can }: { routine: Routine; s: FlowStep; can: boolean }) {
  if (!can) return null;
  return (
    <span className="au-card-sw">
      <Switch
        label={`${STEP_WORDS[s.type] ?? 'Step'} on`}
        labelHidden
        checked={s.enabled !== false}
        onCheckedChange={(on) => void press('automations.setStepOn', { routine: routine.id, step: s.id, on })}
      />
    </span>
  );
}

function StepCard({ routine, s, open, href, can, name }: { routine: Routine; s: FlowStep; open: boolean; href: EditHref; can: boolean; name: (id: string) => string }) {
  let body: ReactNode = null;
  if (s.type === 'filter') {
    body = (s.rules ?? []).map((r: FlowRule, i) => (
      <div key={i} className="au-rule">
        <span>{condsChip(r)}</span>
        <Icon name="arrow" />
        {[...thenWords(r.then), ...(r.to ? [String(r.to)] : [])].map((x, j) => (
          <span key={j} className="au-chip">
            {x}
          </span>
        ))}
      </div>
    ));
  } else if (s.type === 'decide') {
    const ids = questionIds(s);
    body = (
      <div className="au-chips">
        {ids.length ? (
          ids.map((q) => (
            <span key={q} className="au-chip">
              {q}
            </span>
          ))
        ) : (
          <span className="au-quiet">No questions</span>
        )}
      </div>
    );
  } else if (s.type === 'llm' || s.type === 'bot_turn') {
    body = <p className="au-prompt">{s.prompt || 'No prompt yet'}</p>;
  } else if (s.type === 'find') {
    const bits = [s.query, s.since === 'last_run' ? 'since last run' : '', s.limit ? `up to ${s.limit}` : ''].filter(Boolean);
    body = bits.length ? <p className="au-prompt">{bits.join(' · ')}</p> : null;
  } else if (s.type === 'action') {
    const who = actionWho(s, name);
    body = who ? <p className="au-quiet">{who}</p> : null;
  }
  const cls = ['au-card', s.enabled === false && 'is-off', open && 'is-open'].filter(Boolean).join(' ');
  return (
    <div className={cls}>
      <div className="au-card-h">
        <Tag type={s.type} />
        <Link className="au-card-t" href={href(s.id)} data-target={`step:${s.id}`}>
          {stepTitle(s, name) || FIND_SOURCES[s.source ?? ''] || STEP_WORDS[s.type]}
        </Link>
        <StepSwitch routine={routine} s={s} can={can} />
      </div>
      {body}
    </div>
  );
}

/** A fork: its card, then a column per path and Otherwise, each with its condition chips and its own steps. */
function PathsBlock({ routine, s, edit, href, can, name }: { routine: Routine; s: FlowStep; edit?: string; href: EditHref; can: boolean; name: (id: string) => string }) {
  const cols = [
    ...(s.paths ?? []).map((p, i) => ({ p, key: `${s.id}:${i}`, other: false })),
    { p: { name: 'Otherwise', when: [], steps: s.otherwise?.steps ?? [] }, key: `${s.id}:other`, other: true },
  ];
  const cls = ['au-card', 'au-pcard', s.enabled === false && 'is-off', edit === s.id && 'is-open'].filter(Boolean).join(' ');
  return (
    <div className="au-pb">
      <div className={cls}>
        <div className="au-card-h">
          <Tag type="paths" />
          <Link className="au-card-t is-quiet" href={href(s.id)} data-target={`step:${s.id}`}>
            {`${cols.length} paths`}
          </Link>
          <StepSwitch routine={routine} s={s} can={can} />
        </div>
      </div>
      <div className="au-paths" role="list" aria-label="Paths">
        {cols.map(({ p, key, other }) => (
          <div key={key} className={other ? 'au-path is-other' : 'au-path'} role="listitem">
            <span className="au-path-n">{p.name || 'Path'}</span>
            <div className="au-chips">
              {other ? (
                <span className="au-quiet">Nothing else</span>
              ) : (p.when ?? []).length ? (
                (p.when ?? []).map((c, i) => (
                  <span key={i} className="au-chip">
                    {condChip(c)}
                  </span>
                ))
              ) : (
                <span className="au-quiet">No condition</span>
              )}
            </div>
            <Steps routine={routine} steps={p.steps ?? []} listKey={key} edit={edit} href={href} can={can} name={name} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the board

/** A condition as the board reads it: the field and its check in words, the value as code. */
function BoardCond({ c, code }: { c: Cond; code: boolean }) {
  const f = String(c.field ?? '').includes('.') ? String(c.field).split('.').slice(1).join('.') : String(c.field ?? '');
  const v = c.value === true ? 'yes' : c.value === false ? 'no' : String(c.value ?? '');
  return (
    <>
      {code ? <code>{f}</code> : <span className="au-f">{f}</span>}
      <span className="au-f">{OPS[c.op || 'is'] ?? c.op}</span>
      <code>{v}</code>
    </>
  );
}

/** A row's chain: its actions in order, then where it goes; a chain that moves on says so, none says End. */
function Chain({ then, to, next, board, name }: { then: Act[] | undefined; to?: string; next: string; board: Board; name: (id: string) => string }) {
  const chips: string[] = (then ?? []).map((x) => actLine(x, name));
  if (to) chips.push(destName(to, board.dests));
  const moves = !to && next === 'rule';
  if (!chips.length) chips.push(moves ? 'Move on' : 'End');
  return (
    <span className={moves ? 'au-chain is-moves' : 'au-chain'}>
      {chips.map((c, i) => (
        <Fragment key={i}>
          {i > 0 && <Icon name="arrow" />}
          <span className={to && i === chips.length - 1 ? 'au-bchip is-dest' : 'au-bchip'}>{c}</span>
        </Fragment>
      ))}
    </span>
  );
}

function BoardDraw({ routine, board, edit, href, can }: { routine: Routine; board: Board; edit?: string; href: EditHref; can: boolean }) {
  const name = useNamer();
  const t = routine.trigger;
  const code = t.type !== 'message';
  const src = t.type === 'message' ? t.inbox || 'Message received' : [KIND_WORDS[t.type] ?? cap(t.type), sourceWords(t, { who: name, schedule: (c) => cap(scheduleWords(c)) })].filter(Boolean).join(' · ');
  const L = board.llm;
  const modes = llmModes(routine);
  const row = (key: string, cond: ReactNode, chain: ReactNode) => (
    <div key={key} className={edit === `board|${key}` ? 'au-brow is-open' : 'au-brow'}>
      <div className="au-bcond">
        {can ? (
          <Link href={href(`board|${key}`)} className="au-bopen" data-target={`board:${routine.id}:${key}`}>
            {cond}
          </Link>
        ) : (
          cond
        )}
      </div>
      {chain}
    </div>
  );
  const head = (n: number, label: string, title: string, add?: { label: string; key: string }) => (
    <div className="au-bh">
      <span className="au-tag" title={`Step ${n}`}>
        {label}
      </span>
      <b>{title}</b>
      {can && add && (
        <Link href={href(`board|${add.key}`)} className="au-badd">
          {add.label}
        </Link>
      )}
    </div>
  );
  const pick = new Set(L.to);
  return (
    <div className="au-board">
      <Link className={edit === 'trigger' ? 'au-btrig is-open' : 'au-btrig'} href={href('trigger')} data-target={`trigger:${routine.id}`}>
        <Icon name={KIND_ICONS[t.type] ?? 'clock'} />
        <b>{src}</b>
      </Link>
      <div className="au-bsp" />
      {(board.rules.length > 0 || can) && (
        <section className="au-bsec" aria-label="Rules">
          {head(1, 'Rules', 'Match exactly', { label: 'Add rule', key: 'rule|new' })}
          {board.rules.map((r, i) =>
            row(
              `rule|${i}`,
              (r.when ?? []).length ? (
                (r.when ?? []).map((c, j) => (
                  <Fragment key={j}>
                    {j > 0 && <span className="au-f">{r.match === 'any' ? 'or' : 'and'}</span>}
                    <BoardCond c={c} code={code} />
                  </Fragment>
                ))
              ) : (
                <span className="au-f">Always</span>
              ),
              <Chain then={r.then} to={r.to} next={r.next} board={board} name={name} />,
            ),
          )}
        </section>
      )}
      {(board.rules.length > 0 || can) && (
        <div className="au-bsp">
          <span>No match: move on</span>
        </div>
      )}
      {(board.questions.length > 0 || can) && (
        <section className="au-bsec" aria-label="Questions">
          {head(2, 'Questions', 'Ask a small model, in order', { label: 'Add question', key: 'question|new' })}
          {board.questions.map((q, i) => row(`question|${i}`, <span className="au-btext">{q.text}</span>, <Chain then={q.then} to={q.to} next="end" board={board} name={name} />))}
        </section>
      )}
      {(board.questions.length > 0 || can) && (
        <div className="au-bsp">
          <span>All no: move on</span>
        </div>
      )}
      <section className={edit === 'board|llm' ? 'au-bsec au-bllm is-open' : 'au-bsec au-bllm'} aria-label="LLM">
        <div className="au-bh">
          <span className="au-tag is-strong" title="Step 3">
            LLM
          </span>
          <b>Read what’s left, pick one below</b>
          {modes.length > 1 && (
            <Segmented
              label="Mode"
              options={modes}
              value={L.mode}
              disabled={!can}
              onValueChange={(mode) => void press('automations.saveBoardItem', { routine: routine.id, part: 'llm', mode })}
            />
          )}
        </div>
        <div className="au-bllm-b">
          {can ? (
            <Link href={href('board|llm')} className="au-bprompt" data-target={`board:${routine.id}:llm`}>
              {L.prompt || 'No instructions'}
            </Link>
          ) : (
            <span className="au-bprompt">{L.prompt || 'No instructions'}</span>
          )}
          {L.to.length > 1 && <span className="au-bun">{`Unsure → ${destName(L.default, board.dests)}`}</span>}
        </div>
      </section>
      <div className="au-branch" aria-hidden="true" />
      <div className="au-dests" role="list" aria-label="Destinations">
        {board.dests.map((d, i) => {
          const first = (d.then ?? [])[0];
          const more = (d.then ?? []).length - 1;
          const cls = ['au-dest', pick.has(d.id) && 'is-pick', edit === `board|destination|${i}` && 'is-open'].filter(Boolean).join(' ');
          return (
            <div key={d.id} className={cls} role="listitem">
              {can ? (
                <Link href={href(`board|destination|${i}`)} className="au-dest-n" data-target={`board:${routine.id}:destination:${i}`}>
                  {d.name || d.id}
                </Link>
              ) : (
                <b className="au-dest-n">{d.name || d.id}</b>
              )}
              <span className="au-dest-a">
                {first ? <Icon name={ACT_ICON[actKey(first)] ?? 'spark'} /> : null}
                {actLine(first, name)}
                {more > 0 ? ` +${more}` : ''}
              </span>
            </div>
          );
        })}
        {can && (
          <Link href={href('board|destination|new')} className="au-dest is-add">
            <Icon name="plus" />
            Destination
          </Link>
        )}
      </div>
    </div>
  );
}

const ACT_ICON: Record<string, 'tasks' | 'message' | 'inbox' | 'archive' | 'share' | 'reply' | 'link' | 'updates' | 'edit' | 'people'> = {
  create_task: 'tasks',
  wake_bot: 'message',
  leave_for_you: 'inbox',
  archive: 'archive',
  forward: 'share',
  draft_reply: 'reply',
  send_reply: 'reply',
  call_webhook: 'link',
  post_update: 'updates',
  note: 'edit',
  assign: 'people',
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
