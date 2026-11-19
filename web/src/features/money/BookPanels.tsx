import { useQuery } from '@tanstack/react-query';
import { useSearch } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { refusal } from '@/actions';
import { ApiError } from '@/api/client';
import { fmtDate } from '@/lib';
import { Button } from '@/kit/Button';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { Section, SidePanel } from '@/pagekinds';
import { DocDecide, LimitDialog, PaidDialog, ScheduleDialog, VoidDialog, WriteOffDialog } from './BillForms';
import { useMay, useRun } from './dialogs';
import { cash } from './model';
import { Pfig, Prow, usePress, useViewAs, Who } from './parts';
import { cardsQuery, docQuery, type Card, type DocKind } from './queries';
import { validateFinanceSearch } from './search';

/* The books tabs' side panels (s475): a bill or an invoice with its lines, payments and decisions; a card with its
   freeze and limit. Each opens at its own address, `?panel=<kind>:<id>`. A transaction opens in place on its list. */

/** The Finance address as the books tabs read it: the open panel, the balance sheet's day, the Transactions filter. */
export const useBookSearch = () => validateFinanceSearch(useSearch({ strict: false }) as Record<string, unknown>);

function Fact({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="fact">
      <span>{name}</span>
      <span className="fact-v">{children}</span>
    </div>
  );
}

const day = (d: string | null | undefined) => (d ? fmtDate(d) : '');
const STATUS: Record<string, string> = {
  draft: 'Draft',
  open: 'Open',
  scheduled: 'Scheduled',
  sent: 'Sent',
  partial: 'Partly paid',
  paid: 'Paid',
  void: 'Void',
};
// A payment on its way: the bill moves to another day only before one is (finance-docs.ts FIN_PAY_LIVE).
const LIVE = new Set(['queued', 'sending', 'awaiting_bank_approval', 'submitted']);
const FROM: Record<string, string> = { payment: 'Payment', feed: 'Bank', credit: 'Credit', account: 'Account', expected: 'Expected' };

