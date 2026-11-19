import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Fragment, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { useActor } from '@/data';
import { fmtDate } from '@/lib';
import { Button } from '@/kit/Button';
import { FlowMark } from '@/kit/FlowMark';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { EmptyRow, GroupRow, Table } from '@/kit/Table';
import { VirtualList } from '@/kit/VirtualList';
import { Segmented } from '@/kit/Segmented';
import { FilterRow, List, PageMore, Section } from '@/pagekinds';
import { AddBillDialog, EntryDialog, NewInvoiceDialog } from './BillForms';
import { CardPanel, DocPanel, useBookSearch } from './BookPanels';
import { acctKey, asOfDay, compareDay, type AsOf } from './ledger';
import { FIN, FinanceOwner, plural, useFinanceHead } from './head';
import { cash } from './model';
import { CardDialog, ConfirmDialog, TransferDialog, useMay } from './dialogs';
import { BillsAddressDialog, copied, ImportDialog, RuleDialog, ShareDialog, type RuleRow } from './forms';
import { ActLink, Meter, PhoneRows, readState, usePhone, usePress, useViewAs, Who, type Fr } from './parts';
import {
  draftsQuery,
  journalChartQuery,
  type DocKind,
  balanceQuery,
  billsQuery,
  cardsQuery,
  invoicesQuery,
  payFromQuery,
  rulesQuery,
  taxQuery,
  transactionsQuery,
  type DocRow,
  type Tx,
} from './queries';
import { panelOf, TX_STATUSES, type FinanceTab } from './search';

/* The rest of Finance, each a list read from its endpoint and drawn as the mock draws it, with the old app's dialogs
   for a transfer and a card. */

type Read = { isPending: boolean; isError: boolean; error: unknown; fetchStatus?: string; refetch: () => unknown };

function Page({
  q,
  empty,
  tools,
  actions,
  more,
  panel,
  filter,
  children,
}: {
  q: Read;
  empty?: { title: string; action?: ReactNode };
  /** Before the owner's face: a filter, a switch. */
  tools?: ReactNode;
  /** After it: the one filled button. */
  actions?: ReactNode;
  more?: ReactNode;
  panel?: ReactNode;
  filter?: ReactNode;
  children: ReactNode;
}) {
  const head = useFinanceHead();
  return (
    <List
      {...head}
      state={readState(q, !!empty)}
      empty={empty}
      onRetry={() => void q.refetch()}
      actions={
        <>
          {tools}
          <FinanceOwner />
          {actions}
        </>
      }
      more={more || undefined}
      panel={panel || undefined}
      filter={filter || undefined}
      sections={children}
    />
  );
}

/** The record open beside a books tab: `bill:<id>` on Payables, and so on. */
function usePanel(kind: 'bill' | 'invoice' | 'card' | 'tx'): string | null {
  const open = panelOf(useBookSearch().panel);
  return open && open[0] === kind ? open[1] : null;
}

export function BooksPage({ tab }: { tab: Exclude<FinanceTab, 'overview' | 'pnl' | 'budgets' | 'mine' | 'sources' | 'access'> }) {
  switch (tab) {
    case 'transactions':
      return <Transactions />;
    case 'payables':
      return <Payables />;
    case 'receivables':
      return <Receivables />;
    case 'balance':
      return <Balance />;
    case 'cards':
      return <Cards />;
    case 'tax':
      return <Tax />;
    default:
      return <Attribution />;
  }
}

const Th = ({ children, num, w }: { children?: ReactNode; num?: boolean; w?: string }) => (
  <th scope="col" className={[num && 'num', w].filter(Boolean).join(' ') || undefined}>
    {children}
  </th>
);

// ---------------------------------------------------------------- Transactions

/** Where an entry came from, as people name it: the card or account and its last four. */
function sourceOf(t: Tx): string {
  const m = t.money_account as { name?: unknown; last4?: unknown } | null | undefined;
  const name = typeof m?.name === 'string' ? m.name : '';
  const last4 = typeof m?.last4 === 'string' ? m.last4 : '';
  if (name) return last4 && !name.includes(last4) ? `${name} ${last4}` : name;
  return t.source ? t.source.charAt(0).toUpperCase() + t.source.slice(1) : '';
}
const describe = (t: Tx) => t.vendor_name || (typeof t.vendor === 'object' ? t.vendor?.name : t.vendor) || t.description || t.counterparty || '';

const SRC = { feed: 'Bank', entry: 'Entries' } as const;
const cap = (w: string) => w.charAt(0).toUpperCase() + w.slice(1);
type TxFilterIn = { src?: 'feed' | 'entry' | null; status?: string | null; line?: string | null };

