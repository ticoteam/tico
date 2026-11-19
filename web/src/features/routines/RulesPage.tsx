import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { MenuItem } from '@/kit/Menu';
import { pipelinesQuery, type Pipeline } from '@/features/tasks/routing';
import { List, PageMore, Section, type PageTab } from '@/pagekinds';
import { hasWho, ROUTINES, RULES, type RuleTab } from './actions';
import { CompanyRules, holdsOf, OrgRuleRow, partiesOf, PartyPick } from './CompanyRules';
import { Find, press, RemoveItem, Rows, RuleRow, useConfirmRemove, useName } from './parts';
import { ReadDocument } from './ReadDocument';
import { dueQuery, holdsQuery, orgRulesQuery, routinesQuery, ruleGroupsQuery, stageRulesQuery, teamsQuery } from './queries';
import { RulePanel } from './RulePanel';
import { runnerOf } from './RoutinesPage';
import type { DueDate, Group, OrgRule, Routine, StageRule } from './types';
import { agentWords, dayWords, stageParts, triggerWords } from './words';
import './routines.css';

/* Rules (pages/3-company/rules.js): one List, eight tabs on `?tab=`. Every rule is one dense row with no title; a row
   opens its rule in `?panel=` (`org:<id>`, `stage:<id>`, a due date's `due:<id>`), where it is read, changed or
   removed. Wakes and routines on tasks are routines: their rows open the routine on Routines. The head's "⋯" adds a
   due date or a document to read for outside rules. */

export interface RulesSearch {
  tab?: Exclude<RuleTab, ''>;
  panel?: string;
  /** Company: whose rules, a party (`group:<id>`, `bot:<id>`, `human:<id>`); none is Everyone. */
  who?: string;
  /** Any tab: the words searched for. */
  q?: string;
}

/** Every word searched for is in the text, in any order. */
export const hasWords = (text: string, q: string | undefined) => !q || q.toLowerCase().split(/\s+/).every((w) => text.toLowerCase().includes(w));

const tab = (label: string, t: RuleTab): PageTab => ({ label, to: RULES, search: { tab: t }, target: `tab:${t || 'company'}` });
export const rulesTabs: readonly PageTab[] = [
  tab('Company', ''),
  tab('Pipelines and stages', 'stages'),
  tab('Built in', 'built-in'),
  tab('Outside', 'outside'),
  tab('Due', 'due'),
  tab('Wakes', 'wakes'),
  tab('Routines on tasks', 'on-tasks'),
  tab('Suggested', 'suggested'),
];

const close = () => void press('rules.closePanel', {});

type State = 'loading' | 'fault' | 'empty' | 'ready';
const stateOf = (q: { isPending: boolean; isError: boolean }, rows: number): State => (q.isPending ? 'loading' : q.isError ? 'fault' : rows ? 'ready' : 'empty');

/** The day n days from now, YYYY-MM-DD. */
const dayIn = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);

/** Sections in first-seen order. */
function grouped<T>(list: T[], key: (x: T) => string, first?: string): [string, T[]][] {
  const m = new Map<string, T[]>();
  if (first) m.set(first, []);
  for (const x of list) {
    const k = key(x);
    m.set(k, [...(m.get(k) ?? []), x]);
  }
  return [...m].filter(([, xs]) => xs.length);
}

type Confirm = (what: string, go: () => void) => void;
interface TabProps {
  search: RulesSearch;
  confirm: Confirm;
  /** The page's "Remove …?" dialog. */
  asked: ReactNode;
}

/** Each tab is its own small page reading only its own data. */
const TABS: Record<RuleTab, (p: TabProps) => ReactNode> = {
  '': CompanyTab,
  stages: StagesTab,
  'built-in': BuiltInTab,
  outside: OutsideTab,
  due: DueTab,
  wakes: AutoTab,
  'on-tasks': AutoTab,
  suggested: SuggestedTab,
};

export function RulesPage({ search }: { search: RulesSearch }) {
  const remove = useConfirmRemove();
  const Tab = TABS[search.tab ?? ''];
  return <Tab search={search} confirm={remove.confirm} asked={remove.node} />;
}

