import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useRef, useState, type ReactNode } from 'react';
import { Button } from '@/kit/Button';
import { useActors } from '@/data';
import { Field } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { FoldButton } from '@/kit/SectionHead';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { Table, Tr } from '@/kit/Table';
import { toast } from '@/kit/Toast';
import { fmtAge, fmtDate, fmtMoney } from '@/lib';
import { FiguresStrip, List, PageMore, Record, RecordPanel, Section, SidePanel } from '@/pagekinds';
import type { Risk, RiskDetail } from '@/api/schema';
import { Markdown } from '@/kit/Markdown';
import { Face, Go, NameOf, PageOwner, PanelLink, PanelSection, useDo } from './parts';
import { fileSearchQuery, partySearchQuery, productsQuery, matterDatesQuery, matterFilesQuery, mattersQuery, riskQuery, risksQuery, waitingTasksQuery, type MatterRow, type RiskDate } from './queries';
import './company.css';

/* Risk (pages/10-records/records.js riskPage): a List of risks by level, a live one with its open matters under it.
   A risk opens as its own Record (`?risk=<id>`), a matter in the panel (`?panel=matter:<task>`), on the list and on
   the one risk alike. The switch in the head shows Disputes instead: every dispute in one table. */

export interface RiskSearch {
  risk?: string;
  panel?: string;
}
export type RiskView = 'risks' | 'disputes';

const BASE = '/company/risk';
const VIEWS = [
  { value: 'risks', label: 'Risks' },
  { value: 'disputes', label: 'Disputes' },
] as const;

/** Risk's head: the Risks or Disputes switch after the title, then the owner's face. */
function useHead(view: RiskView) {
  const show = useDo('risk.view');
  return {
    title: 'Risk',
    path: view === 'disputes' ? `${BASE}/disputes` : BASE,
    className: 'co',
    after: <Segmented label="Show" options={VIEWS} value={view} onValueChange={(v) => void show.run({ view: v })} />,
  };
}
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
/** As backend/risk.py NEW_DAYS: an open matter this new is marked New. */
const NEW_DAYS = 7;
const isNew = (m: MatterRow) => {
  const at = Date.parse(m.matter?.created_at || m.task.created_at || '');
  return m.task.status === 'open' && Number.isFinite(at) && Date.now() - at < NEW_DAYS * 86_400_000;
};
/** The matters that wait on the reader (their Needs you). */
function useWaiting(): Set<string> {
  return new Set(useQuery(waitingTasksQuery).data ?? []);
}
/** A matter row's mark and pills: the waits-on-you mark, and New while it is (records.js matterRow). */
function MatterMark({ m, waiting }: { m: MatterRow; waiting: Set<string> }) {
  return <span className="row-mark">{waiting.has(m.task.id) && <FlowMark size={10} />}</span>;
}
function NewPill({ m }: { m: MatterRow }) {
  return <span className="row-pills">{isNew(m) && <Pill>New</Pill>}</span>;
}

export function RiskPage({ view = 'risks', search }: { view?: RiskView; search: RiskSearch }) {
  if (search.risk) return <OneRisk key={search.risk} id={search.risk} panel={search.panel} />;
  return view === 'disputes' ? <Disputes search={search} /> : <Risks search={search} />;
}

function panelFor(panel: string | undefined, rows: MatterRow[]) {
  if (panel === 'new') return <NewRisk />;
  const task = panel?.startsWith('matter:') ? panel.slice(7) : null;
  const row = task ? rows.find((m) => m.task.id === task) : undefined;
  return row ? <MatterPanel key={row.task.id} row={row} /> : null;
}

/** One risk's own panels: a matter, a new matter, Edit, one date (`due:<id>:<day>`). */
function onePanel(panel: string | undefined, d: RiskDetail) {
  if (panel === 'matter-new') return <NewMatter risk={d.risk.id} />;
  if (panel === 'edit' && d.editable) return <EditRisk d={d} />;
  if (panel?.startsWith('due:')) {
    const [, due, on] = panel.split(':');
    const date = d.obligations.find((o) => o.id === due && o.due_on === on);
    return date ? <DatePanel key={panel} risk={d.risk.id} d={date} /> : null;
  }
  return panelFor(panel, d.matters);
}

