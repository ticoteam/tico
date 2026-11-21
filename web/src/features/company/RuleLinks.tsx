import { useSuspenseQuery } from '@tanstack/react-query';
import { Table, Td, Th, Tr } from '@/kit/Table';
import { List } from '@/pagekinds';
import { ruleLinksQuery, type RuleLink } from './queries';
import './company.css';

/* Rules › Links (backend/rule_links.py): one table of the ways an agent's writes leave the company. A link is Enforced
   or Informed only, with its last 30 days of checked writes; on an Informed only link, refused and held are what would
   have happened. A Not covered path is not measured. */

const MODE: Record<RuleLink['mode'], string> = { enforced: 'Enforced', informed: 'Informed only', not_covered: 'Not covered' };

export function RuleLinks() {
  const links = useSuspenseQuery(ruleLinksQuery).data;
  return (
    <List
      title="Links"
      path="/company/rules/links"
      className="co"
      sections={
        <Table label="Links, last 30 days" stick>
          <thead>
            <tr>
              <Th>Link</Th>
              <Th>Mode</Th>
              <Th num>Writes</Th>
              <Th num>Refused</Th>
              <Th num>Held</Th>
            </tr>
          </thead>
          <tbody>
            {links.map((l) => (
              <LinkRow key={l.id} link={l} />
            ))}
          </tbody>
        </Table>
      }
    />
  );
}

function LinkRow({ link }: { link: RuleLink }) {
  const c = link.counts;
  const n = (v: number | undefined) => (v === undefined ? '' : String(v));
  return (
    <Tr kind={c ? undefined : 'quiet'}>
      <td className="t-name" data-target={`link:${link.id}`}>
        {link.name}
      </td>
      <td className="t-words">{MODE[link.mode]}</td>
      <Td num zero={c?.passed === 0 && c.refused === 0 && c.held === 0}>
        {n(c ? c.passed + c.refused + c.held : undefined)}
      </Td>
      <Td num zero={c?.refused === 0}>
        {n(c?.refused)}
      </Td>
      <Td num zero={c?.held === 0}>
        {n(c?.held)}
      </Td>
    </Tr>
  );
}