/** The page around one tab's rows: the head, its states and the open rule. */
function Page({ search, asked, state, retry, empty, dialogs, children }: Omit<TabProps, 'confirm'> & { state: State; retry: () => void; empty: string; dialogs?: ReactNode; children: ReactNode }) {
  const t: RuleTab = search.tab ?? '';
  const [reading, setReading] = useState(false);
  const kind = t === 'stages' ? 'stage' : t === 'outside' ? 'outside' : 'org';
  const add = (
    <Button variant="primary" onClick={() => void press('rules.new', { kind })}>
      Add rule
    </Button>
  );
  return (
    <List
      title="Rules"
      path={RULES}
      tabs={rulesTabs}
      className="rr"
      search={<Find label="Search rules" value={search.q} onFind={(words) => void press('rules.list', { tab: t, q: words })} />}
      actions={
        <>
          {add}
          {asked}
          {dialogs}
          {reading && <ReadDocument onClose={() => setReading(false)} />}
        </>
      }
      more={
        <PageMore>
          <MenuItem label="Add due date" icon="calendar" onSelect={() => void press('rules.newDue', {})} />
          <MenuItem label="Add document" icon="file" onSelect={() => setReading(true)} />
        </PageMore>
      }
      state={state}
      onRetry={retry}
      empty={{ title: empty, action: t === 'suggested' || t === 'built-in' ? undefined : add }}
      sections={children}
      panel={search.panel && <RulePanel key={search.panel} panel={search.panel} tab={t} onClose={close} />}
    />
  );
}

/** A team's name by its id. */
function useTeamName() {
  const teams = useQuery(teamsQuery).data ?? [];
  return (id: string) => teams.find((g) => g.id === id)?.name;
}

function CompanyTab({ search, confirm, asked }: TabProps) {
  const org = useQuery(orgRulesQuery('active'));
  return (
    <Page search={search} asked={asked} state={org.isPending ? 'loading' : org.isError ? 'fault' : 'ready'} retry={() => void org.refetch()} empty="No rules yet">
      <CompanyRules who={search.who ?? '*'} panel={search.panel} confirm={confirm} q={search.q} />
    </Page>
  );
}

function StagesTab({ search, confirm, asked }: TabProps) {
  const stage = useQuery(stageRulesQuery('active'));
  const team = useTeamName();
  const items = (stage.data?.items ?? []).filter((r) => hasWords([r.name, r.pipeline_name, ...stageParts(r, team), r.jev, r.reason].join(' '), search.q));
  const every = 'Every pipeline';
  return (
    <Page search={search} asked={asked} state={stateOf(stage, items.length)} retry={() => void stage.refetch()} empty="No stage rules yet">
      {grouped(items, (r) => (r.scope === 'company' ? every : (r.pipeline_name ?? 'A pipeline')), every).map(([g, rs]) => (
        <Section key={g} name={g} count={rs.length} target={`section:${g}`}>
          <Rows
            label={g}
            items={rs}
            getKey={(r) => r.id}
            draw={(r) => <StageRow key={r.id} r={r} team={team} selected={search.panel === `stage:${r.id}`} search={{ tab: 'stages', panel: `stage:${r.id}` }} confirm={confirm} />}
          />
        </Section>
      ))}
    </Page>
  );
}

function BuiltInTab({ search, asked }: TabProps) {
  const groups = useQuery(ruleGroupsQuery);
  const list = (groups.data?.built_in ?? []).filter((b) => hasWords(`${b.name} ${b.what ?? ''}`, search.q));
  return (
    <Page search={search} asked={asked} state={stateOf(groups, list.length)} retry={() => void groups.refetch()} empty="No built-in rules">
      <Section name="Always on" count={list.length} target="section:built-in">
        {list.map((b) => (
          <RuleRow key={b.name} how="fixed" parts={b.what ? [agentWords(b.name), agentWords(b.what)] : [agentWords(b.name)]} fixed target={`builtin:${b.name}`} />
        ))}
      </Section>
    </Page>
  );
}

