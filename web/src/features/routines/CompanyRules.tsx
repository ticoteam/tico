import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Fragment, useState, type ReactNode } from 'react';
import { useActors, useDirectory, type Directory } from '@/data';
import { Icon } from '@/kit/Icon';
import { Row } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { Section } from '@/pagekinds';
import { RULES } from './actions';
import { actorKey, Face, press, RemoveItem, RuleRow, type How } from './parts';
import { holdsQuery, orgRulesQuery, partiesOf, reachQuery, teamsQuery } from './queries';
import type { Group, OrgRule, Reach, ReachBook, ReachMode, RuleHold } from './types';
import { orgParts } from './words';

/* Rules › Company (rules.js rulesBody): who on the left (Everyone, then each team and its members), what applies to
   them on the right: their reach and daily limits, their rules, and what they take from their team and from Everyone. */

const MODES: { value: ReachMode; label: string }[] = [
  { value: 'open', label: 'Anyone' },
  { value: 'nearby', label: 'Nearby' },
  { value: 'closed', label: 'Listed' },
];
const BOOKS: { value: ReachBook; label: string }[] = [
  { value: 'anyone', label: 'Anyone' },
  { value: 'nearby', label: 'Nearby' },
  { value: 'none', label: 'No one' },
];
const FIELDS = { mode: 'Reach', book: 'Agents may book', chats_per_day: 'Chats a day', tasks_per_day: 'Tasks a day' } as const;

export { partiesOf };

const members = (g: Group) => [...g.bots.map((b) => `bot:${b}`), ...g.people.map((p) => `human:${p}`)];

/** Every name the directory answers to, by any id form: it changes on a rename, not on an agent's status line. */
const namesOf = (d: Directory) => Object.fromEntries(Object.entries(d.byId).map(([id, a]) => [id, a.name]));

/** A party's name: Everyone, a team, or whoever the directory knows by that id or slug. */
export function useNamer() {
  const names = useDirectory(namesOf);
  const teams = useQuery(teamsQuery).data ?? [];
  return (party: string): string => {
    if (party === '*') return 'Everyone';
    if (party.startsWith('group:')) return teams.find((g) => g.id === party.slice(6))?.name ?? 'A team';
    // Unknown only once the agent or person is gone from the company.
    return names?.[actorKey(party) ?? party] ?? (party.startsWith('bot:') ? 'An agent' : 'A person');
  };
}

/** Several parties picked: each a chip with its ×, then a picker for one more. Everyone stands alone: picking it
   clears the rest, picking anyone else clears it. */
