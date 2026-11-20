import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { meQuery, useActor, useDirectory, type Directory } from '@/data';
import { Button } from '@/kit/Button';
import { Avatar } from '@/kit/Avatar';
import { DatePick } from '@/kit/DatePick';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { MenuItem } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { Table } from '@/kit/Table';
import { List, PageMore, Section, SidePanel } from '@/pagekinds';
import type { AccessList, Grant, Mate, Team } from './caps';
import { Ask, DefaultsDialog, useAsk } from './dialogs';
import { ViewAsDialog } from './forms';
import { FIN, FinanceOwner, useFinanceHead } from './head';
import { budgetDay, count, dayOf, monthSpan } from './model';
import { ActLink, readState, usePress, useViewAs, Who } from './parts';
import { accessQuery, balanceQuery, pnlQuery } from './queries';
import { panelOf, type FinanceSearch } from './search';

/* Access: who sees what in Finance and who approves. A teammate opens beside (`?panel=mate:<id>`) with what they see
   and why, each view given to them, and Take back; Give access and the Finance approver are here too
   (ui/app/finance-admin.ts finAccessPage). */

const SEES: Record<Mate['default'], string> = {
  everything: 'Everything',
  team_expenses: "Their team's lines",
  own_expenses: 'What they spend',
  none: 'Nothing',
};

const WHAT: Record<string, string> = {
  everything: 'Everything',
  pnl: 'P&L',
  expenses: 'Expenses',
  pnl_lines: 'P&L line',
  balance_sheet: 'Balance sheet',
  bs_lines: 'Balance sheet line',
  payables: 'Payables',
  receivables: 'Receivables',
  sources: 'Sources',
};
/** What a manager may give: their teams' expenses and expense lines (core/view.py check_grant). */
const MANAGER_WHAT = ['expenses', 'pnl_lines'];
const LINES = new Set(['pnl_lines', 'bs_lines']);
/** Everything, the balance sheet, payables, receivables and sources are company wide. */
const SCOPED = new Set(['expenses', 'pnl', 'pnl_lines']);

const nameIn = (teams: readonly Team[] | undefined, id: string | null | undefined) => teams?.find((t) => t.id === id)?.name ?? '';

/** A view given, in words: "Sales expenses", "P&L, Sales", "Balance sheet" (finance-admin.ts finGrantLabel). */
function grantLabel(g: Grant, teams: readonly Team[] | undefined) {
  const whose = g.whose === 'own' ? 'own' : g.whose === 'team' ? nameIn(teams, g.whose_team) : 'company';
  if (g.what === 'expenses') return g.whose === 'own' ? 'Own expenses' : g.whose === 'team' ? `${nameIn(teams, g.whose_team)} expenses` : 'Company expenses';
  if (g.what === 'everything' || g.whose === 'company' || g.what === 'balance_sheet' || g.what === 'bs_lines' || !SCOPED.has(g.what)) return WHAT[g.what] ?? g.what;
  return `${WHAT[g.what] ?? g.what}, ${whose}`;
}