/** Source ›, Status ›, Line › (finance-books.ts finTxFilter): each choice lands in the address; the one on takes it off. */
function TxFilterMenu({ on, lines, onPick }: { on: TxFilterIn; lines: [string, string][]; onPick: (f: TxFilterIn) => void }) {
  return (
    <Menu trigger={<IconButton icon="filter" label="Filter" on={!!(on.src || on.status || on.line)} data-phone />}>
      <MenuSub label="Source">
        {(['feed', 'entry'] as const).map((k) => (
          <MenuItem key={k} label={SRC[k]} on={on.src === k} onSelect={() => onPick({ src: on.src === k ? null : k })} />
        ))}
      </MenuSub>
      <MenuSub label="Status">
        {TX_STATUSES.map((k) => (
          <MenuItem key={k} label={cap(k)} on={on.status === k} onSelect={() => onPick({ status: on.status === k ? null : k })} />
        ))}
      </MenuSub>
      {lines.length > 0 && (
        <MenuSub label="Line">
          {lines.map(([k, name]) => (
            <MenuItem key={k} label={name} on={on.line === k} onSelect={() => onPick({ line: on.line === k ? null : k })} />
          ))}
        </MenuSub>
      )}
    </Menu>
  );
}

/** Every entry, newest first. Past 100 rows only the rows in view are drawn; on a phone, rows under each day. A bank or
    card row with no line opens in place (`?panel=tx:<id>`) to be put on one. */
function Transactions() {
  const may = useMay();
  const as = useViewAs();
  const qc = useQueryClient();
  const phone = usePhone();
  const press = usePress();
  const at = useBookSearch();
  const f = { src: at.src, status: at.status, line: at.line };
  const q = useInfiniteQuery(transactionsQuery(as, f));
  const book = may('finance.book');
  const open = usePanel('tx');
  const chart = useQuery({ ...journalChartQuery, enabled: book && !!open });
  const [line, setLine] = useState<string | null>(null);
  const [find, setFind] = useState('');
  const [moving, setMoving] = useState(false);
  const [entering, setEntering] = useState(false);
  const words = find.trim().toLowerCase();
  const byId = new Map<string, Tx>();
  for (const page of q.data?.pages ?? []) {
    for (const row of page.rows) {
      if (!byId.has(row.id)) byId.set(row.id, row);
    }
  }
  const all = [...byId.values()];
  const rows = words ? all.filter((t) => `${describe(t)} ${t.account ?? ''} ${sourceOf(t)}`.toLowerCase().includes(words)) : all;
  // The server holds more than was read: the next page comes as the last row is drawn (a search reads only what is here).
  const hasMore = q.hasNextPage && !words;
  const next = () => void q.fetchNextPage({ cancelRefetch: false });
  const more = hasMore && !q.isFetching && !q.isFetchNextPageError ? next : undefined;
  const share = may('finance.attribute') ? setLine : undefined;
  // The lines to filter by: those the rows read so far are on.
  const named = new Map<string, string>();
  const plain = qc.getQueryData(transactionsQuery(as).queryKey);
  for (const t of [...(plain?.pages.flatMap((p) => p.rows) ?? []), ...all]) if (t.account_id && t.account) named.set(t.account_id, t.account);
  const lines = [...named].sort((a, b) => a[1].localeCompare(b[1]));
  const pick = (change: TxFilterIn) => void press('finance.filterTransactions', change);
  const chips: { label: string; onClear: () => void }[] = [];
  if (f.src) chips.push({ label: SRC[f.src], onClear: () => pick({ src: null }) });
  if (f.status) chips.push({ label: cap(f.status), onClear: () => pick({ status: null }) });
  if (f.line) chips.push({ label: named.get(f.line) ?? 'One line', onClear: () => pick({ line: null }) });
  const editing: Editing = {
    open: book ? open : null,
    settable: (t) => book && settable(t),
    lines: (chart.data?.accounts ?? []).map((a) => ({ value: a.id, label: a.name })),
    onPick: (tx, to) => void press('finance.setLine', { tx, line: to }),
  };
  // A transfer needs two bank accounts to move between (finance-books.ts finXferAdd).
  const transfer = async () => {
    try {
      const d = await qc.fetchQuery(payFromQuery);
      if (d.accounts.length < 2) toast({ text: 'Needs two bank accounts', kind: 'failed' });
      else setMoving(true);
    } catch {
      toast({ text: "Couldn't read the accounts", kind: 'failed' });
    }
  };
  return (
    <Page
      q={{ ...q, isError: q.isError && !q.isFetchNextPageError }}
      tools={<TxFilterMenu on={f} lines={lines} onPick={pick} />}
      actions={
        book && (
          <>
            <Button variant="primary" onClick={() => setEntering(true)}>
              Add entry
            </Button>
            {entering && <EntryDialog onOpenChange={setEntering} />}
          </>
        )
      }
      more={
        may('finance.pay') && (
          <>
            <PageMore>
              <MenuItem label="New transfer" onSelect={() => void transfer()} />
            </PageMore>
            {moving && <TransferDialog onOpenChange={setMoving} />}
          </>
        )
      }
      filter={chips.length > 0 && <FilterRow filters={chips} onClearAll={chips.length > 1 ? () => pick({ src: null, status: null, line: null }) : undefined} />}
    >
      {line && <ShareDialog line={line} onOpenChange={(o) => !o && setLine(null)} />}
      <div className="ctl-row">
        <Search label="Search transactions" value={find} onValueChange={setFind} placeholder="Search" />
        <span className="grow" />
        <span className="count">{`${plural(rows.length, 'entry', 'entries')}${hasMore ? '+' : ''}`}</span>
      </div>
      {phone ? (
        <TxPhone rows={rows} onMore={more} edit={editing} />
      ) : (
        <div className="t-scroll">
          <Table label="Transactions" stick>
            <colgroup>
              <col className="t-date" />
              <col />
              <col className="t-line" />
              <col className="t-src" />
              <col className="t-col" />
              {share && <col className="t-act" />}
            </colgroup>
            <thead>
              <tr>
                <Th>Date</Th>
                <Th>Description</Th>
                <Th>Line</Th>
                <Th>Source</Th>
                <Th num>Amount</Th>
                {share && <Th />}
              </tr>
            </thead>
            <TxBody rows={rows} onEnd={more} onShare={share} edit={editing} />
          </Table>
        </div>
      )}
      {hasMore && q.isFetchNextPageError && (
        <button type="button" className="row fold" onClick={next}>
          Try again
        </button>
      )}
    </Page>
  );
}

