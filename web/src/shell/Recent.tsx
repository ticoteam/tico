import { Menu as BaseMenu } from '@base-ui/react/menu';
import { useRouter } from '@tanstack/react-router';
import { Fragment, useEffect, useRef, useState, type MouseEvent } from 'react';
import { useRecentViews, type RecentView } from '@/data';
import { Icon } from '@/kit/Icon';
import { usePlacement } from '@/kit/Popover';
import { openSearch } from '@/search';

/* Recent views (spec of 2026-11-26): a clock left of the trail's first part. It opens the last views before this one,
   newest first (data/recentViews.ts); a row opens its view as it was last seen, ⌘/Ctrl-click in a new tab. The trail is
   drawn on a computer only: a phone reaches the same list from Search with nothing typed. */

export const RECENT_LABEL = 'Recent views';
export const RECENT_NONE = 'Pages you open show here.';

/* The drawn button's opener, for `g r` (shell.recentViews). */
let opener: (() => void) | null = null;

/** Opens the list from the keyboard, its top row ready; where no trail is drawn (a phone), Search shows it instead. */
export function openRecentViews(): void {
  if (opener) opener();
  else openSearch();
}

export const trailText = (v: RecentView) => v.trail.join(' › ');

export function RecentViews() {
  const rows = useRecentViews();
  const at = usePlacement();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const popup = useRef<HTMLDivElement>(null);
  const [byKey, setByKey] = useState(false);
  useEffect(() => {
    opener = () => {
      setByKey(true);
      setOpen(true);
    };
    return () => {
      opener = null;
    };
  }, []);
  // opened by `g r`: the top row has the focus, as Enter on the button would give it
  useEffect(() => {
    if (!open || !byKey) return;
    const f = requestAnimationFrame(() => popup.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus());
    setByKey(false);
    return () => cancelAnimationFrame(f);
  }, [open, byKey]);

  const go = (e: MouseEvent, v: RecentView) => {
    // a new tab or window is the browser's own
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    router.history.push(v.to);
  };

  return (
    <BaseMenu.Root open={open} onOpenChange={setOpen}>
      <BaseMenu.Trigger
        render={<button type="button" className="shell-recent" aria-label={RECENT_LABEL} title={RECENT_LABEL} data-target="shell:recent" />}
      >
        <Icon name="clock" />
      </BaseMenu.Trigger>
      <BaseMenu.Portal>
        <BaseMenu.Positioner className={at.layer} style={at.style} side="bottom" align="start" sideOffset={6}>
          <BaseMenu.Popup ref={popup} className={`menu shell-recent-list ${at.look}`} aria-label={RECENT_LABEL}>
            <div className="menu-group-label" aria-hidden="true">
              {RECENT_LABEL}
            </div>
            {rows.length ? (
              rows.map((v) => (
                <BaseMenu.Item key={v.key} render={<a href={v.to} title={trailText(v)} />} label={trailText(v)} onClick={(e) => go(e, v)}>
                  <span className="shell-recent-trail">
                    {v.trail.map((part, i) => (
                      <Fragment key={i}>
                        {i > 0 && <Icon name="chevron" />}
                        <span>{part}</span>
                      </Fragment>
                    ))}
                  </span>
                </BaseMenu.Item>
              ))
            ) : (
              <p className="shell-recent-none">{RECENT_NONE}</p>
            )}
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}
