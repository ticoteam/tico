import { Children, type ReactNode } from 'react';
import { IconButton } from '@/kit/IconButton';
import { Menu } from '@/kit/Menu';

export interface PageMoreProps {
  /** `MenuItem`s and `MenuRule`s. A destructive item comes last, under a rule, and names the thing. */
  children?: ReactNode;
  /** `<kind>:<id>` for Tico's flights ("tasks:more"). */
  target?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/** The head's "⋯", always last: the page's rare verbs in a menu. Nothing at all when it has no items. */
export function PageMore({ children, target, open, onOpenChange }: PageMoreProps) {
  if (Children.toArray(children).length === 0) return null;
  return (
    <Menu trigger={<IconButton icon="more" label="More" data-target={target} />} open={open} onOpenChange={onOpenChange}>
      {children}
    </Menu>
  );
}