/** A bank or card row that can be put on a line here: no line yet, settled, not reconciled (finance-books.ts finTxCatOn). */
const settable = (t: Tx) => t.kind === 'feed' && !t.account && !t.pending && !t.reconciled;

/** The row open in place, and what putting it on a line needs. */
interface Editing {
  open: string | null;
  settable: (t: Tx) => boolean;
  lines: { value: string; label: string }[];
  onPick: (tx: string, line: string) => void;
}

/** The line picker under a row opened in place. */
const LinePick = ({ t, edit }: { t: Tx; edit: Editing }) => (
  <div className="tx-edit">
    <Select label="Line" options={edit.lines} value={null} placeholder="Choose" onValueChange={(v) => v && edit.onPick(t.id, v)} />
  </div>
);

/** One entry on a phone, with its day over it when it starts one. */
function TxPhoneRow({ t, day, edit }: { t: Tx; day: boolean; edit: Editing }) {
  return (
    <>
      {day && (
        <div className="fr-group" role="presentation">
          <span>{fmtDate(t.date)}</span>
        </div>
      )}
      <div className={t.pending ? 'row fr is-quiet' : 'row fr'} role="listitem">
        <span className="fr-main">
          <span className="row-title">
            <TxName t={t} edit={edit} />
            {!t.account && <Pill flag>No line</Pill>}
          </span>
          <span className="fr-sub">
            {t.account && <span>{t.account}</span>}
            {sourceOf(t) && <span>{sourceOf(t)}</span>}
          </span>
        </span>
        <span className="row-fig">{cash(t.amount, true)}</span>
      </div>
      {edit.open === t.id && edit.settable(t) && <LinePick t={t} edit={edit} />}
    </>
  );
}

/** On a phone: each day a group, each entry its words, line and source, and its amount. Past 100 only the rows in view
    are drawn and the next page comes at the end; under that, "More" reads it. */
function TxPhone({ rows, onMore, edit }: { rows: readonly Tx[]; onMore?: () => void; edit: Editing }) {
  if (rows.length === 0) return <div className="fold">No entries</div>;
  const day = (i: number) => i === 0 || rows[i - 1]?.date !== rows[i]?.date;
  if (rows.length > 100)
    return (
      <VirtualList
        className="frs tx-long"
        label="Transactions"
        items={rows}
        getKey={(t) => t.id}
        estimateSize={52}
        measure
        renderItem={(t, i) => <TxPhoneRow t={t} day={day(i)} edit={edit} />}
        onReachEnd={onMore}
      />
    );
  return (
    <div className="frs" role="list" aria-label="Transactions">
      {rows.map((t, i) => (
        <Fragment key={t.id}>
          <TxPhoneRow t={t} day={day(i)} edit={edit} />
        </Fragment>
      ))}
      {onMore && (
        <button type="button" className="row fold" onClick={onMore}>
          More
        </button>
      )}
    </div>
  );
}

/** The lines a row holds, whose attribution can be read and set: a bank row's lines, or an entry's own line. */
function linesOf(t: Tx): { id: string; account: string }[] {
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  if (t.kind === 'entry') return t.entry_id ? [{ id: t.id, account: t.account ?? '' }] : [];
  return (t.lines ?? []).flatMap((l) => (str(l.line_id) ? [{ id: str(l.line_id), account: str(l.account) }] : []));
}

/** A row's words. One that can be put on a line opens in place, and closes again. */
function TxName({ t, edit }: { t: Tx; edit: Editing }) {
  const words = describe(t) || 'Entry';
  if (!edit.settable(t)) return <>{words}</>;
  const on = edit.open === t.id;
  return (
    <ActLink
      to={FIN}
      search={{ tab: 'transactions', panel: on ? undefined : `tx:${t.id}` }}
      action={on ? 'finance.closePanel' : 'finance.openRecord'}
      input={on ? {} : { kind: 'tx', record: t.id }}
      className="t-link"
    >
      {words}
    </ActLink>
  );
}

