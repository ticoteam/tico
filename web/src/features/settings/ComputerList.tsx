import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { agentsQuery, computersQuery } from '@/features/team/routing';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { ComputerPanel, agentsOnComputer, computerKind, computerState } from './ComputerPanel';
import { agentsWord } from './parts';

export function ComputerList({ open }: { open?: string }) {
  const { data: computers } = useQuery(computersQuery);
  const { data: roster } = useQuery(agentsQuery);
  const navigate = useNavigate();
  if (!computers) return null;
  const live = computers.filter((c) => !c.revoked_at);
  // Old links put the bare id in the address.
  const opened = live.find((c) => open === c.id || open === `computer:${c.id}`);
  if (!live.length) return <Empty icon="computer" title="No computers yet" />;
  return (
    <div className="computer-list">
      <div className="tbl-head g-computer" aria-hidden="true">
        <span>Computer</span>
        <span>Kind</span>
        <span>State</span>
        <span>Version</span>
        <span className="is-num">Agents</span>
      </div>
      {live.map((c) => {
        const selected = opened?.id === c.id;
        return (
          <Link
            key={c.id}
            to="/settings/$section"
            params={{ section: 'computers' }}
            search={selected ? {} : { open: `computer:${c.id}` }}
            className={selected ? 'row g-computer is-selected' : 'row g-computer'}
            data-target={selected ? undefined : `computer:${c.id}`}
            aria-expanded={selected}
          >
            <span className="row-title" title={c.label || c.id}>{c.label || c.id}</span>
            <span className="row-cell">{computerKind(c)}</span>
            <span className="row-cell computer-state">{computerState(c)}</span>
            <span className="row-cell" title={c.version || undefined}>{c.version || '—'}</span>
            <span className="row-fig">{roster ? agentsWord(agentsOnComputer(c, roster).length) : '—'}</span>
            <Icon name="panel" />
          </Link>
        );
      })}
      {opened && <ComputerPanel id={opened.id} onClose={() => void navigate({ to: '/settings/$section', params: { section: 'computers' }, search: {} })} />}
    </div>
  );
}