// ---------------------------------------------------------------- the list

function Risks({ search }: { search: RiskSearch }) {
  const { risks } = useSuspenseQuery(risksQuery).data;
  const matters = useQuery(mattersQuery('open'));
  const open = useDo('company.openPanel');
  const rows = matters.data?.matters ?? [];
  const add = (
    <Button variant={search.panel ? 'plain' : 'primary'} onClick={() => void open.run({ panel: 'new' })}>
      Add risk
    </Button>
  );
  return (
    <List
      {...useHead('risks')}
      actions={
        <>
          <PageOwner page="risk" />
          {add}
        </>
      }
      state={risks.length ? 'ready' : 'empty'}
      // One line; Add risk stays the head's one filled button (records.js risk, state empty).
      empty={{ title: 'No risks yet' }}
      sections={LEVELS.map(([level, name]) => {
        const list = risks.filter((r) => r.level === level);
        if (!list.length) return null;
        return (
          <Section key={level} name={name} count={list.length}>
            {level === 'live'
              ? list.map((r) => <LiveRisk key={r.id} risk={r} matters={rows.filter((m) => m.risks.some((x) => x.id === r.id))} selected={search.panel} />)
              : list.map((r) => <RiskRow key={r.id} risk={r} matters={rows.filter((m) => m.risks.some((x) => x.id === r.id))} />)}
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
  const waiting = useWaiting();
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
            <MatterMark m={m} waiting={waiting} />
            <span className="row-title">{m.task.title ?? m.task.label}</span>
            <NewPill m={m} />
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

/** An Open risk shows who its open matters are with; a Quiet one its next date and how many it closed. */
function RiskRow({ risk: r, matters }: { risk: Risk; matters: MatterRow[] }) {
  const quiet = r.level === 'quiet';
  const parties = [...new Set(matters.map((m) => m.matter?.party?.name).filter(Boolean))].join(', ');
  return (
    <Link to={BASE as never} search={{ risk: r.id } as never} className={quiet ? 'row rk-r is-quiet' : 'row rk-r'} data-target={`risk:${r.id}`}>
      <span className="row-mark" />
      <span className="row-title rk-name">{r.label}</span>
      <span className="rk-what">{quiet ? (r.next_date ? fmtDate(r.next_date) : '') : parties}</span>
      <span className="rk-n">{quiet ? (r.closed ? `${r.closed} closed` : 'None') : `${r.open} open`}</span>
      <span className="rk-fig" />
      <span className="rk-rules">{r.has_rules && <Icon name="rules" />}</span>
    </Link>
  );
}

// ---------------------------------------------------------------- disputes

/** What the other side claims across the open disputes, as the risks' totals count it (not our own claims). */
function openClaimed(rows: MatterRow[]): string {
  const sums = new Map<string, number>();
  for (const m of rows) if (m.matter?.amount && m.matter.side !== 'ours') sums.set(m.matter.currency || 'USD', (sums.get(m.matter.currency || 'USD') ?? 0) + m.matter.amount);
  return [...sums].map(([c, a]) => money(a, c)).join(' + ');
}

function Disputes({ search }: { search: RiskSearch }) {
  const rows = useSuspenseQuery(mattersQuery('disputes')).data.matters;
  const waiting = useWaiting();
  const open = useDo('company.openPanel');
  const [shut, setShut] = useState<readonly string[]>([]);
  const groups: [string, MatterRow[]][] = [
    ['Open', rows.filter((m) => m.task.status === 'open')],
    ['Closed', rows.filter((m) => m.task.status !== 'open')],
  ];
  return (
    <List
      {...useHead('disputes')}
      actions={
        <>
          <PageOwner page="risk" />
          <Button variant={search.panel ? 'plain' : 'primary'} onClick={() => void open.run({ panel: 'new' })}>
            Add risk
          </Button>
        </>
      }
      state={rows.length ? 'ready' : 'empty'}
      empty={{ title: 'No disputes' }}
      // One table, its head once at the top; Open and Closed are named rows in it that fold (records.js disputesPage).
      sections={
        <Table label="Disputes" stick>
          <thead>
            <tr>
              <th className="t-mark" aria-hidden="true" />
              <th scope="col" className="t-name">
                Name
              </th>
              <th scope="col" className="c-risk">
                Risk
              </th>
              <th scope="col" className="num c-amt">
                Amount
              </th>
              <th scope="col" className="c-forum">
                Forum
              </th>
              <th scope="col" className="c-stage">
                Stage
              </th>
              <th scope="col" className="c-next">
                Next
              </th>
              <th className="t-who" aria-label="Owner" />
            </tr>
          </thead>
          {groups
            .filter(([, list]) => list.length)
            .map(([name, list]) => {
              const folded = shut.includes(name);
              return (
                <tbody key={name} aria-label={`${name} disputes`}>
                  <tr className="is-group">
                    <td className="t-mark" />
                    <td className="t-name" colSpan={2}>
                      <span className="sec-name">
                        <FoldButton name={name} count={list.length} open={!folded} onToggle={() => setShut(folded ? shut.filter((x) => x !== name) : [...shut, name])} />
                      </span>
                    </td>
                    <td className="num">{name === 'Open' ? openClaimed(list) : ''}</td>
                    <td colSpan={4} />
                  </tr>
                  {!folded &&
                    list.map((m) => (
                      <Tr key={m.task.id} kind={name === 'Closed' ? 'quiet' : undefined} selected={search.panel === `matter:${m.task.id}`} linked>
                        <td className="t-mark">{waiting.has(m.task.id) && <FlowMark size={10} />}</td>
                        <td className="t-name" data-opens>
                          <PanelLink panel={`matter:${m.task.id}`} data-target={`matter:${m.task.id}`}>
                            {m.task.title ?? m.task.label}
                          </PanelLink>
                          {isNew(m) && <Pill>New</Pill>}
                        </td>
                        <td className="t-words">{m.risks.map((r) => r.label).join(', ')}</td>
                        <td className="num">{money(m.matter?.amount, m.matter?.currency)}</td>
                        <td className="t-words c-forum">{m.matter?.forum}</td>
                        <td className="t-words">{m.task.stage}</td>
                        <td className="t-words c-next">{m.task.status === 'open' && m.task.due_at ? fmtDate(m.task.due_at) : ''}</td>
                        <td className="num t-who">
                          <Face id={m.task.owner_id} />
                        </td>
                      </Tr>
                    ))}
                </tbody>
              );
            })}
        </Table>
      }
      panel={panelFor(search.panel, rows)}
    />
  );
}

// ---------------------------------------------------------------- one matter, one risk

const KIND: Record<string, string> = { dispute: 'Dispute', request: 'Request', notice: 'Notice', incident: 'Incident' };
const BASIS: Record<string, string> = { law: 'Law', order: 'Order', agency: 'Agency', contract: 'Contract', ours: 'Ours', asked: 'Asked', theirs: 'Theirs' };
const SIDE: Record<string, string> = { theirs: 'Theirs', ours: 'Ours' };

function MatterPanel({ row }: { row: MatterRow }) {
  const close = useDo('company.closePanel');
  const fact = (label: string, value: string | null | undefined, to?: string, search?: Record<string, string>) =>
    value ? (
      <div className="fact fld">
        <span>{label}</span>
        <span className="fact-v">
          {to ? (
            <Go to={to} search={search} className="fld-link">
              {value}
            </Go>
          ) : (
            <span>{value}</span>
          )}
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
          <MatterFacts row={row} />
          {fact('Due', row.task.due_at ? fmtDate(row.task.due_at) : null)}
        </div>
      }
      sections={
        <>
          {row.risks[0] && <MatterDates task={row.task.id} risk={row.risks[0].id} />}
          {row.risks.length > 0 && (
            <PanelSection name="Risks" count={row.risks.length}>
              {row.risks.map((r) => (
                <Link key={r.id} to={BASE as never} search={{ risk: r.id } as never} className="row lk">
                  <Icon name="rules" />
                  <span className="row-title">{r.label}</span>
                </Link>
              ))}
            </PanelSection>
          )}
          <MatterFiles task={row.task.id} />
        </>
      }
    />
  );
}

/** Minor units from what was typed ("1,240.50" is 124050, "1,240.50 EUR" names the currency), null for a blank,
    undefined for anything else (risk.ts rkMinor). */
export function typedAmount(typed: string): { amount: number | null; currency?: string } | undefined {
  const t = typed.trim();
  if (!t) return { amount: null };
  const m = /^([$€£]?\s*[\d,]+(?:\.\d{1,2})?)\s*([A-Za-z]{3})?$/.exec(t);
  if (!m?.[1]) return undefined;
  const amount = Math.round(Number(m[1].replace(/[$€£,\s]/g, '')) * 100);
  return Number.isFinite(amount) ? { amount, ...(m[2] && { currency: m[2].toUpperCase() }) } : undefined;
}

/** A matter's own facts, each changed in place and saved at once (risk.ts rkFormHTML): kind and side as quiet picks,
    party and product picked and linked, amount, claim and forum typed. */
function MatterFacts({ row }: { row: MatterRow }) {
  const save = useDo('risk.setMatter');
  const m = row.matter;
  const set = (facts: Record<string, unknown>) => void save.run({ task: row.task.id, ...(m && { version: m.version }), ...facts });
  const pick = (label: string, map: Record<string, string>, value: string | null | undefined, key: string) => (
    <div className="fact fld">
      <span>{label}</span>
      <span className="fact-v">
        <Select label={label} labelHidden look="quiet" placeholder="None" options={Object.entries(map).map(([v, l]) => ({ value: v, label: l }))} value={value ?? null} onValueChange={(v) => set({ [key]: v })} />
      </span>
    </div>
  );
  const typed = (label: string, shown: string, start: string, save: (v: string) => void) => (
    <div className="fact fld">
      <span>{label}</span>
      <span className="fact-v">
        <EditFact label={label} shown={shown} start={start} onSave={save} />
      </span>
    </div>
  );
  const product = m?.product;
  return (
    <>
      {pick('Kind', KIND, m?.kind, 'kind')}
      <LinkedFact
        label="Party"
        name={m?.party?.name}
        to="/library/contacts"
        search={m?.party ? { ...(m.party.id.startsWith('organization/') && { type: 'organization' }), panel: m.party.id } : undefined}
        choose={(close) => <PartyPick onPick={(id, name) => (set({ party: id, ...(name && { party_name: name }) }), close())} clearable={!!m?.party} />}
      />
      {typed('Amount', m ? money(m.amount, m.currency) : '', m?.amount != null ? `${m.amount / 100}${m.currency && m.currency !== 'USD' ? ` ${m.currency}` : ''}` : '', (v) => {
        const a = typedAmount(v);
        if (!a) return void toast({ text: 'Check the fields', name: 'Amount is a number', kind: 'failed' });
        set({ amount: a.amount, currency: a.amount == null ? null : (a.currency ?? m?.currency ?? 'USD') });
      })}
      {typed('Claim', m?.claim ?? '', m?.claim ?? '', (v) => set({ claim: v.trim() || null }))}
      {pick('Side', SIDE, m?.side, 'side')}
      {typed('Forum', m?.forum ?? '', m?.forum ?? '', (v) => set({ forum: v.trim() || null }))}
      <LinkedFact
        label="Product"
        name={product?.name}
        to="/company/products"
        search={product ? { panel: product.id.replace(/^product\//, '') } : undefined}
        choose={(close) => <ProductPick value={m?.product_id ?? null} onPick={(id, name) => (set({ product: id, ...(name && { product_name: name }) }), close())} />}
      />
    </>
  );
}

/** A typed fact: its value as a quiet button; pressed, a field where Enter saves and Escape puts it back. */
function EditFact({ label, shown, start, onSave }: { label: string; shown: string; start: string; onSave: (v: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft == null)
    return (
      <button type="button" className="fld-v" title={`Change ${label.toLowerCase()}`} onClick={() => setDraft(start)}>
        <span className={shown ? undefined : 'none'}>{shown || 'None'}</span>
      </button>
    );
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setDraft(null);
        if (draft.trim() !== start.trim()) onSave(draft);
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          setDraft(null);
        }
      }}
    >
      <Field label={label} labelHidden value={draft} onValueChange={setDraft} autoFocus />
    </form>
  );
}

/** A fact that names another record: its link, and a quiet Change that opens the pick under it. */
function LinkedFact({ label, name, to, search, choose }: { label: string; name?: string | null; to: string; search?: Record<string, string>; choose: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="fact fld">
        <span>{label}</span>
        <span className="fact-v">
          {name ? (
            <Go to={to} search={search} className="fld-link">
              {name}
            </Go>
          ) : (
            <span className="none">None</span>
          )}
          <IconButton icon="edit" label={`Change ${label.toLowerCase()}`} size="sm" quiet on={open} onClick={() => setOpen(!open)} />
        </span>
      </div>
      {open && choose(() => setOpen(false))}
    </>
  );
}

/** The party from Contacts: people, then organizations, by name. */
function PartyPick({ onPick, clearable }: { onPick: (id: string | null, name?: string) => void; clearable: boolean }) {
  const [find, setFind] = useState('');
  const found = useQuery(partySearchQuery(find));
  return (
    <div className="mkt-ask">
      <Field label="Party" labelHidden placeholder="Contacts" value={find} onValueChange={setFind} autoFocus />
      {(found.data ?? []).map((p) => (
        <button key={p.id} type="button" className="rail-row" onClick={() => onPick(p.id, p.name)}>
          <Icon name="people" />
          <span className="row-title">{p.name}</span>
        </button>
      ))}
      {found.isSuccess && !found.data.length && <p className="none">None found</p>}
      {clearable && (
        <Button variant="quiet" onClick={() => onPick(null)}>
          No party
        </Button>
      )}
    </div>
  );
}

/** The product from our products. */
function ProductPick({ value, onPick }: { value: string | null; onPick: (id: string | null, name?: string) => void }) {
  const q = useQuery(productsQuery);
  const options = [{ value: '', label: 'None' }, ...(q.data?.products ?? []).map((p) => ({ value: p.id, label: p.name }))];
  return (
    <div className="mkt-ask">
      <Select label="Product" labelHidden placeholder="Product" options={options} value={value ?? ''} onValueChange={(v) => onPick(v || null, options.find((o) => o.value === v)?.label)} />
    </div>
  );
}

/** A date's mark: a tick when met, Flow's mark when it waits, a hollow ring while it is due (records.js dMark). */
function DateMark({ d }: { d: RiskDate }) {
  if (d.mark && d.mark.outcome === 'met') return <span className="row-check" title="Done"><Icon name="check" /></span>;
  return <i className="hollow" title="Due" />;
}
const soon = (on: string) => {
  const days = (Date.parse(`${on}T12:00:00`) - Date.now()) / 86_400_000;
  return days >= 0 && days <= 14;
};
const proofWords = (p: NonNullable<RiskDate['mark']>['proof']) => (!p ? '' : p.kind === 'email' ? 'Sent mail' : p.kind === 'file' ? p.name || 'File' : p.ref || 'Link');

/** The matter's own dates, from its risk's (records.js matterRec Dates): each opens on its risk's page. */
function MatterDates({ task, risk }: { task: string; risk: string }) {
  const q = useQuery(matterDatesQuery(risk));
  const dates = (q.data ?? []).filter((d) => d.task?.id === task);
  if (!dates.length) return null;
  return (
    <PanelSection name="Dates" count={dates.length}>
      {dates.map((d) => (
        <div key={`${d.id}:${d.due_on}`} className={d.mark ? 'dt is-done' : 'dt'}>
          <span className="dt-mark">
            <DateMark d={d} />
          </span>
          <Go to={BASE} search={{ risk, panel: `due:${d.id}:${d.due_on}` }} className="row-title">
            {d.title || d.rule.text}
          </Go>
          <span className={!d.mark && soon(d.due_on) ? 'row-age is-due-soon' : 'row-age'}>{fmtDate(d.due_on)}</span>
          {d.mark?.proof && (
            <div className="dt-proof">
              <span className="chip-file">
                <Icon name={d.mark.proof.kind === 'email' ? 'mail' : d.mark.proof.kind === 'file' ? 'file' : 'link'} />
                <span>{proofWords(d.mark.proof)}</span>
              </span>
              <Face id={d.mark.satisfied_by} />
            </div>
          )}
        </div>
      ))}
    </PanelSection>
  );
}

/** The files on the matter's task, each opening as itself, and Add a file from the person's disk. */
function MatterFiles({ task }: { task: string }) {
  const q = useQuery(matterFilesQuery(task));
  const add = useDo('risk.addFile');
  const pick = useRef<HTMLInputElement>(null);
  const files = q.data ?? [];
  return (
    <PanelSection
      name="Files"
      count={files.length}
      actions={
        <>
          <IconButton icon="plus" label="Add a file" size="sm" quiet busy={add.pending} onClick={() => pick.current?.click()} />
          <input
            ref={pick}
            type="file"
            hidden
            aria-label="File"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) void add.run({ task, file });
            }}
          />
        </>
      }
    >
      {files.length > 0 && (
        <div className="chips">
          {files.map((f) => (
            <a key={f.id} className="chip-file" href={`/api/v2/files/${encodeURIComponent(f.id)}`} target="_blank" rel="noopener" title={f.title || f.name}>
              <Icon name="file" />
              <span>{f.title || f.name}</span>
            </a>
          ))}
        </div>
      )}
    </PanelSection>
  );
}

/** One risk as its own page: figures, its matters, its dates, the rules and sources it rests on. */
function OneRisk({ id, panel }: { id: string; panel?: string }) {
  const d = useSuspenseQuery(riskQuery(id)).data;
  const open = useDo('company.openPanel');
  return (
    <Record
      title={d.risk.label}
      path={BASE}
      crumb={[{ label: 'Risk', to: BASE }]}
      className="co"
      actions={
        <Button variant={panel ? 'plain' : 'primary'} data-phone onClick={() => void open.run({ panel: 'matter-new' })}>
          Add matter
        </Button>
      }
      more={
        d.editable && (
          <PageMore target="risk:more">
            <MenuItem label="Edit" onSelect={() => void open.run({ panel: 'edit' })} />
          </PageMore>
        )
      }
      facts={<RiskFacts d={d} />}
      figures={
        <FiguresStrip
          label="Risk figures"
          figures={[
            { label: 'Claimed', value: claimed(d.risk) || '$0' },
            { label: 'Next date', value: d.risk.next_date ? fmtDate(d.risk.next_date) : 'None' },
            { label: 'Open', value: d.risk.open },
            { label: 'Closed', value: d.risk.closed },
          ]}
        />
      }
      sections={<RiskSections d={d} open={panel} />}
      panel={onePanel(panel, d)}
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

function RiskSections({ d, open }: { d: RiskDetail; open?: string }) {
  const waiting = useWaiting();
  const rules = d.rules as Linked[];
  const sources = d.sources as Linked[];
  return (
    <>
      {d.matters.length > 0 && (
        <Section name="Matters" count={d.matters.length}>
          {d.matters.map((m) => (
            <PanelLink
              key={m.task.id}
              panel={`matter:${m.task.id}`}
              className={['row rk-m is-one', m.task.status !== 'open' && 'is-quiet', open === `matter:${m.task.id}` && 'is-selected'].filter(Boolean).join(' ')}
              data-target={`matter:${m.task.id}`}
            >
              <MatterMark m={m} waiting={waiting} />
              <span className="row-title">{m.task.title ?? m.task.label}</span>
              <NewPill m={m} />
              <span className="rk-amt">{money(m.matter?.amount, m.matter?.currency)}</span>
              <span className="rk-forum">{m.matter?.forum}</span>
              <span className="rk-stage">{m.task.stage}</span>
              <Face id={m.task.owner_id} />
              <span className="row-age">{m.task.created_at ? fmtAge(m.task.created_at) : ''}</span>
            </PanelLink>
          ))}
        </Section>
      )}
      {d.obligations.length > 0 && (
        <Section name="Dates" count={d.obligations.length}>
          {d.obligations.map((o) => (
            <div key={`${o.id}:${o.due_on}`} className={['row rk-d', o.mark && 'is-done', open === `due:${o.id}:${o.due_on}` && 'is-selected'].filter(Boolean).join(' ')}>
              <span className="row-mark">
                <DateMark d={o} />
              </span>
              <PanelLink panel={`due:${o.id}:${o.due_on}`} className="row-title" data-target={`date:${o.id}`}>
                {o.title || o.rule.text}
              </PanelLink>
              <span className="rk-basis">{BASIS[o.basis] ?? ''}</span>
              <span className="rk-proof">
                {o.mark?.proof && (
                  <span className="chip-file">
                    <Icon name={o.mark.proof.kind === 'email' ? 'mail' : o.mark.proof.kind === 'file' ? 'file' : 'link'} />
                    <span>{proofWords(o.mark.proof)}</span>
                  </span>
                )}
              </span>
              <Face id={o.mark?.satisfied_by ?? o.owner} />
              <span className={!o.mark && soon(o.due_on) ? 'row-age is-due-soon' : 'row-age'}>{fmtDate(o.due_on)}</span>
            </div>
          ))}
        </Section>
      )}
      {(rules.length > 0 || sources.length > 0) && (
        <div className="two-col">
          {rules.length > 0 && (
            <Section name="Rules" count={rules.length}>
              {rules.map((r) => (
                <Go key={r.link_id} to="/company/rules" search={{ panel: `org:${r.id}` }} className="row rk-l">
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
                <Go key={s.link_id} to="/library/contacts" search={{ ...(s.id?.startsWith('organization/') && { type: 'organization' }), panel: s.id }} className="row rk-l">
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
          <Markdown text={d.risk.markdown} />
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

/** A new matter under this risk: what it is called and its kind. */
function NewMatter({ risk }: { risk: string }) {
  const add = useDo('risk.addMatter');
  const close = useDo('company.closePanel');
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState<string>('dispute');
  const save = async () => {
    if (title.trim() && (await add.run({ risk, title: title.trim(), kind }))) void close.run({});
  };
  return (
    <SidePanel
      title="New matter"
      target="panel:matter-new"
      onClose={() => void close.run({})}
      foot={
        <>
          <Button variant="primary" busy={add.pending} onClick={() => void save()}>
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
        <Field label="Name" value={title} onValueChange={setTitle} autoFocus />
        <Select label="Kind" options={Object.entries(KIND).map(([value, label]) => ({ value, label }))} value={kind} onValueChange={setKind} />
      </form>
    </SidePanel>
  );
}

/** The risk's name and its page text (risk.ts rkEditDialog). */
function EditRisk({ d }: { d: RiskDetail }) {
  const edit = useDo('risk.edit');
  const close = useDo('company.closePanel');
  const [label, setLabel] = useState(d.risk.label);
  const [text, setText] = useState(d.risk.markdown ?? '');
  const save = async () => {
    if (label.trim() && (await edit.run({ risk: d.risk.id, version: d.risk.version, label: label.trim(), markdown: text }))) void close.run({});
  };
  return (
    <SidePanel
      title="Edit risk"
      target="panel:risk-edit"
      onClose={() => void close.run({})}
      foot={
        <>
          <Button variant="primary" busy={edit.pending} onClick={() => void save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={() => void close.run({})}>
            Cancel
          </Button>
        </>
      }
    >
      <div className="rail-sec">
        <Field label="Name" value={label} onValueChange={setLabel} autoFocus />
        <Field label="Page" area value={text} onValueChange={setText} />
      </div>
    </SidePanel>
  );
}

type Act = 'proof' | 'waived' | 'missed';

/** One date: where it comes from and how it stands, and for whoever may mark it, Add proof, Waive and Missed
    (risk.ts rkDueSide). A date that is theirs to meet, or already proved, is only watched. */
function DatePanel({ risk, d }: { risk: string; d: RiskDate }) {
  const close = useDo('company.closePanel');
  const mark = useDo('risk.markDate');
  const [act, setAct] = useState<Act | null>(null);
  const [kind, setKind] = useState<'link' | 'file'>('link');
  const [link, setLink] = useState('');
  const [find, setFind] = useState('');
  const [file, setFile] = useState<{ id: string; title: string } | null>(null);
  const [note, setNote] = useState('');
  const found = useQuery({ ...fileSearchQuery(find), enabled: act === 'proof' && kind === 'file' && !file && !!find.trim() });
  const m = d.mark;
  const may = !!d.can_satisfy && d.basis !== 'theirs' && !m?.proof;
  const ready = act === 'proof' ? (kind === 'link' ? /^https:\/\/\S+$/i.test(link.trim()) : !!file) : act === 'waived' ? !!note.trim() : act === 'missed';
  const save = async () => {
    if (!act || !ready) return;
    const input = { risk, date: d.id, on: d.due_on, how: act, ...(act === 'proof' ? (kind === 'link' ? { link: link.trim() } : { file: file?.id }) : note.trim() ? { note: note.trim() } : {}) };
    if (await mark.run(input)) setAct(null);
  };
  const fact = (label: string, value: ReactNode) =>
    value ? (
      <div className="fact fld">
        <span>{label}</span>
        <span className="fact-v">{value}</span>
      </div>
    ) : null;
  const outcome = m ? { met: 'Met', waived: 'Waived', missed: 'Missed' }[m.outcome] : null;
  return (
    <SidePanel
      title={d.title || d.rule.text}
      target={`date:${d.id}`}
      onClose={() => void close.run({})}
      foot={
        act ? (
          <>
            <Button variant="primary" busy={mark.pending} disabled={!ready} onClick={() => void save()}>
              {act === 'proof' ? 'Save' : act === 'waived' ? 'Waive' : 'Missed'}
            </Button>
            <Button variant="quiet" onClick={() => setAct(null)}>
              Cancel
            </Button>
          </>
        ) : (
          may && (
            <>
              <Button variant="primary" onClick={() => setAct('proof')}>
                Add proof
              </Button>
              {d.can_waive && m?.outcome !== 'waived' && <Button onClick={() => setAct('waived')}>Waive</Button>}
              {m?.outcome !== 'missed' && (
                <Button variant="quiet" onClick={() => setAct('missed')}>
                  Missed
                </Button>
              )}
            </>
          )
        )
      }
    >
      <section className="rail-sec">
        {fact('Due', fmtDate(d.due_on))}
        {fact('Basis', BASIS[d.basis])}
        {fact(
          'Rule',
          <Go to="/company/rules" search={{ panel: `org:${d.rule.id}` }} className="fld-link">
            {d.rule.text}
          </Go>,
        )}
        {fact('Matter', d.task && (d.task.title || d.task.label))}
        {fact(
          'Owner',
          d.owner && (
            <>
              <Face id={d.owner} />
              <NameOf id={d.owner} />
            </>
          ),
        )}
        {fact('State', !m && d.state === 'unconfirmed' ? 'Unconfirmed' : !m && d.state === 'passed' ? 'Passed' : null)}
        {m && outcome && fact(outcome, `${fmtDate(m.satisfied_at)}`)}
        {fact('Proof', m?.proof && proofWords(m.proof))}
        {fact('Note', m?.note)}
      </section>
      {act && (
        <form
          className="rail-sec"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {act === 'proof' ? (
            <>
              <Select
                label="Proof"
                options={[
                  { value: 'link', label: 'Link' },
                  { value: 'file', label: 'File' },
                ]}
                value={kind}
                onValueChange={(v) => setKind(v === 'file' ? 'file' : 'link')}
              />
              {kind === 'link' ? (
                <Field label="Link" type="url" placeholder="https://" value={link} onValueChange={setLink} autoFocus />
              ) : (
                <>
                  <Field
                    label="File"
                    placeholder="Files"
                    value={file ? file.title : find}
                    onValueChange={(v) => {
                      setFile(null);
                      setFind(v);
                    }}
                    autoFocus
                  />
                  {(found.data ?? []).map((f) => (
                    <button key={f.id} type="button" className="rail-row" onClick={() => setFile(f)}>
                      <Icon name="file" />
                      <span className="row-title">{f.title}</span>
                    </button>
                  ))}
                </>
              )}
            </>
          ) : (
            <Field label="Note" area value={note} onValueChange={setNote} autoFocus />
          )}
        </form>
      )}
    </SidePanel>
  );
}