function TxRow({ t, first, onShare, edit }: { t: Tx; first: boolean; onShare?: (line: string) => void; edit: Editing }) {
  const lines = onShare ? linesOf(t) : [];
  const on = edit.open === t.id && edit.settable(t);
  return (
    <>
      <tr className={[t.pending && 'is-quiet', on && 'is-selected'].filter(Boolean).join(' ') || undefined}>
        <td className="t-words">{first ? fmtDate(t.date) : ''}</td>
        <td className="t-name">
          <TxName t={t} edit={edit} />
          {!t.account && <Pill flag>No line</Pill>}
        </td>
        <td className="t-words">{t.account ?? ''}</td>
        <td className="t-words">{sourceOf(t)}</td>
        <td className="num t-fig">{cash(t.amount, true)}</td>
        {onShare && (
          <td className="t-act">
            {lines.length > 0 && (
              <Menu trigger={<IconButton icon="more" label={`More for ${describe(t)}`} size="sm" quiet />}>
                {lines.map((l) => (
                  <MenuItem key={l.id} label={lines.length > 1 && l.account ? `Attributed to, ${l.account}` : 'Attributed to'} onSelect={() => onShare(l.id)} />
                ))}
              </Menu>
            )}
          </td>
        )}
      </tr>
      {on && (
        <tr className="is-selected">
          <td />
          <td colSpan={onShare ? 5 : 4}>
            <LinePick t={t} edit={edit} />
          </td>
        </tr>
      )}
    </>
  );
}

const ROW = 34;

/** `onEnd` reads the next page once the last row is drawn. */
function TxBody({ rows, onEnd, onShare, edit }: { rows: readonly Tx[]; onEnd?: () => void; onShare?: (line: string) => void; edit: Editing }) {
  // The virtualizer mutates itself between renders; the React Compiler must not memoize what it returns.
  'use no memo';
  const [scroller, setScroller] = useState<HTMLElement | null>(null);
  // eslint-disable-next-line react-hooks/incompatible-library -- 'use no memo' above opts this component out
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => scroller, estimateSize: () => ROW, overscan: 12 });
  const first = (i: number) => i === 0 || rows[i - 1]?.date !== rows[i]?.date;
  const items = v.getVirtualItems();
  const last = rows.length <= 100 ? rows.length - 1 : items.at(-1)?.index;
  useEffect(() => {
    if (rows.length > 0 && last === rows.length - 1) onEnd?.();
  }, [last, rows.length, onEnd]);
  if (rows.length === 0)
    return (
      <tbody>
        <EmptyRow span={onShare ? 6 : 5}>No entries</EmptyRow>
      </tbody>
    );
  if (rows.length <= 100)
    return (
      <tbody>
        {rows.map((t, i) => (
          <TxRow key={t.id} t={t} first={first(i)} onShare={onShare} edit={edit} />
        ))}
      </tbody>
    );
  const before = items[0]?.start ?? 0;
  const after = v.getTotalSize() - (items.at(-1)?.end ?? 0);
  return (
    <tbody ref={(el) => setScroller(el?.closest<HTMLElement>('.page-body') ?? null)}>
      {before > 0 && <tr aria-hidden="true" style={{ height: before } as CSSProperties} />}
      {items.map((it) => {
        const t = rows[it.index]!;
        return <TxRow key={t.id} t={t} first={first(it.index)} onShare={onShare} edit={edit} />;
      })}
      {after > 0 && <tr aria-hidden="true" style={{ height: after } as CSSProperties} />}
    </tbody>
  );
}

// ---------------------------------------------------------------- Payables, Receivables

const late = (d: DocRow) => (d.days_late && d.days_late > 0 ? `${d.days_late} days late` : '');
const soon = (d: DocRow) => {
  if (!d.due_date) return false;
  const days = (new Date(`${d.due_date}T00:00:00`).getTime() - Date.now()) / 86_400_000;
  return days <= 7;
};
const sum = (list: readonly DocRow[]) => list.reduce((a, d) => a + d.remaining, 0);

/** Documents in named groups that fold, each with its total, and the total of all; on a phone, rows. The `marked`
    group's rows carry the mark of what waits on you. */