function OutsideTab({ search, confirm, asked }: TabProps) {
  const org = useQuery(orgRulesQuery('active'));
  const due = useQuery(dueQuery).data;
  const holds = useQuery(holdsQuery).data;
  const list = (org.data ?? []).filter((r) => r.kind === 'external' && hasWords(`${r.text} ${r.description ?? ''} ${r.source?.name ?? ''}`, search.q));
  const dueOf = (id: string) => due?.find((d) => d.rule.id === id);
  return (
    <Page search={search} asked={asked} state={stateOf(org, list.length)} retry={() => void org.refetch()} empty="No outside rules yet">
      {grouped(list, (r) => r.source?.name ?? 'Other').map(([g, rs]) => (
        <Section key={g} name={g} count={rs.length}>
          {rs.map((r) => (
            <OrgRuleRow
              key={r.id}
              r={r}
              search={{ tab: 'outside', panel: `org:${r.id}` }}
              selected={search.panel === `org:${r.id}`}
              holds={holdsOf(holds, r.id)}
              onRemove={() => confirm('this rule', () => void press('rules.remove', { rule: r.id, kind: 'org' }))}
              cols={{ src: true, who: true, at: true }}
              src={r.source?.name}
              who={dueOf(r.id)?.owner ?? firstMember(r)}
              at={dayWords(dueOf(r.id)?.due_on)}
            />
          ))}
        </Section>
      ))}
    </Page>
  );
}

function DueTab({ search, asked }: TabProps) {
  const due = useQuery(dueQuery);
  const list = (due.data ?? []).filter((d) => hasWords(d.rule.text, search.q));
  const soon = dayIn(30);
  const when = (d: DueDate) => (d.status === 'overdue' ? 'Late' : d.due_on <= soon ? 'Next 30 days' : 'Later');
  return (
    <Page search={search} asked={asked} state={stateOf(due, list.length)} retry={() => void due.refetch()} empty="No due dates">
      {grouped([...list].sort((a, b) => a.due_on.localeCompare(b.due_on)), when, 'Late').map(([g, ds]) => (
        <Section key={g} name={g} count={ds.length}>
          {ds.map((d) => (
            <DueRow key={d.id} d={d} selected={search.panel === `due:${d.id}`} />
          ))}
        </Section>
      ))}
    </Page>
  );
}

/** A due date: what is due, how early its task opens, and who gets that task. */
function DueRow({ d, selected }: { d: DueDate; selected: boolean }) {
  const owner = useName(d.owner);
  return (
    <RuleRow
      how="date"
      parts={[agentWords(d.rule.text), ...(d.task_days ? [`${d.task_days} days before`, ...(owner ? [`${owner} gets a task`] : [])] : [])]}
      to={RULES}
      search={{ tab: 'due', panel: `due:${d.id}` }}
      selected={selected}
      word={d.status === 'overdue' ? 'Late' : undefined}
      cols={{ who: true, at: true }}
      who={d.owner}
      at={dayWords(d.due_on)}
      target={`due:${d.id}`}
    />
  );
}

/** Wakes, and routines on tasks: both are routines, read from the rule groups; each row opens on Routines. */
function AutoTab({ search, asked }: TabProps) {
  const wake = search.tab === 'wakes';
  const groups = useQuery(ruleGroupsQuery);
  const routines = useQuery(routinesQuery).data;
  const teams = useQuery(teamsQuery).data ?? [];
  const pipes = useQuery(pipelinesQuery).data ?? [];
  const byId = new Map((routines ?? []).map((r) => [r.id, r]));
  const words = (w: { id: string; name: string }) => {
    const r = byId.get(w.id);
    return `${w.name} ${r ? triggerWords(r.trigger) : ''}`;
  };
  const list = ((wake ? groups.data?.wakes : groups.data?.automations) ?? []).filter((w) => hasWords(words(w), search.q));
  const sectionOf = (r: Routine | undefined) => (wake ? wakeSection(r, teams, pipes) : pipelineSection(r, pipes));
  return (
    <Page search={search} asked={asked} state={stateOf(groups, list.length)} retry={() => void groups.refetch()} empty={wake ? 'No wakes yet' : 'No routines on tasks yet'}>
      {grouped(list, (w) => sectionOf(byId.get(w.id))).map(([g, ws]) => (
        <Section key={g} name={g} count={ws.length}>
          {ws.map((w) => (
            <AutoRuleRow key={w.id} id={w.id} name={w.name} on={w.enabled} r={byId.get(w.id)} wake={wake} />
          ))}
        </Section>
      ))}
    </Page>
  );
}

