import { useEffect, useState } from 'react';
import { run } from '@/actions';
import { Button } from '@/kit/Button';
import { CenteredCard } from '@/pagekinds';
import { Cover } from './Cover';
import { SUPPORT_URL } from './queries';
import { watchAssets } from './recovery';

/** For the shell, on every page: when one of the app's own files does not load (most often a release replaced it),
    a card over the app whose one way on is Reload. Nothing otherwise. */
export function AssetRecovery() {
  const [failed, setFailed] = useState(false);
  useEffect(() => watchAssets(() => setFailed(true)), []);
  if (!failed) return null;
  return (
    <Cover label="Tico couldn't finish loading">
      <CenteredCard
        title="Tico couldn't finish loading"
        path="/reload"
        fault="alert"
        actions={
          <Button variant="primary" onClick={() => void run('notices.reload', {}, { via: 'click' })}>
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
