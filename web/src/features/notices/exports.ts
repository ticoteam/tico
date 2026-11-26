// Notices over every page, for the shell to mount beside the frame. Each takes no props and draws nothing until needed.
export { PlanLock } from './PlanLock';
export { AssetRecovery } from './AssetRecovery';
export { startRecovery } from './recovery';
export { UpdateNotice } from './UpdateNotice';
// Where the desktop app downloads from, for this computer (the first run's Stay in touch).
export { desktopDownloadQuery, downloadHref, systemOf } from './queries';
