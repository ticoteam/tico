import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { TaskRequirement } from '@/api/schema';
import { Ago } from '@/kit/Ago';
import { DateText } from '@/kit/DateText';
import { Icon } from '@/kit/Icon';
import { LatePill, Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { Section as Group } from '@/kit/SectionHead';
import { PageBody, Section } from '@/pagekinds';
import { DETAILS, tabAddress } from './model';
import { Face, Mark, NameOf, Go } from './parts';
import { taskFilesQuery, type TaskData } from './queries';

/* Details (more.js detailsBody): named sections in a fixed order, from what it is judged by, to the work, to the
   record. A section with nothing in it is not drawn; what is finished folds into one line; the record starts folded. */

const taskHref = (n: number | null | undefined, id: string) => `/task/${n ?? id}`;

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

function Kv({ k, children, target }: { k: string; children: ReactNode; target?: string }) {
  return (
    <div className="dt-kv" data-target={target}>
      <span className="dt-k">{k}</span>
      <span className="dt-v">{children}</span>
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

function Tasks({ name, rows }: { name: string; rows: Linked[] }) {
  if (!rows.length) return null;
  return (
    <Section name={name} count={rows.length}>
      {rows.map((t) => (
        <TaskLine key={t.id} t={t} />
      ))}
    </Section>
  );
}

const paragraphs = (text: string) => text.split(/\n\s*\n/).filter((p) => p.trim());

export function Details({ t, flow }: { t: TaskData; flow: boolean }) {
  const files = useQuery(taskFilesQuery(t.id));
  const criteria = t.acceptance_criteria ?? [];
  const leave = t.to_leave ?? [];
  const left = leave.filter((r) => !r.met);
  const met = leave.filter((r) => r.met);
  const kids: Linked[] = t.children.map((c) => ({ id: c.id, title: c.title, owner: c.owner_id, stage: c.step, done: c.status !== 'open' }));
  const open = kids.filter((k) => !k.done);
  const fin = kids.filter((k) => k.done);
  const related = Object.values(t.relations ?? {})
    .flat()
    .map((x) => ({ id: x.id, title: x.title, number: x.number, owner: x.owner_id, stage: x.stage, done: x.status !== 'open' }));
  const code = (t.inputs ?? []).filter((x) => x.kind === 'pr');
  const shown = (files.data ?? []).filter((f) => !f.archived);
  const details = tabAddress(t, DETAILS, flow);
  const fileSearch = (id: string) => (flow ? { tab: DETAILS, file: id } : { file: id });
  const request = t.request?.text || t.body || '';

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
        <Kv k="Has it" target={`task-owner-line:${t.id}`}>
          <Face id={t.owner_id} />
          <span>
            <NameOf id={t.owner_id} />
          </span>
          {t.stage_since && t.stage && (
            <small>
              <Ago at={t.stage_since} /> in {t.stage.name}
            </small>
          )}
        </Kv>
        {t.turn?.who && t.turn.who !== t.owner_id && (
          <Kv k="Waiting on">
            <Face id={t.turn.who} />
            <span>
              <NameOf id={t.turn.who} />
            </span>
            {t.turn.line && <small>{t.turn.line}</small>}
          </Kv>
        )}
        {t.due_at && (
          <Kv k="Due" target={`task-due:${t.id}`}>
            <DateText at={t.due_at} />
            {t.late && <LatePill />}
          </Kv>
        )}
      </Section>

      {leave.length > 0 && (
        <Section name={`To leave ${t.stage?.name ?? 'this stage'}`} count={leave.length}>
          {left.map((r) => (
            <Requirement key={r.requirement_id} r={r} />
          ))}
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
      />
      <Tasks name="Blocks" rows={(t.blocks ?? []).map((b) => ({ id: b.id, title: b.label || `#${b.number}`, number: b.number }))} />
      <Tasks name="Related" rows={related} />

      {kids.length > 0 && (
        <Section name="Subtasks" count={kids.length}>
          {open.map((k) => (
            <TaskLine key={k.id} t={k} />
          ))}
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
        <Section name="Files" count={shown.length}>
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
        {t.labels.length > 0 && (
          <Kv k="Tags" target={`task-tags:${t.id}`}>
            {t.labels.map((l) => (
              <Pill key={l}>{l}</Pill>
            ))}
          </Kv>
        )}
        {t.public_title && <Kv k="Public title">{t.public_title}</Kv>}
        {t.public_summary && <Kv k="Public summary">{t.public_summary}</Kv>}
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
    </PageBody>
  );
}
