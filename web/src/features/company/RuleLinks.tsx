import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { MenuItem } from '@/kit/Menu';
import { Table, Td, Th, Tr } from '@/kit/Table';
import { List } from '@/pagekinds';
import { accessQuery, ruleLinksQuery, type RuleLink } from './queries';
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
              <Th num>Unchecked</Th>
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
      <Td num zero={c ? c.passed + c.refused + c.held + c.unchecked === 0 : false}>
        {n(c ? c.passed + c.refused + c.held + c.unchecked : undefined)}
      </Td>
      <Td num zero={c?.refused === 0}>
        {n(c?.refused)}
      </Td>
      <Td num zero={c?.held === 0}>
        {n(c?.held)}
      </Td>
      <Td num zero={c?.unchecked === 0}>
        {n(c?.unchecked)}
      </Td>
    </Tr>
  );
}

/** The Rules page's "⋯" entry to Links, for the owner and Admins only (the server refuses anyone else). */
export function LinksEntry() {
  const you = useQuery(accessQuery).data?.you;
  const navigate = useNavigate();
  if (you !== 'owner' && you !== 'admin') return null;
  return <MenuItem label="Links" icon="link" onSelect={() => void navigate({ to: '/company/rules/links' })} />;
}