function DocTable({ label, name, number, groups, marked }: { label: string; name: string; number?: boolean; groups: [string, DocRow[]][]; marked?: string }) {
  const phone = usePhone();
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (g: string) => setFolded((was) => (was.has(g) ? new Set([...was].filter((x) => x !== g)) : new Set([...was, g])));
  const kind: DocKind = number ? 'invoice' : 'bill';
  const shown = groups.filter(([, list]) => list.length > 0);
  const all = shown.flatMap(([, list]) => list);
  const span = number ? 4 : 3;
  const words = (d: DocRow) => (number ? d.number : String((d.account as { name?: unknown } | null)?.name ?? ''));
  const title = (g: string, d: DocRow) => (
    <>
      {g === marked && <FlowMark size={12} className="t-mark" />}
      <ActLink
        to={FIN}
        search={{ tab: number ? 'receivables' : 'payables', panel: `${kind}:${d.id}` }}
        action="finance.openRecord"
        input={{ kind, record: d.id }}
        className="t-link"
      >
        {(number ? d.customer?.name : d.vendor?.name) || d.number || 'Untitled'}
      </ActLink>
      {late(d) && <Pill>{late(d)}</Pill>}
    </>
  );
  if (phone)
    return (
      <PhoneRows
        label={label}
        groups={[
          ...shown.map(([g, list]) => ({
            name: g,
            count: list.length,
            rows: list.map((d): Fr => ({ key: d.id, title: title(g, d), sub: [words(d), d.due_date && `Due ${fmtDate(d.due_date)}`], fig: cash(d.remaining) })),
          })),
          { rows: [{ key: 'total', title: 'Total', fig: cash(sum(all)), cls: 'is-total' }] },
        ]}
      />
    );
  return (
    <div className="t-scroll">
      <Table label={label} stick>
        <colgroup>
          <col />
          <col className={number ? 't-no' : 't-line'} />
          <col className="t-date" />
          <col className="t-col" />
        </colgroup>
        <thead>
          <tr>
            <Th>{name}</Th>
            <Th>{number ? 'Invoice' : 'Line'}</Th>
            <Th>Due</Th>
            <Th num>Amount</Th>
          </tr>
        </thead>
        <tbody>
          {shown.map(([g, list]) => [
            <GroupRow key={`g:${g}`} name={g} count={list.length} open={!folded.has(g)} onToggle={() => toggle(g)} span={span - 1} total={cash(sum(list))} />,
            ...(folded.has(g) ? [] : list).map((d) => (
              <tr key={d.id}>
                <td className="t-name">{title(g, d)}</td>
                <td className="t-words">{words(d)}</td>
                <td className="t-words">{d.due_date ? fmtDate(d.due_date) : ''}</td>
                <td className="num t-fig">{cash(d.remaining)}</td>
              </tr>
            )),
          ])}
          <tr className="is-total">
            <td className="t-name" colSpan={span - 1}>
              Total
            </td>
            <td className="num">{cash(sum(all))}</td>
          </tr>
        </tbody>
      </Table>
    </div>
  );
}

function Payables() {
  const may = useMay();
  const as = useViewAs();
  const q = useQuery(billsQuery(as));
  // Drafts are not in the unpaid list. They wait for Approve: first, marked, for whoever may approve; else last.
  const drafts = useQuery(draftsQuery('bill', as)).data ?? [];
  const open = usePanel('bill');
  const [setting, setSetting] = useState(false);
  const [adding, setAdding] = useState(false);
  const approves = may('finance.ap');
  const email = q.data?.bills_email ?? '';
  const bills = q.data?.bills ?? [];
  const over = bills.filter((b) => late(b));
  const week = bills.filter((b) => !late(b) && soon(b));
  const later = bills.filter((b) => !late(b) && !soon(b));
  const dated: [string, DocRow[]][] = [
    ['Overdue', over],
    ['This week', week],
    ['Later', later],
  ];
  return (
    <Page
      q={q}
      empty={q.data && bills.length === 0 && drafts.length === 0 ? { title: 'Nothing to pay' } : undefined}
      actions={
        approves && (
          <>
            <Button variant="primary" onClick={() => setAdding(true)}>
              Add bill
            </Button>
            {adding && <AddBillDialog onOpenChange={setAdding} />}
          </>
        )
      }
      panel={open && <DocPanel kind="bill" id={open} />}
      more={
        // The owner or an admin sets the bills address; anyone else copies it (finance-docs.ts finDocEmailDraw).
        // The dialog sits with its menu: with no bills the page shows only its empty state.
        <>
          <PageMore>
            {[
              may('finance.admin') ? (
                <MenuItem key="set" label="Bills address" meta={email || 'None'} onSelect={() => setSetting(true)} />
              ) : (
                email ? <MenuItem key="copy" label={`Copy ${email}`} onSelect={() => copied(email)} /> : null
              ),
            ]}
          </PageMore>
          {setting && <BillsAddressDialog address={email} onOpenChange={setSetting} />}
        </>
      }
    >
      <DocTable
        label="Bills"
        name="Bill"
        marked="Waits on you"
        groups={approves ? [['Waits on you', drafts], ...dated] : [...dated, ['Draft', drafts]]}
      />
    </Page>
  );
}

const AGES = [
  ['current', 'Current'],
  ['1_30', '1 to 30 days'],
  ['31_60', '31 to 60 days'],
  ['61_90', '61 to 90 days'],
  ['over_90', 'Over 90 days'],
] as const;

