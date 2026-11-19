import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { Button } from '@/kit/Button';
import { useActors } from '@/data';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Select } from '@/kit/Select';
import { Table, Th } from '@/kit/Table';
import { fmtAge, fmtDate, fmtMoney } from '@/lib';
import { FiguresStrip, List, Record, RecordPanel, Section, SidePanel, type PageTab } from '@/pagekinds';
import type { Risk, RiskDetail } from '@/api/schema';
import { Face, Go, PanelLink, useDo } from './parts';
import { mattersQuery, riskQuery, risksQuery, type MatterRow } from './queries';
import './company.css';

/* Risk (pages/10-records/records.js riskPage): a List of risks by level, a live one with its open matters under it.
   A risk opens as its own Record (`?risk=<id>`), a matter in the panel (`?panel=matter:<task>`). Disputes is the
   second tab: every dispute in one table. */

export interface RiskSearch {
  risk?: string;
  panel?: string;
}
export type RiskView = 'risks' | 'disputes';

const BASE = '/company/risk';
const tabs: readonly PageTab[] = [
  { label: 'Risks', to: BASE, target: 'tab:risks' },
  { label: 'Disputes', to: `${BASE}/disputes`, target: 'tab:disputes' },
];
const LEVELS: [Risk['level'], string][] = [
  ['live', 'Live'],
  ['open', 'Open'],
  ['quiet', 'Quiet'],
];

/** Claimed money, in whole units of its currency (the server keeps minor units). */
export function money(amount: number | null | undefined, currency: string | null | undefined): string {
  if (amount == null) return '';
  const c = (currency ?? '').toUpperCase();
  return !c || c === 'USD' ? fmtMoney(amount / 100) : `${Math.round(amount / 100).toLocaleString('en-US')} ${c}`;
}
const claimed = (r: Risk) => r.claimed.map((c) => money(c.amount, c.currency)).join(', ');
const taskPath = (t: MatterRow['task']) => (t.pipeline_id && t.number != null ? `/tasks/${encodeURIComponent(t.pipeline_id)}/${t.number}` : `/task/${encodeURIComponent(t.id)}`);

export function RiskPage({ view = 'risks', search }: { view?: RiskView; search: RiskSearch }) {
  if (search.risk) return <OneRisk id={search.risk} />;
  return view === 'disputes' ? <Disputes search={search} /> : <Risks search={search} />;
}

function panelFor(panel: string | undefined, rows: MatterRow[]) {
  if (panel === 'new') return <NewRisk />;
  const task = panel?.startsWith('matter:') ? panel.slice(7) : null;
  const row = task ? rows.find((m) => m.task.id === task) : undefined;
  return row ? <MatterPanel row={row} /> : null;
}

// ---------------------------------------------------------------- the list

function Risks({ search }: { search: RiskSearch }) {
  const q = useQuery(risksQuery);
  const matters = useQuery(mattersQuery('open'));
  const open = useDo('company.openPanel');
  const risks = q.data?.risks ?? [];
  const rows = matters.data?.matters ?? [];
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : risks.length ? 'ready' : 'empty';
  const add = (
    <Button variant={search.panel ? 'plain' : 'primary'} icon="plus" onClick={() => void open.run({ panel: 'new' })}>
      Add risk
    </Button>
  );
  return (
    <List
      title="Risk"
      path={BASE}
      tabs={tabs}
      className="co"
      actions={add}
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No risks yet', action: add }}
      sections={LEVELS.map(([level, name]) => {
        const list = risks.filter((r) => r.level === level);
        if (!list.length) return null;
        return (
          <Section key={level} name={name} count={list.length}>
            {level === 'live'
              ? list.map((r) => <LiveRisk key={r.id} risk={r} matters={rows.filter((m) => m.risks.some((x) => x.id === r.id))} selected={search.panel} />)
              : list.map((r) => <RiskRow key={r.id} risk={r} />)}
          </Section>
        );
      })}
      panel={panelFor(search.panel, rows)}
    />
  );
}

