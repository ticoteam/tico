import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { useEffect, useEffectEvent, useMemo, useRef, useState, useSyncExternalStore, type MouseEvent } from 'react';
import { flushSync } from 'react-dom';
import { meQuery, recordVisit } from '@/data';
import { useFileOpen } from '@/features/library/routing';
import { DockSlot } from '@/kit/Dock';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { useCards } from '@/kit/Popover';
import { Segmented } from '@/kit/Segmented';
import { Viewer } from '@/kit/Viewer';
import { closeTask, coldOf, hearPlaces, openOf, othersListed, stuckToBottom, watchKeyboard, type Keys, type Steps } from '@/lib';
import { CHAT } from './model';
import { taskDetailQuery, taskRefQuery } from './queries';
import { loadTask } from './routing';
import { TaskList } from './TaskLists';
import { TaskChat, TaskPage } from './TaskPage';
import { questionsFor } from './waits';
import './modal.css';

type Pane = 'task' | 'chat';
const PANES = [
  { value: 'task' as const, label: 'Task' },
  { value: 'chat' as const, label: 'Chat' },
];

/**
 * A task open over the page you are on (plan: the task modal), whenever the address names one (`?task=`, lib
 * taskAddress): the list it was opened from, the agent's chat on it, and the task. ×, Esc or Back closes it and the page
 * under it is as it was; a file opened from it opens one layer up, and Esc closes that first. Under 1100px the list
 * folds to a tab at the left edge; on a phone the task and its chat are tabs and the list pulls up from the bottom.
 */