function Receivables() {
  const may = useMay();
  const as = useViewAs();
  const q = useQuery(invoicesQuery(as));
  // Drafts are not in the unpaid list; they wait here for Send.
  const drafts = useQuery(draftsQuery('invoice', as)).data ?? [];
  const open = usePanel('invoice');
  const [making, setMaking] = useState(false);
  const list = q.data?.invoices ?? [];
  const aging = q.data?.open.aging;
  const max = aging ? Math.max(1, ...AGES.map(([k]) => aging[k])) : 1;
  return (
    <Page
      q={q}
      empty={q.data && list.length === 0 && drafts.length === 0 ? { title: 'Nobody owes you' } : undefined}
      actions={
        may('finance.ar') && (
          <>
            <Button variant="primary" onClick={() => setMaking(true)}>
              New invoice
            </Button>
            {making && <NewInvoiceDialog onOpenChange={setMaking} />}
          </>
        )
      }
      panel={open && <DocPanel kind="invoice" id={open} />}
    >
      {aging && (
        <Section name="By age">
          <div className="hbars">
            {AGES.map(([k, words]) => (
              <div key={k} className="hbar">
                <span className="hbar-name">{words}</span>
                <span className="hbar-track">
                  <i style={{ '--w': `${((aging[k] / max) * 100).toFixed(1)}%` } as CSSProperties} />
                </span>
                <span className="row-fig">{cash(aging[k])}</span>
                <span className="count" />
              </div>
            ))}
          </div>
        </Section>
      )}
      <Section name="Invoices" count={list.length + drafts.length}>
        <DocTable
          label="Invoices"
          name="Customer"
          number
          groups={[
            ['Late', list.filter((d) => late(d))],
            ['Current', list.filter((d) => !late(d))],
            ['Draft', drafts],
          ]}
        />
      </Section>
    </Page>
  );
}

// ---------------------------------------------------------------- Balance sheet

type Account = { name: string; balance: number; code: string; account_id: string | null };
const total = (list: readonly Account[]) => list.reduce((a, r) => a + r.balance, 0);

/** The day before, read beside the day shown: each account's balance then, by its id (or code and name). */
type Was = { head: string; at: Map<string, number> } | null;

function Side({ label, head, was, parts }: { label: string; head: string; was: Was; parts: { name: string; rows: readonly Account[]; total: string }[] }) {
  const then = (rows: readonly Account[]) => rows.reduce((a, r) => a + (was?.at.get(acctKey(r)) ?? 0), 0);
  const cols = was ? 3 : 2;
  return (
    <Table label={label}>
      <colgroup>
        <col />
        {was && <col className="t-col" />}
        <col className="t-col" />
      </colgroup>
      <thead>
        <tr>
          <Th>Account</Th>
          {was && <Th num>{was.head}</Th>}
          <Th num>{head}</Th>
        </tr>
      </thead>
      <tbody>
        {parts.map((p) => [
          <tr key={`g:${p.name}`} className="is-group">
            <td colSpan={cols}>{p.name}</td>
          </tr>,
          ...p.rows.map((r) => {
            const before = was?.at.get(acctKey(r));
            return (
              <tr key={`${p.name}:${r.code}:${r.name}`}>
                <td className="t-name">{r.name}</td>
                {was && <td className={before ? 'num t-was' : 'num zero t-was'}>{cash(before ?? 0)}</td>}
                <td className={r.balance === 0 ? 'num zero' : 'num'}>{cash(r.balance)}</td>
              </tr>
            );
          }),
          <tr key={`t:${p.name}`} className="is-total">
            <td className="t-name">{p.total}</td>
            {was && <td className="num t-was">{cash(then(p.rows))}</td>}
            <td className="num">{cash(total(p.rows))}</td>
          </tr>,
        ])}
        {parts.length > 1 && (
          <tr className="is-net">
            <td className="t-name">{parts.map((p) => p.name).join(' and ')}</td>
            {was && <td className="num t-was">{cash(parts.reduce((a, p) => a + then(p.rows), 0))}</td>}
            <td className="num">{cash(parts.reduce((a, p) => a + total(p.rows), 0))}</td>
          </tr>
        )}
      </tbody>
    </Table>
  );
}

const AS_OF: { value: AsOf; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: 'month', label: 'Month end' },
  { value: 'year', label: 'Year end' },
];

