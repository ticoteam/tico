import { useQuery } from '@tanstack/react-query';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Pill } from '@/kit/Pill';
import { Section } from '@/pagekinds';
import { contactRulesQuery, usQuery, type Card, type CardLink, type PlaceInfo, type Us } from './queries';

/* A contact's Rules (the mock's first section; old directory-entity.ts rules card), and what a place adds: its kind,
   what it is inside, who governs it, the bodies over it, what sits in it, and why its rules bind us. */

type Href = (id: string, type?: string) => string;

/** A rule's effect as the old card said it. */
const EFFECTS: Record<string, string> = { block: 'Block', escalate: 'Ask', warn: 'Warn', ok: 'Ok', skip: 'Skip' };

export function ContactRules({ id }: { id: string }) {
  const rules = useQuery(contactRulesQuery(id));
  const rows = rules.data ?? [];
  return (
    <Section name="Rules" count={rules.data ? rows.length : undefined}>
      {rules.isError && <Empty title="Rules did not load" icon="alert" />}
      {rules.data && !rows.length && <p className="rec-none t-small">None apply</p>}
      {rows.map((r, i) => {
        const why = [(r.path ?? []).join(' → '), r.cite ?? '', r.reason ?? ''].filter(Boolean).join(' · ');
        return (
          <div key={r.id ?? i} className="row lrow" title={why ? `${r.text}\n${why}` : r.text}>
            <span className="lead">
              <Icon name="rules" />
            </span>
            <span className="row-title">{r.text}</span>
            <span className="row-ctx">{r.effect && <Pill flag={r.effect === 'block'}>{EFFECTS[r.effect] ?? r.effect}</Pill>}</span>
          </div>
        );
      })}
    </Section>
  );
}

const placeOf = (c: Card): PlaceInfo => (c as Card & { place?: PlaceInfo }).place ?? { kind: '', governing_body: '', shared: false };
const linksOf = (c: Card) => c.links as CardLink[];
const into = (c: Card) => linksOf(c).filter((l) => l.from_entity_id !== undefined && l.from_entity_id !== c.id);
const words = (key: string) => key.replace(/_/g, ' ').replace(/^./, (x) => x.toUpperCase());

/** Ours, for a place: whether we operate here. */
export function useUs(c: Card | undefined): Us | null | undefined {
  return useQuery({ ...usQuery, enabled: c?.type === 'place' }).data;
}

/** A place's facts: its kind, the place it is in, who governs it, and whether we operate here or it is shared. */
export function PlaceFacts({ c, us, href }: { c: Card; us: Us | null | undefined; href: Href }) {
  const place = placeOf(c);
  const parent = linksOf(c).find((l) => l.from_entity_id === c.id && l.link_key === 'located_in' && l.other_type === 'place');
  const governor = place.governing_body || into(c).find((l) => l.link_key === 'governs')?.other_name || '';
  const operate = !!us?.operates_in.some((p) => p.id === c.id);
  return (
    <>
      {place.kind && <LabelValue label="Kind">{words(place.kind)}</LabelValue>}
      {parent && (
        <LabelValue label="Located in">
          <Link href={href(parent.other_id, parent.other_type)}>{parent.other_name}</Link>
        </LabelValue>
      )}
      {governor && <LabelValue label="Governed by">{governor}</LabelValue>}
      {(operate || place.shared) && (
        <LabelValue label="Here">
          <span className="rec-pills">
            {operate && <Pill>We operate here</Pill>}
            {place.shared && <Pill>Shared</Pill>}
          </span>
        </LabelValue>
      )}
    </>
  );
}

/** A place's sections in the old page's order: Bodies, Inside, Located here, Why it applies. */
export function PlaceSections({ c, us, href }: { c: Card; us: Us | null | undefined; href: Href }) {
  const incoming = into(c);
  const bodies = incoming.filter((l) => l.link_key === 'governs' || l.link_key === 'regulates');
  const inside = incoming.filter((l) => l.link_key === 'located_in');
  const places = inside.filter((l) => l.other_type === 'place');
  const here = inside.filter((l) => l.other_type !== 'place');
  const operate = !!us?.operates_in.some((p) => p.id === c.id);
  const list = (name: string, rows: CardLink[], ctx?: (l: CardLink) => string) =>
    rows.length > 0 && (
      <Section name={name} count={rows.length}>
        {rows.map((l) => (
          <Link key={l.id} className="row lrow" href={href(l.other_id, l.other_type)} title={l.other_name}>
            <span className="lead">
              <Icon name="link" />
            </span>
            <span className="row-title">{l.other_name}</span>
            <span className="row-ctx">{ctx?.(l)}</span>
          </Link>
        ))}
      </Section>
    );
  return (
    <>
      {list('Bodies', bodies, (l) => l.inverse_name || l.link_name)}
      {list('Places inside', places)}
      {list('Located here', here)}
      {operate && us && (
        <Section name="Why it applies">
          <Link className="row lrow" href={href(us.id, 'organization')} title={`${us.name} operates in ${c.name}`}>
            <span className="lead">
              <Icon name="link" />
            </span>
            <span className="row-title">{us.name}</span>
            <span className="row-ctx">Operates here</span>
          </Link>
        </Section>
      )}
    </>
  );
}
