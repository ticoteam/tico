import { useRouter } from '@tanstack/react-router';
import { useEffect } from 'react';
import { SearchDialog } from '@/search';
import { bind, type Bound } from './bind';
import { installKeys } from './keys';
import { startScreen } from './screen';

export interface ActionsRootProps {
  /** `dot.el` from @/tico/dot: where Tico's flights start. */
  dot: Bound['dot'];
  /** The shell's hand fold for a sheet (undefined flips it). */
  fold: NonNullable<Bound['fold']>;
}

/** Rendered once by the shell, inside the router: lends the router, the Dot and folding to dispatch, and starts the
    one key listener, Tico's `screen` topic and the search dialog. */
export function ActionsRoot({ dot, fold }: ActionsRootProps) {
  const router = useRouter();
  useEffect(() => {
    bind({ router, dot, fold });
    const offKeys = installKeys();
    const offScreen = startScreen();
    return () => {
      offKeys();
      offScreen();
    };
  }, [router, dot, fold]);
  return <SearchDialog />;
}
