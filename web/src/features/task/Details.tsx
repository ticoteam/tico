import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { run } from '@/actions';
import type { TaskRequirement } from '@/api/schema';
import { Ago } from '@/kit/Ago';
import { DatePick } from '@/kit/DatePick';
import { DateText } from '@/kit/DateText';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { ContextMenu, Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { LatePill, Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { Section as Group } from '@/kit/SectionHead';
import { toast } from '@/kit/Toast';
import { PageBody, Section } from '@/pagekinds';
import { ActorItems, OwnerMenu, tagsOf } from './Head';
import { EditKv, Inline, Kv, saved } from './Fact';
import { Meetings, MoreFacts, PublicFacts, SizeFact } from './Facts';
import { DETAILS, failedToast, tabAddress } from './model';
import { NewPage } from './NewPage';
import { Face, Mark, NameOf, Go } from './parts';
import { taskFilesQuery, type TaskData } from './queries';

/* Details (more.js detailsBody): named sections in a fixed order, from what it is judged by, to the work, to the
   record. A section with nothing in it is not drawn; what is finished folds into one line; the record starts folded.
   Each fact edits in place and saves at once; Add (and a section's quiet +) opens a new line in its section. */

/** What a new line is being written for: the Add menu's choices. */
export type Adding = 'subtask' | 'depends_on' | 'blocks' | 'related' | 'ask' | 'link' | 'page';

const taskHref = (n: number | null | undefined, id: string) => `/task/${n ?? id}`;
const day = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const domainOf = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
};

interface Linked {
  id: string;
  title: string;
  number?: number | null;
  owner?: string | null;
  stage?: string | null;
  done?: boolean;
}

function TaskLine({ t }: { t: Linked }) {
  return (
    <Row className={t.done ? 'task has-stage is-done' : 'task has-stage'} title={t.title} href={taskHref(t.number, t.id)} target={`task:${t.number ?? t.id}`}>
      <span className="row-mark">{t.done ? <Icon name="check" /> : null}</span>
      <span className="row-title">{t.title}</span>
      <span className="row-sub">
        <span className="row-pills" />
        <span className="row-stage">{t.stage ?? ''}</span>
      </span>
      {t.owner ? <Face id={t.owner} decorative /> : <span className="av-gap" />}
      <span className="row-age" />
    </Row>
  );
}

/** A new line at the end of its section (more.js "is-new"). */
function NewLine({ children }: { children: ReactNode }) {
  return (
    <div className="dt-line is-new">
      <span className="dt-mark" />
      {children}
    </div>
  );
}

/** Ask someone: what they are asked to OK, and who. */
function AskLine({ t, onEnd }: { t: TaskData; onEnd: () => void }) {
  const [to, setTo] = useState<string | null>(null);
  return (
    <div className="dt-line is-new is-ask">
      <span className="dt-mark" />
      <Inline
        label="What to OK"
        placeholder="What to OK"
        onSave={(line) => (to ? saved('task.ask', { task: t.id, to, line }) : Promise.resolve('Pick who'))}
        onEnd={onEnd}
      />
      <Menu
        trigger={
          <button className="tv-pick" type="button" aria-label={to ? 'Who is asked' : 'Pick who'}>
            {to ? <Face id={to} decorative /> : <Icon name="people" />}
          </button>
        }
      >
        <ActorItems current={to} onPick={setTo} />
      </Menu>
    </div>
  );
}

function Requirement({ r }: { r: TaskRequirement }) {
  return (
    <div className="dt-line">
      <Mark met={r.met} />
      <span className="dt-text">{r.line}</span>
      <span className="dt-end">{r.who ? <Face id={r.who} /> : null}</span>
    </div>
  );
}

const plus = (label: string, onClick: () => void) => <IconButton icon="plus" label={label} size="sm" quiet onClick={onClick} />;

function Tasks({ name, rows, adding, onAdd, children }: { name: string; rows: Linked[]; adding: boolean; onAdd: () => void; children: ReactNode }) {
  if (!rows.length && !adding) return null;
  return (
    <Section name={name} count={rows.length || undefined} actions={plus('Add a task', onAdd)} open={adding || undefined}>
      {rows.map((t) => (
        <TaskLine key={t.id} t={t} />
      ))}
      {adding && children}
    </Section>
  );
}

/** Paid shots whose outcome is unknown (backend/paid_renders.py): the card waits on its person to mark them reconciled. */
const isShots = (w: { requirement_id: string | null } | null | undefined) => !!w?.requirement_id?.startsWith('paid_render:');
/** The shots in batches of 50, the most one Mark reconciled sends. */
const batches = <T,>(all: T[]) => Array.from({ length: Math.ceil(all.length / 50) }, (_, i) => all.slice(i * 50, i * 50 + 50));

/** Mark reconciled: busy until the server answers, a refusal in a toast. */
function Reconcile({ task, keys }: { task: string; keys: string[] }) {
  const [busy, setBusy] = useState(false);
  const mark = async () => {
    setBusy(true);
    const r = await run('task.reconcileShots', { task, shots: keys }, { via: 'click' });
    setBusy(false);
    if (!r.ok) toast(failedToast(r));
  };
  return (
    <button type="button" className="dt-act" disabled={busy} aria-busy={busy} onClick={() => void mark()}>
      Mark reconciled
    </button>
  );
}

/** The waiting question as a line: a leading "❓" goes, since the line already says it waits (and the emoji draws in red). */
const question = (line: string | null | undefined) => (line ?? '').replace(/^\s*\u2753\uFE0F?\s*/u, '');

const paragraphs = (text: string) => text.split(/\n\s*\n/).filter((p) => p.trim());

export interface DetailsProps {
  t: TaskData;
  flow: boolean;
  /** The new line open now, from Add or a section's +. */
  adding?: Adding | null;
  onAdding?: (what: Adding | null) => void;
}

export function Details({ t, flow, adding = null, onAdding = () => undefined }: DetailsProps) {
  const files = useQuery(taskFilesQuery(t.id));
  const criteria = t.acceptance_criteria ?? [];
  const leave = t.to_leave ?? [];
  const shots = t.waits?.find(isShots);
  const left = leave.filter((r) => !r.met);
  const met = leave.filter((r) => r.met);
  const kids: Linked[] = t.children.map((c) => ({ id: c.id, title: c.title, owner: c.owner_id, stage: c.stage, done: c.status !== 'open' }));
  const open = kids.filter((k) => !k.done);
  const fin = kids.filter((k) => k.done);
  // A task can be linked in several ways, such as a parent that also blocks this task.
  const relations = new Map(Object.values(t.relations ?? {}).flat().map((link) => [link.id, link]));
  const related = [...relations.values()].map((x) => ({ id: x.id, title: x.title, number: x.number, owner: x.owner_id, stage: x.stage, done: x.status !== 'open' }));
  const code = (t.inputs ?? []).filter((x) => x.kind === 'pr');
  const shown = (files.data ?? []).filter((f) => !f.archived);
  const web = (t.links ?? []).filter((l) => l.kind !== 'pr' && l.kind !== 'session');
  const details = tabAddress(t, DETAILS, flow);
  const fileSearch = (id: string) => (flow ? { tab: DETAILS, file: id } : { file: id });
  const request = t.request?.text || t.body || '';
  const end = () => onAdding(null);
  const navigate = useNavigate();
  // Links starts folded, with the record; a link being added opens it, and it stays open to show the new row.
  const [linksOpen, setLinksOpen] = useState(false);
  if (adding === 'link' && !linksOpen) setLinksOpen(true);

  /** A task named by its number, for Blocked by, Blocks and Related. */
  const relating = (kind: 'depends_on' | 'blocks' | 'related') => (
    <NewLine>
      <Inline label="Task number" placeholder="Task number" onSave={(other) => saved('task.relate', { task: t.id, other, kind })} onEnd={end} />
    </NewLine>
  );

  return (
    <PageBody className="dt">
      {criteria.length > 0 && (
        <Section name="Done looks like" count={criteria.length}>
          {criteria.map((c, i) => (
            <div key={i} className="dt-line">
              <Mark met={false} />
              <span className="dt-text">{c}</span>
            </div>
          ))}
        </Section>
      )}

      <Section name="Now">
        <div className="dt-kv" data-target={`task-owner-line:${t.id}`}>
          <span className="dt-k">Has it</span>
          <OwnerMenu
            t={t}
            trigger={
              <button type="button" className="dt-v dt-val">
                <Face id={t.owner_id} />
                <span>
                  <NameOf id={t.owner_id} />
                </span>
                {t.stage_since && t.stage && (
                  <small>
                    <Ago at={t.stage_since} /> in {t.stage.name}
                  </small>
                )}
              </button>
            }
          />
        </div>
        {t.turn?.who && t.turn.who !== t.owner_id && !isShots(t.turn) && (
          <Kv k="Waiting on">
            <Face id={t.turn.who} />
            <span>
              <NameOf id={t.turn.who} />
            </span>
            {question(t.turn.line) && <small>{question(t.turn.line)}</small>}
          </Kv>
        )}
        {shots && (
          <Kv k="Waiting on" target={`task-shots:${t.id}`}>
            {shots.who && <Face id={shots.who} />}
            <small>{shots.line}</small>
          </Kv>
        )}
        {/* Each batch the button sends (the server takes 50 keys at most), with every shot's full name it marks. */}
        {batches(shots?.shots ?? []).map((batch) => (
          <Kv key={batch[0]!.key} k="">
            <small>{batch.map((s) => s.name || s.key).join(', ')}</small>
            {t.can_comment && <Reconcile task={t.id} keys={batch.map((s) => s.key)} />}
          </Kv>
        ))}
        <EditKv
            k="Due"
            target={`task-due:${t.id}`}
            value={
              t.due_at ? (
                <>
                  <DateText at={t.due_at} />
                  {t.late && <LatePill />}
                </>
              ) : (
                <small>None</small>
              )
            }
            edit={(done) => (
              <span className="dt-in is-row">
                <DatePick
                  label="Due"
                  labelHidden
                  withTime={false}
                  value={t.due_at ? new Date(t.due_at) : null}
                  onValueChange={(d) => void saved('task.setDue', { task: t.id, due: day(d) }).then(done)}
                />
                {t.due_at && (
                  <button type="button" className="dt-act" onClick={() => void saved('task.setDue', { task: t.id, due: null }).then(done)}>
                    Clear
                  </button>
                )}
                <button type="button" className="dt-act" onClick={done}>
                  Cancel
                </button>
              </span>
            )}
          />
      </Section>

      {(leave.length > 0 || adding === 'ask') && (
        <Section
          name={`To leave ${t.stage?.name ?? 'this stage'}`}
          count={leave.length || undefined}
          actions={plus('Ask someone', () => onAdding('ask'))}
          open={adding === 'ask' || undefined}
          target={`task-leave:${t.id}`}
        >
          {left.map((r) => (
            <Requirement key={r.requirement_id} r={r} />
          ))}
          {adding === 'ask' && <AskLine t={t} onEnd={end} />}
          {met.length > 0 && (
            <Group level={2} name="Met" count={met.length} defaultOpen={false}>
              {met.map((r) => (
                <Requirement key={r.requirement_id} r={r} />
              ))}
            </Group>
          )}
        </Section>
      )}

      <Tasks
        name="Blocked by"
        rows={(t.blocked_by ?? []).filter((b) => b.id).map((b) => ({ id: b.id as string, title: b.title || b.label || `#${b.number}`, number: b.number }))}
        adding={adding === 'depends_on'}
        onAdd={() => onAdding('depends_on')}
      >
        {relating('depends_on')}
      </Tasks>
      <Tasks
        name="Blocks"
        rows={(t.blocks ?? []).map((b) => ({ id: b.id, title: b.label || `#${b.number}`, number: b.number }))}
        adding={adding === 'blocks'}
        onAdd={() => onAdding('blocks')}
      >
        {relating('blocks')}
      </Tasks>
      <Tasks name="Related" rows={related} adding={adding === 'related'} onAdd={() => onAdding('related')}>
        {relating('related')}
      </Tasks>

      {(kids.length > 0 || adding === 'subtask') && (
        <Section
          name="Subtasks"
          count={kids.length || undefined}
          actions={plus('New subtask', () => onAdding('subtask'))}
          open={adding === 'subtask' || undefined}
          target={`task-subtasks:${t.id}`}
        >
          {open.map((k) => (
            <TaskLine key={k.id} t={k} />
          ))}
          {adding === 'subtask' && (
            <NewLine>
              <Inline label="Subtask" placeholder="What needs doing" onSave={(title) => saved('task.subtask', { task: t.id, title, ...(t.owner_id && { owner: t.owner_id }) })} onEnd={end} />
            </NewLine>
          )}
          {fin.length > 0 && (
            <Group level={2} name="Done" count={fin.length} defaultOpen={false}>
              {fin.map((k) => (
                <TaskLine key={k.id} t={k} />
              ))}
            </Group>
          )}
        </Section>
      )}

      {code.length > 0 && (
        <Section name="Code" count={code.length}>
          {code.map((c, i) => (
            <a key={c.ref ?? i} className="row dt-link" href={c.url ?? undefined} target="_blank" rel="noreferrer" title={c.line}>
              <span className="row-mark">
                <Icon name="link" />
              </span>
              <span className="row-title">{c.line}</span>
              <span className="row-stage">{c.ref ?? ''}</span>
              <span className="row-age" />
            </a>
          ))}
        </Section>
      )}

      {shown.length > 0 && (
        <Section name="Files" count={shown.length} actions={plus('Add a file', () => onAdding('page'))}>
          {shown.map((f) => {
            const last = f.versions.at(-1);
            return (
              <Go key={f.id} to={details.to} search={fileSearch(f.id)} className="row dt-file" title={f.title || f.name}>
                <span className="row-mark">
                  <Icon name={f.mime.includes('html') || f.mime.includes('markdown') ? 'rules' : 'file'} />
                </span>
                <span className="row-title">{f.title || f.name}</span>
                <span className="row-stage">{f.current_version > 1 ? `v${f.current_version}` : ''}</span>
                {last ? <Face id={last.by} decorative /> : <span className="av-gap" />}
                {last ? <Ago at={last.created_at} className="row-age" /> : <span className="row-age" />}
              </Go>
            );
          })}
        </Section>
      )}

      <Section name="Facts" defaultOpen={false}>
        <Kv k="Number">{t.label || (t.number != null ? `#${t.number}` : t.id)}</Kv>
        <Kv k="Opened">
          <DateText at={t.created_at} />
          <small>
            by <NameOf id={t.requester_id} />
          </small>
        </Kv>
        <EditKv
          k="Part of"
          label="Part of"
          target={`task-parent:${t.id}`}
          value={t.parent ? <span>{t.parent.title}</span> : <small>None</small>}
          edit={(done) => <Inline label="Part of" placeholder="Task number" onSave={(parent) => saved('task.subtask', { task: parent, subtask: t.id })} onEnd={done} />}
        />
        <SizeFact t={t} />
        <EditKv
          k="Tags"
          target={`task-tags:${t.id}`}
          value={t.labels.length ? t.labels.map((l) => <Pill key={l}>{l}</Pill>) : <small>None</small>}
          edit={(done) => (
            <Inline label="Tags" initial={t.labels.join(', ')} placeholder="bug, checkout" onSave={(text) => saved('task.tag', { task: t.id, labels: tagsOf(text) })} onEnd={done} />
          )}
        />
        <MoreFacts t={t} />
        <PublicFacts t={t} />
        {request && (
          <div className="dt-kv">
            <span className="dt-k">Asked</span>
            <div className="dt-v dt-desc">
              {paragraphs(request).map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          </div>
        )}
      </Section>

      {(web.length > 0 || adding === 'link') && (
        <Section name="Links" count={web.length || undefined} actions={plus('Add a link', () => onAdding('link'))} open={linksOpen} onOpenChange={setLinksOpen} target={`task-links:${t.id}`}>
          {web.map((l) => (
            <ContextMenu key={l.id} items={<MenuItem label="Remove" icon="close" onSelect={() => void run('task.removeLink', { task: t.id, link: l.id }, { via: 'click' }).then((r) => r.ok || toast(failedToast(r)))} />}>
              <a className="row dt-link" href={l.url} target="_blank" rel="noreferrer" title={l.title || l.url}>
                <span className="row-mark">
                  <Icon name="link" />
                </span>
                <span className="row-title">{l.title || l.url}</span>
                <span className="row-stage">{domainOf(l.url)}</span>
                {l.created_at ? <Ago at={l.created_at} className="row-age" /> : <span className="row-age" />}
              </a>
            </ContextMenu>
          ))}
          {adding === 'link' && (
            <NewLine>
              <Inline label="Link" placeholder="https://" onSave={(url) => saved('task.link', { task: t.id, url: url.trim() })} onEnd={end} />
            </NewLine>
          )}
        </Section>
      )}

      {t.runs.length > 0 && (
        <Section name="Sessions" count={t.runs.length} defaultOpen={false}>
          {t.runs.map((r) => (
            <div key={r.run} className="row dt-ses" title={r.outcome ?? 'Running'}>
              <span className="row-mark" />
              <span className="row-title">{r.finished_at ? (r.outcome ?? 'Finished') : 'Running'}</span>
              <span className="row-stage">
                <NameOf id={r.bot.includes(':') ? r.bot : `bot:${r.bot}`} />
              </span>
              <Face id={r.bot.includes(':') ? r.bot : `bot:${r.bot}`} decorative />
              {r.started_at ? <Ago at={r.started_at} className="row-age" /> : <span className="row-age" />}
            </div>
          ))}
        </Section>
      )}
      <Meetings t={t} />
      {adding === 'page' && (
        <NewPage task={t.id} onClose={end} onSaved={(file) => void navigate({ to: details.to as never, search: fileSearch(file) as never })} />
      )}
    </PageBody>
  );
}

/** Details' one control of its own (more.js addBtn): what a task can be given, so a section not drawn yet has a way in. */
export function AddMenu({ onAdding }: { onAdding: (what: Adding) => void }) {
  return (
    <Menu
      trigger={
        <button className="tv-ver tv-add" type="button">
          <Icon name="plus" />
          Add
        </button>
      }
    >
      <MenuItem label="Subtask" onSelect={() => onAdding('subtask')} />
      <MenuItem label="Blocked by" onSelect={() => onAdding('depends_on')} />
      <MenuItem label="Blocks" onSelect={() => onAdding('blocks')} />
      <MenuItem label="Related task" onSelect={() => onAdding('related')} />
      <MenuRule />
      <MenuItem label="Ask someone" onSelect={() => onAdding('ask')} />
      <MenuRule />
      <MenuItem label="Link" onSelect={() => onAdding('link')} />
      <MenuItem label="File or page" onSelect={() => onAdding('page')} />
    </Menu>
  );
}
