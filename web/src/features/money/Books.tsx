import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Fragment, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { useActor } from '@/data';
import { fmtAge, fmtDate } from '@/lib';
import { Button } from '@/kit/Button';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { toast } from '@/kit/Toast';
import { EmptyRow, Table } from '@/kit/Table';
import { List, Section, SidePanel, type PageState } from '@/pagekinds';
import { plural, useFinanceHead } from './head';
import { cash, count } from './model';
import { CardDialog, ConfirmDialog, ConnectBooksDialog, ConnectKeyDialog, DefaultsDialog, ShownOnce, TransferDialog, useMay, WebhookDialog } from './dialogs';
import { BillsAddressDialog, copied, ImportDialog, RuleDialog, ShareDialog, StatementDialog, ViewAsDialog, type RuleRow } from './forms';
import { Meter, usePhone, usePress, useViewAs, Who } from './parts';
import {
  accessQuery,
  BOOKS,
  balanceQuery,
  billsQuery,
  cardsQuery,
  hooksQuery,
  invoicesQuery,
  KEY_SOURCES,
  loadMoreTransactions,
  payFromQuery,
  rulesQuery,
  sourcesQuery,
  taxQuery,
  transactionsQuery,
  type AccessTeammate,
  type DocRow,
  type KeyProvider,
  type SourceGroup,
  type SourceRow,
  type Tx,
} from './queries';
import type { FinanceTab } from './search';

/* The rest of Finance, each a list read from its endpoint and drawn as the mock draws it, with the old app's dialogs
   for a transfer, a card, connecting a source, the books, Stripe's webhook and the access defaults. */

type Read = { isPending: boolean; isError: boolean; refetch: () => unknown };
const stateOf = (q: Read, empty = false): PageState => (q.isPending ? 'loading' : q.isError ? 'fault' : empty ? 'empty' : 'ready');

function Page({
  q,
  empty,
  actions,
  more,
  panel,
  children,
}: {
  q: Read;
  empty?: { title: string; action?: ReactNode };
  actions?: ReactNode;
  more?: ReactNode;
  panel?: ReactNode;
  children: ReactNode;
}) {
  const head = useFinanceHead();
  return (
    <List {...head} state={stateOf(q, !!empty)} empty={empty} onRetry={() => void q.refetch()} actions={actions} more={more || undefined} panel={panel || undefined} sections={children} />
  );
}

/** The page's ⋯ with its items, or nothing when it has none. */
const More = ({ children }: { children: ReactNode[] }) =>
  children.some(Boolean) ? <Menu trigger={<IconButton icon="more" label="More" />}>{children}</Menu> : null;

/** `failure`: a books sign-in that came back unconnected (useBooksReturn), shown beside Sources until put away. */
export function BooksPage({ tab, failure = null, onDismiss = () => {} }: { tab: FinanceTab; failure?: string | null; onDismiss?: () => void }) {
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
    case 'attribution':
      return <Attribution />;
    case 'sources':
      return <Sources failure={failure} onDismiss={onDismiss} />;
    default:
      return <Access />;
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

/** Every entry, newest first. Past 100 rows only the rows in view are drawn; on a phone, rows under each day. */
function Transactions() {
  const may = useMay();
  const as = useViewAs();
  const qc = useQueryClient();
  const phone = usePhone();
  const q = useQuery(transactionsQuery(as));
  const [line, setLine] = useState<string | null>(null);
  const [find, setFind] = useState('');
  const [moving, setMoving] = useState(false);
  const words = find.trim().toLowerCase();
  const all = q.data?.rows ?? [];
  const rows = words ? all.filter((t) => `${describe(t)} ${t.account ?? ''} ${sourceOf(t)}`.toLowerCase().includes(words)) : all;
  // The server holds more than was read: the next page comes as the last row is drawn (a search reads only what is here).
  const more = q.data?.next && !words ? () => void loadMoreTransactions(as) : undefined;
  const share = may('finance.attribute') ? setLine : undefined;
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
      q={q}
      actions={
        may('finance.pay') && (
          <>
            <IconButton icon="plus" label="New transfer" onClick={() => void transfer()} />
            {moving && <TransferDialog onOpenChange={setMoving} />}
          </>
        )
      }
    >
      {line && <ShareDialog line={line} onOpenChange={(o) => !o && setLine(null)} />}
      <div className="ctl-row">
        <Search label="Search transactions" value={find} onValueChange={setFind} placeholder="Search" />
        <span className="grow" />
        <span className="count">{`${plural(rows.length, 'entry', 'entries')}${more ? '+' : ''}`}</span>
      </div>
      {phone ? (
        <TxPhone rows={rows} onMore={more} />
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
            <TxBody rows={rows} onEnd={more} onShare={share} />
          </Table>
        </div>
      )}
    </Page>
  );
}

