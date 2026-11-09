// The bot page's open bot: `let BOT` in ui/app/bot-page.js. It is set when a bot's page renders and null when the page
// leaves, so .ts readers call botPageView() (ui/app/bot-page-view.ts) each time. The fields the page's cards add later
// are optional. The server answers the pause state and the updates as `unknown` (api.d.ts), so those stay unknown.
interface BotPageView {
  slug: string;
  /** The tab on show (showBotTab in bot-page.js); null until the first paint. */
  tab: string | null;
  /** The sections this page has loaded once (the `once` helper in bot-page.js). */
  loaded: Set<string>;
  /** The viewer may not read the bot's details, so the page shows the limited view. */
  limited: boolean;
  /** The work tabs sit beside the chat (set on each tab change). */
  split?: boolean;
  /** The bot's chat thread mounted on the page: its kind and the thread view it returned. */
  thread?: { kind: string; view: unknown } | null;
  /** The newest unread update in the rail (GET /v2/updates, one row). */
  latest?: unknown;
  /** Teardown the bot's Settings tab registered (settings-bot-editor.js). */
  settingsCleanup?: () => void;
  /** GET /v2/bots/{bot}/pause-state. */
  pauseState?: unknown;
  pauseLoading?: boolean;
  pauseLoadedAt?: number;
  pauseGeneration?: number;
  pauseBusy?: boolean;
  pauseError?: string;
  /** The pause or resume action to retry after it failed. */
  pauseRetry?: string | null;
}