export function TaskModal() {
  const search = useRouterState({ select: (s) => s.location.search as Record<string, unknown> });
  const open = useMemo(() => openOf(search), [search]);
  const router = useRouter();
  // the library's file viewer over it (a file in the chat): Esc and a press on it close that, not the task
  const fileOpen = useFileOpen();
  // the last task shown stays drawn while the layer closes
  const [last, setLast] = useState(open);
  if (open && open !== last) setLast(open);
  const shown = open ?? last;
  const task = shown?.task ?? '';
  const ref = useQuery({ ...taskRefQuery(task), enabled: !!task });
  const id = ref.data;
  const read = useQuery({ ...taskDetailQuery(id), enabled: false });
  const t = read.data;
  const me = useQuery(meQuery).data?.id;
  const client = useQueryClient();
  const cards = useCards();

  // the task's first reads (its page, its chat's view) start as it opens
  const words = shown?.words;
  // once per task, with the words it opened with (they change as it is read)
  const warm = useEffectEvent((n: string) => void loadTask(client, n, words?.tab, words?.session));
  useEffect(() => {
    if (task) warm(task);
  }, [task]);

  // each task opened goes to the person's page history, by its own name
  const title = t?.title;
  const visit = open?.task;
  useEffect(() => {
    if (!visit || !title) return;
    const at = setTimeout(() => recordVisit(`/task/${visit}`, title), 400);
    return () => clearTimeout(at);
  }, [visit, title]);

  // a phone shows the task or its chat, chosen once the task is read (never flipped after by itself): the chat when the
  // address names it or a ❓ in it waits on the person. Then what the person picks shows: a session (Details) raises
  // the chat; another of the task's things (an approval card in the chat, a tab, a file) its page, so its Approve is
  // never pressed unseen. The first tab the page names as it opens picks nothing.
  type Picked = { task: string; pane: Pane; session?: string; tab?: string; ask?: string; file?: string };
  const [pane, setPane] = useState<Picked | null>(null);
  const session = words?.session;
  const at = { task, session, tab: words?.tab, ask: words?.ask, file: words?.file };
  if (task && pane?.task !== task && (t || read.isError || ref.isError))
    setPane({ ...at, pane: at.tab === CHAT || session || (t && questionsFor(t, me).length) ? 'chat' : 'task' });
  else if (pane?.task === task && (session !== pane.session || at.tab !== pane.tab || at.ask !== pane.ask || at.file !== pane.file)) {
    const chat = (!!session && session !== pane.session) || (at.tab === CHAT && pane.tab !== CHAT);
    const page = (!!pane.tab && at.tab !== pane.tab && at.tab !== CHAT) || (!!at.ask && at.ask !== pane.ask) || (!!at.file && at.file !== pane.file);
    setPane({ ...at, pane: chat ? 'chat' : page ? 'task' : pane.pane });
  }
  const on = pane?.task === task ? pane.pane : undefined;
  // a link in it to this task's own session or thing, pressed though the address already names it: the same
  const raise = (e: MouseEvent) => {
    const a = e.target instanceof Element ? e.target.closest('a[href]') : null;
    if (!cards || !a || !pane || pane.task !== task) return;
    const url = new URL(a.getAttribute('href')!, window.location.href);
    const to = coldOf(url.pathname, Object.fromEntries(url.searchParams)) ?? openOf(Object.fromEntries(url.searchParams));
    if (!to || (to.task !== task && to.task !== t?.id && to.task !== String(t?.number ?? ''))) return;
    setPane({ ...pane, pane: to.words.session || to.words.tab === CHAT ? 'chat' : 'task' });
  };
  // the list pulled up (a phone) or out (a narrow window) for this task: a task picked from it folds it again
  const [upFor, setUpFor] = useState<string | null>(null);
  const listUp = upFor === task;
  const setListUp = (up: boolean) => setUpFor(up ? task : null);
  // the list over the rest, which is out of reach meanwhile; folded, back to what pulled it out
  const pull = useRef<HTMLButtonElement>(null);
  const listBox = useRef<HTMLElement>(null);
  const foldList = () => {
    flushSync(() => setListUp(false));
    pull.current?.focus();
  };
  useEffect(() => {
    if (listUp) listBox.current?.querySelector<HTMLElement>('.li.is-on, .li-pick')?.focus();
  }, [listUp]);
  const others = useSyncExternalStore(hearPlaces, othersListed);
  const [dock, setDock] = useState<HTMLElement | null>(null);

  // the phone's keyboard: the modal sits on a layer of its own, outside the phone's cards, so it rides the keys itself,
  // its bottom (and the toasts' Undo, on its layer) kept above them while something in it is typed in, as the keys or
  // the focus move, its chat kept at its last line (lib keyboard)
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    const layer = box?.closest<HTMLElement>('.vw');
    if (!cards || !box || !layer) return;
    let keys: Keys = { kb: 0, top: 0, up: false };
    let blur = 0;
    let frame = 0;
    const update = () => {
      const typing = keys.up && box.contains(document.activeElement);
      const kb = `${typing ? Math.max(0, keys.kb - keys.top) : 0}px`;
      if (layer.style.getPropertyValue('--kb') === kb) return;
      const stuck = stuckToBottom(box);
      layer.style.setProperty('--kb', kb);
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => stuck.forEach((el) => (el.scrollTop = el.scrollHeight)));
    };
    const stop = watchKeyboard((k) => {
      keys = k;
      update();
    });
    const later = () => {
      clearTimeout(blur);
      blur = window.setTimeout(update, 50);
    };
    box.addEventListener('focusin', update);
    box.addEventListener('focusout', later);
    return () => {
      stop();
      clearTimeout(blur);
      cancelAnimationFrame(frame);
      box.removeEventListener('focusin', update);
      box.removeEventListener('focusout', later);
      layer.style.removeProperty('--kb');
    };
  }, [cards, box]);

  /* Esc, after what sits over or in the task takes its own: another layer (Cmd+K's search, a file opened from it, whose
     Esc closes that), a field that keeps it (a title being edited), the list pulled out over it; then the task. */
  const close = (esc?: KeyboardEvent) => {
    if (fileOpen) return;
    if (esc) {
      const layer = box?.closest('.vw');
      const other = esc.target instanceof Element ? esc.target.closest('[role="dialog"],[role="alertdialog"]') : null;
      if (esc.defaultPrevented || !layer || (other && other !== layer)) return;
      if (listUp) return foldList();
    }
    closeTask(router as unknown as Steps);
  };
  const fold = <IconButton icon="close" label="Close the list" quiet className="tmd-fold" onClick={foldList} />;
  // covered by the list pulled out: out of reach
  const under = listUp || undefined;
  return (
    <Viewer open={!!open} onClose={close} onClosed={() => setLast(null)} title={title ?? 'Task'} look="sheet">
      {shown ? (
        <DockSlot value={cards ? dock : null}>
          <div ref={setBox} className={['tmd', listUp && 'is-list-up'].filter(Boolean).join(' ')} data-pane={on} onClickCapture={raise}>
            {cards ? (
              <div className="tmd-panes" inert={under}>
                <Segmented label="Show" options={PANES} value={on ?? 'task'} onValueChange={(p) => setPane({ ...at, pane: p })} />
              </div>
            ) : (
              <button ref={pull} type="button" className="tmd-tab" aria-label="Show the list" title="Show the list" inert={under} onClick={() => setListUp(true)}>
                <Icon name="list" />
              </button>
            )}
            {/* a press on a row here opens it in place, with this list (router.tsx) */}
            <nav ref={listBox} className="pane nav list tmd-list" aria-label="List" data-list={shown.list ?? ''}>
              <TaskList list={shown.list} open={shown.task} current={t} end={fold} />
            </nav>
            <aside className="pane side chat chat-2 tmd-chat" aria-label="Chat" inert={under}>
              <TaskChat task={shown.task} />
            </aside>
            {/* the task's page is the column itself (its own region), filling it */}
            <div className="tmd-task" inert={under}>
              <TaskPage task={shown.task} tab={shown.words.tab} file={shown.words.file} />
            </div>
            {cards ? (
              <>
                <div className="tmd-dock" ref={setDock} inert={under} />
                <button ref={pull} type="button" className="tmd-more" inert={under} onClick={() => setListUp(true)}>
                  <Icon name="chevdown" className="flip" />
                  {others ? `${others} more` : 'The list'}
                </button>
              </>
            ) : null}
            {listUp ? <button type="button" className="tmd-dim" aria-label="Close the list" tabIndex={-1} onClick={foldList} /> : null}
          </div>
        </DockSlot>
      ) : null}
    </Viewer>
  );
}
