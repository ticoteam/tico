import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import type { AgentSubscriptionList, ModelSubscription } from '@/api/schema';
import { meQuery, useActor } from '@/data';
import { subscriptionsQuery } from '@/features/settings/routing';
import { Menu, MenuGroup, MenuItem, MenuRule } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { ValueButton } from '@/kit/ValueButton';
import { fmtWhen } from '@/lib';
import { useFollow } from '@/live/follow';
import { paidByQuery, setPaidBy, type PayerPick } from './queries';

const TOOLS = { claude: 'Claude', codex: 'Codex', cursor: 'Cursor', musecode: 'Muse Code' };
type Tool = keyof typeof TOOLS;
const isTool = (runtime: string): runtime is Tool => Object.hasOwn(TOOLS, runtime);
const isPlan = (provider: ModelSubscription) => provider.kind.endsWith('_login') || provider.kind === 'claude_token';

type Pool = { id: string; name: string; providers: ModelSubscription[] };
function poolsOf(providers: ModelSubscription[], mine?: string): Pool[] {
  const pools = new Map<string, Pool>();
  for (const provider of providers) {
    let pool = pools.get(provider.owner_id);
    if (!pool) {
      pool = { id: provider.owner_id, name: provider.owner_name || (provider.owner_id ? 'A teammate' : 'Company keys'), providers: [] };
      pools.set(pool.id, pool);
    }
    pool.providers.push(provider);
  }
  return [...pools.values()].sort((a, b) => Number(b.id === mine) - Number(a.id === mine) || a.name.localeCompare(b.name));
}

function Picker({ bot, tool, payer, pools, editable, showTool }: {
  bot: string;
  tool: Tool;
  payer: AgentSubscriptionList['subscriptions'][string] | undefined;
  pools: Pool[];
  editable: boolean;
  showTool: boolean;
}) {
  const [saving, setSaving] = useState(false);
  const person = useActor(payer?.kind === 'pool' ? payer.person : undefined);
  const poolLabel = (name: string) => `${name}'s pool`;
  const picked = payer?.kind === 'pool'
    ? poolLabel(pools.find((pool) => pool.id === payer.person)?.name ?? person?.name ?? 'A teammate')
    : payer ? pools.flatMap((pool) => pool.providers).find((s) => s.id === payer.subscription_id)?.name ?? 'Unknown provider'
    : 'Not set';
  const save = async (pick: PayerPick) => {
    setSaving(true);
    try {
      await setPaidBy(bot, tool, pick);
    } catch (error) {
      toast({ text: error instanceof Error ? error.message : "Couldn't save Paid by", kind: 'failed' });
    } finally {
      setSaving(false);
    }
  };
  const button = <ValueButton label={`Paid by for ${TOOLS[tool]}`} muted={!payer} disabled={saving} aria-busy={saving}>{picked}</ValueButton>;
  return (
    <span className="paid-by-tool">
      {showTool && <span>{TOOLS[tool]}</span>}
      <Menu align="start" className="paid-by-menu" trigger={button}>
        {pools.map(({ id, name, providers }) => {
          const matching = providers.filter((s) => s.runtime === tool);
          const plans = matching.filter(isPlan);
          const allowPool = !!id && (tool === 'claude' || tool === 'codex');
          if (!allowPool && !matching.length) return null;
          return (
            <MenuGroup key={id} label={name}>
              {allowPool && <MenuItem
                label={poolLabel(name)}
                meta={`${plans.length} ${TOOLS[tool]} ${plans.length === 1 ? 'plan' : 'plans'}`}
                disabled={!editable || !providers.some((s) => s.can_assign)}
                on={payer?.kind === 'pool' && payer.person === id}
                onSelect={() => void save({ kind: 'pool', person: id })}
              />}
              {matching.map((s) => (
                <MenuItem key={s.id} label={s.name} meta={isPlan(s) ? 'Plan' : 'API key'} disabled={!editable || !s.can_assign}
                  on={payer?.subscription_id === s.id}
                  onSelect={() => void save({ kind: isPlan(s) ? 'plan' : 'key', subscription_id: s.id })}
                />
              ))}
            </MenuGroup>
          );
        })}
        {pools.length > 0 && <MenuRule />}
        <MenuItem label="Not set" on={!payer} disabled={!editable} onSelect={() => void save(null)} />
      </Menu>
    </span>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="fact paid-by-fact"><span>{label}</span><div className="paid-by-values">{children}</div></div>;
}

function LastTurn({ turn }: { turn: AgentSubscriptionList['last_turn'] }) {
  return (
    <Fact label="Last turn">
      {turn?.waits.length ? turn.waits.map(({ reason, until, fixer }, i) => (
        <div key={i} className="paid-by-turn">
          {until ? <time dateTime={until} title={fmtWhen(until)}>{reason}</time> : <span>{reason}</span>}
          <small>{fixer}</small>
        </div>
      )) : turn ? (
        <div className="paid-by-turn">
          <span>{turn.payers.join(' · ')}</span>
          {turn.at && <small><time dateTime={turn.at}>{fmtWhen(turn.at)}</time></small>}
        </div>
      ) : '—'}
    </Fact>
  );
}

export function PaidBy({ bot, runtime, fleet = false }: { bot: string; runtime?: string; fleet?: boolean }) {
  const { data: me } = useQuery(meQuery);
  const active = runtime && isTool(runtime) ? runtime : undefined;
  const payerQuery = paidByQuery(bot, !fleet);
  useFollow(fleet ? null : { topic: 'runs' }, [payerQuery.queryKey]);
  const { data } = useQuery({ ...payerQuery, enabled: !fleet || !!active });
  const { data: catalog } = useQuery({ ...subscriptionsQuery, enabled: !fleet || !!active });
  if (!data?.subscriptions || !catalog?.subscriptions) return fleet ? <span className="c-paid-by" /> : null;
  const pools = poolsOf(catalog.subscriptions, me?.id);
  const tools = [...new Set([...(catalog.kinds ?? []).map((k) => k.runtime), ...Object.keys(data.subscriptions)])].filter(isTool);
  const picker = (tool: Tool) => (
    <Picker key={tool} bot={bot} tool={tool} payer={data.subscriptions[tool]} pools={pools} editable={data.can_edit === true} showTool={!fleet} />
  );
  return fleet ? <div className="c-paid-by">{active && picker(active)}</div> : (
    <>
      <Fact label="Paid by">{tools.map(picker)}</Fact>
      <LastTurn turn={data.last_turn} />
    </>
  );
}
