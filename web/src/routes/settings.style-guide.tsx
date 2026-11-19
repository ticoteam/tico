import { createFileRoute } from '@tanstack/react-router';
import { lazy, Suspense } from 'react';

/* `/settings/style-guide`: the target design drawn with the app's own parts. `?at=<area>` is the area in view (Color
   is the bare address). Loaded on its own so it adds nothing to the first load. */
const StyleGuidePage = lazy(() => import('@/features/styleguide/exports').then((m) => ({ default: m.StyleGuidePage })));

export const Route = createFileRoute('/settings/style-guide')({
  validateSearch: (raw: Record<string, unknown>): { at?: string } =>
    typeof raw.at === 'string' && /^[a-z]+$/.test(raw.at) && raw.at !== 'color' ? { at: raw.at } : {},
  component: StyleGuideRoute,
});

function StyleGuideRoute() {
  const { at } = Route.useSearch();
  return (
    <Suspense fallback={null}>
      <StyleGuidePage at={at} />
    </Suspense>
  );
}