/** A live risk heads its open matters: its name opens the risk, the fold hides the matters. */
function LiveRisk({ risk: r, matters, selected }: { risk: Risk; matters: MatterRow[]; selected?: string }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rk-live">
      <div className="row rk-r is-live">
        <button type="button" className="rk-fold" aria-expanded={open} aria-label={open ? `Fold ${r.label}` : `Unfold ${r.label}`} onClick={() => setOpen(!open)}>
          <Icon name="chevdown" />
        </button>
        <Link to={BASE as never} search={{ risk: r.id } as never} className="row-title rk-name" data-target={`risk:${r.id}`}>
          {r.label}
        </Link>
        <span className="rk-n">{r.open} open</span>
        <span className="rk-fig">{claimed(r) && `${claimed(r)} claimed`}</span>
        <span className="rk-rules">{r.has_rules && <Icon name="rules" />}</span>
      </div>
      {open &&
        matters.map((m) => (
          <PanelLink
            key={m.task.id}
            panel={`matter:${m.task.id}`}
            className={selected === `matter:${m.task.id}` ? 'row rk-m is-in is-selected' : 'row rk-m is-in'}
            data-target={`matter:${m.task.id}`}
          >
            <span className="row-mark" />
            <span className="row-title">{m.task.title ?? m.task.label}</span>
            <span className="row-pills" />
            <span className="rk-stage">{m.task.stage ?? ''}</span>
            <Face id={m.task.owner_id} />
            <span className="row-age">{m.task.created_at ? fmtAge(m.task.created_at) : ''}</span>
          </PanelLink>
        ))}
      {open && r.closed > 0 && (
        <Link to={BASE as never} search={{ risk: r.id } as never} className="rk-sub">
          {r.closed} closed
        </Link>
      )}
    </div>
  );
}

function RiskRow({ risk: r }: { risk: Risk }) {
  return (
    <Link to={BASE as never} search={{ risk: r.id } as never} className={r.level === 'quiet' ? 'row rk-r is-quiet' : 'row rk-r'} data-target={`risk:${r.id}`}>
      <span className="row-mark" />
      <span className="row-title rk-name">{r.label}</span>
      <span className="rk-what">{r.next_date ? `Next ${fmtDate(r.next_date)}` : ''}</span>
      <span className="rk-n">{r.open ? `${r.open} open` : ''}</span>
      <span className="rk-fig">{claimed(r)}</span>
      <span className="rk-rules">{r.has_rules && <Icon name="rules" />}</span>
    </Link>
  );
}

// ---------------------------------------------------------------- disputes

