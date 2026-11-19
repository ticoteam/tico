import type RFB from '@novnc/novnc';
import { useEffect, useEffectEvent, useRef, useState, type CSSProperties } from 'react';
import { useAction } from '@/actions';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { IconButton } from '@/kit/IconButton';
import { Segmented } from '@/kit/Segmented';
import { MateRow } from './parts';
import { screenSocket, type Computer, type ScreenSession } from './queries';

/* A computer's screen (old app: #/computers/{id}/screen, ui/app/computer-screen.ts). A session from
   POST /api/v2/computers/{id}/screen/sessions, then noVNC over the hub's websocket at its ws_path, view only or in
   control (the computer enforces it). noVNC loads on first use, so it adds nothing to the first load. */

let loading: Promise<typeof RFB> | undefined;
const loadViewer = (): Promise<typeof RFB> => (loading ??= import('@novnc/novnc').then((m) => m.default));

/** Why the hub closed the socket; anything else (a used ticket, the hour's limit, the other side) ends the session. */
const CLOSED: Record<number, string> = { 4404: 'The computer did not answer', 4403: 'The screen is not available' };
const REFUSED: Record<string, string> = {
  screen_off: 'The screen is off',
  screen_unavailable: 'This computer has no screen',
  screen_view_only: 'The screen is view only',
  forbidden: 'Only its owner or an admin can open it',
  not_allowed: 'Only its owner or an admin can open it',
};
const refusal = (error = '') => REFUSED[error.split(':')[0] ?? ''] ?? 'Could not open the screen';

export type ScreenMode = 'view' | 'control';
const MODES = [
  { value: 'view', label: 'View' },
  { value: 'control', label: 'Control' },
] as const;

/** Whether the person can open it at all, and with which modes: drawn in the tab row and the body alike. */
export function screenOpen(c: Computer): 'view' | 'control' | null {
  if (!c.online || !c.screen_available || c.may_manage === false) return null;
  return c.screen === 'control' ? 'control' : c.screen === 'view' ? 'view' : null;
}

/** The tab row's end: View | Control when control is allowed, and full screen. */
export function ScreenTools({ c, mode, onMode }: { c: Computer; mode: ScreenMode; onMode: (m: ScreenMode) => void }) {
  const full = useAction('computers.fullScreen');
  const allowed = screenOpen(c);
  if (!allowed) return null;
  return (
    <>
      {allowed === 'control' && <Segmented label="Mode" options={MODES} value={mode} onValueChange={onMode} />}
      <IconButton icon="expand" size="sm" quiet label="Full screen" onClick={() => void full.run({ computer: c.id })} />
    </>
  );
}

/** Who is on it now, as bare rows, then the picture (blank until it connects). */
export function Screen({ c, mode }: { c: Computer; mode: ScreenMode }) {
  const actors = useActors();
  const setScreen = useAction('computers.setScreen');
  const [attempt, setAttempt] = useState(0);
  if (!c.online) return <Empty icon="offline" title="It did not answer" />;
  if (!c.screen_available) return <Empty icon="computer" title="This computer has no screen" />;
  if (c.may_manage === false) return <Empty icon="lock" title="Only its owner or an admin can see its screen" />;
  const allowed = screenOpen(c);
  if (!allowed)
    return (
      <Empty icon="computer" title="The screen is off">
        <Button variant="primary" busy={setScreen.pending} onClick={() => void setScreen.run({ computer: c.id, mode: 'control' })}>
          Turn on
        </Button>
      </Empty>
    );
  const control = allowed === 'control' && mode === 'control';
  const busy = actors.filter((a) => a.kind === 'agent' && a.working && c.bots?.includes(a.id.slice(a.id.indexOf(':') + 1)));
  return (
    <>
      {busy.map((a) => (
        <MateRow key={a.id} actor={a} />
      ))}
      <Live key={`${c.id}:${control}:${attempt}`} c={c} control={control} onReconnect={() => setAttempt((n) => n + 1)} />
    </>
  );
}

type Phase = { is: 'connecting' } | { is: 'live'; control: boolean; width: number; height: number } | { is: 'ended'; line: string };

/** One session. Mounted again (by key) for a new mode or a reconnect; leaving closes it. */
function Live({ c, control, onReconnect }: { c: Computer; control: boolean; onReconnect: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [phase, setPhase] = useState<Phase>({ is: 'connecting' });
  const watch = useAction('computers.watchScreen');
  const drive = useAction('computers.controlScreen');
  const openSession = useEffectEvent(() => (control ? drive : watch).run({ computer: c.id }));
  useEffect(() => {
    const target = box.current;
    if (!target) return;
    let gone = false;
    let viewer: RFB | null = null;
    void (async () => {
      const [r, Viewer] = await Promise.all([
        openSession(),
        loadViewer().catch(() => null),
      ]);
      if (gone) return;
      // The ticket lives only here, for as long as it takes to connect.
      const session = r.ok ? (r.out as ScreenSession | undefined) : undefined;
      if (!session || !Viewer) {
        setPhase({ is: 'ended', line: r.ok ? 'Could not open the screen' : refusal(r.error) });
        return;
      }
      let ws: WebSocket;
      try {
        ws = screenSocket(session.ws_path);
      } catch {
        setPhase({ is: 'ended', line: 'Could not open the screen' });
        return;
      }
      let code = 0;
      // Added before noVNC's own handler, so the code is known when it says it disconnected.
      ws.addEventListener('close', (e) => (code = e.code));
      const rfb = new Viewer(target, ws, { shared: true });
      viewer = rfb;
      rfb.viewOnly = !session.control;
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.background = 'transparent';
      rfb.focusOnClick = session.control;
      rfb.addEventListener('connect', () => {
        if (gone) return;
        setPhase({ is: 'live', control: session.control, width: session.width, height: session.height });
        if (session.control && matchMedia('(hover: hover)').matches) rfb.focus();
      });
      rfb.addEventListener('disconnect', () => {
        viewer = null;
        if (!gone) setPhase({ is: 'ended', line: CLOSED[code] ?? 'Session ended' });
      });
    })();
    return () => {
      gone = true;
      try {
        viewer?.disconnect();
      } catch {
        // already closed
      }
    };
  }, [c.id, control]);
  const shape = phase.is === 'live' && phase.width && phase.height ? ({ aspectRatio: `${phase.width} / ${phase.height}` } as CSSProperties) : undefined;
  return (
    <div className="scr" data-target={`screen:${c.id}`} style={shape} aria-label={`The screen of ${c.label}`}>
      <div className="scr-view" ref={box} />
      {phase.is === 'ended' && (
        <div className="scr-msg" role="status">
          <span>{phase.line}</span>
          <Button onClick={onReconnect}>Reconnect</Button>
        </div>
      )}
      {phase.is === 'live' && <span className="scr-rec">{phase.control ? 'You have control' : 'View only'}</span>}
    </div>
  );
}
