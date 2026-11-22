import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions/run';
import { agentsQuery, computersQuery as teamComputers, modelName, whoHref } from '@/features/team/routing';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Segmented } from '@/kit/Segmented';
import { toast } from '@/kit/Toast';
// The Settings kind has no panel prop (List and Record do); the slot itself draws it beside the page.
import { PanelSlot, SidePanel } from '@/pagekinds';
import { Line, SwitchLine, act, useAgentFaces } from './parts';
import { computersQuery } from './queries';

type Mode = 'off' | 'view' | 'control';
const SCREEN: { value: Mode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'view', label: 'View' },
  { value: 'control', label: 'Control' },
];

export function computerState(c: { online: boolean; restarting?: unknown }): string {
  return c.restarting ? 'Restarting' : c.online ? 'Online' : 'Offline';
}

/**
 * A computer opened beside the agents list or Settings › Computers (settings.js fleet panel): its facts, its agents,
 * the screen setting, Restart, Rename and Remove. Restart waits for running turns on the hub (POST …/restart).
 */
export function ComputerPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const computers = useQuery(teamComputers);
  const settings = useQuery(computersQuery);
  const roster = useQuery(agentsQuery);
  const agents = useAgentFaces();
  const [asking, setAsking] = useState<'restart' | 'rename' | 'remove' | null>(null);
  const c = computers.data?.find((x) => x.id === id);
  const own = settings.data?.find((x) => x.id === id);
  const [name, setName] = useState(c?.label ?? '');
  if (!c || c.revoked_at) return null;
  const label = c.label || c.id;
  const manage = c.may_manage !== false;
  const on = (roster.data ?? []).filter((r) => r.machine?.runner_id === c.id || c.bots?.includes(r.id));
  const restart = async () => {
    setAsking(null);
    const r = await run('computers.restart', { id: c.id }, { via: 'click' });
    if (r.ok && r.did) toast({ text: r.did, kind: 'done' });
  };
  const remove = async () => {
    setAsking(null);
    if (await act('settings.removeComputer', { computer: c.id })) onClose();
  };
  const cancel = (
    <Button variant="quiet" onClick={() => setAsking(null)}>
      Cancel
    </Button>
  );
  return (
    <PanelSlot>
      <SidePanel
        title={label}
        target={`computer:${c.id}`}
        lead={<Icon name="computer" />}
        onClose={onClose}
        className="cp"
        foot={
          manage && (
            <>
              <Button disabled={!!c.restarting} onClick={() => setAsking('restart')}>
                Restart
              </Button>
              <Button
                variant="quiet"
                onClick={() => {
                  setName(c.label ?? '');
                  setAsking('rename');
                }}
              >
                Rename
              </Button>
              <Button variant="quiet" onClick={() => setAsking('remove')}>
                Remove
              </Button>
            </>
          )
        }
      >
        <div className="rail-sec">
          <Facts label={label}>
            <LabelValue label="State">{computerState(c)}</LabelValue>
            <LabelValue label="Kind">{c.cloud_machine ? (c.cloud_machine.shared ? 'Shared, in the cloud' : 'In the cloud') : 'Your own'}</LabelValue>
            {c.cloud_machine?.size && <LabelValue label="Size">{c.cloud_machine.size}</LabelValue>}
            {c.cloud_machine?.placement && <LabelValue label="Place">{c.cloud_machine.placement}</LabelValue>}
            {c.platform && <LabelValue label="Platform">{c.platform}</LabelValue>}
            {c.version && <LabelValue label="Version">{c.version}</LabelValue>}
            {c.screen_available && (
              <Line label="Screen">
                <Segmented
                  label="Screen"
                  options={SCREEN}
                  value={(c.screen as Mode | undefined) ?? 'off'}
                  disabled={!manage}
                  onValueChange={(mode) => void act('computers.setScreen', { computer: c.id, mode })}
                />
              </Line>
            )}
            {own && (
              <SwitchLine
                label="Fall back to Tico Gateway"
                checked={own.gateway_fallback === true}
                disabled={!manage}
                onChange={(v) => void act('settings.gatewayFallback', { computer: c.id, on: v })}
              />
            )}
          </Facts>
        </div>
        <div className="rail-sec">
          <h3>
            Agents<span className="cp-n">{on.length}</span>
          </h3>
          {on.length === 0 ? (
            <div className="row is-off">
              <span className="row-title">No agents yet</span>
            </div>
          ) : (
            on.map((r) => {
              const a = agents.find((x) => x.slug === r.slug);
              const who = a?.name ?? r.display_name ?? r.slug;
              return (
                <Link key={r.slug} className="row cp-agent" href={a ? whoHref(a) : `/team/${encodeURIComponent(r.slug)}`} title={who}>
                  <Avatar kind="agent" name={who} hue={a?.hue} decorative />
                  <span className="row-title">{who}</span>
                  <span className="row-cell">{modelName(r.resolved_model_label ?? r.resolved_model ?? r.model).replace(/^Claude /, '')}</span>
                </Link>
              );
            })
          )}
        </div>
      </SidePanel>
      <Dialog
        open={asking === 'restart'}
        onOpenChange={(o) => !o && setAsking(null)}
        title={`Restart ${label}?`}
        description="Its agents start again in about a minute."
        actions={
          <>
            {cancel}
            <Button variant="primary" onClick={() => restart()}>
              Restart
            </Button>
          </>
        }
      />
      <Dialog
        open={asking === 'rename'}
        onOpenChange={(o) => !o && setAsking(null)}
        title="Rename computer"
        actions={
          <>
            {cancel}
            <Button
              variant="primary"
              disabled={!name.trim()}
              onClick={() => {
                setAsking(null);
                void act('settings.nameComputer', { computer: c.id, name: name.trim() });
              }}
            >
              Rename
            </Button>
          </>
        }
      >
        <Field label="Name" value={name} onValueChange={setName} autoFocus />
      </Dialog>
      <Dialog
        open={asking === 'remove'}
        onOpenChange={(o) => !o && setAsking(null)}
        alert
        title={`Remove ${label}?`}
        description="It stops taking work at once. Its agents wait until they have another computer."
        actions={
          <>
            {cancel}
            <Button variant="primary" onClick={() => remove()}>
              Remove
            </Button>
          </>
        }
      />
    </PanelSlot>
  );
}