function Disputes({ search }: { search: RiskSearch }) {
  const q = useQuery(mattersQuery('disputes'));
  const rows = q.data?.matters ?? [];
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : rows.length ? 'ready' : 'empty';
  const groups: [string, MatterRow[]][] = [
    ['Open', rows.filter((m) => m.task.status === 'open')],
    ['Closed', rows.filter((m) => m.task.status !== 'open')],
  ];
  return (
    <List
      title="Risk"
      path={`${BASE}/disputes`}
      tabs={tabs}
      className="co"
      state={state}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No disputes' }}
      sections={groups
        .filter(([, list]) => list.length)
        .map(([name, list]) => (
          <Section key={name} name={name} count={list.length}>
            <Table label={`${name} disputes`}>
              <thead>
                <tr>
                  <th className="t-mark" aria-hidden="true" />
                  <th className="t-title">Name</th>
                  <Th>Risk</Th>
                  <Th num>Amount</Th>
                  <Th>Forum</Th>
                  <Th>Stage</Th>
                  <th className="num t-who">Owner</th>
                </tr>
              </thead>
              <tbody>
                {list.map((m) => (
                  <tr key={m.task.id} className={name === 'Closed' ? 'is-quiet' : undefined}>
                    <td className="t-mark" />
                    <td className="t-title">
                      <PanelLink panel={`matter:${m.task.id}`} data-target={`matter:${m.task.id}`}>
                        {m.task.title ?? m.task.label}
                      </PanelLink>
                    </td>
                    <td className="t-words">{m.risks.map((r) => r.label).join(', ')}</td>
                    <td className="num">{money(m.matter?.amount, m.matter?.currency)}</td>
                    <td className="t-words">{m.matter?.forum}</td>
                    <td className="t-words">{m.task.stage}</td>
                    <td className="num t-who">
                      <Face id={m.task.owner_id} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Section>
        ))}
      panel={panelFor(search.panel, rows)}
    />
  );
}

// ---------------------------------------------------------------- one matter, one risk

const KIND: Record<string, string> = { dispute: 'Dispute', request: 'Request', notice: 'Notice', incident: 'Incident' };
const SIDE: Record<string, string> = { theirs: 'Theirs', ours: 'Ours' };

function MatterPanel({ row }: { row: MatterRow }) {
  const close = useDo('company.closePanel');
  const m = row.matter;
  const fact = (label: string, value: string | null | undefined) =>
    value ? (
      <div className="fact fld">
        <span>{label}</span>
        <span className="fact-v">
          <span>{value}</span>
        </span>
      </div>
    ) : null;
  return (
    <RecordPanel
      title={row.task.title ?? row.task.label ?? 'Matter'}
      target={`matter:${row.task.id}`}
      actions={
        <Go to={taskPath(row.task)} className="btn">
          Open task
        </Go>
      }
      onClose={() => void close.run({})}
      facts={
        <div className="rec-flds">
          {fact('Kind', m && KIND[m.kind])}
          {fact('Party', m?.party?.name)}
          {fact('Amount', m && money(m.amount, m.currency))}
          {fact('Claim', m?.claim)}
          {fact('Side', m && SIDE[m.side])}
          {fact('Forum', m?.forum)}
          {fact('Product', m?.product?.name)}
          {fact('Due', row.task.due_at ? fmtDate(row.task.due_at) : null)}
        </div>
      }
      sections={
        row.risks.length > 0 && (
          <section className="rail-sec" aria-label="Risks">
            <h3>Risks</h3>
            {row.risks.map((r) => (
              <Link key={r.id} to={BASE as never} search={{ risk: r.id } as never} className="row lk">
                <Icon name="rules" />
                <span className="row-title">{r.label}</span>
              </Link>
            ))}
          </section>
        )
      }
    />
  );
}

/** One risk as its own page: figures, its matters, its dates, the rules and sources it rests on. */
function OneRisk({ id }: { id: string }) {
  const q = useQuery(riskQuery(id));
  const d = q.data;
  const state = q.isPending ? 'loading' : q.isError ? 'fault' : 'ready';
  return (
    <Record
      title={d?.risk.label ?? 'Risk'}
      path={BASE}
      crumb={[{ label: 'Risk', to: BASE }]}
      className="co"
      state={state}
      onRetry={() => void q.refetch()}
      facts={d && <RiskFacts d={d} />}
      figures={
        d && (
          <FiguresStrip
            label="Risk figures"
            figures={[
              { label: 'Claimed', value: claimed(d.risk) || '$0' },
              { label: 'Next date', value: d.risk.next_date ? fmtDate(d.risk.next_date) : 'None' },
              { label: 'Open', value: d.risk.open },
              { label: 'Closed', value: d.risk.closed },
            ]}
          />
        )
      }
      sections={d && <RiskSections d={d} />}
    />
  );
}

/** Who owns the risk: a quiet pick that saves at once, for those who may change it. */
function RiskFacts({ d }: { d: RiskDetail }) {
  const update = useDo('risk.update');
  const actors = useActors();
  const r = d.risk;
  return (
    <div className="fact fld">
      <span>Owner</span>
      <span className="fact-v">
        {d.editable ? (
          <Select
            label="Owner"
            labelHidden
            look="quiet"
            placeholder="None"
            options={actors.map((a) => ({ value: a.id, label: a.name, meta: a.kind === 'agent' ? 'Agent' : undefined }))}
            value={r.owner}
            onValueChange={(owner) => void update.run({ risk: r.id, version: r.version, owner })}
          />
        ) : (
          <Face id={r.owner} />
        )}
      </span>
    </div>
  );
}

type Linked = { link_id: string; id?: string; text?: string; name?: string };

function RiskSections({ d }: { d: RiskDetail }) {
  const rules = d.rules as Linked[];
  const sources = d.sources as Linked[];
  return (
    <>
      {d.matters.length > 0 && (
        <Section name="Matters" count={d.matters.length}>
          {d.matters.map((m) => (
            <Go key={m.task.id} to={taskPath(m.task)} className={m.task.status === 'open' ? 'row rk-m is-one' : 'row rk-m is-one is-quiet'} data-target={`matter:${m.task.id}`}>
              <span className="row-mark" />
              <span className="row-title">{m.task.title ?? m.task.label}</span>
              <span className="row-pills" />
              <span className="rk-amt">{money(m.matter?.amount, m.matter?.currency)}</span>
              <span className="rk-forum">{m.matter?.forum}</span>
              <span className="rk-stage">{m.task.stage}</span>
              <Face id={m.task.owner_id} />
              <span className="row-age">{m.task.created_at ? fmtAge(m.task.created_at) : ''}</span>
            </Go>
          ))}
        </Section>
      )}
      {d.obligations.length > 0 && (
        <Section name="Dates" count={d.obligations.length}>
          {d.obligations.map((o) => (
            <div key={o.id} className={o.mark ? 'row rk-d is-done' : 'row rk-d'}>
              <span className="row-mark">{o.mark && <Icon name="check" />}</span>
              <span className="row-title">{o.rule.text}</span>
              <span className="rk-basis">{o.basis}</span>
              <span className="rk-proof">{o.mark?.proof?.name ?? ''}</span>
              <Face id={o.mark?.satisfied_by} />
              <span className="row-age">{fmtDate(o.due_on)}</span>
            </div>
          ))}
        </Section>
      )}
      {(rules.length > 0 || sources.length > 0) && (
        <div className="two-col">
          {rules.length > 0 && (
            <Section name="Rules" count={rules.length}>
              {rules.map((r) => (
                <Go key={r.link_id} to="/company/rules" search={{ rule: r.id }} className="row rk-l">
                  <span className="row-mark" />
                  <span className="row-title">
                    <Icon name="rules" />
                    <span>{r.text ?? r.name}</span>
                  </span>
                </Go>
              ))}
            </Section>
          )}
          {sources.length > 0 && (
            <Section name="Sources" count={sources.length}>
              {sources.map((s) => (
                <Go key={s.link_id} to="/library/contacts" search={{ contact: s.id }} className="row rk-l">
                  <span className="row-mark" />
                  <span className="row-title">
                    <Icon name="people" />
                    <span>{s.name ?? s.text}</span>
                  </span>
                </Go>
              ))}
            </Section>
          )}
        </div>
      )}
      {d.risk.markdown && (
        <div className="rk-text">
          {d.risk.markdown
            .split(/\n\s*\n/)
            .filter((p) => p.trim())
            .map((p, i) => (
              <p key={i}>{p.trim()}</p>
            ))}
        </div>
      )}
    </>
  );
}

function NewRisk() {
  const create = useDo('risk.create');
  const close = useDo('company.closePanel');
  const [label, setLabel] = useState('');
  const save = async () => {
    if (label.trim() && (await create.run({ label: label.trim() }))) void close.run({});
  };
  return (
    <SidePanel
      title="New risk"
      target="panel:risk-new"
      onClose={() => void close.run({})}
      foot={
        <>
          <Button variant="primary" busy={create.pending} onClick={() => void save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={() => void close.run({})}>
            Cancel
          </Button>
        </>
      }
    >
      <form
        className="rail-sec"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Field label="Name" value={label} onValueChange={setLabel} autoFocus />
      </form>
    </SidePanel>
  );
}
