import { Button } from '@/kit/Button';
import { CenteredCard } from '@/pagekinds';
import { Cover } from './Cover';
import { SUPPORT_URL } from './queries';
import { useAssetsFailed } from './recovery';

/** Beside the router (main.tsx), outside any part that can fail: the card over the app whose one way on is Reload, once
    recovery (recovery.ts startRecovery) cannot reload on its own. Nothing otherwise. */
export function AssetRecovery() {
  if (!useAssetsFailed()) return null;
  return (
    <Cover label="Tico couldn't finish loading">
      <CenteredCard
        title="Tico couldn't finish loading"
        path="/reload"
        fault="alert"
        // not through the actions: they are not there when the shell is not (start, boot, a fault)
        actions={
          <Button variant="primary" onClick={() => location.reload()}>
            Reload
          </Button>
        }
        foot={
          <a href={SUPPORT_URL} target="_blank" rel="noopener noreferrer">
            Contact Tico support
          </a>
        }
      />
    </Cover>
  );
}