function SuggestedTab({ search, asked }: TabProps) {
  const orgSugg = useQuery(orgRulesQuery('suggested'));
  const stageSugg = useQuery(stageRulesQuery('suggested'));
  // a suggested change or retire names the rule in force it is about
  const live = useQuery(stageRulesQuery('active')).data?.items ?? [];
  const team = useTeamName();
  const [whoFor, setWhoFor] = useState<OrgRule | null>(null);
  const opened = (id: string) => search.panel === `stage:${id}`;
  const orgs = (orgSugg.data ?? []).filter((r) => hasWords(`${r.text} ${r.description ?? ''}`, search.q));
  const stages = (stageSugg.data?.items ?? []).filter((r) => hasWords([r.name, ...stageParts(r, team)].join(' '), search.q));
  const n = orgs.length + stages.length;
  const both = { isPending: orgSugg.isPending && stageSugg.isPending, isError: orgSugg.isError && stageSugg.isError };
  const cols = { who: true, at: true };
  return (
    <Page
      search={search}
      asked={asked}
      state={stateOf(both, n)}
      retry={() => void Promise.all([orgSugg.refetch(), stageSugg.refetch()])}
      empty="No suggestions"
      dialogs={whoFor && <AcceptWho rule={whoFor} onClose={() => setWhoFor(null)} />}
    >
      <Section name="Suggested" count={n} target="section:suggested">
        {orgs.map((r) => (
          <RuleRow
            key={r.id}
            how={r.if_broken === 'escalate' && r.reviewer ? 'person' : 'model'}
            by={r.reviewer ? `human:${r.reviewer.id}` : null}
            parts={[agentWords(r.text)]}
            cols={cols}
            who={r.suggested_by}
            at={r.created_at ? <Ago at={r.created_at} /> : ''}
            verb={{ label: 'Add', onClick: () => (hasWho(r) ? void press('rules.accept', { rule: r.id, kind: 'org' }) : setWhoFor(r)) }}
            more={<MenuItem label="Set aside" icon="archive" onSelect={() => void press('rules.skip', { rule: r.id, kind: 'org' })} />}
            target={`rule:${r.id}`}
          />
        ))}
        {stages.map((r) => {
          const was = r.suggests ? live.find((x) => x.id === r.suggests) : undefined;
          const retires = !!r.suggests && !!r.then_params?.retire;
          const shown = retires && was ? was : r;
          return (
            <RuleRow
              key={r.id}
              how={shown.jev ? 'model' : 'fixed'}
              parts={retires ? [`Retire: ${stageParts(shown, team)[0] ?? ''}`, ...stageParts(shown, team).slice(1)] : stageParts(r, team)}
              cost={!!shown.jev}
              cols={cols}
              who={r.created_by}
              at={r.updated_at ? <Ago at={r.updated_at} /> : ''}
              to={RULES}
              search={{ tab: 'suggested', panel: `stage:${r.id}` }}
              selected={opened(r.id)}
              verb={{ label: retires ? 'Retire' : r.suggests ? 'Accept change' : 'Add', onClick: () => void press('rules.accept', { rule: r.id, kind: 'stage' }) }}
              more={<MenuItem label="Set aside" icon="archive" onSelect={() => void press('rules.skip', { rule: r.id, kind: 'stage' })} />}
              target={`rule:${r.id}`}
            />
          );
        })}
      </Section>
    </Page>
  );
}

const same = (a: string | null | undefined, b: string) => !!a && a.toLowerCase() === b.toLowerCase();

/** A wake's section: the team it wakes, a pipeline's name, or Every agent (pages/3-company/rules.js WAKES). */
function wakeSection(r: Routine | undefined, teams: Group[], pipes: Pipeline[]): string {
  const w = r?.wakes;
  const id = w?.scope_id ?? '';
  const teamOfBot = (bot: string | null | undefined) => {
    const key = bot?.replace(/^bot:/, '');
    return key ? teams.find((g) => g.bots.includes(key))?.name : undefined;
  };
  switch (w?.scope_kind) {
    case 'bot':
      return teamOfBot(id) ?? 'Every agent';
    case 'department':
      return teams.find((g) => g.id === id || same(g.name, id))?.name ?? (id || 'Every agent');
    case 'pipeline':
      return pipes.find((p) => p.id === id || same(p.name, id))?.name ?? 'Every agent';
    case 'team':
      return 'Every agent';
    default:
      return teamOfBot(r && runnerOf(r)) ?? 'Every agent';
  }
}

