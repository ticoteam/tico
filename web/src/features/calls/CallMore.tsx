import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { Call } from '@/api/schema';
import { useActors } from '@/data';
import { FilePreview } from '@/features/library/exports';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuSub } from '@/kit/Menu';
import { Face, press, useNameOf, type InCall } from './parts';
import { stageFilesQuery } from './queries';
import { attachScreen, canShare, type CallState } from './session';

/* What a call does beyond mute and leave (old thread-call-live.ts callBarDraw, call-window.ts cwPop): share a screen or
   show a Tico file, bring an agent in, and End for whoever started it. */

/** The starter's: End the call for everyone, asked first. */
export function EndCall({ room, open, onOpenChange }: { room: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      alert
      title="End the call for everyone?"
      actions={
        <>
          <Button variant="quiet" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              onOpenChange(false);
              void press('calls.end', { id: room });
            }}
          >
            End call
          </Button>
        </>
      }
    />
  );
}

/** The bar's ⋯: an agent in, and End for the starter. */
export function BarMore({ room, c, me }: { room: string; c: Call | undefined; me: string | undefined }) {
  const [ending, setEnding] = useState(false);
  const inCall = new Set((c?.bots ?? []).map((b) => (b.startsWith('bot:') ? b : `bot:${b}`)));
  const agents = useActors().filter((a) => a.id.startsWith('bot:') && !inCall.has(a.id));
  const mine = !!c && !!me && c.connector === me;
  if (!agents.length && !mine) return null;
  return (
    <>
      <Menu trigger={<IconButton icon="more" label="More" />}>
        {agents.length > 0 && (
          <MenuSub label="Add agent" icon="plus">
            {agents.map((a) => (
              <MenuItem key={a.id} label={a.name} onSelect={() => void press('calls.add', { id: room, with: [a.id] })} />
            ))}
          </MenuSub>
        )}
        {mine && <MenuItem label="End call" icon="leave" onSelect={() => setEnding(true)} />}
      </Menu>
      <EndCall room={room} open={ending} onOpenChange={setEnding} />
    </>
  );
}

/** The window's Share (calls.js full window): the screen, where the browser can send one; a Tico file; stop. */
export function ShareControl({ room, c, s, me }: { room: string; c: Call | undefined; s: CallState; me: string | undefined }) {
  const [picking, setPicking] = useState(false);
  const stage = c?.stage;
  const stoppable = s.sharing || (!!stage && (stage.by === (s.me ?? me) || c?.connector === (s.me ?? me)));
  const word = s.sharing ? 'Sharing' : 'Share';
  return (
    <>
      <Menu
        align="start"
        side="top"
        trigger={
          <button type="button" className="cc" aria-label={word} title={word}>
            <Icon name="share" />
            <span className="cc-l">{word}</span>
          </button>
        }
      >
        {canShare() && !s.sharing && <MenuItem label="Screen" icon="share" onSelect={() => void press('calls.share', { id: room, on: true })} />}
        <MenuItem label="Show a Tico file" icon="file" onSelect={() => setPicking(true)} />
        {stoppable && <MenuItem label="Stop sharing" icon="close" onSelect={() => void press('calls.stopShow', { id: room })} />}
      </Menu>
      <FilePick room={room} open={picking} onOpenChange={setPicking} />
    </>
  );
}

/** Find a file or page; the pick shows on the stage for everyone. */
function FilePick({ room, open, onOpenChange }: { room: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [q, setQ] = useState('');
  const files = useQuery({ ...stageFilesQuery(q), enabled: open });
  const list = files.data ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Show a Tico file">
      <div className="stage-files">
        <Search label="Find a file" value={q} onValueChange={setQ} placeholder="Find a file or page" wide autoFocus />
        {files.isError ? (
          <Empty title="Files did not load" icon="alert">
            <Button onClick={() => void files.refetch()}>Try again</Button>
          </Empty>
        ) : files.isSuccess && !list.length ? (
          <Empty title="No files" />
        ) : (
          list.map((f) => (
            <button
              key={f.id}
              type="button"
              className="rail-row call-rail"
              onClick={() => {
                onOpenChange(false);
                setQ('');
                void press('calls.showFile', { id: room, file: f.id });
              }}
            >
              <Icon name="file" />
              <span>{f.title}</span>
            </button>
          ))
        )}
      </div>
    </Dialog>
  );
}

/** A screen or a file on the stage, everyone small under it (calls.js t-full-share). */
export function Shared({ c, s, who, me }: { c: Call; s: CallState; who: InCall[]; me: string | undefined }) {
  const nameOf = useNameOf();
  const stage = c.stage;
  if (!stage) return null;
  const by = stage.by === (s.me ?? me) ? 'You' : nameOf(stage.by).split(' ')[0];
  return (
    <div className="win-stage">
      <figure className="shared">
        <figcaption>
          <b>{by}</b>
          {stage.kind === 'screen' ? (by === 'You' ? 'are sharing' : 'is sharing') : (stage.title ?? 'a file')}
        </figcaption>
        <div className="shot">
          {stage.kind === 'screen' ? (
            <Screen key={`${s.sharing ? 'me' : (s.screen ?? '')}`} s={s} />
          ) : stage.no_access || !stage.file_id ? (
            <Empty title="You can't open this file" icon="lock" />
          ) : (
            <FilePreview file={stage.file_id} version={stage.version} />
          )}
        </div>
      </figure>
      <div className="tile-strip">
        {who.map((x) => (
          <div key={x.id} className={x.talks ? 'tile is-sm is-speaking' : 'tile is-sm'} title={nameOf(x.id)}>
            <Face actor={x.id} size={36} speaking={x.talks} muted={x.muted} />
          </div>
        ))}
      </div>
    </div>
  );
}

/** The shared screen as video: mine while I share, else the one coming in. */
function Screen({ s }: { s: CallState }) {
  const ref = useRef<HTMLVideoElement>(null);
  const on = s.sharing || !!s.screen;
  useEffect(() => (on && ref.current ? attachScreen(ref.current) : undefined), [on]);
  return <video ref={ref} autoPlay muted playsInline aria-label="Shared screen" />;
}
