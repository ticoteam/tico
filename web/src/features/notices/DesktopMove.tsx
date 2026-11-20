import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { inOldDesktopApp } from '@/desktop';
import { NavNote } from '@/kit/OfflineBar';
import { desktopDownloadQuery, downloadHref, type DesktopSystem } from './queries';

/* Inside the older desktop app (the Tauri shell), whose page gets none of the desktop features: one note at the foot of
   the menu saying the new app is ready, with its download for this computer. Put away, it stays away for the session
   (until the app quits). Nothing in a browser or in the new app. */

const PUT_AWAY = 'tico-next:desktop-move';

function putAway(): boolean {
  try {
    return sessionStorage.getItem(PUT_AWAY) === '1';
  } catch {
    return false;
  }
}

/** Which installer to offer. The older app's user agent names no system ("TicoHub/2.0 Tauri"), so the platform
    decides, and the user agent only when there is no platform. Never used to decide what the page does. */
export function systemOf(nav: Pick<Navigator, 'platform' | 'userAgent'> & { userAgentData?: { platform?: string } }): DesktopSystem | null {
  const s = `${nav.userAgentData?.platform || nav.platform || nav.userAgent || ''}`.toLowerCase();
  if (/mac/.test(s)) return 'mac';
  if (/\bwin/.test(s)) return 'windows';
  if (/linux|x11/.test(s)) return 'linux';
  return null;
}

/** For the menu's note slot: nothing outside the older desktop app, or once put away. */
export function DesktopMove() {
  const [away, setAway] = useState(putAway);
  const old = inOldDesktopApp();
  const system = old ? systemOf(navigator) : null;
  const { data } = useQuery({ ...desktopDownloadQuery(system ?? 'mac'), enabled: !!system && !away });
  if (!old || away) return null;
  const dismiss = () => {
    try {
      sessionStorage.setItem(PUT_AWAY, '1');
    } catch {
      // no storage: it stays away until the page reloads
    }
    setAway(true);
  };
  return (
    <NavNote icon="download" title="A new Tico desktop app is ready." onDismiss={dismiss}>
      {/* A new tab: the older app opens it in the default browser, which downloads the file. */}
      <a className="btn" href={downloadHref(system ? data : undefined)} target="_blank" rel="noopener noreferrer">
        Download
      </a>
    </NavNote>
  );
}
