import { Link, useLocation, useRouter, type LinkProps } from '@tanstack/react-router';
import { Fragment, useEffect, useState } from 'react';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { useSheet } from '@/shell';
import { showPageName } from '@/tico/pageName';
import type { HeadProps } from '@/pagekinds/types';
import { EditableTitle } from './EditableTitle';

export type PageHeadProps = HeadProps;

const to = (path: string) => path as LinkProps['to'];

type AppRouter = ReturnType<typeof useRouter>;

/** Close an artifact opened from a thread (plan/layers.html): back, when the step before is in the app; else the page's
    parent (`up`), the thread still beside it. */
function closeArtifact(router: AppRouter, up: string | undefined) {
  if (router.history.canGoBack()) return router.history.back();
  const chat = (router.state.location.search as Record<string, unknown>).chat;
  const u = new URL(up ?? '/', location.origin);
  const search = { ...router.options.parseSearch(u.search), ...(typeof chat === 'string' && chat ? { chat } : {}) };
  void router.navigate({ to: u.pathname as never, search: search as never });
}

/**
 * The page's head, one height on every page (`--head-h`). The trail stands before the title at the title's size, muted,
 * a chevron between ("Docs › Returns and refunds", every mock's `.crumb`); the title holds only the name. A task's
 * trail (`sub`) stays in its small where line. Then the controls, "⋯" last, then a button for each sheet the frame
 * tucked behind the page, then ✕ when the page was opened from a thread (`close`).
 */
export function PageHead({ title, path, crumb, lead, after, actions, search, more, onRename, sub, size, top, close }: PageHeadProps) {
  const sheet = useSheet();
  const router = useRouter();
  const opened = useLocation({ select: (l) => (l.search as Record<string, unknown>).via === 'thread' });
  const closes = close ?? opened;
  const big = size === 'large' ? 't-figure' : undefined;
  const back = crumb?.at(-1);
  const [renaming, setRenaming] = useState(false);
  // a name that arrives after the route drew (its data came later, a rename) reaches the tab title and Tico's chip
  useEffect(() => showPageName(title), [title]);
  const trail = crumb && crumb.length > 0 && (
    <nav className={sub ? 'pk-crumb' : 'pk-crumb is-inline'} aria-label="Breadcrumb">
      {crumb.map((c, i) => (
        <Fragment key={c.to}>
          {i > 0 && <Icon name="chevron" />}
          <Link to={to(c.to)}>{c.label}</Link>
        </Fragment>
      ))}
      {!sub && <Icon name="chevron" />}
    </nav>
  );
  const head = (
    <header className="page-head pk-head">
      {/* a phone's back: to the thread the page was opened from, else up the trail */}
      {closes ? (
        <button type="button" className="ib back pk-back" onClick={() => closeArtifact(router, back?.to)} aria-label="Back" title="Back">
          <Icon name="chevron" className="flip" />
        </button>
      ) : back ? (
        <Link className="ib back pk-back" to={to(back.to)} aria-label={`Back to ${back.label}`} title={`Back to ${back.label}`}>
          <Icon name="chevron" className="flip" />
        </Link>
      ) : null}
      {lead}
      <div className={trail && !sub ? 'pk-title has-crumb' : 'pk-title'}>
        {sub ? (
          <div className="tv-where pk-where">
            {trail}
            {sub}
          </div>
        ) : (
          trail
        )}
        {onRename && renaming ? (
          <h1 data-target={`page:${path}`} className={big}>
            <EditableTitle
              title={title}
              label="Title"
              onDone={(next) => {
                setRenaming(false);
                if (next && next !== title) onRename(next);
              }}
            />
          </h1>
        ) : onRename ? (
          <h1
            data-target={`page:${path}`}
            title={title}
            className={big ? `is-editable ${big}` : 'is-editable'}
            tabIndex={0}
            onClick={() => setRenaming(true)}
            onKeyDown={(e) => e.key === 'Enter' && setRenaming(true)}
          >
            {title}
          </h1>
        ) : (
          <h1 data-target={`page:${path}`} title={title} className={big}>
            {title}
          </h1>
        )}
      </div>
      {after}
      <span className="grow" />
      {(search || actions) && (
        <div className="pk-acts">
          {search}
          {actions}
        </div>
      )}
      {typeof more === 'function' ? <IconButton icon="more" label="More" onClick={more} /> : more}
      {sheet.tucked.map((t) => (
        <IconButton key={t.id} icon="panel" label={t.label} quiet onClick={t.open} />
      ))}
      {closes && <IconButton icon="close" label="Close" quiet className="pk-close" onClick={() => closeArtifact(router, back?.to)} />}
    </header>
  );
  return top ? (
    <>
      {top}
      {head}
    </>
  ) : (
    head
  );
}