function Balance() {
  const may = useMay();
  const as = useViewAs();
  const press = usePress();
  const asof: AsOf = useBookSearch().asof ?? 'today';
  const q = useQuery(balanceQuery(asOfDay(asof), as));
  // The compare column: the month end (or year end) before the day shown, as the mock draws "Oct 31" beside today.
  const c = useQuery(balanceQuery(compareDay(asof), as)).data;
  const [importing, setImporting] = useState(false);
  const d = q.data;
  const head = d ? fmtDate(d.as_of) : '';
  const was: Was = c && d && c.as_of !== d.as_of ? { head: fmtDate(c.as_of), at: new Map([...c.assets, ...c.liabilities, ...c.equity].map((a): [string, number] => [acctKey(a), a.balance])) } : null;
  return (
    <Page
      q={q}
      actions={
        <span data-phone>
          <Segmented label="As of" options={AS_OF} value={asof} onValueChange={(v) => void press('finance.setAsOf', { asof: v })} />
        </span>
      }
      more={
        <>
          <PageMore>
            {[
              <MenuItem key="export" label="Export" onSelect={() => void press('finance.exportBalance', {})} />,
              may('finance.book') && <MenuItem key="import" label="Import history" onSelect={() => setImporting(true)} />,
            ]}
          </PageMore>
          {importing && <ImportDialog onOpenChange={setImporting} />}
        </>
      }
    >
      {d && (
        <div className="two-col">
          <div>
            <Side label="Assets" head={head} was={was} parts={[{ name: 'Assets', rows: d.assets, total: 'Total assets' }]} />
          </div>
          <div>
            <Side
              label="Liabilities and equity"
              head={head}
              was={was}
              parts={[
                { name: 'Liabilities', rows: d.liabilities, total: 'Total liabilities' },
                { name: 'Equity', rows: d.equity, total: 'Total equity' },
              ]}
            />
          </div>
        </div>
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- Cards, Tax

const STATUS: Record<string, string> = { frozen: 'Frozen', paused: 'Frozen', closed: 'Closed', canceled: 'Closed', inactive: 'Off' };

function Cards() {
  const may = useMay();
  const [making, setMaking] = useState(false);
  const open = usePanel('card');
  const q = useQuery(cardsQuery(useViewAs()));
  const cards = q.data?.cards ?? [];
  const spent = cards.reduce((a, c) => a + (c.spent ?? 0), 0);
  const limit = cards.reduce((a, c) => a + (c.limit ?? 0), 0);
  const phone = usePhone();
  const name = (c: (typeof cards)[number]) => (
    <>
      <ActLink to={FIN} search={{ tab: 'cards', panel: `card:${c.id}` }} action="finance.openRecord" input={{ kind: 'card', record: c.id }} className="t-link">
        {c.nickname || 'Card'}
      </ActLink>
      {STATUS[c.status] && <Pill>{STATUS[c.status]}</Pill>}
    </>
  );
  const holder = (c: (typeof cards)[number]) => (/^(human|bot):/.test(c.holder) ? <Who id={c.holder} /> : c.holder);
  return (
    <Page
      q={q}
      actions={
        may('finance.cards') && (
          <>
            <Button variant="primary" onClick={() => setMaking(true)}>
              Add card
            </Button>
            {making && <CardDialog onOpenChange={setMaking} />}
          </>
        )
      }
      empty={q.data && cards.length === 0 ? { title: 'No cards yet' } : undefined}
      panel={open && <CardPanel id={open} />}
    >
      {phone ? (
        <PhoneRows
          label="Cards"
          groups={[
            {
              rows: [
                ...cards.map((c): Fr => ({
                  key: c.id,
                  title: name(c),
                  sub: [holder(c), c.last4 && `Ends in ${c.last4}`, c.limit != null && `of ${cash(c.limit)}`],
                  fig: cash(c.spent),
                  cls: !!STATUS[c.status] && 'is-quiet',
                  bar: c.limit ? <Meter pct={((c.spent ?? 0) / c.limit) * 100} /> : undefined,
                })),
                { key: 'total', title: 'Total', sub: [`of ${cash(limit)}`], fig: cash(spent), cls: 'is-total' },
              ],
            },
          ]}
        />
      ) : (
      <div className="t-scroll">
        <Table label="Cards" stick>
          <colgroup>
            <col />
            <col className="t-who" />
            <col className="t-no" />
            <col className="t-col" />
            <col className="t-col" />
            <col className="t-meter" />
          </colgroup>
          <thead>
            <tr>
              <Th>Card</Th>
              <Th>Holder</Th>
              <Th>Ends in</Th>
              <Th num>Spent</Th>
              <Th num>Limit</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {cards.map((c) => {
              const off = STATUS[c.status];
              return (
                <tr key={c.id} className={off ? 'is-quiet' : undefined}>
                  <td className="t-name">{name(c)}</td>
                  <td className="t-words">{holder(c)}</td>
                  <td className="t-words">{c.last4 ?? ''}</td>
                  <td className="num t-fig">{cash(c.spent)}</td>
                  <td className="num">{cash(c.limit)}</td>
                  <td className="t-bar">{c.limit ? <Meter pct={((c.spent ?? 0) / c.limit) * 100} /> : null}</td>
                </tr>
              );
            })}
            <tr className="is-total">
              <td className="t-name" colSpan={3}>
                Total
              </td>
              <td className="num">{cash(spent)}</td>
              <td className="num">{cash(limit)}</td>
              <td />
            </tr>
          </tbody>
        </Table>
      </div>
      )}
    </Page>
  );
}

function Tax() {
  const q = useQuery(taxQuery(useViewAs()));
  const rows = q.data?.rows ?? [];
  const due = rows.filter((r) => r.due > 0);
  const filed = rows.filter((r) => r.due <= 0);
  const phone = usePhone();
  const fr = (list: typeof rows, paid: boolean): Fr[] =>
    list.map((r) => ({
      key: `${r.code}:${r.period}`,
      title: r.jurisdiction || r.code,
      sub: [r.period, r.due_on && `${paid ? 'Filed' : 'Due'} ${fmtDate(r.due_on)}`],
      fig: cash(paid ? (r.tax_remitted ?? r.tax_collected) : r.due),
      cls: paid && 'is-quiet',
    }));
  const table = (label: string, list: typeof rows, paid: boolean) =>
    phone ? (
      <PhoneRows label={label} groups={[{ rows: fr(list, paid) }]} />
    ) : (
    <Table label={label}>
      <colgroup>
        <col />
        <col className="t-line" />
        <col className="t-col" />
        <col className="t-date" />
        <col className="t-col" />
      </colgroup>
      <thead>
        <tr>
          <Th>State</Th>
          <Th>Period</Th>
          <Th num>Taxable sales</Th>
          <Th>{paid ? 'Filed' : 'Due'}</Th>
          <Th num>{paid ? 'Paid' : 'Owed'}</Th>
        </tr>
      </thead>
      <tbody>
        {list.map((r) => (
          <tr key={`${r.code}:${r.period}`} className={paid ? 'is-quiet' : undefined}>
            <td className="t-name">
              {paid && (
                <span className="row-check" title="Done">
                  <Icon name="check" />
                </span>
              )}
              {r.jurisdiction || r.code}
            </td>
            <td className="t-words">{r.period}</td>
            <td className="num">{cash(r.taxable_sales)}</td>
            <td className="t-words">{r.due_on ? fmtDate(r.due_on) : ''}</td>
            <td className="num t-fig">{cash(paid ? (r.tax_remitted ?? r.tax_collected) : r.due)}</td>
          </tr>
        ))}
        {!paid && (
          <tr className="is-total">
            <td className="t-name" colSpan={4}>
              Total
            </td>
            <td className="num">{cash(list.reduce((a, r) => a + r.due, 0))}</td>
          </tr>
        )}
      </tbody>
    </Table>
    );
  return (
    <Page q={q} empty={q.data && rows.length === 0 ? { title: 'No sales tax yet' } : undefined}>
      {due.length > 0 && (
        <Section name="Due" count={due.length}>
          {table('Due', due, false)}
        </Section>
      )}
      {filed.length > 0 && (
        <Section name="Filed" count={filed.length}>
          {table('Filed', filed, true)}
        </Section>
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- Attribution

function SplitTo({ team, teammate, share }: { team: string; teammate: string; share: number }) {
  const who = useActor(teammate || null);
  const name = teammate ? (who?.name ?? teammate.slice(teammate.indexOf(':') + 1)) : team;
  return <>{share < 10_000 ? `${name} ${Math.round(share / 100)}%` : name}</>;
}

/** The rules, with Add rule, Edit and Delete for whoever may change them (finance-attribution.ts finAttrPage). */
function Attribution() {
  const q = useQuery(rulesQuery);
  const viewing = !!useViewAs();
  const can = !!q.data?.can_change && !viewing;
  const [open, setOpen] = useState<{ kind: 'edit'; rule?: RuleRow } | { kind: 'delete'; rule: RuleRow } | null>(null);
  const close = (o: boolean) => !o && setOpen(null);
  const rules = q.data?.rules ?? [];
  const add = (
    <Button variant="primary" onClick={() => setOpen({ kind: 'edit' })}>
      Add rule
    </Button>
  );
  return (
    <Page
      q={q}
      // The dialogs sit with Add rule: with no rules the page shows only its empty state.
      actions={
        (can || open) && (
          <>
            {can && add}
            {open?.kind === 'edit' && <RuleDialog rule={open.rule} onOpenChange={close} />}
            {open?.kind === 'delete' && <ConfirmDialog title={`Delete ${open.rule.name}`} submit="Delete" action="finance.deleteRule" input={{ rule: open.rule.id }} onOpenChange={close} />}
          </>
        )
      }
      empty={q.data && rules.length === 0 ? { title: 'No rules yet', action: can ? add : undefined } : undefined}
    >
      <Section name="Rules" count={rules.length}>
        {rules.map((r) => (
          <div key={r.id} className={['row rule', !r.enabled && 'is-quiet', can && 'has-act'].filter(Boolean).join(' ')} data-target={`rule:${r.id}`}>
            <span className="row-title">{r.match.label || r.name}</span>
            <Icon name="arrow" />
            <span className="rule-to">
              {r.splits.length === 1 && r.splits[0]!.teammate && <Who id={r.splits[0]!.teammate} />}
              <span>
                {r.splits.map((s, i) => (
                  <span key={i}>
                    {i > 0 && ', '}
                    <SplitTo team={s.team} teammate={s.teammate} share={s.share_bps} />
                  </span>
                ))}
              </span>
            </span>
            <span className="count">{r.enabled ? '' : 'Off'}</span>
            {can && (
              <Menu trigger={<IconButton icon="more" label={`More for ${r.name}`} size="sm" quiet />}>
                <MenuItem label="Edit" onSelect={() => setOpen({ kind: 'edit', rule: r })} />
                <MenuRule />
                <MenuItem label="Delete" onSelect={() => setOpen({ kind: 'delete', rule: r })} />
              </Menu>
            )}
          </div>
        ))}
      </Section>
    </Page>
  );
}