export function AccessPage({ search }: { search: FinanceSearch }) {
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [giving, setGiving] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const head = useFinanceHead();
  const viewing = !!useViewAs();
  const q = useQuery(accessQuery);
  const acc = q.data as AccessList | null | undefined;
  const all = acc?.teammates ?? [];
  const seeing = (t: Mate) => t.default !== 'none' || (t.grants?.length ?? 0) > 0;
  const [unfolded, setUnfolded] = useState(false);
  const rest = all.filter((t) => !seeing(t));
  const sees = unfolded ? all : all.filter(seeing);
  const may = !viewing && all.some((t) => t.may_grant);
  const at = panelOf(search.panel);
  const sel = at?.[0] === 'mate' ? all.find((t) => t.teammate === at[1]) : undefined;
  return (
    <List
      {...head}
      state={readState(q, q.data === null)}
      onRetry={() => void q.refetch()}
      empty={{ title: "You don't see who has access" }}
      actions={
        !viewing && (
          <>
            <FinanceOwner />
            {may && (
              <Button variant="primary" onClick={() => setGiving('')}>
                Give access
              </Button>
            )}
            <PageMore>
              {acc?.can_view_as && <MenuItem label="View as" onSelect={() => setPicking(true)} />}
              {acc?.can_edit_defaults && <MenuItem label="Edit defaults" onSelect={() => setEditing(true)} />}
              {acc?.can_edit_defaults && <MenuItem label="Finance approver" onSelect={() => setApproving(true)} />}
            </PageMore>
            {editing && <DefaultsDialog defaults={acc?.defaults ?? {}} onOpenChange={setEditing} />}
            {picking && <ViewAsDialog onOpenChange={setPicking} />}
            {giving !== null && acc && <GiveDialog acc={acc} teammate={giving} onOpenChange={(o) => !o && setGiving(null)} />}
            {approving && acc && <ApproverDialog acc={acc} onOpenChange={setApproving} />}
          </>
        )
      }
      panel={sel && acc ? <MatePanel mate={sel} acc={acc} viewing={viewing} onGive={() => setGiving(sel.teammate)} /> : undefined}
      sections={
        <>
          <Table label="Access" stick>
            <colgroup>
              <col />
              <col className="t-line t-sees" />
              <col className="t-line t-approves" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Teammate</th>
                <th scope="col">Sees</th>
                <th scope="col">Approves</th>
              </tr>
            </thead>
            <tbody>
              {sees.map((t) => (
                <tr key={t.teammate} data-target={`mate:${t.teammate}`} className={sel?.teammate === t.teammate ? 'is-selected' : undefined}>
                  <td className="t-name">
                    <ActLink to={FIN} search={{ tab: 'access', panel: `mate:${t.teammate}` }} action="finance.openMate" input={{ teammate: t.teammate }} className="t-link">
                      <Who id={t.teammate} />
                    </ActLink>
                  </td>
                  <td className="t-words">{SEES[t.default] ?? ''}</td>
                  <td className="t-words">{t.default === 'everything' || t.teammate === acc?.finance_approver ? 'Any amount' : ''}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          {rest.length > 0 && !unfolded && (
            <button type="button" className="fold" onClick={() => setUnfolded(true)}>
              <span className="faces">
                {rest.slice(0, 6).map((t) => (
                  <Face key={t.teammate} id={t.teammate} />
                ))}
              </span>
              <span>{`${count(rest.length)} ${rest.length === 1 ? 'other sees' : 'others see'} nothing`}</span>
              <span className="grow" />
              <Icon name="chevron" />
            </button>
          )}
        </>
      }
    />
  );
}

/** A small face in the fold of those who see nothing. */
function Face({ id }: { id: string }) {
  const a = useActor(id);
  return <Avatar kind={a?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={a?.name ?? 'Teammate'} hue={a?.hue} size={16} decorative />;
}

/** One teammate: where they sit, what they see by default and each view given, with Take back for whoever gave it
    (or the owner) (finance-admin.ts finAccRail). */
function MatePanel({ mate: t, acc, viewing, onGive }: { mate: Mate; acc: AccessList; viewing: boolean; onGive: () => void }) {
  const press = usePress();
  const me = useQuery(meQuery).data?.id;
  const who = useActor(t.teammate);
  const boss = useActor(t.reports_to ?? null);
  const name = who?.name ?? t.teammate.slice(t.teammate.indexOf(':') + 1);
  const path: string[] = [];
  for (let id = t.team, n = 0; id && n < 12; n++) {
    const team = acc.teams?.find((x) => x.id === id);
    if (!team) break;
    path.unshift(team.name);
    id = team.parent;
  }
  return (
    <SidePanel
      title={name}
      target={`mate:${t.teammate}`}
      onClose={() => void press('finance.closePanel', {})}
      foot={
        !viewing &&
        t.may_grant && (
          <Button variant="quiet" onClick={onGive}>
            Give access
          </Button>
        )
      }
    >
      <div className="money pbody">
        <Facts label="Teammate">
          {path.length > 0 && <LabelValue label="Team">{path.join(' › ')}</LabelValue>}
          <LabelValue label="Kind">{t.kind === 'bot' ? 'Agent' : 'Person'}</LabelValue>
          {t.reports_to && <LabelValue label="Reports to">{boss?.name ?? t.reports_to.slice(t.reports_to.indexOf(':') + 1)}</LabelValue>}
        </Facts>
        <Section name="Sees">
          <div className="row prow">
            <span className="row-title">{SEES[t.default] ?? ''}</span>
            <span className="row-sub">Default</span>
          </div>
          {(t.grants ?? []).map((g) => {
            const label = grantLabel(g, acc.teams);
            const mine = !viewing && (!!acc.can_view_as || (!!me && g.granted_by === me));
            return (
              <div key={g.id} className="row prow" data-target={`grant:${g.id}`}>
                <span className="row-title">{label}</span>
                <span className="row-sub">{[g.status && g.status !== 'active' ? 'Paused' : '', g.expires_at ? `Until ${g.expires_at.slice(0, 10)}` : ''].filter(Boolean).join(', ')}</span>
                {mine && <IconButton icon="close" label={`Take back ${label}`} size="sm" quiet onClick={() => void press('finance.takeAccess', { grant: g.id })} />}
              </div>
            );
          })}
        </Section>
      </div>
    </SidePanel>
  );
}

/** Every name the directory answers to, by any id form: it changes on a rename, not on an agent's status line. */
const namesOf = (d: Directory) => Object.fromEntries(Object.entries(d.byId).map(([id, a]) => [id, a.name]));

/** A view of Finance for a teammate: what, which line, whose, until when (finance-admin.ts:643-671). The owner may give
    any view; a manager their teams' expenses and expense lines. */
function GiveDialog({ acc, teammate, onOpenChange }: { acc: AccessList; teammate: string; onOpenChange: (open: boolean) => void }) {
  const { busy, error, setError, go } = useAsk(onOpenChange);
  const me = useQuery(meQuery).data?.id;
  const names = useDirectory(namesOf);
  const owner = !!acc.can_view_as;
  const [who, setWho] = useState<string | null>(teammate || null);
  const [what, setWhat] = useState('expenses');
  const [line, setLine] = useState<string | null>(null);
  const [whose, setWhose] = useState('own');
  const [until, setUntil] = useState<Date | null>(null);
  const ym = budgetDay().slice(0, 7);
  const pnl = useQuery({ ...pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }), enabled: what === 'pnl_lines' });
  const sheet = useQuery({ ...balanceQuery(), enabled: what === 'bs_lines' });
  const people = acc.teammates
    .filter((t) => t.may_grant && t.teammate !== me)
    .map((t) => ({ value: t.teammate, label: names?.[t.teammate] ?? t.teammate.slice(t.teammate.indexOf(':') + 1) }));
  const whats = Object.entries(WHAT)
    .filter(([v]) => owner || MANAGER_WHAT.includes(v))
    .map(([value, label]) => ({ value, label }));
  const lines =
    what === 'pnl_lines'
      ? (pnl.data?.rows ?? []).filter((r) => !r.private && (owner || r.type === 'expense')).map((r) => ({ value: r.account_id, label: r.name }))
      : [...(sheet.data?.assets ?? []), ...(sheet.data?.liabilities ?? []), ...(sheet.data?.equity ?? [])]
          .filter((r): r is typeof r & { account_id: string } => !!r.account_id)
          .map((r) => ({ value: r.account_id, label: r.name }));
  // The teams a manager leads, and those under them.
  const led = new Set((acc.teams ?? []).filter((t) => t.lead === me).map((t) => t.id));
  for (let grew = true; grew; ) {
    grew = false;
    for (const t of acc.teams ?? []) if (t.parent && led.has(t.parent) && !led.has(t.id)) grew = !!led.add(t.id);
  }
  const wholes = [
    { value: 'own', label: 'Their own' },
    ...(acc.teams ?? []).filter((t) => owner || led.has(t.id)).map((t) => ({ value: `team:${t.id}`, label: t.name })),
    ...(owner ? [{ value: 'company', label: 'Company' }] : []),
  ];
  const submit = () => {
    if (!who) return setError('Choose a teammate');
    if (LINES.has(what) && !line) return setError('Choose a line');
    const scoped = SCOPED.has(what);
    void go('finance.giveAccess', {
      teammate: who,
      what,
      ...(scoped ? { whose: whose.startsWith('team:') ? 'team' : whose } : {}),
      ...(scoped && whose.startsWith('team:') ? { team: whose.slice(5) } : {}),
      ...(LINES.has(what) && line ? { line } : {}),
      ...(until ? { until: dayOf(until) } : {}),
    });
  };
  return (
    <Ask title="Give access" submit="Give access" onOpenChange={onOpenChange} onSubmit={submit} busy={busy} error={error}>
      <Select label="Teammate" options={people} value={who} placeholder="Choose" onValueChange={setWho} />
      <Select
        label="What"
        options={whats}
        value={what}
        onValueChange={(v) => {
          setWhat(v);
          setLine(null);
        }}
      />
      {LINES.has(what) && <Select label="Line" options={lines} value={line} placeholder="Choose" onValueChange={setLine} />}
      {SCOPED.has(what) && <Select label="Whose" options={wholes} value={whose} onValueChange={setWhose} />}
      <DatePick label="Until" value={until} onValueChange={setUntil} withTime={false} placeholder="No end" />
    </Ask>
  );
}

const OWNER = 'owner';

/** The one person finance-admin approvals go to: the owner's to name, the owner by default (finance-admin.ts:551). */
function ApproverDialog({ acc, onOpenChange }: { acc: AccessList; onOpenChange: (open: boolean) => void }) {
  const { busy, error, go } = useAsk(onOpenChange);
  const names = useDirectory(namesOf);
  const people = acc.teammates.filter((t) => t.kind !== 'bot');
  const owner = people.find((t) => t.default === 'everything');
  const label = (id: string) => names?.[id] ?? id.slice(id.indexOf(':') + 1);
  const options = [
    { value: OWNER, label: owner ? label(owner.teammate) : 'Owner' },
    ...people.filter((t) => t !== owner).map((t) => ({ value: t.teammate, label: label(t.teammate) })),
  ];
  const [pick, setPick] = useState(acc.finance_approver || OWNER);
  return (
    <Ask
      title="Finance approver"
      submit="Save"
      onOpenChange={onOpenChange}
      onSubmit={() => void go('finance.setApprover', pick === OWNER ? {} : { approver: pick })}
      busy={busy}
      error={error}
    >
      <Select label="Finance approver" options={options} value={pick} onValueChange={setPick} />
    </Ask>
  );
}
