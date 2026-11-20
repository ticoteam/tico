import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { fmtAge } from '@/lib';
import { Button } from '@/kit/Button';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { Table } from '@/kit/Table';
import { List, Section, SidePanel } from '@/pagekinds';
import { ConfirmDialog, ConnectBooksDialog, ConnectKeyDialog, ShownOnce, useMay, WebhookDialog } from './dialogs';
import { StatementDialog } from './forms';
import { FIN, FinanceOwner, plural, useFinanceHead } from './head';
import { cash } from './model';
import { ActLink, PhoneRows, Prow, readState, usePhone, usePress, useViewAs } from './parts';
import { balanceQuery, BOOKS, hooksQuery, KEY_SOURCES, sourcesQuery, type KeyProvider, type SourceGroup, type SourceRow } from './queries';
import { panelOf, type FinanceSearch } from './search';

/* Sources: each feed, with a finance admin's way to connect another and, per source, the books, its webhook and
   Disconnect in its ⋯; opened beside (`?panel=source:<id>`), its accounts, the line each books to, Sync now and
   Reconnect (ui/app/finance-admin.ts finSrcRail). */

/** A source row with what the route carries past the shared type (backend/finance/tab.py _money_account_row, _sync). */
type Src = Omit<SourceRow, 'sync'> & {
  balance?: number | null;
  account?: { id: string; code?: string; name: string } | null;
  sync?: (NonNullable<SourceRow['sync']> & { connection_id?: string | null }) | null;
};

const holds = (r: Src) =>
  [r.last4 && `Ends in ${r.last4}`, r.entries && plural(r.entries, 'entry', 'entries'), r.bills && plural(r.bills, 'bill'), r.invoices && plural(r.invoices, 'invoice')]
    .filter(Boolean)
    .join(', ');
/** What each kind of source feeds in Finance, as the mock says it ("Cash, Transactions"). */
const FEEDS: Record<string, string> = {
  banks: 'Cash, Transactions',
  cards: 'Transactions, Cards',
  payments: 'Revenue, Payment fees',
  payroll: 'Payroll',
  bills_invoices: 'Payables, Receivables',
  tico: 'AI services',
  accounting: 'P&L, Balance sheet',
  manual: 'Transactions',
};
const feeds = (g: { group: string; name: string }) => FEEDS[g.group] ?? g.name;
const failing = (r: Src) => r.sync?.status === 'error' || r.sync?.status === 'failed' || !!r.sync?.last_error;
const live = (r: Src) => r.sync?.status === 'active' || r.sync?.status === 'new';
const mirroring = (r: Src) => r.mirror === 'mirror' || r.mirror === 'exporting';
const booksName = (r: Src) => BOOKS[r.provider as keyof typeof BOOKS] ?? r.name;
/** A bank, card or payments account: it books to one line of the balance sheet (finance-admin.ts finSrcMoney). */
const isMoney = (r: Src) => 'account' in r && !['connection', 'usage', 'documents', 'entries'].includes(r.kind);
/** The connection a source syncs through: itself, or the one its account came from. */
const connOf = (r: Src) => (r.kind === 'connection' ? r.id : (r.sync?.connection_id ?? ''));

type Open =
  | { kind: 'key'; provider: KeyProvider }
  | { kind: 'books'; provider: keyof typeof BOOKS }
  | { kind: 'mirror' | 'stop' | 'disconnect'; row: Src; books: boolean }
  | { kind: 'hook' }
  | { kind: 'statement'; row: Src };

const ADS: [string, string][] = [
  ['google-ads', 'Google Ads'],
  ['meta-ads', 'Meta Ads'],
];

