// The bot page's open bot: `let BOT` in ui/app/bot-page.ts. It is set when a bot's page renders and null when the page
// leaves, so .ts readers call botPageView() (ui/app/bot-page-view.ts) each time. The fields the page's cards add later
// are optional. The pause state is BotPauseState, below.
interface BotPageView {
  slug: string;
  /** The tab on show (showBotTab in bot-page.ts); null until the first paint. */
  tab: string | null;
  /** The sections this page has loaded once (the `once` helper in bot-page.ts). */
  loaded: Set<string>;
  /** The viewer may not read the bot's details, so the page shows the limited view. */
  limited: boolean;
  /** The work tabs sit beside the chat (set on each tab change). */
  split?: boolean;
  /** The bot's chat thread mounted on the page: its kind and the thread view it returned. */
  thread?: { kind: string; view: ThvView } | null;
  /** The newest unread update in the rail (GET /v2/updates, one row). */
  latest?: UpdUpdate | null;
  /** Teardown the bot's Settings tab registered (settings-bot-editor.js). */
  settingsCleanup?: () => void;
  /** GET /v2/bots/{bot}/pause-state. */
  pauseState?: BotPauseState;
  pauseLoading?: boolean;
  pauseLoadedAt?: number;
  pauseGeneration?: number;
  pauseBusy?: boolean;
  pauseError?: string;
  /** The pause or resume action to retry after it failed. */
  pauseRetry?: string | null;
}

/** GET /v2/bots/{bot}/pause-state (api.d.ts answers it as unknown): the fields the pause row (ui/app/bot-page-pause.ts) reads.
 *  A finished pause or unpause keeps the rest and sets state, revision and paused_by. */
interface BotPauseState {
  state: string;
  /** Who paused it and when, while it is paused by a person; null otherwise. */
  paused_by: { name: string; at: string } | null;
  can_control: boolean;
  company_paused: boolean;
  open_tasks: number;
  /** The operator's name, or their id, or "its operator". */
  owner_id: string | null;
  revision: unknown;
}
