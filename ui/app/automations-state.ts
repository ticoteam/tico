// AU (ui/app/automations.ts) is null between visits; this returns it typed with that null. Read it each time: AU is reassigned.
'use strict';
function automationsState(): AutomationsState | null { return AU; }
