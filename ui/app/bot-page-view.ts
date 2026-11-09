/* ui/app/bot-page-view.ts — The bot page's open bot (BOT in bot-page.ts), typed for the .ts files that read it.
   Classic script, loaded after bot-page.ts and its parts: its globals are shared with the other files under ui/app/. */
'use strict';

// BOT is reassigned as the page changes (null when no bot page is open), so readers call this each time and narrow it.
function botPageView(): BotPageView | null { return BOT; }
