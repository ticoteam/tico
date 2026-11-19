import { useEffect, useState } from 'react';
import { run } from '@/actions/run';
import { desktopApp, useCallWindow, type UpdateState } from '@/desktop';
import { isCallActive, useCall } from '@/features/calls/exports';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Line, SwitchLine, Verb, useVisible } from './parts';

/* General › Desktop app: only inside the desktop app (a browser has no bridge and shows none of it). Its version, its
   update (restarting is refused while a call is on), its log, and whether it opens at login: this computer's own. */

const WORD: Record<UpdateState['state'], string> = { none: 'Up to date', downloading: 'Downloading', ready: 'Ready', error: 'Update failed' };
const AFTER_CALL = 'Restart after the call ends';

export function DesktopRows() {
  const app = desktopApp();
  return app ? <Desktop version={app.version} /> : null;
}

function Desktop({ version }: { version: string }) {
  const visible = useVisible();
  const call = useCall();
  const away = useCallWindow();
  const [update, setUpdate] = useState<UpdateState | null>(null);
  const [login, setLogin] = useState<boolean | null>(null);
  const [refused, setRefused] = useState('');
  const onCall = isCallActive(call) || away.open;

  // The bridge has no change event: read again whenever this tab comes back on screen.
  useEffect(() => {
    if (!visible) return;
    let on = true;
    desktopApp()
      ?.updateState()
      .then((s) => on && setUpdate(s), () => undefined);
    return () => {
      on = false;
    };
  }, [visible]);
  useEffect(() => {
    let on = true;
    desktopApp()
      ?.openAtLogin()
      .then((v) => on && setLogin(v), () => undefined);
    return () => {
      on = false;
    };
  }, []);

  const restart = async () => {
    if (onCall) return setRefused(AFTER_CALL);
    const r = await run('settings.restartToUpdate', {}, { via: 'click' });
    if (!r.ok) setRefused(/call is live/i.test(r.error ?? '') ? AFTER_CALL : "Couldn't restart");
  };
  const showLog = async () => {
    const r = await run('settings.showLog', {}, { via: 'click' });
    if (!r.ok) toast({ text: "Couldn't open the log", kind: 'failed' });
  };
  const setOpenAtLogin = async (want: boolean) => {
    setLogin(want);
    const r = await run('settings.openAtLogin', { on: want }, { via: 'click' });
    if (r.ok && typeof r.out === 'boolean') return setLogin(r.out);
    if (!r.ok) {
      setLogin(!want);
      toast({ text: "Couldn't save", kind: 'failed' });
    }
  };

  return (
    <Section name="Desktop app" target="setting:desktop">
      <Facts label="Desktop app">
        <LabelValue label="Version">{version}</LabelValue>
        <Line label="Updates" target="setting:updates">
          {update ? WORD[update.state] : ''}
          {update?.state === 'ready' && <Verb onClick={() => void restart()}>Restart to update</Verb>}
          {refused && (
            <span className="set-cmd-note" role="status">
              {refused}
            </span>
          )}
        </Line>
        <Line label="Log" target="setting:log">
          <Verb onClick={() => void showLog()}>Show log</Verb>
        </Line>
        {login !== null && <SwitchLine label="Open at login" target="setting:open-at-login" checked={login} onChange={(on) => void setOpenAtLogin(on)} />}
      </Facts>
    </Section>
  );
}
