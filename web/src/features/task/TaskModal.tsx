import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { useEffect, useEffectEvent, useMemo, useState, useSyncExternalStore } from 'react';
import { meQuery, recordVisit } from '@/data';
import { useFileOpen } from '@/features/library/routing';
import { DockSlot } from '@/kit/Dock';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { useCards } from '@/kit/Popover';
import { Segmented } from '@/kit/Segmented';
import { Viewer } from '@/kit/Viewer';
import { closeTask, hearPlaces, openOf, othersListed, watchKeyboard, type Steps } from '@/lib';
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

  // a phone shows the task or its chat, chosen once the task is read (never flipped after): the chat when the address
  // names it or a ❓ in it waits on the person; a session picked later (Details) raises the chat
  const [pane, setPane] = useState<{ task: string; pane: Pane; session?: string } | null>(null);
  const session = words?.session;
  if (task && pane?.task !== task && (t || read.isError || ref.isError))
    setPane({ task, session, pane: words?.tab === CHAT || session || (t && questionsFor(t, me).length) ? 'chat' : 'task' });
  else if (pane?.task === task && session && session !== pane.session) setPane({ task, session, pane: 'chat' });
  const on = pane?.task === task ? pane.pane : undefined;
  // the list pulled up (a phone) or out (a narrow window) for this task: a task picked from it folds it again
  const [upFor, setUpFor] = useState<string | null>(null);
  const listUp = upFor === task;
  const setListUp = (up: boolean) => setUpFor(up ? task : null);
  const others = useSyncExternalStore(hearPlaces, othersListed);
  const [dock, setDock] = useState<HTMLElement | null>(null);

  // the phone's keyboard: the modal sits on a layer of its own, outside the phone's cards, so it rides the keys itself,
  // its bottom kept above them while something in it is typed in (lib keyboard)
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!cards || !box) return;
    const stop = watchKeyboard((k) => {
      const typing = k.up && box.contains(document.activeElement);
      box.style.setProperty('--kb', `${typing ? Math.max(0, k.kb - k.top) : 0}px`);
    });
    return () => {
      stop();
      box.style.removeProperty('--kb');
    };
  }, [cards, box]);

  /* Esc, after what sits over or in the task takes its own: another layer (Cmd+K's search, a file opened from it, whose
     Esc closes that), a field that keeps it (a title being edited), the list pulled out over it; then the task. */
  const close = (esc?: KeyboardEvent) => {
    if (fileOpen) return;
    if (esc) {
      const layer = box?.closest('.vw');
      if (esc.defaultPrevented || !layer || !(esc.target instanceof Node && layer.contains(esc.target))) return;
      if (listUp) return setListUp(false);
    }
    closeTask(router as unknown as Steps);
  };
  const fold = <IconButton icon="close" label="Close the list" quiet className="tmd-fold" onClick={() => setListUp(false)} />;
  return (
    <Viewer open={!!open} onClose={close} onClosed={() => setLast(null)} title={title ?? 'Task'} look="sheet">
      {shown ? (
        <DockSlot value={cards ? dock : null}>
          <div ref={setBox} className={['tmd', listUp && 'is-list-up'].filter(Boolean).join(' ')} data-pane={on}>
            {cards ? (
              <div className="tmd-panes">
                <Segmented label="Show" options={PANES} value={on ?? 'task'} onValueChange={(p) => setPane({ task, session, pane: p })} />
              </div>
            ) : (
              <button type="button" className="tmd-tab" aria-label="Show the list" title="Show the list" onClick={() => setListUp(true)}>
                <Icon name="list" />
              </button>
            )}
            {/* a press on a row here opens it in place, with this list (router.tsx) */}
            <nav className="pane nav list tmd-list" aria-label="List" data-list={shown.list ?? ''}>
              <TaskList list={shown.list} open={shown.task} current={t} end={fold} />
            </nav>
            <aside className="pane side chat chat-2 tmd-chat" aria-label="Chat">
              <TaskChat task={shown.task} />
            </aside>
            {/* the task's page is the column itself (its own region), filling it */}
            <div className="tmd-task">
              <TaskPage task={shown.task} tab={shown.words.tab} file={shown.words.file} />
            </div>
            {cards ? (
              <>
                <div className="tmd-dock" ref={setDock} />
                <button type="button" className="tmd-more" onClick={() => setListUp(true)}>
                  <Icon name="chevdown" className="flip" />
                  {others ? `${others} more` : 'The list'}
                </button>
              </>
            ) : null}
            {listUp ? <button type="button" className="tmd-dim" aria-label="Close the list" tabIndex={-1} onClick={() => setListUp(false)} /> : null}
          </div>
        </DockSlot>
      ) : null}
    </Viewer>
  );
}