/** On a phone: each day a group, each entry its words, line and source, and its amount; "More" reads the next page. */
function TxPhone({ rows, onMore }: { rows: readonly Tx[]; onMore?: () => void }) {
  if (rows.length === 0) return <div className="fold">No entries</div>;
  return (
    <div className="frs" role="list" aria-label="Transactions">
      {rows.map((t, i) => (
        <Fragment key={t.id}>
          {(i === 0 || rows[i - 1]?.date !== t.date) && (
            <div className="fr-group" role="presentation">
              <span>{fmtDate(t.date)}</span>
            </div>
          )}
          <div className={t.pending ? 'row fr is-quiet' : 'row fr'} role="listitem">
            <span className="fr-main">
              <span className="row-title">
                {describe(t)}
                {!t.account && <Pill flag>No line</Pill>}
              </span>
              <span className="fr-sub">
                {t.account && <span>{t.account}</span>}
                {sourceOf(t) && <span>{sourceOf(t)}</span>}
              </span>
            </span>
            <span className="row-fig">{cash(t.amount, true)}</span>
          </div>
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
export function linesOf(t: Tx): { id: string; account: string }[] {
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  if (t.kind === 'entry') return t.entry_id ? [{ id: t.id, account: t.account ?? '' }] : [];
  return (t.lines ?? []).flatMap((l) => (str(l.line_id) ? [{ id: str(l.line_id), account: str(l.account) }] : []));
}

function TxRow({ t, first, onShare }: { t: Tx; first: boolean; onShare?: (line: string) => void }) {
  const lines = onShare ? linesOf(t) : [];
  return (
    <tr className={t.pending ? 'is-quiet' : undefined}>
      <td className="t-words">{first ? fmtDate(t.date) : ''}</td>
      <td className="t-name">
        {describe(t)}
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
  );
}

const ROW = 34;

/** `onEnd` reads the next page once the last row is drawn. */
function TxBody({ rows, onEnd, onShare }: { rows: readonly Tx[]; onEnd?: () => void; onShare?: (line: string) => void }) {
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
          <TxRow key={t.id} t={t} first={first(i)} onShare={onShare} />
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
        return <TxRow key={t.id} t={t} first={first(it.index)} onShare={onShare} />;
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

/** Documents in named groups, each with its total, and the total of all. */
function DocTable({ label, name, number, groups }: { label: string; name: string; number?: boolean; groups: [string, DocRow[]][] }) {
  const shown = groups.filter(([, list]) => list.length > 0);
  const all = shown.flatMap(([, list]) => list);
  const span = number ? 4 : 3;
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
            <tr key={`g:${g}`} className="is-group">
              <td colSpan={span - 1}>
                {g}
                <span className="count">{list.length}</span>
              </td>
              <td className="num">{cash(sum(list))}</td>
            </tr>,
            ...list.map((d) => (
              <tr key={d.id}>
                <td className="t-name">
                  {(number ? d.customer?.name : d.vendor?.name) || d.number}
                  {late(d) && <Pill>{late(d)}</Pill>}
                </td>
                <td className="t-words">{number ? d.number : String((d.account as { name?: unknown } | null)?.name ?? '')}</td>
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
  const q = useQuery(billsQuery(useViewAs()));
  const [setting, setSetting] = useState(false);
  const email = q.data?.bills_email ?? '';
  const bills = q.data?.bills ?? [];
  const over = bills.filter((b) => late(b));
  const week = bills.filter((b) => !late(b) && soon(b));
  const later = bills.filter((b) => !late(b) && !soon(b));
  return (
    <Page
      q={q}
      empty={q.data && bills.length === 0 ? { title: 'Nothing to pay' } : undefined}
      more={
        // The owner or an admin sets the bills address; anyone else copies it (finance-docs.ts finDocEmailDraw).
        // The dialog sits with its menu: with no bills the page shows only its empty state.
        <>
          <More>
            {[
              may('finance.admin') ? (
                <MenuItem key="set" label="Bills address" meta={email || 'None'} onSelect={() => setSetting(true)} />
              ) : (
                email && <MenuItem key="copy" label={`Copy ${email}`} onSelect={() => copied(email)} />
              ),
            ]}
          </More>
          {setting && <BillsAddressDialog address={email} onOpenChange={setSetting} />}
        </>
      }
    >
      <DocTable
        label="Bills"
        name="Bill"
        groups={[
          ['Overdue', over],
          ['This week', week],
          ['Later', later],
        ]}
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
  const q = useQuery(invoicesQuery(useViewAs()));
  const list = q.data?.invoices ?? [];
  const aging = q.data?.open.aging;
  const max = aging ? Math.max(1, ...AGES.map(([k]) => aging[k])) : 1;
  return (
    <Page q={q} empty={q.data && list.length === 0 ? { title: 'Nobody owes you' } : undefined}>
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
      <Section name="Invoices" count={list.length}>
        <DocTable
          label="Invoices"
          name="Customer"
          number
          groups={[
            ['Late', list.filter((d) => late(d))],
            ['Current', list.filter((d) => !late(d))],
          ]}
        />
      </Section>
    </Page>
  );
}

// ---------------------------------------------------------------- Balance sheet

type Account = { name: string; balance: number; code: string };
const total = (list: readonly Account[]) => list.reduce((a, r) => a + r.balance, 0);

function Side({ label, head, parts }: { label: string; head: string; parts: { name: string; rows: readonly Account[]; total: string }[] }) {
  return (
    <Table label={label}>
      <colgroup>
        <col />
        <col className="t-col" />
      </colgroup>
      <thead>
        <tr>
          <Th>Account</Th>
          <Th num>{head}</Th>
        </tr>
      </thead>
      <tbody>
        {parts.map((p) => [
          <tr key={`g:${p.name}`} className="is-group">
            <td colSpan={2}>{p.name}</td>
          </tr>,
          ...p.rows.map((r) => (
            <tr key={`${p.name}:${r.code}:${r.name}`}>
              <td className="t-name">{r.name}</td>
              <td className={r.balance === 0 ? 'num zero' : 'num'}>{cash(r.balance)}</td>
            </tr>
          )),
          <tr key={`t:${p.name}`} className="is-total">
            <td className="t-name">{p.total}</td>
            <td className="num">{cash(total(p.rows))}</td>
          </tr>,
        ])}
        {parts.length > 1 && (
          <tr className="is-net">
            <td className="t-name">{parts.map((p) => p.name).join(' and ')}</td>
            <td className="num">{cash(parts.reduce((a, p) => a + total(p.rows), 0))}</td>
          </tr>
        )}
      </tbody>
    </Table>
  );
}

function Balance() {
  const may = useMay();
  const q = useQuery(balanceQuery(undefined, useViewAs()));
  const [importing, setImporting] = useState(false);
  const d = q.data;
  const head = d ? fmtDate(d.as_of) : '';
  return (
    <Page
      q={q}
      more={
        <>
          <More>{[may('finance.book') && <MenuItem key="import" label="Import history" onSelect={() => setImporting(true)} />]}</More>
          {importing && <ImportDialog onOpenChange={setImporting} />}
        </>
      }
    >
      {d && (
        <div className="two-col">
          <div>
            <Side label="Assets" head={head} parts={[{ name: 'Assets', rows: d.assets, total: 'Total assets' }]} />
          </div>
          <div>
            <Side
              label="Liabilities and equity"
              head={head}
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
  const q = useQuery(cardsQuery(useViewAs()));
  const cards = q.data?.cards ?? [];
  const spent = cards.reduce((a, c) => a + (c.spent ?? 0), 0);
  const limit = cards.reduce((a, c) => a + (c.limit ?? 0), 0);
  return (
    <Page
      q={q}
      actions={
        may('finance.cards') && (
          <>
            <IconButton icon="plus" label="New card" onClick={() => setMaking(true)} />
            {making && <CardDialog onOpenChange={setMaking} />}
          </>
        )
      }
      empty={q.data && cards.length === 0 ? { title: 'No cards yet' } : undefined}
    >
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
                  <td className="t-name">
                    {c.nickname}
                    {off && <Pill>{off}</Pill>}
                  </td>
                  <td className="t-words">{/^(human|bot):/.test(c.holder) ? <Who id={c.holder} /> : c.holder}</td>
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
    </Page>
  );
}

function Tax() {
  const q = useQuery(taxQuery(useViewAs()));
  const rows = q.data?.rows ?? [];
  const due = rows.filter((r) => r.due > 0);
  const filed = rows.filter((r) => r.due <= 0);
  const table = (label: string, list: typeof rows, paid: boolean) => (
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

// ---------------------------------------------------------------- Attribution, Sources, Access

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

const holds = (r: SourceRow) =>
  [r.last4 && `Ends in ${r.last4}`, r.entries && plural(r.entries, 'entry', 'entries'), r.bills && plural(r.bills, 'bill'), r.invoices && plural(r.invoices, 'invoice')]
    .filter(Boolean)
    .join(', ');
const failing = (r: SourceRow) => r.sync?.status === 'error' || r.sync?.status === 'failed' || !!r.sync?.last_error;

type SourceDialog =
  | { kind: 'key'; provider: KeyProvider }
  | { kind: 'books'; provider: keyof typeof BOOKS }
  | { kind: 'mirror' | 'stop' | 'disconnect'; row: SourceRow; books: boolean }
  | { kind: 'hook' }
  | { kind: 'statement'; row: SourceRow };

const ADS: [string, string][] = [
  ['google-ads', 'Google Ads'],
  ['meta-ads', 'Meta Ads'],
];
const live = (r: SourceRow) => r.sync?.status === 'active' || r.sync?.status === 'new';
const mirroring = (r: SourceRow) => r.mirror === 'mirror' || r.mirror === 'exporting';
const booksName = (r: SourceRow) => BOOKS[r.provider as keyof typeof BOOKS] ?? r.name;

/** Each feed, with a finance admin's way to connect another and, per source, the books, its webhook and Disconnect. */
function Sources({ failure, onDismiss }: { failure: string | null; onDismiss: () => void }) {
  const press = usePress();
  const may = useMay();
  const admin = may('finance.admin');
  const q = useQuery(sourcesQuery(useViewAs()));
  const hooks = useQuery({ ...hooksQuery, enabled: admin });
  const [open, setOpen] = useState<SourceDialog | null>(null);
  const [shown, setShown] = useState<[string, string][] | null>(null);
  const groups = q.data?.groups ?? [];
  const rows = groups.flatMap((g) => g.rows.map((r) => ({ g, r })));
  const hooked = (hooks.data?.triggers ?? []).some((t) => t.preset === 'stripe_finance');
  const close = (o: boolean) => !o && setOpen(null);

  // What a source's ⋯ holds (finance-admin.ts, the source rail's controls).
  const items = (g: SourceGroup, r: SourceRow) => {
    const books = g.group === 'accounting' && r.kind === 'connection';
    const out: ReactNode[] = [];
    if (books && may('finance.book') && live(r))
      out.push(
        mirroring(r) ? (
          <MenuItem key="stop" label="Stop using as books" onSelect={() => setOpen({ kind: 'stop', row: r, books })} />
        ) : (
          <MenuItem key="mirror" label="Use as books" onSelect={() => setOpen({ kind: 'mirror', row: r, books })} />
        ),
      );
    // A bank or card account's statement file, for whoever keeps the books (finance-admin.ts `upload`).
    if ((r.kind === 'bank' || r.kind === 'card_program') && may('finance.book'))
      out.push(<MenuItem key="statement" label="Upload statement" onSelect={() => setOpen({ kind: 'statement', row: r })} />);
    if (admin && r.provider === 'stripe' && (r.kind === 'connection' || r.kind === 'stripe') && hooks.data && !hooked)
      out.push(<MenuItem key="hook" label="Add webhook" onSelect={() => setOpen({ kind: 'hook' })} />);
    if (admin && r.kind === 'connection' && r.sync?.status !== 'disconnected') {
      if (out.length) out.push(<MenuRule key="rule" />);
      out.push(<MenuItem key="off" label={`Disconnect ${r.name}`} onSelect={() => setOpen({ kind: 'disconnect', row: r, books })} />);
    }
    return out;
  };

  const connect = admin && (
    <Menu trigger={<Button variant="primary">Add source</Button>}>
      {(Object.keys(KEY_SOURCES) as KeyProvider[]).map((p) => (
        <MenuItem key={p} label={KEY_SOURCES[p]} onSelect={() => setOpen({ kind: 'key', provider: p })} />
      ))}
      {(Object.keys(BOOKS) as (keyof typeof BOOKS)[]).map((p) => (
        <MenuItem key={p} label={BOOKS[p]} onSelect={() => setOpen({ kind: 'books', provider: p })} />
      ))}
      <MenuRule />
      {ADS.map(([id, name]) => (
        // Ad spend is set up on the platform's page in Tools.
        <MenuItem key={id} label={name} onSelect={() => void press('finance.addSource', {})} />
      ))}
    </Menu>
  );
  const dialogs = (
    <>
      {open?.kind === 'key' && <ConnectKeyDialog provider={open.provider} onOpenChange={close} />}
      {open?.kind === 'books' && <ConnectBooksDialog provider={open.provider} onOpenChange={close} />}
      {open?.kind === 'mirror' && (
        <ConfirmDialog title={`Use ${booksName(open.row)} as the books`} submit="Use as books" action="finance.startMirror" input={{ connection: open.row.id }} onOpenChange={close} />
      )}
      {open?.kind === 'stop' && <ConfirmDialog title={`Stop using ${booksName(open.row)}`} submit="Stop" action="finance.stopMirror" input={{}} onOpenChange={close} />}
      {open?.kind === 'disconnect' && (
        <ConfirmDialog
          title={`Disconnect ${open.row.name}`}
          submit="Disconnect"
          action="finance.disconnect"
          input={{ connection: open.row.id, ...(open.books ? { books: true } : {}) }}
          onOpenChange={close}
        />
      )}
      {open?.kind === 'statement' && <StatementDialog account={open.row} onOpenChange={close} />}
      {open?.kind === 'hook' && <WebhookDialog onOpenChange={close} onShown={setShown} />}
      {shown && <ShownOnce title="Stripe webhook" pairs={shown} onClose={() => setShown(null)} />}
    </>
  );
  // The books sign-in that came back unconnected: what happened, and the way to connect again.
  const panel = failure && (
    <SidePanel
      title="Books not connected"
      target="source:books"
      onClose={onDismiss}
      foot={
        admin &&
        (Object.keys(BOOKS) as (keyof typeof BOOKS)[]).map((p) => (
          <Button
            key={p}
            onClick={() => {
              onDismiss();
              setOpen({ kind: 'books', provider: p });
            }}
          >
            {`Connect ${BOOKS[p]}`}
          </Button>
        ))
      }
    >
      <p className="t-read">{failure}</p>
    </SidePanel>
  );
  return (
    <Page
      q={q}
      actions={
        <>
          {connect}
          {dialogs}
        </>
      }
      panel={panel}
      empty={q.data !== undefined && rows.length === 0 ? { title: 'No source connected' } : undefined}
    >
      <div className="t-scroll">
        <Table label="Sources" stick>
          <colgroup>
            <col />
            <col className="t-line" />
            <col className="t-line" />
            <col className="t-col" />
            <col className="t-act" />
          </colgroup>
          <thead>
            <tr>
              <Th>Source</Th>
              <Th>Feeds</Th>
              <Th>Holds</Th>
              <Th num>Last sync</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {rows.map(({ g, r }) => {
              const more = items(g, r);
              return (
                <tr key={`${g.group}:${r.id}`} data-target={`source:${r.id}`}>
                  <td className="t-name">
                    {r.name}
                    {failing(r) && <Pill flag>Sign in again</Pill>}
                  </td>
                  <td className="t-words">{g.name}</td>
                  <td className="t-words">{r.manual ? 'Entered by hand' : holds(r)}</td>
                  <td className="num t-words">{r.sync?.last_ok_at ? fmtAge(r.sync.last_ok_at) : ''}</td>
                  <td className="t-act">{more.length > 0 && <Menu trigger={<IconButton icon="more" label={`More for ${r.name}`} size="sm" quiet />}>{more}</Menu>}</td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </div>
    </Page>
  );
}

const SEES: Record<AccessTeammate['default'], string> = {
  everything: 'Everything',
  team_expenses: "Their team's lines",
  own_expenses: 'What they spend',
  none: 'Nothing',
};

function Access() {
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const viewing = !!useViewAs();
  const q = useQuery(accessQuery);
  const all = q.data?.teammates ?? [];
  const sees = all.filter((t) => t.default !== 'none' || (t.grants?.length ?? 0) > 0);
  const rest = all.length - sees.length;
  return (
    <Page
      q={q}
      actions={
        !viewing && (
          <>
            {q.data?.can_view_as && <Button onClick={() => setPicking(true)}>View as</Button>}
            {q.data?.can_edit_defaults && <Button onClick={() => setEditing(true)}>Edit defaults</Button>}
            {editing && <DefaultsDialog defaults={q.data?.defaults ?? {}} onOpenChange={setEditing} />}
            {picking && <ViewAsDialog onOpenChange={setPicking} />}
          </>
        )
      }
      empty={q.data === null ? { title: "You don't see who has access" } : undefined}
    >
      <Table label="Access" stick>
        <colgroup>
          <col />
          <col className="t-line" />
          <col className="t-line" />
        </colgroup>
        <thead>
          <tr>
            <Th>Teammate</Th>
            <Th>Sees</Th>
            <Th>Approves</Th>
          </tr>
        </thead>
        <tbody>
          {sees.map((t) => (
            <tr key={t.teammate}>
              <td className="t-name">
                <Who id={t.teammate} />
              </td>
              <td className="t-words">{SEES[t.default] ?? ''}</td>
              <td className="t-words">{t.rights?.includes('approve') ? 'Yes' : ''}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {rest > 0 && <div className="fold">{`${count(rest)} ${rest === 1 ? 'other sees' : 'others see'} nothing`}</div>}
    </Page>
  );
}
