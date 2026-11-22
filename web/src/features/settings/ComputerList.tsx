import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { computersQuery } from '@/features/team/routing';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { ComputerPanel, computerState } from './ComputerPanel';
import { agentsWord } from './parts';

export function ComputerList({ open }: { open?: string }) {
  const { data: computers } = useQuery(computersQuery);
  const navigate = useNavigate();
  // Old links put the bare id in the address.
  const opened = computers?.find((c) => open === c.id || open === `computer:${c.id}`);
  if (!computers) return null;
  if (!computers.length) return <Empty icon="computer" title="No computers yet" />;
  return (
    <div className="computer-list">
      <div className="tbl-head g-computer" aria-hidden="true">
        <span>Computer</span>
        <span>Kind</span>
        <span>State</span>
        <span>Version</span>
        <span className="is-num">Agents</span>
      </div>
      {computers.map((c) => {
        const selected = opened?.id === c.id;
        return (
          <Link
            key={c.id}
            to="/settings/$section"
            params={{ section: 'computers' }}
            search={selected ? {} : { open: `computer:${c.id}` }}
            className={selected ? 'row g-computer is-selected' : 'row g-computer'}
            aria-expanded={selected}
          >
            <span className="row-title" title={c.label || c.id}>{c.label || c.id}</span>
            <span className="row-cell">{c.cloud_machine ? (c.cloud_machine.shared ? 'Cloud shared' : 'Cloud') : 'Your own'}</span>
            <span className="row-cell computer-state">{computerState(c)}</span>
            <span className="row-cell" title={c.version || undefined}>{c.version || '—'}</span>
            <span className="row-fig">{agentsWord(c.bots?.length ?? 0)}</span>
            <Icon name="panel" />
          </Link>
        );
      })}
      {opened && <ComputerPanel id={opened.id} onClose={() => void navigate({ to: '/settings/$section', params: { section: 'computers' }, search: {} })} />}
    </div>
  );
}
