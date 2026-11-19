// Which permission a page gets (platforms.md 4.3): the microphone and camera for calls, notifications, clipboard
// writes and fullscreen, and only for the company's own origin in the top frame. Everything else is refused. Pure.

const GRANTED = new Set(['media', 'notifications', 'clipboard-sanitized-write', 'fullscreen']);

export function permissionAllowed(
  permission: string,
  details: { requestingUrl?: string; isMainFrame?: boolean; mediaTypes?: readonly string[] },
  origin: string,
): boolean {
  if (!GRANTED.has(permission) || details.isMainFrame !== true || !details.requestingUrl) return false;
  let requesting: string;
  try {
    requesting = new URL(details.requestingUrl).origin;
  } catch {
    return false;
  }
  if (requesting !== origin) return false;
  if (permission === 'media') {
    const types = details.mediaTypes ?? [];
    return types.every((t) => t === 'audio' || t === 'video');
  }
  return true;
}