export function PartyPick({ label, value, onChange, disabled }: { label: string; value: readonly string[]; onChange: (next: string[]) => void; disabled?: boolean }) {
  const name = useNamer();
  const teams = useQuery(teamsQuery).data ?? [];
  const actors = useActors();
  const all = [{ value: '*', label: 'Everyone' }, ...teams.map((g) => ({ value: `group:${g.id}`, label: g.name })), ...actors.map((a) => ({ value: a.id, label: a.name }))];
  const add = (p: string) => onChange(p === '*' ? ['*'] : [...value.filter((x) => x !== '*' && x !== p), p]);
  return (
    <div className="f" role="group" aria-label={label}>
      <span className="f-label">{label}</span>
      {value.length > 0 && (
        <div className="rr-chips">
          {value.map((p) => (
            <span key={p} className="scope">
              {name(p)}
              {!disabled && (
                <button type="button" aria-label={`Remove ${name(p)}`} onClick={() => onChange(value.filter((x) => x !== p))}>
                  <Icon name="close" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {!disabled && <Select label={label} labelHidden options={all.filter((o) => !value.includes(o.value))} value={null} onValueChange={add} placeholder={value.length ? 'Add' : 'Pick'} />}
    </div>
  );
}

/** How many tasks the rule holds now. */
export const holdsOf = (holds: RuleHold[] | undefined, id: string) => (holds ?? []).filter((h) => h.state === 'held' && h.rules.some((r) => r.id === id)).length;

/** A company rule's row: who decides when it is broken, its words, its holds. */
export function OrgRuleRow({
  r,
  search,
  selected,
  holds,
  onRemove,
  cols,
  who,
  at,
  src,
}: {
  r: OrgRule;
  search: Record<string, string>;
  selected: boolean;
  holds: number;
  onRemove: () => void;
  cols?: { src?: boolean; who?: boolean; at?: boolean };
  who?: string | null;
  at?: ReactNode;
  src?: string;
}) {
  const how: How = r.if_broken === 'escalate' && r.reviewer ? 'person' : 'model';
  return (
    <RuleRow
      how={how}
      by={r.reviewer ? `human:${r.reviewer.id}` : null}
      parts={orgParts(r)}
      to={RULES}
      search={search}
      selected={selected}
      cost={how === 'model'}
      holds={holds}
      cols={cols}
      who={who}
      at={at}
      src={src}
      target={`rule:${r.id}`}
      more={r.can_edit ? <RemoveItem onSelect={onRemove} /> : undefined}
    />
  );
}

export function CompanyRules({ who, panel, confirm, q }: { who: string; panel?: string; confirm: (what: string, go: () => void) => void; q?: string }) {
  const words = q?.toLowerCase().split(/\s+/) ?? [];
  const rules = useSuspenseQuery(orgRulesQuery('active')).data.filter((r) => r.kind !== 'external' && words.every((w) => `${r.text} ${r.description ?? ''}`.toLowerCase().includes(w)));
  const teams = useQuery(teamsQuery).data ?? [];
  const reach = useQuery(reachQuery).data;
  const holds = useQuery(holdsQuery).data;
  const name = useNamer();
  const forParty = (p: string) => rules.filter((r) => partiesOf(r.who).includes(p));
  const myTeams = who.startsWith('group:') || who === '*' ? [] : teams.filter((g) => members(g).includes(who));
  const open = panel?.startsWith('org:') ? panel.slice(4) : null;
  const searchFor = (r: OrgRule) => ({ ...(who !== '*' ? { who } : {}), panel: `org:${r.id}` });
  const rows = (list: OrgRule[]) =>
    list.map((r) => (
      <OrgRuleRow
        key={r.id}
        r={r}
        search={searchFor(r)}
        selected={r.id === open}
        holds={holdsOf(holds, r.id)}
        onRemove={() => confirm('this rule', () => void press('rules.remove', { rule: r.id, kind: 'org' }))}
      />
    ));
  const own = forParty(who);
  const everyone = who === '*' ? [] : forParty('*');
  const exceptions = who === '*' && reach ? reach.settings.filter((s) => s.party !== '*') : [];
  const choices = [{ value: '*', label: 'Everyone' }, ...teams.flatMap((g) => [{ value: `group:${g.id}`, label: g.name }, ...members(g).map((m) => ({ value: m, label: name(m) }))])];
  if (!choices.some((c) => c.value === who)) choices.push({ value: who, label: name(who) });

  return (
    <div className="split">
      <div className="scope-col" role="navigation" aria-label="Whose rules">
        <ScopeRow party="*" name="Everyone" n={forParty('*').length} on={who === '*'} lead={<Icon name="company" />} />
        {teams.map((g) => {
          const party = `group:${g.id}`;
          const opened = who === party || members(g).includes(who);
          return (
            <Fragment key={g.id}>
              <ScopeRow party={party} name={g.name} n={forParty(party).length} on={who === party} chev={opened ? 'open' : 'shut'} />
              {opened && members(g).map((m) => <ScopeRow key={m} party={m} name={name(m)} n={forParty(m).length} on={who === m} kid />)}
            </Fragment>
          );
        })}
      </div>
      <div className="scope-main">
        <div className="scope-select">
          <Select label="Whose rules" labelHidden options={choices} value={who} onValueChange={(v) => void press('rules.pickScope', { who: v })} />
        </div>
        {reach && <Limits party={who} reach={reach} name={name} />}
        <Section name="Rules" count={own.length} target={`section:rules:${who}`}>
          {rows(own)}
        </Section>
        {myTeams.map((g) => {
          const list = forParty(`group:${g.id}`);
          return list.length ? (
            <Section key={g.id} name={`From ${g.name}`} count={list.length}>
              {rows(list)}
            </Section>
          ) : null;
        })}
        {everyone.length > 0 && (
          <Section name="From Everyone" count={everyone.length}>
            {rows(everyone)}
          </Section>
        )}
        {exceptions.length > 0 && (
          <Section name="Exceptions" count={exceptions.length} target="section:exceptions">
            {exceptions.flatMap((s) =>
              (Object.keys(FIELDS) as (keyof typeof FIELDS)[])
                .filter((f) => s[f] != null)
                .map((f) => (
                  <RuleRow
                    key={`${s.party}:${f}`}
                    how="set"
                    parts={[name(s.party), FIELDS[f], valueWords(f, s[f])]}
                    to={RULES}
                    search={{ who: s.party }}
                    cols={{ who: true }}
                    who={s.party.startsWith('group:') ? null : s.party}
                    target={`reach:${s.party}:${f}`}
                  />
                )),
            )}
          </Section>
        )}
      </div>
    </div>
  );
}

function valueWords(f: keyof typeof FIELDS, v: unknown): string {
  if (f === 'mode') return MODES.find((m) => m.value === v)?.label ?? String(v);
  if (f === 'book') return BOOKS.find((b) => b.value === v)?.label ?? String(v);
  return String(v);
}

function ScopeRow({ party, name, n, on, lead, chev, kid }: { party: string; name: string; n: number; on: boolean; lead?: ReactNode; chev?: 'open' | 'shut'; kid?: boolean }) {
  const href = party === '*' ? RULES : `${RULES}?${new URLSearchParams({ who: party }).toString()}`;
  return (
    <Row className={kid ? 'scope-row is-kid' : 'scope-row'} title={name} href={href} selected={on} target={`scope:${party}`}>
      {kid ? (
        <Face id={party} />
      ) : (
        <span className={chev ? `scope-ico is-chev${chev === 'open' ? ' is-open' : ''}` : 'scope-ico'}>{chev ? <Icon name="chevron" /> : lead}</span>
      )}
      <span className="row-title">{name}</span>
      {n ? <span className="count">{n}</span> : <span />}
    </Row>
  );
}

/** Reach and the daily limits for one party; each says where it comes from when it is not set here. */
function Limits({ party, reach, name }: { party: string; reach: Reach; name: (p: string) => string }) {
  const e = reach.effective[party] ?? {};
  const d = reach.defaults;
  const can = reach.may_edit.includes(party);
  const from = (f: keyof typeof FIELDS) => {
    const p = e.from?.[f];
    return p && p !== party ? `From ${name(p)}` : '';
  };
  const set = (patch: Record<string, unknown>) => void press('rules.setReach', { who: party, ...patch });
  return (
    <section className="sets" data-target={`reach:${party}`} aria-label="Reach and limits">
      <div className="set">
        <span className="set-name">{FIELDS.mode}</span>
        <Segmented label={FIELDS.mode} options={MODES} value={e.mode ?? d.mode ?? 'nearby'} onValueChange={(mode) => set({ mode })} disabled={!can} />
        <span className="set-from">{from('mode')}</span>
      </div>
      <div className="set">
        <span className="set-name">{FIELDS.book}</span>
        <Segmented label={FIELDS.book} options={BOOKS} value={e.book ?? d.book ?? 'nearby'} onValueChange={(book) => set({ book })} disabled={!can} />
        <span className="set-from">{from('book')}</span>
      </div>
      <div className="set">
        <span className="set-name">{FIELDS.chats_per_day}</span>
        <Num key={`c:${party}`} label={FIELDS.chats_per_day} value={e.chats_per_day ?? d.chats_per_day} disabled={!can} onSave={(n) => set({ chatsPerDay: n })} />
        <span className="set-from">{from('chats_per_day')}</span>
      </div>
      <div className="set">
        <span className="set-name">{FIELDS.tasks_per_day}</span>
        <Num key={`t:${party}`} label={FIELDS.tasks_per_day} value={e.tasks_per_day ?? d.tasks_per_day} disabled={!can} onSave={(n) => set({ tasksPerDay: n })} />
        <span className="set-from">{from('tasks_per_day')}</span>
      </div>
    </section>
  );
}

/** A number saved when the field is left or Enter is pressed; empty goes back to what it takes from above. */
function Num({ label, value, disabled, onSave }: { label: string; value: number | null | undefined; disabled: boolean; onSave: (n: number | null) => void }) {
  const shown = value == null ? '' : String(value);
  const [text, setText] = useState(shown);
  const [was, setWas] = useState(shown);
  if (was !== shown) {
    setWas(shown);
    setText(shown);
  }
  const commit = () => {
    const t = text.trim();
    const n = t === '' ? null : Number(t);
    if (n !== null && (!Number.isInteger(n) || n < 0)) return setText(shown);
    if (n !== (value ?? null)) onSave(n);
  };
  return (
    <span className="field is-num">
      <input
        inputMode="numeric"
        aria-label={label}
        value={text}
        disabled={disabled}
        onChange={(ev) => setText(ev.target.value)}
        onBlur={commit}
        onKeyDown={(ev) => {
          if (ev.key === 'Enter') ev.currentTarget.blur();
        }}
      />
    </span>
  );
}