/** A routine on tasks' section: the one pipeline its filters name (`task.pipeline`, or a `task.stage` only one pipeline
   has), else Every pipeline (pages/3-company/rules.js ONTASKS). The routine carries no pipeline of its own. */
function pipelineSection(r: Routine | undefined, pipes: Pipeline[]): string {
  const named = new Set<string>();
  for (const step of r?.steps ?? []) {
    if (step.type !== 'filter' || !Array.isArray(step.rules)) continue;
    for (const rule of step.rules as { when?: { field?: string; value?: unknown }[] }[]) {
      for (const c of rule.when ?? []) {
        const v = typeof c.value === 'string' ? c.value : '';
        if (c.field === 'task.pipeline') {
          const p = pipes.find((x) => x.id === v || same(x.name, v));
          if (p) named.add(p.name);
        } else if (c.field === 'task.stage') {
          const ps = pipes.filter((x) => x.stages.some((st) => st.id === v || same(st.name, v)));
          if (ps.length === 1 && ps[0]) named.add(ps[0].name);
        }
      }
    }
  }
  const [only] = named;
  return named.size === 1 && only ? only : 'Every pipeline';
}

/** An outside rule's owner when nothing is due: the one agent or person it names. */
const firstMember = (r: OrgRule): string | null => {
  const p = partiesOf(r.who)[0];
  return p && (p.startsWith('bot:') || p.startsWith('human:')) ? p : null;
};

function StageRow({
  r,
  team,
  selected,
  search,
  confirm,
}: {
  r: StageRule;
  team: (id: string) => string | undefined;
  selected: boolean;
  search: Record<string, string>;
  confirm: Confirm;
}) {
  return (
    <RuleRow
      how={r.jev ? 'model' : 'fixed'}
      parts={stageParts(r, team)}
      cost={!!r.jev}
      off={!!r.not_acting}
      to={RULES}
      search={search}
      selected={selected}
      target={`rule:${r.id}`}
      more={r.can_edit === false ? undefined : <RemoveItem onSelect={() => confirm(r.name || 'this rule', () => void press('rules.remove', { rule: r.id, kind: 'stage' }))} />}
    />
  );
}

/** A wake or a routine on tasks: what starts it, then the agent it wakes or the routine it runs. Opens on Routines. */
function AutoRuleRow({ id, name, on, r, wake }: { id: string; name: string; on: boolean; r: Routine | undefined; wake: boolean }) {
  const runner = r ? runnerOf(r) : null;
  const who = useName(runner);
  const when = r ? triggerWords(r.trigger) : '';
  const then = wake ? `Wake ${who || 'its agent'}` : `Run ${agentWords(name)}`;
  const at = r?.last_run?.started_at ?? r?.last_run?.finished_at;
  return (
    <RuleRow
      how={wake ? 'wake' : 'auto'}
      parts={when ? [when, then] : [agentWords(name), then]}
      to={ROUTINES}
      search={{ panel: `routine:${id}` }}
      off={!on}
      word={r?.last_run?.status === 'failed' ? 'Failed' : undefined}
      cols={{ who: true, at: true }}
      who={runner}
      at={at ? <Ago at={at} /> : ''}
      target={`routine:${id}`}
    />
  );
}

/** A suggestion that names no one is added once someone is picked to follow it (the server takes none without). */
function AcceptWho({ rule, onClose }: { rule: OrgRule; onClose: () => void }) {
  const [who, setWho] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    const ok = await press('rules.accept', { rule: rule.id, kind: 'org', who });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Who follows it?"
      description={agentWords(rule.text)}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!who.length} onClick={() => void add()}>
            Add
          </Button>
        </>
      }
    >
      <div className="rr-form">
        <PartyPick label="Who" value={who} onChange={setWho} />
      </div>
    </Dialog>
  );
}
