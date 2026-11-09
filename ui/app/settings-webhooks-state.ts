/* ui/app/settings-webhooks-state.ts — The incoming webhooks the Settings > Webhooks part holds, for the typed readers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// settings-webhooks.js reassigns WH_TRIGGERS (null until a part opens, then the list), so a reader calls this each time.
function webhookTriggers(): WebhookTrigger[] | null { return WH_TRIGGERS; }
