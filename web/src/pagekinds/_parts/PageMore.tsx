import { Children, type ReactNode } from 'react';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { askReplay, introKey } from '@/tico/intros';

export interface PageMoreProps {
  /** `MenuItem`s and `MenuRule`s. A destructive item comes last, under a rule, and names the thing. */
  children?: ReactNode;
  /** `<kind>:<id>` for Tico's flights ("tasks:more"). */
  target?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/** The head's "⋯", always last: the page's rare verbs in a menu. Nothing at all when it has no items. A page with a
    first-visit intro (tico/intros.ts) leads with "Show me how this works", which plays it again. */
export function PageMore({ children, target, open, onOpenChange }: PageMoreProps) {
  if (Children.toArray(children).length === 0) return null;
  const intro = introKey(window.location.pathname) !== null;
  return (
    <Menu trigger={<IconButton icon="more" label="More" data-target={target} />} open={open} onOpenChange={onOpenChange}>
      {intro && <MenuItem label="Show me how this works" icon="help" onSelect={() => askReplay()} />}
      {children}
    </Menu>
  );
}
