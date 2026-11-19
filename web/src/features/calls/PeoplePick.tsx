import { useState, type ReactNode } from 'react';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { Dot } from '@/tico/dot';
import { useMe } from './parts';

export interface PeoplePickProps {
  /** Actor ids picked so far. */
  value: readonly string[];
  onChange: (next: string[]) => void;
  /** Starting a call: "Just Tico" heads the list and is on while nobody is picked. */
  tico?: boolean;
  /** The list shows only once something is typed (a form's guests). */
  quiet?: boolean;
  autoFocus?: boolean;
}

interface Team {
  name: string;
  ids: string[];
}

/** Who to call or ask: chips for who is picked, then people, agents and teams to pick (cal-merge.js call picker). */
export function PeoplePick({ value, onChange, tico, quiet, autoFocus }: PeoplePickProps) {
  const [q, setQ] = useState('');
  const me = useMe();
  const actors = useActors().filter((a) => a.id !== me);
  const byId = new Map(actors.map((a) => [a.id, a]));
  const teams = teamsOf(actors);
  const has = (s: string) => s.toLowerCase().includes(q.trim().toLowerCase());
  const toggle = (ids: string[]) => {
    const all = ids.every((i) => value.includes(i));
    onChange(all ? value.filter((v) => !ids.includes(v)) : [...value, ...ids.filter((i) => !value.includes(i))]);
    setQ('');
  };
  const people = actors.filter((a) => has(a.name));
  const shownTeams = teams.filter((t) => has(t.name));
  const list = !quiet || q.trim();
  return (
    <>
      <div className="to">
        {value.map((id) => (
          <span key={id} className="to-chip">
            {byId.get(id)?.name ?? id}
            <button type="button" aria-label={`Remove ${byId.get(id)?.name ?? id}`} onClick={() => toggle([id])}>
              <Icon name="close" />
            </button>
          </span>
        ))}
        <input
          className="to-in"
          aria-label="Who"
          placeholder={value.length ? '' : 'Name, agent or team'}
          value={q}
          autoFocus={autoFocus}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Backspace' && !q && value.length) onChange(value.slice(0, -1));
            if (e.key === 'Enter' && q.trim() && people[0]) {
              e.preventDefault();
              toggle([people[0].id]);
            }
          }}
        />
      </div>
      {list && (
        <div className="pick" role="group" aria-label="People, agents and teams">
          {tico && !q.trim() && (
            <>
              <PickRow
                on={!value.length}
                label="Just Tico"
                onPress={() => onChange([])}
                lead={<Avatar kind="agent" name="Tico" size={22} dot={<Dot size={22} still />} decorative />}
              />
              <hr />
            </>
          )}
          {people.map((a) => (
            <PickRow
              key={a.id}
              on={value.includes(a.id)}
              label={a.name}
              sub={a.kind === 'agent' ? 'Agent' : undefined}
              onPress={() => toggle([a.id])}
              lead={<Avatar kind={a.kind} name={a.name} hue={a.hue} size={22} decorative />}
            />
          ))}
          {shownTeams.length > 0 && people.length > 0 && <hr />}
          {shownTeams.map((t) => (
            <PickRow
              key={t.name}
              on={t.ids.every((i) => value.includes(i))}
              label={t.name}
              sub={String(t.ids.length)}
              onPress={() => toggle(t.ids)}
              lead={
                <span className="pick-ico">
                  <Icon name="people" />
                </span>
              }
            />
          ))}
        </div>
      )}
    </>
  );
}

function PickRow({ on, label, sub, lead, onPress }: { on: boolean; label: string; sub?: string; lead: ReactNode; onPress: () => void }) {
  return (
    <button type="button" role="checkbox" aria-checked={on} className={on ? 'pick-row is-on' : 'pick-row'} onClick={onPress}>
      {lead}
      <span className="pick-name">{label}</span>
      {sub && <small>{sub}</small>}
      <em className="pick-box">
        <Icon name="check" />
      </em>
    </button>
  );
}

function teamsOf(actors: readonly Actor[]): Team[] {
  const m = new Map<string, string[]>();
  for (const a of actors) if (a.team) m.set(a.team, [...(m.get(a.team) ?? []), a.id]);
  return [...m].map(([name, ids]) => ({ name, ids })).sort((a, b) => a.name.localeCompare(b.name));
}
