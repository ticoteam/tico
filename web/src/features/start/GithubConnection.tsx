import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { GitHubApp } from '@/api/schema';
import { run, useAction } from '@/actions';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { SkeletonRows } from '@/kit/Skeleton';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { PartFault } from './Cards';
import { forgetSetup, isGithubPage, pendingSetup, reasonOf } from './handoff';
import { githubQuery } from './queries';

/* The company's GitHub App, for Tools › GitHub (the old ui/github-connect.ts): connected or not, which account, what it
   may do, and the fixes. Connect opens the short Connect GitHub dialog over the app (`?setup=github`). The owner only;
   anyone else sees nothing here. */

const click = { via: 'click' } as const;

export function GithubConnection() {
  const { data: me } = useQuery(meQuery);
  const owner = me?.role === 'owner';
  const app = useQuery({ ...githubQuery, enabled: owner });
  const connected = app.data?.connected;
  // Connected: the setup this tab kept is used up.
  useEffect(() => {
    if (connected) forgetSetup();
  }, [connected]);
  if (!owner) return null;
  return (
    <Section name="GitHub app" target="section:github" actions={app.data?.connected ? <More app={app.data} /> : undefined}>
      {app.isPending ? (
        <SkeletonRows rows={3} />
      ) : app.isError ? (
        <PartFault name="GitHub" onRetry={() => void app.refetch()} />
      ) : app.data.connected ? (
        <Connected app={app.data} />
      ) : (
        <NotConnected />
      )}
    </Section>
  );
}

/** Runs an action and keeps its refusal, in the server's words. */
function usePress(id: string) {
  const action = useAction(id);
  const [error, setError] = useState<string | null>(null);
  const press = async (input: unknown = {}) => {
    setError(null);
    const r = await action.run(input);
    if (!r.ok) setError(reasonOf(r));
    return r.ok;
  };
  return { press, pending: action.pending, error };
}

function Refusal({ text }: { text: string | null }) {
  return text ? (
    <span className="f-err" role="alert">
      {text}
    </span>
  ) : null;
}

function NotConnected() {
  // A setup this tab sent to GitHub that came back with no session: finish it here.
  const [pending] = useState(pendingSetup);
  const finish = usePress('github.finish');
  return (
    <>
      <Facts>
        <LabelValue
          label="State"
          trail={
            <>
              {pending && (
                <Button busy={finish.pending} onClick={() => finish.press()}>
                  Finish
                </Button>
              )}
              <Button variant="primary" onClick={() => run('setup.open', { step: 'github' }, click)}>
                Connect
              </Button>
            </>
          }
        >
          Not connected
        </LabelValue>
      </Facts>
      <Refusal text={finish.error} />
    </>
  );
}

function Connected({ app }: { app: GitHubApp }) {
  const personal = app.account_type === 'User';
  const install = usePress('tools.installGithub');
  const check = usePress('github.checkPermissions');
  const authorize = usePress('github.authorize');
  const made = app.bot_repositories;
  const needsAuthorizing = personal && !app.authorized;
  return (
    <>
      <Facts>
        <LabelValue label="Account">{app.org}</LabelValue>
        {app.slug && <LabelValue label="App">{app.slug}</LabelValue>}
        <LabelValue
          label="Installed"
          trail={
            !app.installed && (
              <Button variant="primary" busy={install.pending} onClick={() => install.press()}>
                Install
              </Button>
            )
          }
        >
          {app.installed ? 'Yes' : 'Not yet'}
        </LabelValue>
        <LabelValue
          label="Repositories"
          trail={
            !app.administration ? (
              <>
                {isGithubPage(app.permissions_url) && (
                  <a className="btn" href={app.permissions_url} target="_blank" rel="noopener noreferrer">
                    Permissions
                  </a>
                )}
                <Button busy={check.pending} onClick={() => check.press()}>
                  Check again
                </Button>
              </>
            ) : needsAuthorizing ? (
              <Button busy={authorize.pending} onClick={() => authorize.press()}>
                Authorize
              </Button>
            ) : undefined
          }
        >
          {!app.administration ? 'Needs Administration' : needsAuthorizing ? 'Not authorized' : 'Agents create them'}
        </LabelValue>
        {needsAuthorizing && app.callback_url && (
          <LabelValue label="Callback URL">
            <code className="fact-code">{app.callback_url}</code>
          </LabelValue>
        )}
        {made && made.waiting > 0 && app.administration && app.installed && (
          <LabelValue label="Agent repositories" num>
            {`${made.made} of ${made.made + made.waiting}`}
          </LabelValue>
        )}
        {app.checks_withheld && <LabelValue label="Checks">Needs Checks permission</LabelValue>}
      </Facts>
      <Refusal text={install.error ?? check.error ?? authorize.error} />
    </>
  );
}

/** The ⋯ menu: GitHub's own page for the App, and Disconnect after a question. */
function More({ app }: { app: GitHubApp }) {
  const [asking, setAsking] = useState(false);
  const disconnect = usePress('github.disconnect');
  const settings = isGithubPage(app.settings_url) ? app.settings_url : null;
  const yes = async () => {
    if (await disconnect.press()) {
      setAsking(false);
      toast({ text: 'Disconnected', name: 'GitHub' });
    }
  };
  return (
    <>
      <Menu trigger={<IconButton icon="more" label="GitHub app" size="sm" quiet />}>
        {settings && <MenuItem label="Open on GitHub" icon="popout" onSelect={() => void window.open(settings, '_blank', 'noopener,noreferrer')} />}
        <MenuItem label="Disconnect" onSelect={() => setAsking(true)} />
      </Menu>
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title="Disconnect GitHub?"
        description="Agents lose GitHub here. The app stays on GitHub until you delete it there."
        actions={
          <>
            <Button disabled={disconnect.pending} onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={disconnect.pending} onClick={() => yes()}>
              Disconnect
            </Button>
          </>
        }
      >
        <Refusal text={disconnect.error} />
      </Dialog>
    </>
  );
}
