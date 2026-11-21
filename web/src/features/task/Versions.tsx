import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { run } from '@/actions';
import type { TaskDocument } from '@/api/schema';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { Menu, MenuItem } from '@/kit/Menu';
import { fmtDate } from '@/lib';
import { frameDoc, markChanges, parsePage } from './changes';
import { DocFrame, PageBones, PAGE_SANDBOX } from './FileView';
import { PageMenu } from './Files';
import { pageVersionQuery } from './queries';

/* A page's versions (more.js taskflow): "v2" names the one open and lists the rest; "11 changes" says how it differs
   from the one before and, pressed, marks every difference in the page; the two arrows beside it step through them.
   The address holds the version (`v`) and what it is compared with (`vs`), so Back and a shared link keep them. */

export interface Versions {
  doc: TaskDocument;
  /** Newest first. */
  list: TaskDocument['versions'];
  shown: number;
  newest: boolean;
  /** The version its changes are counted against: the one before, or the one the address names. */
  before: number | undefined;
  /** The changes are marked in the page. */
  on: boolean;
  /** How many changes, once both versions are read. */
  count: number | null;
  /** The page as its frame draws it: marked when `on`, else the version shown when it is not the newest. */
  html: string | null;
  /** The version an ask pinned, the default while the address names none: a version picked is then always named. */
  pin: number | undefined;
  failed: boolean;
}

/** `pinned`, the version an ask is about, opens unless the address names another; it is read by its number even when
    the page's list of versions does not reach back to it. */
export function useVersions(doc: TaskDocument | undefined, v?: number, vs?: number, pinned?: number): Versions | null {
  const list = [...(doc?.versions ?? [])].sort((a, b) => b.n - a.n);
  // A version the address names opens, read by its number even when the list does not reach it.
  const named = !!doc && !!v;
  const shown = named ? v : (pinned ?? doc?.version ?? 0);
  const marked = !!vs && vs !== shown && list.some((x) => x.n === vs);
  const before = marked ? vs : list.find((x) => x.n < shown)?.n;
  const newest = shown === doc?.version;
  const file = doc?.file_id ?? '';
  const older = useQuery({ ...pageVersionQuery(file, before ?? 0), enabled: !!doc && !!before });
  const newer = useQuery({ ...pageVersionQuery(file, shown), enabled: !!doc && (!!before || !newest) });
  const md = doc?.kind === 'markdown';
  let count: number | null = null;
  let html: string | null = null;
  if (older.data != null && newer.data != null) {
    const page = parsePage(newer.data, md);
    count = markChanges(parsePage(older.data, md), page);
    if (marked) html = frameDoc(page, true);
  }
  if (!marked && !newest && newer.data != null) html = frameDoc(parsePage(newer.data, md));
  if (!doc) return null;
  return {
    doc,
    list,
    shown,
    newest,
    before,
    on: marked,
    count,
    html,
    failed: newer.isError || (marked && older.isError),
    pin: pinned,
  };
}

const clock = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' });
const when = (at: string) => `${fmtDate(at)}, ${clock.format(new Date(at))}`;

/** At the tab row's end: for a page with more than one version the version pick, Changes and its two steps; for every
    page its ⋯ (who can see it, archive, download, delete). */
export function VersionBar({ task, vs, at, onAt }: { task: string; vs: Versions; at: number; onAt: (at: number) => void }) {
  if (vs.list.length < 2) return <PageMenu task={task} vs={vs} />;
  const n = vs.count ?? 0;
  const step = (d: number, label: string, flip: boolean) => (
    <button className="ib ib-sm" type="button" aria-label={label} title={label} disabled={!n} onClick={() => onAt(at < 0 ? (d > 0 ? 0 : n - 1) : (at + d + n) % n)}>
      <Icon name="chevdown" className={flip ? 'flip' : undefined} />
    </button>
  );
  return (
    <>
      <Menu
        trigger={
          <button className="tv-ver" type="button" aria-label={`Version ${vs.shown}`} data-target={`task-version:${task}`}>
            v{vs.shown}
            <Icon name="chevdown" />
          </button>
        }
      >
        {vs.list.map((x) => (
          <MenuItem
            key={x.n}
            label={`v${x.n}`}
            meta={[when(x.at), x.note].filter(Boolean).join(', ')}
            on={x.n === vs.shown}
            onSelect={() => void run('task.showVersion', { task, version: x.n === vs.doc.version && vs.pin === undefined ? null : x.n }, { via: 'click' })}
          />
        ))}
      </Menu>
      {vs.before && (
        <button
          className={vs.on ? 'tv-chg is-on' : 'tv-chg'}
          type="button"
          aria-pressed={vs.on}
          title={`Changes since v${vs.before}`}
          data-target={`task-changes:${task}`}
          disabled={!vs.on && vs.count === 0}
          onClick={() => void run('task.compare', { task, against: vs.on ? null : (vs.before ?? null) }, { via: 'click' })}
        >
          {vs.count == null ? 'Changes' : vs.count === 0 ? 'No changes' : `${vs.count} change${vs.count === 1 ? '' : 's'}`}
        </button>
      )}
      {vs.on && step(-1, 'Previous change', true)}
      {vs.on && step(1, 'Next change', false)}
      <PageMenu task={task} vs={vs} />
    </>
  );
}

/** The open page: its newest version as it is framed, else the version picked, marked when Changes is on. */
export function PageView({ task, vs, at }: { task: string; vs: Versions; at: number }) {
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    if (vs.on && at >= 0) frame.current?.contentWindow?.postMessage({ tico_change: at }, '*');
  }, [vs.on, at]);
  const name = vs.doc.title || vs.doc.name;
  if (vs.newest && !vs.on) return <DocFrame task={task} doc={vs.doc} pin={vs.pin === undefined ? undefined : vs.shown} />;
  if (vs.failed) return <Empty icon="alert" title={`${name} did not load`} />;
  return (
    <div className="frame is-inset">
      {vs.html ? (
        <iframe ref={frame} srcDoc={vs.html} title={`${name}, v${vs.shown}`} sandbox={PAGE_SANDBOX} referrerPolicy="no-referrer" />
      ) : (
        <PageBones />
      )}
    </div>
  );
}