function Unread({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (error instanceof ApiError && error.status === 403) return <p className="t-read">Not in your view</p>;
  return (
    <div className="stack">
      <p className="t-read">Did not load</p>
      <Button onClick={onRetry}>Try again</Button>
    </div>
  );
}

type DocOpen = 'approve' | 'send' | 'schedule' | 'paid' | 'void' | 'writeoff';

/** A bill or an invoice: what it is, its lines and payments, and the decisions its status allows (finance-docs.ts finDocActs). */
export function DocPanel({ kind, id }: { kind: DocKind; id: string }) {
  const press = usePress();
  const may = useMay();
  const q = useQuery(docQuery(kind, id, useViewAs()));
  const [open, setOpen] = useState<DocOpen | null>(null);
  const close = (o: boolean) => !o && setOpen(null);
  const d = q.data;
  const bill = kind === 'bill';
  const title = (d && ((bill ? d.vendor?.name : d.customer?.name) || d.number)) || (bill ? 'Bill' : 'Invoice');
  const right = may(bill ? 'finance.ap' : 'finance.ar');
  const pay = may('finance.pay');
  const st = d?.status ?? '';
  // The decisions, the main one last; Void stays in the head's menu.
  const acts: [DocOpen, string][] = !d || !right
    ? []
    : bill
      ? st === 'draft'
        ? [['approve', 'Approve']]
        : st === 'open'
          ? [['paid', 'Mark paid'], ...(pay ? ([['schedule', 'Schedule']] as [DocOpen, string][]) : [])]
          : st === 'scheduled'
            ? [['paid', 'Mark paid'], ...(pay && !LIVE.has(d.payment?.status ?? '') ? ([['schedule', 'Reschedule']] as [DocOpen, string][]) : [])]
            : []
      : st === 'draft'
        ? [['send', 'Send']]
        : st === 'sent' || st === 'partial'
          ? [['writeoff', 'Write off'], ['paid', 'Mark paid']]
          : [];
  const voids = !!d && right && bill && ['draft', 'open', 'scheduled'].includes(st);
  const asked = (d?.actions ?? []).filter((a) => a.approval_id || a.status === 'needs_approval');
  const from = typeof d?.schedule?.money_account_id === 'string' ? d.schedule.money_account_id : undefined;
  return (
    <SidePanel
      title={title}
      target={`${kind}:${id}`}
      onClose={() => void press('finance.closePanel', {})}
      actions={
        voids && (
          <Menu trigger={<IconButton icon="more" label={`More for ${title}`} size="sm" quiet />}>
            <MenuItem label="Void" onSelect={() => setOpen('void')} />
          </Menu>
        )
      }
      foot={
        acts.length > 0 &&
        acts.map(([v, label], i) => (
          <Button key={v} variant={i === acts.length - 1 ? 'primary' : 'plain'} onClick={() => setOpen(v)}>
            {label}
          </Button>
        ))
      }
    >
      <div className="money">
        {q.isError ? (
          <Unread error={q.error} onRetry={() => void q.refetch()} />
        ) : (
          d && (
            <>
              <Pfig big={cash(d.total)} sub={STATUS[st] ?? st} />
              <section className="sec facts">
                {d.number && <Fact name={bill ? 'Bill' : 'Invoice'}>{d.number}</Fact>}
                <Fact name="Date">{day(d.date)}</Fact>
                {!bill && d.sent_at && <Fact name="Sent">{day(d.sent_at)}</Fact>}
                {d.due_date && <Fact name="Due">{day(d.due_date)}</Fact>}
                {bill && d.pay_on && <Fact name="Pays">{day(d.pay_on)}</Fact>}
                {bill && d.approved_by && (
                  <Fact name="Approved by">
                    <Who id={d.approved_by} />
                  </Fact>
                )}
                {!!d.paid && <Fact name="Paid">{cash(d.paid)}</Fact>}
                {d.remaining !== d.total && <Fact name="Left">{cash(d.remaining)}</Fact>}
                {!bill && !!d.written_off && <Fact name="Written off">{cash(d.written_off)}</Fact>}
                {d.memo && <Fact name="Memo">{d.memo}</Fact>}
              </section>
              {d.lines.length > 0 && (
                <Section name="Lines" count={d.lines.length}>
                  {d.lines.map((l) => (
                    <Prow key={l.id} name={String(l.account?.name ?? '') || l.description} sub={l.taxable ? 'Taxable' : ''} fig={cash(l.amount, true)} />
                  ))}
                </Section>
              )}
              {d.settlements.length > 0 && (
                <Section name="Payments" count={d.settlements.length}>
                  {d.settlements.map((s) => (
                    <Prow key={s.id} name={day(s.settled_on)} sub={s.voided_at ? 'Void' : (FROM[s.from_kind] ?? '')} fig={cash(s.amount, true)} />
                  ))}
                </Section>
              )}
              {asked.length > 0 && (
                <Section name="Approvals" count={asked.length}>
                  {asked.map((a) => (
                    <Prow key={a.action_id} name={a.approval_to ? <Who id={a.approval_to} /> : a.kind} sub={a.status === 'needs_approval' ? 'Waiting' : a.status} fig="" />
                  ))}
                </Section>
              )}
            </>
          )
        )}
      </div>
      {d && open === 'approve' && (
        <DocDecide title="Approve bill" submit="Approve" kind="bill_approve" payload={{ bill_id: d.id }} action="finance.approveBill" input={{ bill: d.id }} doc={d} onOpenChange={close} />
      )}
      {d && open === 'send' && (
        <DocDecide title="Send invoice" submit="Send" kind="invoice_send" payload={{ invoice_id: d.id }} action="finance.sendInvoice" input={{ invoice: d.id }} doc={d} onOpenChange={close} />
      )}
      {d && open === 'schedule' && <ScheduleDialog doc={d} from={from} onOpenChange={close} />}
      {d && open === 'paid' && <PaidDialog kind={kind} doc={d} onOpenChange={close} />}
      {d && open === 'void' && <VoidDialog doc={d} onOpenChange={close} />}
      {d && open === 'writeoff' && <WriteOffDialog doc={d} onOpenChange={close} />}
    </SidePanel>
  );
}

/** Who froze a frozen card: its budget (lifted when the budget has room) or a person (backend/finance/tab.py). */
const frozenBy = (c: Card) => (c as Card & { frozen_by?: string | null }).frozen_by ?? null;
const frozen = (c: Card) => c.status === 'frozen' || c.status === 'paused';
const closed = (c: Card) => c.status === 'canceled' || c.status === 'closed';

/** A card: its holder, budget, limit and spend, with Freeze or Unfreeze and its limit (finance-books.ts finCardRail). */
export function CardPanel({ id }: { id: string }) {
  const press = usePress();
  const run = useRun();
  const may = useMay();
  const q = useQuery(cardsQuery(useViewAs()));
  const [limit, setLimit] = useState(false);
  const [busy, setBusy] = useState(false);
  const c = q.data?.cards.find((x) => x.id === id);
  const title = c ? c.nickname || 'Card' : 'Card';
  const can = !!c && may('finance.cards') && !closed(c);
  // A budget's freeze lifts itself when the budget has room again; only a person's is undone by hand.
  const toggle = c && can ? (frozen(c) ? (frozenBy(c) === 'budget' ? null : 'Unfreeze') : 'Freeze') : null;
  const flip = async () => {
    if (!c) return;
    setBusy(true);
    const r = await run(frozen(c) ? 'finance.unfreezeCard' : 'finance.freezeCard', { card: c.id });
    setBusy(false);
    if (!r.ok) toast(refusal(r, 'Not done'));
  };
  const budget = c?.budget && typeof c.budget.name === 'string' ? c.budget.name : '';
  return (
    <SidePanel
      title={title}
      target={`card:${id}`}
      onClose={() => void press('finance.closePanel', {})}
      foot={
        can && (
          <>
            {toggle && (
              <Button busy={busy} onClick={() => void flip()}>
                {toggle}
              </Button>
            )}
            <Button variant="primary" onClick={() => setLimit(true)}>
              Set limit
            </Button>
          </>
        )
      }
    >
      <div className="money">
        {q.isError ? (
          <Unread error={q.error} onRetry={() => void q.refetch()} />
        ) : (
          c && (
            <>
              <Pfig big={cash(c.spent ?? 0)} sub={c.limit ? `of ${cash(c.limit)} a month` : 'No limit'} />
              <section className="sec facts">
                <Fact name="Holder">{/^(human|bot):/.test(c.holder) ? <Who id={c.holder} /> : c.holder}</Fact>
                {c.last4 && <Fact name="Ends in">{c.last4}</Fact>}
                {budget && <Fact name="Budget">{budget}</Fact>}
                {frozen(c) && <Fact name="Frozen by">{frozenBy(c) === 'budget' ? 'Budget' : 'Person'}</Fact>}
                {closed(c) && <Fact name="Status">Closed</Fact>}
                {c.expires_at && <Fact name="Expires">{day(c.expires_at)}</Fact>}
              </section>
            </>
          )
        )}
      </div>
      {c && limit && <LimitDialog card={c} onOpenChange={setLimit} />}
    </SidePanel>
  );
}