/** `failure`: a books sign-in that came back unconnected (useBooksReturn), shown beside until put away. */
export function SourcesPage({ failure, onDismiss, search }: { failure: string | null; onDismiss: () => void; search: FinanceSearch }) {
  const press = usePress();
  const may = useMay();
  const admin = may('finance.admin');
  const head = useFinanceHead();
  const phone = usePhone();
  const q = useQuery(sourcesQuery(useViewAs()));
  const hooks = useQuery({ ...hooksQuery, enabled: admin });
  const [open, setOpen] = useState<Open | null>(null);
  const [shown, setShown] = useState<[string, string][] | null>(null);
  const groups = (q.data?.groups ?? []) as (Omit<SourceGroup, 'rows'> & { rows: Src[] })[];
  const rows = groups.flatMap((g) => g.rows.map((r) => ({ g, r })));
  const hooked = (hooks.data?.triggers ?? []).some((t) => t.preset === 'stripe_finance');
  const close = (o: boolean) => !o && setOpen(null);
  const at = panelOf(search.panel);
  const sel = at?.[0] === 'source' ? rows.find(({ r }) => r.id === at[1]) : undefined;

  // What a source's ⋯ holds (finance-admin.ts, the source rail's controls).
  const items = (g: { group: string }, r: Src) => {
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
        <ConfirmDialog title={`Use ${booksName(open.row)} as the books`} submit="Use" action="finance.startMirror" input={{ connection: open.row.id }} onOpenChange={close} />
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
      {open?.kind === 'statement' && <StatementDialog account={open.row as SourceRow} onOpenChange={close} />}
      {open?.kind === 'hook' && <WebhookDialog onOpenChange={close} onShown={setShown} />}
      {shown && <ShownOnce title="Stripe webhook" pairs={shown} onClose={() => setShown(null)} />}
    </>
  );
  // The books sign-in that came back unconnected: what happened, and the way to connect again.
  const panel = failure ? (
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
  ) : (
    sel && <SourcePanel row={sel.r} group={feeds(sel.g)} all={rows.map((x) => x.r)} admin={admin} onReconnect={(p) => setOpen({ kind: 'books', provider: p })} />
  );
  return (
    <List
      {...head}
      state={readState(q, q.data !== undefined && rows.length === 0)}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No source connected' }}
      actions={
        <>
          <FinanceOwner />
          {connect}
          {dialogs}
        </>
      }
      panel={panel || undefined}
      sections={
        phone ? (
          <PhoneRows
            label="Sources"
            groups={[
              {
                rows: rows.map(({ g, r }) => {
                  const more = items(g, r);
                  return {
                    key: `${g.group}:${r.id}`,
                    title: (
                      <>
                        <ActLink to={FIN} search={{ tab: 'sources', panel: `source:${r.id}` }} action="finance.openSource" input={{ source: r.id }} className="t-link">
                          {r.name}
                        </ActLink>
                        {failing(r) && <Pill flag>Sign in again</Pill>}
                      </>
                    ),
                    sub: [feeds(g), r.manual ? 'Entered by hand' : holds(r)],
                    fig: r.sync?.last_ok_at ? fmtAge(r.sync.last_ok_at) : '',
                    act: more.length > 0 && <Menu trigger={<IconButton icon="more" label={`More for ${r.name}`} size="sm" quiet />}>{more}</Menu>,
                  };
                }),
              },
            ]}
          />
        ) : (
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
                <th scope="col">Source</th>
                <th scope="col">Feeds</th>
                <th scope="col">Holds</th>
                <th scope="col" className="num">
                  Last sync
                </th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {rows.map(({ g, r }) => {
                const more = items(g, r);
                return (
                  <tr key={`${g.group}:${r.id}`} data-target={`source:${r.id}`} className={sel?.r.id === r.id ? 'is-selected' : undefined}>
                    <td className="t-name">
                      <ActLink to={FIN} search={{ tab: 'sources', panel: `source:${r.id}` }} action="finance.openSource" input={{ source: r.id }} className="t-link">
                        {r.name}
                      </ActLink>
                      {failing(r) && <Pill flag>Sign in again</Pill>}
                    </td>
                    <td className="t-words">{feeds(g)}</td>
                    <td className="t-words">{r.manual ? 'Entered by hand' : holds(r)}</td>
                    <td className="num t-words">{r.sync?.last_ok_at ? fmtAge(r.sync.last_ok_at) : ''}</td>
                    <td className="t-act">{more.length > 0 && <Menu trigger={<IconButton icon="more" label={`More for ${r.name}`} size="sm" quiet />}>{more}</Menu>}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </div>
        )
      }
    />
  );
}

/** One source, opened: what it holds, the line each account books to, its last sync; Sync now, or Reconnect for books
    whose sign-in ran out (finance-admin.ts:183). */
function SourcePanel({ row: r, group, all, admin, onReconnect }: { row: Src; group: string; all: Src[]; admin: boolean; onReconnect: (p: keyof typeof BOOKS) => void }) {
  const press = usePress();
  const conn = connOf(r);
  const on = !!conn && r.sync?.status !== 'disconnected';
  const books = r.kind === 'connection' && r.provider != null && r.provider in BOOKS;
  const reconnect = books && r.sync?.status === 'reconnect';
  const accounts = r.kind === 'connection' ? all.filter((x) => isMoney(x) && x.sync?.connection_id === r.id) : [];
  return (
    <SidePanel
      title={r.name}
      target={`source:${r.id}`}
      onClose={() => void press('finance.closePanel', {})}
      foot={
        admin &&
        (reconnect ? (
          <Button variant="primary" onClick={() => onReconnect(r.provider as keyof typeof BOOKS)}>
            Reconnect
          </Button>
        ) : (
          on && (
            <Button variant="primary" onClick={() => press('finance.syncSource', { connection: conn })}>
              Sync now
            </Button>
          )
        ))
      }
    >
      <div className="money pbody">
        <Facts label="Source">
          <LabelValue label="Feeds">{group}</LabelValue>
          {r.last4 && <LabelValue label="Account">{`Ends in ${r.last4}`}</LabelValue>}
          {r.kind !== 'connection' && r.balance != null && (
            <LabelValue label="Balance" num>
              {cash(r.balance)}
            </LabelValue>
          )}
          {isMoney(r) && (
            <LabelValue label="Books as">
              <BooksAs row={r} all={all} admin={admin} />
            </LabelValue>
          )}
          {r.sync?.last_ok_at && <LabelValue label="Last sync">{fmtAge(r.sync.last_ok_at)}</LabelValue>}
          {r.sync?.last_error && <LabelValue label="Error">{r.sync.last_error}</LabelValue>}
        </Facts>
        {accounts.length > 0 && (
          <Section name="Accounts" count={accounts.length}>
            {accounts.map((x) => (
              <Prow key={x.id} name={x.last4 ? `${x.name} ${x.last4}` : x.name} sub={x.account?.name ?? 'Not mapped'} fig={x.balance != null ? cash(x.balance) : ''} />
            ))}
          </Section>
        )}
      </div>
    </SidePanel>
  );
}

/** The balance sheet line an account books to; a finance admin picks another: a card program a liability, anything
    else an asset, one account per line (backend/finance/banking.py connection_map). */
function BooksAs({ row: r, all, admin }: { row: Src; all: Src[]; admin: boolean }) {
  const press = usePress();
  const sheet = useQuery({ ...balanceQuery(), enabled: admin });
  if (!admin || !sheet.data) return <>{r.account?.name ?? 'Not mapped'}</>;
  const lines = r.kind === 'card_program' ? sheet.data.liabilities : sheet.data.assets;
  const taken = new Set(all.filter((x) => x.id !== r.id && isMoney(x) && x.account).map((x) => x.account!.id));
  const options = (lines ?? [])
    .filter((l): l is typeof l & { account_id: string } => !!l.account_id && (!taken.has(l.account_id) || l.account_id === r.account?.id))
    .map((l) => ({ value: l.account_id, label: l.name }));
  return (
    <Select
      label="Books as"
      labelHidden
      options={options}
      value={r.account?.id ?? null}
      placeholder="Not mapped"
      onValueChange={(line) => line !== r.account?.id && void press('finance.mapSource', { account: r.id, line })}
    />
  );
}
